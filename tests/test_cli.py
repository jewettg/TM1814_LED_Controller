import json
import socket
import sys
import threading
import time

import pytest
import uvicorn

from tm1814_led import cli, client as client_mod, diagnostics
from tm1814_led.api import create_app
from tm1814_led.client import SessionCache, ServiceClient, ServiceError

from test_controller import FakeController   # a tiny fake controller on a local socket

PASSWORD = "s3cret"


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A real service on a local port (simulated controller), plus an isolated session cache and fake terminal."""
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    app = create_app(data_dir=":memory:", simulate=True, password=PASSWORD, start_background=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(SessionCache, "_default_dir", staticmethod(lambda: tmp_path / "cache"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("TM1814_PASSWORD", raising=False)
    monkeypatch.setenv("TM1814_URL", f"http://127.0.0.1:{port}")
    cli._CLIENT.clear()
    prompts = []

    def fake_getpass(prompt=""):
        prompts.append(prompt)
        return PASSWORD
    monkeypatch.setattr(client_mod.getpass, "getpass", fake_getpass)
    yield type("S", (), {"app": app, "port": port, "prompts": prompts, "cache": SessionCache(tmp_path / "cache")})
    server.should_exit = True
    thread.join(timeout=5)


def run(*argv):
    cli._CLIENT.clear()          # each command line starts a new process in real life
    return cli.main(list(argv))


def test_password_is_asked_once_per_terminal_session(service, capsys):
    assert run("status", "--no-color") == 0
    assert len(service.prompts) == 1 and "Password" in service.prompts[0]
    assert "LED Controller" in capsys.readouterr().out
    assert run("effects", "--no-color", "ghost") == 0              # a later command: no new prompt
    assert run("runlists", "--no-color") == 0
    assert len(service.prompts) == 1
    cache = json.loads(service.cache.path.read_text())
    assert cache["token"] and PASSWORD not in service.cache.path.read_text()   # the password itself is never stored
    assert oct(service.cache.path.stat().st_mode)[-3:] == "600"


def test_logout_ends_the_session(service, capsys):
    run("status", "--no-color"); run("logout", "--no-color")
    assert not service.cache.path.exists()
    run("status", "--no-color")
    assert len(service.prompts) == 2                                # asked again


def test_login_command_always_asks(service, capsys):
    run("login", "--no-color"); run("login", "--no-color")
    assert len(service.prompts) == 2 and "Signed in" in capsys.readouterr().out


def test_wrong_password_is_retried_then_accepted(service, monkeypatch, capsys):
    answers = iter(["wrong", PASSWORD])
    monkeypatch.setattr(client_mod.getpass, "getpass", lambda prompt="": next(answers))
    assert run("status", "--no-color") == 0
    assert "not the password" in capsys.readouterr().err


def test_three_wrong_passwords_fail(service, monkeypatch, capsys):
    monkeypatch.setattr(client_mod.getpass, "getpass", lambda prompt="": "nope")
    assert run("status", "--no-color") == 1
    assert "too many wrong passwords" in capsys.readouterr().err


def test_no_terminal_and_no_env_password_is_a_clear_error(service, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert run("status", "--no-color") == 1
    assert "no terminal" in capsys.readouterr().err


def test_env_password_supports_scripts(service, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setenv("TM1814_PASSWORD", PASSWORD)
    assert run("status", "--no-color") == 0 and service.prompts == []


def test_cached_session_that_no_longer_works_asks_again(service, capsys):
    run("status", "--no-color")
    service.cache.save(service.cache.load(f"http://127.0.0.1:{service.port}") and f"http://127.0.0.1:{service.port}", "v1.1.badbadbad")
    assert run("status", "--no-color") == 0
    assert len(service.prompts) == 2


def test_unreachable_service_gives_a_friendly_message(service, monkeypatch, capsys):
    monkeypatch.setenv("TM1814_URL", "http://127.0.0.1:9")
    assert run("status", "--no-color") == 1
    err = capsys.readouterr()
    assert "cannot reach the service" in err.err and "tm1814-led service" in err.out


def test_effects_play_stop_and_json(service, capsys):
    assert run("effects", "--no-color", "lightning") == 0
    out = capsys.readouterr().out
    assert "Lightning (storm)" in out and "Animated" in out and "Halloween - Embers" not in out
    assert run("effects", "--no-color", "--native", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all(r["family"] == "native" for r in rows)
    assert run("effects", "--no-color", "--fixed", "--json") == 0
    fixed = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in fixed] == ["Night (Bright)", "Night (Dim)", "Night (Low)", "Night (Medium)"]
    assert run("effects", "--no-color", "--animated", "night") == 0
    assert "Night (Hourly 4-6)" in capsys.readouterr().out
    assert run("play", "--no-color", "halloween - embers") == 0                  # case-insensitive exact name
    assert "Playing effect" in capsys.readouterr().out
    assert run("play", "--no-color", "Halloween show", "--runlist") == 0
    assert run("stop", "--no-color") == 0 and "Stopped" in capsys.readouterr().out


def test_play_name_resolution_errors(service, capsys):
    assert run("play", "--no-color", "lightning") == 2                          # three effects match
    assert "matches more than one" in capsys.readouterr().err
    assert run("play", "--no-color", "no such thing") == 2
    assert "nothing named" in capsys.readouterr().err


def test_brightness_pause_resume_next(service, capsys):
    assert run("brightness", "--no-color", "40") == 0 and "40%" in capsys.readouterr().out
    assert run("brightness", "--no-color", "400") == 2
    for c in ("pause", "resume", "next"):
        assert run(c, "--no-color") == 0


def test_schedules_and_logs(service, capsys):
    db = service.app.state.db
    rl = db.list_runlists()[0]
    db.create_schedule("Nightly", rl["id"], {"start_time": "18:00", "end_time": "23:00", "repeat": {"type": "daily"}})
    assert run("schedules", "--no-color") == 0
    out = capsys.readouterr().out
    assert "Nightly" in out and "every day" in out
    assert run("logs", "--no-color", "-n", "5") == 0
    db.add_log(time.time() - 3 * 86400, "WARNING", "tm1814.test", "something three days ago")
    db.add_log(time.time() - 3600, "ERROR", "tm1814.test", "something an hour ago")
    capsys.readouterr()
    assert run("logs", "--no-color", "--since", "7d", "--level", "warning", "--grep", "something") == 0
    out = capsys.readouterr().out
    assert "three days ago" in out and "an hour ago" in out and "2 of 2 matching" in out and "kept for 60 days" in out
    assert run("logs", "--no-color", "--since", "6h", "--grep", "something") == 0
    out = capsys.readouterr().out
    assert "an hour ago" in out and "three days ago" not in out
    assert run("logs", "--no-color", "--since", "banana") == 2


def test_direct_commands_need_the_password_first_and_use_the_fake_controller(service, capsys):
    fake = FakeController()
    try:
        host = f"127.0.0.1:{fake.port}"
        assert run("state", "--no-color", "--controller-host", host) == 0
        assert len(service.prompts) == 1                                       # no lights without the password
        out = capsys.readouterr().out
        assert "Power" in out and "250" in out
        assert run("off", "--no-color", "--controller-host", host) == 0
        assert run("color", "--no-color", "--controller-host", host, "orange") == 0
        deadline = time.time() + 5
        while time.time() < deadline and not (any(r.startswith(b"\x71\x24") for r in fake.received)
                                              and any(r.startswith(b"\xb0\xb1\xb2\xb3") for r in fake.received)):
            time.sleep(0.05)
        assert any(r.startswith(b"\x71\x24") for r in fake.received)           # power-off command reached the controller
        assert any(r.startswith(b"\xb0\xb1\xb2\xb3") for r in fake.received)   # and the color message
    finally:
        fake.close()


def test_direct_commands_without_a_saved_controller_explain_what_to_do(service, capsys):
    assert run("state", "--no-color") == 2
    assert "No controller address" in capsys.readouterr().err


def test_test_command_lists_patterns(service, capsys):
    assert run("test", "--no-color") == 0
    out = capsys.readouterr().out
    assert "halves" in out and "chase" in out


# ---- pure helpers
def test_parse_color_forms():
    assert cli.parse_color("orange") == (255, 90, 0)
    assert cli.parse_color("#ff5a00") == (255, 90, 0) and cli.parse_color("ff5a00") == (255, 90, 0)
    assert cli.parse_color("1, 2,3") == (1, 2, 3)
    for bad in ("nope", "300,0,0", "1,2"):
        with pytest.raises(ValueError):
            cli.parse_color(bad)


def test_formatting_helpers():
    assert cli.fmt_secs(75) == "1:15" and cli.fmt_secs(3725) == "1:02:05" and cli.fmt_secs(None) == "until stopped"
    out = cli.Out(color=False)
    assert out.p("x", "red") == "x"


def test_session_cache_rules(tmp_path):
    c = SessionCache(tmp_path)
    assert c.load("http://a") is None
    c.save("http://a", "tok")
    assert c.load("http://a") == "tok" and c.load("http://b") is None          # tied to the service address
    data = json.loads(c.path.read_text()); data["saved"] -= 13 * 3600
    c.path.write_text(json.dumps(data))
    assert c.load("http://a") is None                                          # expires after 12 hours
    c.save("http://a", "tok2"); c.clear()
    assert c.load("http://a") is None


@pytest.mark.parametrize("name", sorted(diagnostics.PATTERNS))
def test_every_test_pattern_builds_valid_frames(name):
    fn, _ = diagnostics.PATTERNS[name]
    result = fn(250, 100) if name in ("fillto", "single") else fn(250)
    frames = [f for f, _ in result] if name == "chase" else [result[0]]
    assert len(frames[0]) == 250 and all(len(px) == 3 for px in frames[0])
    if name == "halves":
        assert frames[0][0] == (255, 0, 0) and frames[0][-1] == (0, 0, 255)
    if name == "fillto":
        assert frames[0][99] == (0, 255, 0) and frames[0][100] == (0, 0, 0)


def test_serve_refuses_to_start_without_a_password(monkeypatch):
    monkeypatch.delenv("TM1814_PASSWORD", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve"])
    assert "password is required" in str(exc.value)


def test_no_arguments_prints_help(capsys):
    assert cli.main([]) == 0 and "commands:" in capsys.readouterr().out


def test_env_file_supplies_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("TM1814_PASSWORD", raising=False); monkeypatch.delenv("TM1814_PORT", raising=False)
    f = tmp_path / "x.env"
    f.write_text("# comment\nTM1814_PASSWORD=from-file\nTM1814_PORT=9999\n")
    cli.load_env_file(str(f))
    import os
    assert os.environ["TM1814_PASSWORD"] == "from-file" and os.environ["TM1814_PORT"] == "9999"
    monkeypatch.delenv("TM1814_PASSWORD"); monkeypatch.delenv("TM1814_PORT")
