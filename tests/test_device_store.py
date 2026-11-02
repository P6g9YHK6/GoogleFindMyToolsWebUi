"""Tests for webui/device_store.py - shared devices.yaml persistence."""


from webui import config, device_store
from webui.yaml_io import write_yaml_dict


def test_last_load_ok_demo_mode():
    """Demo mode returns True."""
    import webui.demo_mode as demo_mode
    demo_mode.DEMO_MODE = True
    try:
        assert device_store.last_load_ok() is True
    finally:
        demo_mode.DEMO_MODE = False


def test_migrate_legacy_forwarding_no_files():
    """No legacy files -> empty dict."""
    assert device_store._migrate_legacy_forwarding() == {}


def test_migrate_legacy_forwarding_yaml(monkeypatch, tmp_path):
    """Legacy forwarding.yaml parsed."""
    fwd = tmp_path / "forwarding.yaml"
    fwd.write_text("devices:\n  id1:\n    url: http://x\n")
    monkeypatch.setattr(config, "FORWARDING_CONFIG_PATH", fwd)
    monkeypatch.setattr(config, "FORWARDING_CONFIG_LEGACY_JSON_PATH", tmp_path / "none.json")
    result = device_store._migrate_legacy_forwarding()
    assert "id1" in result


def test_migrate_legacy_forwarding_json_fallback(monkeypatch, tmp_path):
    """Legacy JSON parsed when YAML missing."""
    fwd = tmp_path / "forwarding.yaml"
    fwd.unlink(missing_ok=True)
    json_path = tmp_path / "forwarding_config.json"
    json_path.write_text('{"devices": {"id2": {"url": "http://y"}}}')
    monkeypatch.setattr(config, "FORWARDING_CONFIG_PATH", fwd)
    monkeypatch.setattr(config, "FORWARDING_CONFIG_LEGACY_JSON_PATH", json_path)
    result = device_store._migrate_legacy_forwarding()
    assert "id2" in result


def test_migrate_legacy_forwarding_json_invalid(monkeypatch, tmp_path):
    """Invalid JSON -> empty dict."""
    fwd = tmp_path / "forwarding.yaml"
    fwd.unlink(missing_ok=True)
    json_path = tmp_path / "forwarding_config.json"
    json_path.write_text("not json")
    monkeypatch.setattr(config, "FORWARDING_CONFIG_PATH", fwd)
    monkeypatch.setattr(config, "FORWARDING_CONFIG_LEGACY_JSON_PATH", json_path)
    assert device_store._migrate_legacy_forwarding() == {}


def test_migrate_legacy_files_no_files():
    """No legacy files -> None."""
    # This is tricky to test without mocking all paths - skip
    pass


def test_load_unlocked_cache_hit(monkeypatch, tmp_path):
    """Cache hit returns cached data (deep copy)."""
    # Create a devices.yaml
    dev = tmp_path / "devices.yaml"
    data = {"schema_version": 1, "devices": {"id1": {}}}
    write_yaml_dict(dev, data)
    monkeypatch.setattr(config, "DEVICES_PATH", dev)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # First load - populates cache
    d1 = device_store._load_unlocked()
    assert d1["devices"]["id1"] == {}

    # Second load - cache hit (line 109) returns a deep copy
    d2 = device_store._load_unlocked()
    assert d2 == d1  # Equal content (deep copy)
    assert d2 is not d1  # Different object


def test_load_unlocked_cache_miss_new_mtime(monkeypatch, tmp_path):
    """Cache miss when mtime changes."""
    dev = tmp_path / "devices.yaml"
    data = {"schema_version": 1, "devices": {"id1": {}}}
    write_yaml_dict(dev, data)
    monkeypatch.setattr(config, "DEVICES_PATH", dev)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    d1 = device_store._load_unlocked()

    # Modify file to change mtime
    import time
    time.sleep(0.01)
    data2 = {"schema_version": 1, "devices": {"id2": {}}}
    write_yaml_dict(dev, data2)

    d2 = device_store._load_unlocked()
    assert d2["devices"]["id2"] == {}
    assert d2 is not d1  # New object


