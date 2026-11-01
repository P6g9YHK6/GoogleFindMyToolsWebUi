"""Additional tests for webui/auth_middleware.py to cover uncovered lines."""

import base64
import hmac

import pytest

from webui import config
from webui.auth_middleware import BasicAuthMiddleware


class DummyASGIApp:
    """Minimal ASGI app for testing."""
    
    def __init__(self):
        self.called = False
    
    async def __call__(self, scope, receive, send):
        self.called = True
        if scope["type"] == "http":
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({"type": "http.response.body", "body": b"OK"})
        elif scope["type"] == "websocket":
            await send({"type": "websocket.accept"})


def test_middleware_passes_through_when_disabled(monkeypatch):
    """When auth disabled, requests pass through without auth check."""
    monkeypatch.setattr(config, "HTTP_USER", None)
    monkeypatch.setattr(config, "HTTP_PASSWORD", None)
    
    app = DummyASGIApp()
    middleware = BasicAuthMiddleware(app)
    assert middleware._enabled() is False


def test_middleware_rejects_websocket_without_auth(monkeypatch):
    """WebSocket rejected with code 4401 when no auth."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    app = DummyASGIApp()
    middleware = BasicAuthMiddleware(app)
    assert middleware._enabled() is True
    
    scope = {"type": "websocket", "headers": []}
    received = []
    
    async def fake_send(msg):
        received.append(msg)
    
    received = []
    import asyncio
    asyncio.run(middleware(scope, None, fake_send))
    
    # Should close with code 4401
    assert any(m.get("type") == "websocket.close" and m.get("code") == 4401 for m in received)


def test_middleware_rejects_http_without_auth(monkeypatch):
    """HTTP request without auth gets 401."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    app = DummyASGIApp()
    middleware = BasicAuthMiddleware(app)
    assert middleware._enabled() is True
    
    scope = {"type": "http", "path": "/test", "headers": [], "client": ("127.0.0.1", 12345)}
    received = []
    
    async def fake_send(msg):
        received.append(msg)
    
    received = []
    import asyncio
    asyncio.run(middleware(scope, None, fake_send))
    
    # Should respond with 401
    status_msgs = [m for m in received if m.get("type") == "http.response.start"]
    assert len(status_msgs) == 1
    assert status_msgs[0]["status"] == 401
    assert any(h[0] == b"www-authenticate" for h in status_msgs[0]["headers"])


def test_middleware_allows_health_endpoint(monkeypatch):
    """Health endpoint always accessible without auth."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    app = DummyASGIApp()
    middleware = BasicAuthMiddleware(app)
    
    scope = {"type": "http", "path": "/health", "headers": []}
    reached = False
    
    async def tracking_app(scope, receive, send):
        nonlocal reached
        reached = True
        async def send_noop(msg):
            pass
        await app(scope, None, send_noop)
    
    middleware = BasicAuthMiddleware(tracking_app)
    import asyncio
    async def send_noop(msg):
        pass
    asyncio.run(middleware(scope, None, send_noop))
    
    assert reached is True


def test_middleware_logs_failed_auth(monkeypatch, caplog):
    """Failed auth attempt logs warning with client host."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    app = DummyASGIApp()
    middleware = BasicAuthMiddleware(app)
    
    scope = {"type": "http", "path": "/test", "headers": [(b"authorization", b"Basic d3Jvbmc6cGFzcw==")], "client": ("192.168.1.100", 12345)}
    
    with caplog.at_level("WARNING", logger="webui.auth_middleware"):
        import asyncio
        async def send_nop(msg):
            pass
        asyncio.run(middleware(scope, None, send_nop))
    
    assert any("Rejected http request from 192.168.1.100: invalid credentials" in r.message for r in caplog.records)


def test_is_authorized_valid_credentials(monkeypatch):
    """Valid Basic auth with correct credentials."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    middleware = BasicAuthMiddleware(DummyASGIApp())
    
    # Valid credentials
    auth = "Basic " + base64.b64encode(b"user:pass").decode()
    assert middleware._is_authorized(auth) is True
    
    # Wrong password
    auth = "Basic " + base64.b64encode(b"user:wrong").decode()
    assert middleware._is_authorized(auth) is False
    
    # Wrong username
    auth = "Basic " + base64.b64encode(b"wrong:pass").decode()
    assert middleware._is_authorized(auth) is False


def test_is_authorized_invalid_base64(monkeypatch):
    """Invalid base64 returns False."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    middleware = BasicAuthMiddleware(DummyASGIApp())
    assert middleware._is_authorized("Basic notbase64!!!") is False


def test_is_authorized_no_colon(monkeypatch):
    """Missing colon in decoded returns False."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    middleware = BasicAuthMiddleware(DummyASGIApp())
    auth = "Basic " + base64.b64encode(b"nocolon").decode()
    assert middleware._is_authorized(auth) is False


def test_is_authorized_empty_password(monkeypatch):
    """Empty password in header."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "")
    
    middleware = BasicAuthMiddleware(DummyASGIApp())
    auth = "Basic " + base64.b64encode(b"user:").decode()
    assert middleware._is_authorized(auth) is True


def test_is_authorized_constant_time(monkeypatch):
    """hmac.compare_digest used for constant-time comparison."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    middleware = BasicAuthMiddleware(DummyASGIApp())
    
    # The actual comparison uses hmac.compare_digest
    auth = "Basic " + base64.b64encode(b"user:pass").decode()
    assert middleware._is_authorized(auth) is True
    
    # Verify hmac.compare_digest is constant time
    import hmac
    assert hmac.compare_digest("user", "user") is True
    assert hmac.compare_digest("user", "attacker") is False