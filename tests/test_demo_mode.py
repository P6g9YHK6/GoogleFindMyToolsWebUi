"""Demo mode - see webui/demo_mode.py, webui/demo_data.py, webui/config.py's
DEMO_MODE. Two triggers, tested separately: DEMO_MODE=1 (webui.config.DEMO_MODE),
and the "no account configured yet" placeholder (webui.demo_mode's own
is_logged_in binding - see conftest.py's module docstring for why routers
and demo_mode each need patching at their own bound name).
"""

from urllib.parse import urlencode

DEMO_CANONIC_ID = "demo-reverse-engineered-pixel"
DEMO_DEVICE_NAME = "Reverse-Engineered Pixel"


def _post_form(client, path, **fields):
    pairs = []
    for key, value in fields.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((key, v) for v in value)
        else:
            pairs.append((key, value))
    return client.post(path, content=urlencode(pairs), headers={"content-type": "application/x-www-form-urlencoded"})


def _redirect_data_paths(monkeypatch, tmp_path):
    """Every place demo mode must guarantee zero writes to, redirected to a
    throwaway tmp_path so tests can assert on real absence of a file rather
    than just trusting the demo-mode branch was taken."""
    from webui import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DEVICES_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(config, "FORWARDING_CONFIG_PATH", tmp_path / "forwarding.yaml")
    monkeypatch.setattr(config, "FORWARDING_CONFIG_LEGACY_JSON_PATH", tmp_path / "forwarding_config.json")
    monkeypatch.setattr(config, "DEVICE_LOCATIONS_PATH", tmp_path / "device_locations.yaml")
    monkeypatch.setattr(config, "LATEST_VALUES_PATH", tmp_path / "latest_values.yaml")
    monkeypatch.setattr(config, "FORWARD_LOG_PATH", tmp_path / "forward.log")
    monkeypatch.setattr(config, "FORWARD_LOG_LEGACY_JSON_PATH", tmp_path / "forward_log.json")
    monkeypatch.setattr(config, "SYSTEM_LOG_PATH", tmp_path / "system.log")
    monkeypatch.setattr(config, "APP_SETTINGS_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", tmp_path / "registered_trackers.yaml")


# --- DEMO_MODE=1: full public-showcase mode ---------------------------------

def test_devices_table_shows_fake_devices_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/devices/table")
    assert resp.status_code == 200
    assert DEMO_DEVICE_NAME in resp.text
    assert "My Tracker" not in resp.text  # the real stubbed device never shows through


def test_footer_shows_demo_flag_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Demo" in resp.text


def test_footer_has_no_demo_flag_by_default(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "🚩" not in resp.text


def test_login_button_disabled_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/auth")
    assert resp.status_code == 200
    assert 'id="signin-btn" disabled' in resp.text


def test_login_start_blocked_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.post("/auth/login/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is False
    assert "disabled" in body["state"]["message"].lower()


def test_login_poll_blocked_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/auth/login/poll")
    assert resp.status_code == 200
    assert "disabled" in resp.json()["message"].lower()


def test_login_start_not_blocked_when_demo_mode_is_off(client, monkeypatch):
    """Confirms demo_mode's second, narrower trigger (no account configured
    yet - see test_devices_table_shows_fake_devices_as_onboarding_placeholder
    below) never touches real login, only DEMO_MODE=1 does."""
    import webui.browser_provisioning as browser_provisioning

    async def fake_start():
        return {"started": True, "state": browser_provisioning.get_state()}

    monkeypatch.setattr(browser_provisioning, "start", fake_start)
    resp = client.post("/auth/login/start")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_vnc_static_blocked_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/vnc/vnc.html")
    assert resp.status_code == 404


def test_vnc_websocket_blocked_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/vnc/websockify"):
            pass


def test_forwarding_page_shows_fake_endpoints_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert DEMO_DEVICE_NAME in resp.text
    assert "traccar.example.invalid" in resp.text


def test_forwarding_save_not_persisted_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)

    resp = _post_form(client, f"/settings/devices/{DEMO_CANONIC_ID}", display_name="Visitor Edit", ep_order=[])
    assert resp.status_code == 200
    assert "Visitor Edit" in resp.text  # echoed back for this one response...
    assert not (tmp_path / "devices.yaml").exists()  # ...but never actually written


def test_forwarding_send_now_does_not_write_disk_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)

    resp = client.post(f"/settings/devices/{DEMO_CANONIC_ID}/endpoints/0/send-now")
    assert resp.status_code == 200
    assert "Last forward: ok" in resp.text
    assert not (tmp_path / "forward.log").exists()
    assert not (tmp_path / "devices.yaml").exists()


def test_locate_does_not_persist_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)

    resp = client.post(f"/devices/{DEMO_CANONIC_ID}/locate")
    assert resp.status_code == 200
    assert not (tmp_path / "devices.yaml").exists()


def test_logs_page_shows_canned_entries_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/logs")
    assert resp.status_code == 200
    assert "Demo mode active" in resp.text


def test_staleness_table_shows_fake_devices_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)
    resp = client.get("/staleness/table")
    assert resp.status_code == 200
    assert DEMO_DEVICE_NAME in resp.text
    assert "Umbrella" in resp.text  # not the literal quotes - HTML-escaped by Jinja


def test_staleness_toggle_not_persisted_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)

    resp = _post_form(
        client, f"/staleness/devices/{DEMO_CANONIC_ID}",
        enabled="1", threshold_preset="21600", repeat_preset="off", message_template="", muted="0",
    )
    assert resp.status_code == 200
    assert not (tmp_path / "devices.yaml").exists()


