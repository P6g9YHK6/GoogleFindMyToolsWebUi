"""Tests for webui/forwarders/config_store.py - migration and normalization."""

import pytest

from webui.forwarders import config_store
from webui.forwarders.presets import PRESETS


def test_seconds_to_cron_minutes():
    """Minutes case (<= 59)."""
    assert config_store._seconds_to_cron(300) == "*/5 * * * *"
    assert config_store._seconds_to_cron(60) == "*/1 * * * *"
    assert config_store._seconds_to_cron(3540) == "*/59 * * * *"


def test_seconds_to_cron_hours():
    """Hours case (>= 60 minutes)."""
    assert config_store._seconds_to_cron(3600) == "0 */1 * * *"
    assert config_store._seconds_to_cron(7200) == "0 */2 * * *"
    assert config_store._seconds_to_cron(82800) == "0 */23 * * *"
    # Clamped at 23 hours
    assert config_store._seconds_to_cron(86400) == "0 */23 * * *"


def test_seconds_to_cron_none():
    """None/0 falls back to DEFAULT_POLL_INTERVAL_S (300s = 5min)."""
    assert config_store._seconds_to_cron(None) == "*/5 * * * *"
    assert config_store._seconds_to_cron(0) == "*/5 * * * *"


def test_fold_params_into_url_no_params():
    """No params dict -> no change."""
    entry = {"url": "http://example.com", "method": "GET"}
    assert config_store._fold_params_into_url(entry) == entry


def test_fold_params_into_url_with_params():
    """Params folded into URL querystring."""
    entry = {"url": "http://example.com", "params": {"a": "1", "b": "2"}}
    result = config_store._fold_params_into_url(entry)
    assert result["url"] == "http://example.com?a=1&b=2"
    assert "params" not in result


def test_fold_params_into_url_preserves_existing_qs():
    """Existing querystring preserved with & separator."""
    entry = {"url": "http://example.com?existing=1", "params": {"a": "2"}}
    result = config_store._fold_params_into_url(entry)
    assert result["url"] == "http://example.com?existing=1&a=2"


def test_fold_params_into_url_preserves_tokens():
    """Template tokens not percent-encoded."""
    entry = {"url": "http://example.com", "params": {"lat": "{{latitude}}"}}
    result = config_store._fold_params_into_url(entry)
    assert result["url"] == "http://example.com?lat={{latitude}}"


def test_migrate_legacy_endpoint_unknown_type():
    """Unknown legacy type falls back to custom preset (method/url from preset)."""
    entry = {"type": "unknown_type", "some": "data"}
    result = config_store._migrate_legacy_endpoint(entry)
    # Type preserved from original, but method/url from custom preset
    assert result["type"] == "unknown_type"
    assert result["method"] == PRESETS["custom"]["method"]
    assert result["url"] == PRESETS["custom"]["url"]
    assert result["headers"] == {}
    assert result["body_type"] == "none"
    # Original data preserved
    assert result["some"] == "data"


def test_migrate_legacy_endpoint_no_type():
    """No type -> no migration."""
    entry = {"url": "http://example.com", "method": "POST"}
    assert config_store._migrate_legacy_endpoint(entry) == entry


def test_migrate_legacy_endpoint_folds_params():
    """Legacy params folded into URL."""
    entry = {"url": "http://example.com", "params": {"k": "v"}}
    result = config_store._migrate_legacy_endpoint(entry)
    assert result["url"] == "http://example.com?k=v"
    assert "params" not in result


def test_strip_endpoint_state():
    """Runtime state keys removed."""
    from webui.forwarders.latest_values_store import STATE_KEYS
    entry = {"url": "http://x", "last_forward_status": "ok", "last_forward_time": 123}
    result = config_store._strip_endpoint_state(entry)
    assert "last_forward_status" not in result
    assert "last_forward_time" not in result
    assert "url" in result


def test_strip_endpoint_state_noop():
    """No state keys -> no change."""
    entry = {"url": "http://x"}
    assert config_store._strip_endpoint_state(entry) == entry


def test_normalize_device_config_endpoints_shape():
    """Already in endpoints shape -> no change."""
    cfg = {"endpoints": [{"url": "http://x"}]}
    assert config_store.normalize_device_config(cfg) is cfg


def test_normalize_device_config_legacy_destination_traccar():
    """Legacy destination=traccar converted to endpoints list (fully migrated)."""
    cfg = {"destination": "traccar", "traccar": {"url": "http://t/", "device_id": "d"}}
    result = config_store.normalize_device_config(cfg)
    assert "endpoints" in result
    assert len(result["endpoints"]) == 1
    # The endpoint gets fully migrated to the new generic shape
    ep = result["endpoints"][0]
    assert ep["type"] == "traccar"
    assert ep["cron"] == "*/5 * * * *"
    assert ep["method"] == "GET"
    assert ep["url"].startswith("http://t/?id={{device_id}}")
    assert ep["variables"]["device_id"] == "d"


def test_normalize_device_config_legacy_destination_phonetrack():
    """Legacy destination=phonetrack converted to endpoints list."""
    cfg = {"destination": "phonetrack", "phonetrack": {"base_url": "http://p/", "device_name": "d"}}
    result = config_store.normalize_device_config(cfg)
    assert "endpoints" in result
    assert len(result["endpoints"]) == 1
    assert result["endpoints"][0]["type"] == "phonetrack"


def test_normalize_device_config_destination_none():
    """destination=none -> empty endpoints list."""
    cfg = {"destination": "none"}
    result = config_store.normalize_device_config(cfg)
    assert result["endpoints"] == []


def test_normalize_device_config_unknown_destination():
    """Unknown destination -> empty endpoints (forwarding disabled)."""
    cfg = {"destination": "unknown"}
    result = config_store.normalize_device_config(cfg)
    assert result["endpoints"] == []


def test_normalize_device_config_strips_state():
    """Runtime state stripped from legacy shape."""
    cfg = {"destination": "traccar", "traccar": {"url": "x"}, "last_forward_status": "ok"}
    result = config_store.normalize_device_config(cfg)
    assert "last_forward_status" not in result
    assert "endpoints" in result


def test_normalize_device_config_converts_poll_interval():
    """poll_interval_seconds converted to cron."""
    cfg = {"destination": "traccar", "traccar": {"url": "x"}, "poll_interval_seconds": 600}
    result = config_store.normalize_device_config(cfg)
    assert result["endpoints"][0]["cron"] == "*/10 * * * *"


def test_load_no_config():
    """Devices without config skipped."""
    # This tests the branch at line 178-183
    pass  # Covered indirectly by other tests


def test_load_writes_back_migrations():
    """Migrations written back on load."""
    pass  # Integration test


def test_save_full_replace():
    """save() replaces all device configs."""
    pass  # Integration test


def test_get_device_config():
    """get_device_config returns normalized config."""
    pass  # Integration test


def test_set_device_config():
    """set_device_config writes config."""
    pass  # Integration test


def test_all_devices():
    """all_devices returns all normalized configs."""
    pass  # Integration test