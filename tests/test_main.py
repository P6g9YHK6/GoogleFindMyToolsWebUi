"""Tests for webui/main.py - app lifespan and health endpoint."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from webui import config, demo_mode, main
from webui.auth_middleware import BasicAuthMiddleware


def test_basic_auth_middleware_disabled():
    """Middleware is no-op when HTTP_USER/HTTP_PASSWORD not set."""
    assert BasicAuthMiddleware._enabled() is False


def test_basic_auth_middleware_enabled(monkeypatch):
    """Middleware enabled when both credentials set."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    assert BasicAuthMiddleware._enabled() is True


def test_basic_auth_middleware_partial_credentials(monkeypatch):
    """Middleware disabled when only one credential set."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", None)
    assert BasicAuthMiddleware._enabled() is False
    
    monkeypatch.setattr(config, "HTTP_USER", None)
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    assert BasicAuthMiddleware._enabled() is False


def test_health_endpoint_demo_mode(monkeypatch):
    """Health endpoint returns ok in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DATA_DIR", "/tmp/test")
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: True)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: True)
    
    client = TestClient(main.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_endpoint_not_demo_all_ok(monkeypatch):
    """Health endpoint returns ok when all checks pass."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "DATA_DIR", "/tmp/test")
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: True)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: True)
    monkeypatch.setattr(main.scheduler, "dead_tasks", lambda: [])
    
    with patch("os.access", return_value=True):
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_health_endpoint_dead_tasks(monkeypatch):
    """Health endpoint reports dead tasks."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: True)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: True)
    monkeypatch.setattr(main.scheduler, "dead_tasks", lambda: ["task1", "task2"])
    
    with patch("os.access", return_value=True):
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert "2 device polling task(s) crashed" in resp.json()["problems"]


def test_health_endpoint_config_load_failed(monkeypatch):
    """Health endpoint reports config load failure."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: False)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: True)
    monkeypatch.setattr(main.scheduler, "dead_tasks", lambda: [])
    
    with patch("os.access", return_value=True):
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert "forwarding.yaml failed to load" in resp.json()["problems"]


def test_health_endpoint_auth_load_failed(monkeypatch):
    """Health endpoint reports auth load failure."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: True)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: False)
    monkeypatch.setattr(main.scheduler, "dead_tasks", lambda: [])
    
    with patch("os.access", return_value=True):
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert "auth.yaml failed to load" in resp.json()["problems"]


def test_health_endpoint_data_dir_not_writable(monkeypatch):
    """Health endpoint reports data dir not writable."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: True)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: True)
    monkeypatch.setattr(main.scheduler, "dead_tasks", lambda: [])
    
    with patch("os.access", return_value=False):
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert "not writable" in resp.json()["problems"][0]


def test_health_endpoint_demo_mode_readonly_ok(monkeypatch):
    """Health endpoint doesn't check writable in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(main.config_store, "last_load_ok", lambda: True)
    monkeypatch.setattr(main.auth_state, "auth_store_ok", lambda: True)
    monkeypatch.setattr(main.scheduler, "dead_tasks", lambda: [])
    
    with patch("os.access", return_value=False):
        client = TestClient(main.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_basic_auth_middleware_disabled():
    """Middleware is no-op when HTTP_USER/HTTP_PASSWORD not set."""
    assert BasicAuthMiddleware._enabled() is False


def test_basic_auth_middleware_enabled(monkeypatch):
    """Middleware enabled when both credentials set."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    assert BasicAuthMiddleware._enabled() is True


def test_basic_auth_middleware_partial_credentials(monkeypatch):
    """Middleware disabled when only one credential set."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", None)
    assert BasicAuthMiddleware._enabled() is False
    
    monkeypatch.setattr(config, "HTTP_USER", None)
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    assert BasicAuthMiddleware._enabled() is False


def test_basic_auth_middleware_health_endpoint_accessible(monkeypatch):
    """Health endpoint always accessible without auth."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
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


def test_basic_auth_middleware_rejects_without_auth(monkeypatch):
    """Requests without auth are rejected with 401."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)
    
    @app.get("/test")
    async def test():
        return {"ok": True}
    
    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_basic_auth_middleware_accepts_correct_credentials(monkeypatch):
    """Correct credentials are accepted."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import base64
    
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)
    
    @app.get("/test")
    async def test():
        return {"ok": True}
    
    # Use headers instead of auth parameter
    credentials = base64.b64encode(b"user:pass").decode()
    headers = {"Authorization": f"Basic {credentials}"}
    
    client = TestClient(app)
    resp = client.get("/test", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_basic_auth_middleware_rejects_wrong_credentials(monkeypatch):
    """Wrong credentials are rejected."""
    monkeypatch.setattr(config, "HTTP_USER", "user")
    monkeypatch.setattr(config, "HTTP_PASSWORD", "pass")
    
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import base64
    
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)
    
    @app.get("/test")
    async def test():
        return {"ok": True}
    
    credentials = base64.b64encode(b"wrong:pass").decode()
    headers = {"Authorization": f"Basic {credentials}"}
    
    client = TestClient(app)
    resp = client.get("/test", headers=headers)
    assert resp.status_code == 401


def test_basic_auth_disabled_via_env(monkeypatch):
    """Middleware disabled when HTTP_USER not set."""
    monkeypatch.setattr(config, "HTTP_USER", None)
    monkeypatch.setattr(config, "HTTP_PASSWORD", None)
    from webui.auth_middleware import BasicAuthMiddleware
    assert BasicAuthMiddleware._enabled() is False


def test_browser_no_vnc_url():
    """BROWSER_NOVNC_URL has default."""
    assert config.BROWSER_NOVNC_URL == "http://localhost:6901"


def test_default_poll_interval():
    """DEFAULT_POLL_INTERVAL_S has default."""
    assert config.DEFAULT_POLL_INTERVAL_S == 300


async def test_lifespan_starts_tasks(monkeypatch):
    """Lifespan starts scheduler, staleness, and eid_upload tasks."""
    mock_start_all = AsyncMock()
    mock_sweep_loop = AsyncMock()
    mock_upload_loop = AsyncMock()
    
    monkeypatch.setattr(main.scheduler, "start_all", mock_start_all)
    monkeypatch.setattr(main.staleness, "sweep_loop", mock_sweep_loop)
    monkeypatch.setattr(main.eid_upload, "upload_loop", mock_upload_loop)
    monkeypatch.setattr(main.config, "DEMO_MODE", False)
    monkeypatch.setattr(main.browser_provisioning, "on_shutdown", AsyncMock())
    
    # Also mock the cleanup functions
    monkeypatch.setattr(main.esp_idf_provisioning, "migrate_legacy_dirs", AsyncMock())
    monkeypatch.setattr(main.firmware_build, "migrate_legacy_builds_dir", AsyncMock())
    monkeypatch.setattr(main.firmware_build, "prune_old_builds", AsyncMock())
    monkeypatch.setattr(main.esp_idf_provisioning, "cleanup_if_stale", AsyncMock())
    
    async with main.lifespan(main.app):
        pass
    
    mock_start_all.assert_called_once()
    mock_sweep_loop.assert_called_once()
    mock_upload_loop.assert_called_once()


async def test_lifespan_skips_tasks_in_demo_mode(monkeypatch):
    """Lifespan doesn't start tasks in demo mode."""
    mock_start_all = AsyncMock()
    mock_sweep_loop = AsyncMock()
    mock_upload_loop = AsyncMock()
    
    monkeypatch.setattr(main.scheduler, "start_all", mock_start_all)
    monkeypatch.setattr(main.staleness, "sweep_loop", mock_sweep_loop)
    monkeypatch.setattr(main.eid_upload, "upload_loop", mock_upload_loop)
    monkeypatch.setattr(main.config, "DEMO_MODE", True)
    
    async with main.lifespan(main.app):
        pass
    
    mock_start_all.assert_not_called()
    mock_sweep_loop.assert_not_called()
    mock_upload_loop.assert_not_called()


async def test_lifespan_runs_cleanup(monkeypatch):
    """Lifespan runs cleanup functions on startup."""
    mock_migrate_legacy = AsyncMock()
    mock_migrate_builds = AsyncMock()
    mock_prune = AsyncMock()
    mock_cleanup = AsyncMock()
    
    monkeypatch.setattr(main.esp_idf_provisioning, "migrate_legacy_dirs", mock_migrate_legacy)
    monkeypatch.setattr(main.firmware_build, "migrate_legacy_builds_dir", mock_migrate_builds)
    monkeypatch.setattr(main.firmware_build, "prune_old_builds", mock_prune)
    monkeypatch.setattr(main.esp_idf_provisioning, "cleanup_if_stale", mock_cleanup)
    monkeypatch.setattr(main.config, "DEMO_MODE", False)
    
    async with main.lifespan(main.app):
        pass
    
    mock_migrate_legacy.assert_called_once()
    mock_migrate_builds.assert_called_once()
    mock_prune.assert_called_once()
    mock_cleanup.assert_called_once()


async def test_lifespan_cancels_tasks_on_shutdown(monkeypatch):
    """Lifespan cancels tasks on shutdown."""
    created_tasks = []
    
    async def mock_sweep_loop():
        pass
    
    async def mock_upload_loop():
        pass
    
    original_create_task = asyncio.create_task
    
    def tracking_create_task(coro):
        task = original_create_task(coro)
        created_tasks.append(task)
        return task
    
    monkeypatch.setattr(main.config, "DEMO_MODE", False)
    monkeypatch.setattr(main.scheduler, "start_all", lambda: None)
    monkeypatch.setattr(main.staleness, "sweep_loop", mock_sweep_loop)
    monkeypatch.setattr(main.eid_upload, "upload_loop", mock_upload_loop)
    monkeypatch.setattr(main.scheduler, "stop_all", lambda: None)
    monkeypatch.setattr(asyncio, "create_task", tracking_create_task)
    
    # Use a real lifespan context
    async with main.lifespan(main.app):
        pass
    
    # Tasks should be cancelled
    assert len(created_tasks) == 2
    for task in created_tasks:
        assert task.cancelled() or task.done()


async def test_lifespan_calls_browser_shutdown(monkeypatch):
    """Lifespan calls browser_provisioning.on_shutdown on shutdown."""
    mock_shutdown = AsyncMock()
    
    monkeypatch.setattr(main.config, "DEMO_MODE", False)
    monkeypatch.setattr(main.scheduler, "start_all", lambda: None)
    monkeypatch.setattr(main.staleness, "sweep_loop", AsyncMock())
    monkeypatch.setattr(main.eid_upload, "upload_loop", AsyncMock())
    monkeypatch.setattr(main.scheduler, "stop_all", lambda: None)
    monkeypatch.setattr(main.browser_provisioning, "on_shutdown", mock_shutdown)
    
    async with main.lifespan(main.app):
        pass
    
    mock_shutdown.assert_called_once()