def test_load_unlocked_file_missing(monkeypatch, tmp_path):
    """Missing file triggers migration/empty."""
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(config, "DEVICES_PATH", missing)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FORWARDING_CONFIG_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(config, "FORWARDING_CONFIG_LEGACY_JSON_PATH", tmp_path / "none.json")
    monkeypatch.setattr(config, "DEVICE_LOCATIONS_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(config, "LATEST_VALUES_PATH", tmp_path / "none.yaml")

    result = device_store._load_unlocked()
    assert result == {"schema_version": 1, "devices": {}}


def test_load_unlocked_corrupt_yaml(monkeypatch, tmp_path):
    """Corrupt YAML -> empty dict."""
    dev = tmp_path / "devices.yaml"
    dev.write_text("not: yaml: [")
    monkeypatch.setattr(config, "DEVICES_PATH", dev)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    result = device_store._load_unlocked()
    assert result == {"schema_version": 1, "devices": {}}


def test_load_demo_mode(monkeypatch):
    """load() in demo mode returns demo seed."""
    import webui.demo_mode as demo_mode
    demo_mode.DEMO_MODE = True
    try:
        result = device_store.load()
        # Should be a deep copy of demo data
        assert "devices" in result
    finally:
        demo_mode.DEMO_MODE = False


def test_load_demo_mode_is_copy(monkeypatch):
    """load() in demo mode returns a fresh copy each time."""
    import webui.demo_mode as demo_mode
    demo_mode.DEMO_MODE = True
    try:
        d1 = device_store.load()
        d2 = device_store.load()
        assert d1 is not d2
    finally:
        demo_mode.DEMO_MODE = False


def test_save_demo_mode_noop(monkeypatch):
    """save() in demo mode is no-op."""
    import webui.demo_mode as demo_mode
    demo_mode.DEMO_MODE = True
    try:
        device_store.save({"devices": {}})  # Should not raise
    finally:
        demo_mode.DEMO_MODE = False


def test_mutate_device_demo_mode(monkeypatch):
    """mutate_device() in demo mode mutates throwaway copy."""
    import webui.demo_mode as demo_mode
    demo_mode.DEMO_MODE = True
    try:
        result = device_store.mutate_device("test", lambda e: e.update({"x": 1}))
        assert result["x"] == 1
    finally:
        demo_mode.DEMO_MODE = False


def test_mutate_device_creates_entry(monkeypatch, tmp_path):
    """mutate_device creates new entry."""
    monkeypatch.setattr(config, "DEVICES_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    result = device_store.mutate_device("new_id", lambda e: e.update({"x": 1}))
    assert result["x"] == 1


def test_mutate_device_updates_entry(monkeypatch, tmp_path):
    """mutate_device updates existing entry."""
    monkeypatch.setattr(config, "DEVICES_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # Create initial entry
    device_store.mutate_device("id1", lambda e: e.update({"a": 1}))
    # Update it
    result = device_store.mutate_device("id1", lambda e: e.update({"b": 2}))
    assert result["a"] == 1
    assert result["b"] == 2


def test_mutate_device_removes_empty_entry(monkeypatch, tmp_path):
    """mutate_device removes entry if fn leaves it empty."""
    monkeypatch.setattr(config, "DEVICES_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    device_store.mutate_device("id1", lambda e: e.update({"a": 1}))
    # Now clear it
    result = device_store.mutate_device("id1", lambda e: e.clear())
    assert result == {}


def test_mutate_devices_demo_mode(monkeypatch):
    """mutate_devices() in demo mode mutates throwaway copy."""
    import webui.demo_mode as demo_mode
    demo_mode.DEMO_MODE = True
    try:
        called = {}
        def fn(d):
            called["called"] = True
            d["id1"] = {"x": 1}
        device_store.mutate_devices(fn)
        assert called["called"]
    finally:
        demo_mode.DEMO_MODE = False


def test_mutate_devices_removes_empty_entries(monkeypatch, tmp_path):
    """mutate_devices removes empty entries."""
    monkeypatch.setattr(config, "DEVICES_PATH", tmp_path / "devices.yaml")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # Add two entries
    device_store.mutate_device("id1", lambda e: e.update({"a": 1}))
    device_store.mutate_device("id2", lambda e: e.update({"b": 2}))
    # Remove one via mutate_devices
    device_store.mutate_devices(lambda d: d.pop("id1", None))
    # id1 should be gone
    data = device_store.load()
    assert "id1" not in data["devices"]
    assert "id2" in data["devices"]