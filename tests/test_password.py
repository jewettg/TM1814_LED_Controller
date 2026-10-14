import sys

import pytest
from fastapi.testclient import TestClient

from tm1814_led import api as api_module
from tm1814_led.api import create_app
from tm1814_led.auth import Auth, fingerprint, hash_password, verify_hash


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(api_module.time, "sleep", lambda s: None)


def make(tmp_path, password="oldpassword"):
    return create_app(data_dir=str(tmp_path), simulate=True, password=password, start_background=False)


def signed_in(app, password="oldpassword"):
    c = TestClient(app, follow_redirects=False)
    c.__enter__()
    assert c.post("/api/login", json={"password": password}).status_code == 200
    return c


def change(c, current, new):
    return c.post("/api/password", json={"current": current, "new": new})


# ---- hashing and the Auth object
def test_hashes_are_salted_and_verifiable():
    a, b = hash_password("secret123"), hash_password("secret123")
    assert a["hash"] != b["hash"] and a["salt"] != b["salt"]            # a fresh salt every time
    assert verify_hash("secret123", a) and not verify_hash("secret124", a)
    assert "secret123" not in str(a)


def test_an_override_replaces_the_settings_file_password_and_signs_out_old_sessions():
    a = Auth("fromfile", "secret")
    token = a.make_token()
    assert a.check_password("fromfile") and a.valid_token(token)
    a.set_override(hash_password("changed-later"))
    assert a.check_password("changed-later") and not a.check_password("fromfile")
    assert not a.valid_token(token)                                      # the signing key changed
    assert a.valid_token(a.make_token())
    assert Auth(None, "secret", override=hash_password("x" * 8)).enabled is True


def test_fingerprint_differs_per_password_and_is_not_the_password():
    assert fingerprint("a") != fingerprint("b") and len(fingerprint("a")) == 64
    assert fingerprint(None) == fingerprint("")
    assert fingerprint("hunter22") != "hunter22" and "hunter22" not in fingerprint("hunter22")


# ---- the endpoint
def test_change_password_end_to_end(tmp_path):
    app = make(tmp_path)
    mine, other = signed_in(app), signed_in(app)
    assert change(mine, "oldpassword", "brand-new-pw").status_code == 200
    assert mine.get("/api/status").status_code == 200                    # the person who changed it stays signed in
    assert other.get("/api/status").status_code == 401                   # everyone else is signed out
    fresh = TestClient(app, follow_redirects=False)
    assert fresh.post("/api/login", json={"password": "oldpassword"}).status_code == 401
    assert fresh.post("/api/login", json={"password": "brand-new-pw"}).status_code == 200


def test_current_password_must_be_right_and_new_one_acceptable(tmp_path):
    c = signed_in(make(tmp_path))
    r = change(c, "wrong", "brand-new-pw")
    assert r.status_code == 401 and "current password" in r.json()["detail"]
    for bad, text in (("short", "at least 8"), ("oldpassword", "same"), ("x" * 201, "too long")):
        r = change(c, "oldpassword", bad)
        assert r.status_code == 422 and text in r.json()["detail"]
    assert c.post("/api/login", json={"password": "oldpassword"}).status_code == 200   # nothing changed


def test_changing_the_password_requires_being_signed_in(tmp_path):
    anon = TestClient(make(tmp_path), follow_redirects=False)
    assert change(anon, "oldpassword", "brand-new-pw").status_code == 401


def test_guessing_the_current_password_is_throttled(tmp_path):
    c = signed_in(make(tmp_path))
    for _ in range(8):
        assert change(c, "nope", "brand-new-pw").status_code == 401
    assert change(c, "oldpassword", "brand-new-pw").status_code == 429


# ---- persistence and the installer
def test_a_changed_password_survives_a_restart_and_the_old_one_stays_dead(tmp_path):
    first = signed_in(make(tmp_path))
    change(first, "oldpassword", "brand-new-pw")
    again = make(tmp_path)                                               # same settings-file password, new process
    c = TestClient(again, follow_redirects=False)
    assert c.post("/api/login", json={"password": "oldpassword"}).status_code == 401
    assert c.post("/api/login", json={"password": "brand-new-pw"}).status_code == 200


def test_rerunning_the_installer_with_a_new_password_wins_over_a_changed_one(tmp_path):
    first = signed_in(make(tmp_path))
    change(first, "oldpassword", "brand-new-pw")
    reinstalled = make(tmp_path, password="installer-pw")                # the settings file now holds another password
    c = TestClient(reinstalled, follow_redirects=False)
    assert c.post("/api/login", json={"password": "installer-pw"}).status_code == 200
    assert c.post("/api/login", json={"password": "brand-new-pw"}).status_code == 401
    assert reinstalled.state.db.get_password_override() is None          # the stale override was discarded


def test_the_database_stores_a_hash_never_the_password(tmp_path):
    c = signed_in(make(tmp_path))
    change(c, "oldpassword", "brand-new-pw")
    stored = open(tmp_path / "tm1814.db", "rb").read()
    import sqlite3
    row = sqlite3.connect(tmp_path / "tm1814.db").execute("SELECT value FROM settings WHERE key = '_password'").fetchone()[0]
    assert "brand-new-pw" not in row and "salt" in row and "iterations" in row


def test_the_password_is_not_an_editable_setting(tmp_path):
    c = signed_in(make(tmp_path))
    assert "_password" not in c.get("/api/settings").json()
    assert c.put("/api/settings", json={"_password": "x"}).status_code == 422


# ---- command line
def test_cli_password_command(tmp_path, monkeypatch, capsys):
    import socket, threading, time, uvicorn
    from tm1814_led import cli, client as client_mod
    from tm1814_led.client import SessionCache
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    app = make(tmp_path / "data")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    while not server.started:
        time.sleep(0.05)
    monkeypatch.setattr(SessionCache, "_default_dir", staticmethod(lambda: tmp_path / "cache"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("TM1814_PASSWORD", raising=False)
    monkeypatch.setenv("TM1814_URL", f"http://127.0.0.1:{port}")
    answers = iter(["oldpassword",                       # sign-in prompt
                    "oldpassword", "cli-new-pass", "cli-new-pass"])   # current, new, again
    monkeypatch.setattr(client_mod.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    try:
        cli._CLIENT.clear()
        assert cli.main(["password", "--no-color"]) == 0
        assert "Password changed" in capsys.readouterr().out
        cli._CLIENT.clear()
        assert cli.main(["status", "--no-color"]) == 0           # this terminal stayed signed in (no new prompt needed)
        other = TestClient(app, follow_redirects=False)
        assert other.post("/api/login", json={"password": "oldpassword"}).status_code == 401
        assert other.post("/api/login", json={"password": "cli-new-pass"}).status_code == 200
    finally:
        server.should_exit = True
        t.join(timeout=5)
