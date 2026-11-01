"""Tests for webui/auth_middleware.py - Basic Auth middleware."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from webui import config
from webui.auth_middleware import BasicAuthMiddleware


def test_basic_auth_disabled(monkeypatch):
    """No auth when HTTP_USER not set."""
    monkeypatch.setattr(config, "HTTP_USER", None)
    monkeypatch.setattr(config, "HTTP_PASSWORD", None)

    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/test")
    async def test():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_basic_auth_enabled_wrong_credentials(monkeypatch):
    """Wrong credentials rejected."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")

    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/test")
    async def test():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/test", auth=("wrong", "pass"))
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_basic_auth_enabled_correct_credentials(monkeypatch):
    """Correct credentials accepted."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")

    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/test")
    async def test():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/test", auth=("user", "pass"))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_basic_auth_disabled_via_env(monkeypatch):
    """Middleware disabled when HTTP_USER not set."""
    monkeypatch.setattr(config, "HTTP_USER", None)
    monkeypatch.setattr(config, "HTTP_PASSWORD", None)
    from webui.auth_middleware import BasicAuthMiddleware
    assert BasicAuthMiddleware._enabled() is False


def test_basic_auth_enabled_via_env(monkeypatch):
    """Middleware enabled when both set."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    from webui.auth_middleware import BasicAuthMiddleware
    assert BasicAuthMiddleware._enabled() is True


def test_basic_auth_health_endpoint_always_accessible(monkeypatch):
    """Health endpoint accessible without auth even when auth enabled."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")

    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    client = TestClient(app)
    # No auth but health endpoint should work
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}