def test_register_returns_fake_success_and_does_not_persist_in_demo_mode(client, monkeypatch, tmp_path):
    from webui import config, deps
    from webui.routers import register

    monkeypatch.setattr(config, "DEMO_MODE", True)
    _redirect_data_paths(monkeypatch, tmp_path)
    # conftest.py's autouse stub_backend replaces register_tracker with its
    # own fake for every test by default - point this one test back at the
    # real webui.deps.register_tracker so it actually exercises this
    # module's demo-mode branch, same pattern as
    # test_register.py::test_register_then_devices_table_sees_the_new_tracker_immediately.
    monkeypatch.setattr(register, "register_tracker", deps.register_tracker)

    resp = _post_form(client, "/register")
    assert resp.status_code == 200
    assert "de3a70d0" in resp.text  # the fake EID
    assert not (tmp_path / "registered_trackers.yaml").exists()


def test_firmware_build_disabled_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = _post_form(client, "/firmware/build/start", board="esp32", eid_hex="a" * 40)
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is False
    assert "disabled" in body["error"].lower()


def test_firmware_page_shows_disabled_build_button_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/firmware")
    assert resp.status_code == 200
    assert 'id="build-btn" type="submit" disabled' in resp.text


def test_debug_export_live_query_blocked_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = _post_form(client, "/auth/debug-export", include_live_query="true")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["error"].lower()


def test_debug_export_logs_only_still_works_in_demo_mode(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = _post_form(client, "/auth/debug-export", include_logs="true")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-7z-compressed"


def test_metrics_reports_demo_mode_gauge(client, monkeypatch):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "gfmt_demo_mode 1" in resp.text


def test_health_reports_ok_in_demo_mode_even_with_no_data_dir(client, monkeypatch, tmp_path):
    from webui import config

    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "does-not-exist")
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- "No account configured yet" placeholder (DEMO_MODE unset) --------------

def test_devices_table_shows_fake_devices_as_onboarding_placeholder(client, monkeypatch):
    from webui import demo_mode
    from webui.routers import devices

    monkeypatch.setattr(devices, "is_logged_in", lambda: False)
    monkeypatch.setattr(demo_mode, "is_logged_in", lambda: False)
    resp = client.get("/devices/table")
    assert resp.status_code == 200
    assert DEMO_DEVICE_NAME in resp.text


def test_settings_page_unaffected_by_onboarding_placeholder(client, monkeypatch):
    """The placeholder trigger is deliberately scoped to the Devices page
    only (see webui/demo_mode.py) - Settings must still show its normal
    not-signed-in state, not fake data."""
    from webui import demo_mode
    from webui.routers import settings

    monkeypatch.setattr(settings, "is_logged_in", lambda: False)
    monkeypatch.setattr(demo_mode, "is_logged_in", lambda: False)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert DEMO_DEVICE_NAME not in resp.text


def test_staleness_page_unaffected_by_onboarding_placeholder(client, monkeypatch):
    from webui import demo_mode
    from webui.routers import staleness as staleness_router

    monkeypatch.setattr(staleness_router, "is_logged_in", lambda: False)
    monkeypatch.setattr(demo_mode, "is_logged_in", lambda: False)
    resp = client.get("/staleness")
    assert resp.status_code == 200
    assert DEMO_DEVICE_NAME not in resp.text


def test_footer_has_no_demo_flag_for_onboarding_placeholder(client, monkeypatch):
    from webui import demo_mode
    from webui.routers import devices

    monkeypatch.setattr(devices, "is_logged_in", lambda: False)
    monkeypatch.setattr(demo_mode, "is_logged_in", lambda: False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "🚩" not in resp.text


def test_login_start_still_live_for_onboarding_placeholder(client, monkeypatch):
    import webui.browser_provisioning as browser_provisioning
    from webui import demo_mode
    from webui.routers import devices

    monkeypatch.setattr(devices, "is_logged_in", lambda: False)
    monkeypatch.setattr(demo_mode, "is_logged_in", lambda: False)

    async def fake_start():
        return {"started": True, "state": browser_provisioning.get_state()}

    monkeypatch.setattr(browser_provisioning, "start", fake_start)
    resp = client.post("/auth/login/start")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


# --- Low-level network guard --------------------------------------------------

def test_network_guard_blocks_requests_and_httpx():
    import httpx
    import pytest
    import requests
    import websockets

    from webui import demo_network_guard

    orig = (requests.sessions.Session.request, httpx.Client.request, httpx.AsyncClient.request, websockets.connect)
    try:
        demo_network_guard.install(True)

        with pytest.raises(demo_network_guard.DemoNetworkBlocked):
            requests.get("http://example.invalid")
        with pytest.raises(demo_network_guard.DemoNetworkBlocked):
            httpx.Client().get("http://example.invalid")
        with pytest.raises(demo_network_guard.DemoNetworkBlocked):
            websockets.connect("ws://example.invalid")
    finally:
        (
            requests.sessions.Session.request, httpx.Client.request,
            httpx.AsyncClient.request, websockets.connect,
        ) = orig


def test_network_guard_is_a_no_op_when_disabled():
    import httpx
    import requests
    import websockets

    from webui import demo_network_guard

    orig = (requests.sessions.Session.request, httpx.Client.request, httpx.AsyncClient.request, websockets.connect)
    demo_network_guard.install(False)
    assert (
        requests.sessions.Session.request, httpx.Client.request, httpx.AsyncClient.request, websockets.connect,
    ) == orig
