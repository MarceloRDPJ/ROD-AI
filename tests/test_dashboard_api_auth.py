import pytest
from fastapi.testclient import TestClient

from jarvis.api.app import app, get_config
from jarvis.api.dashboard_auth import DashboardAuth, hash_password


PASSWORD = "uma-senha-forte"


@pytest.fixture(autouse=True)
def restore_dashboard_auth_state():
    marker = object()
    previous = getattr(app.state, "dashboard_auth", marker)
    yield
    if previous is marker:
        if hasattr(app.state, "dashboard_auth"):
            delattr(app.state, "dashboard_auth")
    else:
        app.state.dashboard_auth = previous


def _configured_auth() -> DashboardAuth:
    return DashboardAuth(
        enabled=True,
        username="marcelo",
        password_hash=hash_password(PASSWORD, salt=b"0123456789abcdef"),
        session_secret="s" * 48,
    )


def test_dashboard_requires_login_but_health_remains_public():
    app.state.dashboard_auth = _configured_auth()
    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        protected = client.get("/api/system/status")
        health = client.get("/api/system/health")

    assert root.status_code == 307
    assert root.headers["location"] == "/login"
    assert protected.status_code == 401
    assert health.status_code == 200


def test_login_cookie_and_csrf_protect_unsafe_routes():
    app.state.dashboard_auth = _configured_auth()
    with TestClient(app) as client:
        denied = client.post("/api/auth/login", json={"username": "marcelo", "password": "errada"})
        login = client.post("/api/auth/login", json={"username": "marcelo", "password": PASSWORD})
        status = client.get("/api/auth/status")
        csrf = status.json()["csrf_token"]
        without_csrf = client.post("/api/auth/logout")
        logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})

    assert denied.status_code == 401
    assert login.status_code == 200
    assert "httponly" in login.headers["set-cookie"].lower()
    assert "samesite=strict" in login.headers["set-cookie"].lower()
    assert status.json()["authenticated"] is True
    assert without_csrf.status_code == 403
    assert logout.status_code == 200


def test_authentication_fails_closed_when_credentials_are_missing():
    app.state.dashboard_auth = DashboardAuth(
        enabled=True,
        username="marcelo",
        password_hash="",
        session_secret="",
    )
    with TestClient(app) as client:
        protected = client.get("/api/system/status")
        status = client.get("/api/auth/status")

    assert protected.status_code == 503
    assert status.json() == {
        "enabled": True,
        "configured": False,
        "authenticated": False,
        "csrf_token": None,
        "expires_at": None,
    }


def test_openapi_marks_admin_routes_as_cookie_authenticated():
    app.openapi_schema = None
    schema = app.openapi()

    assert schema["paths"]["/api/system/status"]["get"]["security"] == [{"DashboardSession": []}]
    assert "security" not in schema["paths"]["/api/system/health"]["get"]


@pytest.mark.asyncio
async def test_sanitized_config_exposes_only_pc_configuration_flags(monkeypatch):
    from jarvis.config import Config

    monkeypatch.setattr(Config, "PC_MAC", "AA:BB:CC:DD:EE:FF", raising=False)
    monkeypatch.setattr(Config, "PC_IP", "192.168.1.50", raising=False)

    config = await get_config()

    assert config["wake_on_lan_configured"] is True
    assert config["pc_status_configured"] is True
    assert "pc_mac" not in config
    assert "pc_ip" not in config
    assert "AA:BB:CC:DD:EE:FF" not in str(config)
    assert "192.168.1.50" not in str(config)
