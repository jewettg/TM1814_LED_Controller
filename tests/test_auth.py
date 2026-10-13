import pytest
from fastapi.testclient import TestClient

from tm1814_led import api as api_module
from tm1814_led.api import create_app
from tm1814_led.auth import COOKIE, MAX_FAILURES, Auth


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_module.time, "sleep", lambda s: None)       # skip the anti-guessing delay
    app = create_app(data_dir=":memory:", simulate=True, password="s3cret", start_background=False)
    with TestClient(app, follow_redirects=False) as c:
        yield c


def login(c, password="s3cret"):
    return c.post("/api/login", json={"password": password})


def test_api_without_login_gets_json_401_and_no_browser_popup(client):
    r = client.get("/api/status")
    assert r.status_code == 401 and r.json() == {"detail": "login required"}
    assert "www-authenticate" not in r.headers        # a WWW-Authenticate header would trigger the browser's own prompt


def test_pages_redirect_to_the_login_page(client):
    for path in ("/", "/docs", "/js/app.js"):
        r = client.get(path)
        assert r.status_code == 302 and r.headers["location"] == "/login"
    page = client.get("/login")
    assert page.status_code == 200 and "Sign in" in page.text and "text/html" in page.headers["content-type"]
    assert client.get("/style.css").status_code == 200                   # the login page's stylesheet is public


def test_wrong_password_rejected_right_password_sets_session(client):
    assert login(client, "nope").status_code == 401
    assert client.get("/api/status").status_code == 401
    ok = login(client)
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"]
    assert COOKIE in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/status").status_code == 200
    assert client.get("/").status_code == 200                            # the web page itself now loads
    assert client.get("/api/auth").json() == {"required": True, "authenticated": True}


def test_logout_ends_the_session(client):
    login(client)
    assert client.get("/api/status").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/auth").json()["authenticated"] is False


def test_responses_are_never_cached(client):
    assert client.get("/api/auth").headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-cache"                  # redirect, not a cached page
    login(client)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/api/status").headers["cache-control"] == "no-store"


def test_http_basic_is_not_accepted(client):
    r = client.get("/api/status", auth=("anyone", "s3cret"))      # the web page is the only interface
    assert r.status_code == 401


def test_forged_cookie_is_rejected(client):
    client.cookies.set(COOKIE, "v1.99999999999.deadbeef")
    assert client.get("/api/status").status_code == 401


def test_repeated_wrong_passwords_are_throttled(client):
    for _ in range(MAX_FAILURES):
        assert login(client, "bad").status_code == 401
    blocked = login(client, "s3cret")                                    # even the right password waits
    assert blocked.status_code == 429 and "Try again" in blocked.json()["detail"]


def test_no_password_means_open_and_no_login_page():
    app = create_app(data_dir=":memory:", simulate=True, start_background=False)
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/api/status").status_code == 200
        assert c.get("/login").status_code == 302 and c.get("/login").headers["location"] == "/"
        assert c.get("/api/auth").json() == {"required": False, "authenticated": True}


def test_session_secret_is_stable_across_restarts(tmp_path):
    from tm1814_led.db import Database
    path = str(tmp_path / "x.db")
    first = Database(path).auth_secret()
    assert Database(path).auth_secret() == first and len(first) == 64
    token = Auth("pw", first).make_token()
    assert Auth("pw", first).valid_token(token)                          # survives a restart


# ---- the token itself
def test_token_expiry_tampering_and_password_change():
    a = Auth("pw", "secret", session_seconds=100)
    t = a.make_token(now=1000)
    assert a.valid_token(t, now=1050) and not a.valid_token(t, now=1101)
    parts = t.split(".")
    assert not a.valid_token(f"{parts[0]}.{int(parts[1]) + 999}.{parts[2]}", now=1050)    # lifetime cannot be extended
    assert not a.valid_token(None) and not a.valid_token("junk") and not a.valid_token("v2.1.2")
    assert not Auth("different", "secret", 100).valid_token(t, now=1050)                    # new password signs everyone out
    assert not Auth("pw", "other-secret", 100).valid_token(t, now=1050)


def test_throttle_window_expires():
    a = Auth("pw", "s")
    for _ in range(MAX_FAILURES):
        a.record_failure("1.2.3.4", now=0)
    assert a.blocked_for("1.2.3.4", now=1) > 0
    assert a.blocked_for("1.2.3.4", now=10_000) == 0
    assert a.blocked_for("5.6.7.8", now=1) == 0
