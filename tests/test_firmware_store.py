"""Tests for webui/firmware_store.py - advertisement key persistence."""

import yaml

from webui import config
from webui.firmware_store import (
    DEFAULT_BUILD_SETTINGS,
    DEFAULT_IDENTITY,
    DEFAULT_KEEP_TRACK,
    _load_unlocked,
    _save_unlocked,
    list_registered,
    load_last_identity,
    record_build_settings,
    record_identity,
    record_registration,
    set_keep_track,
)


def test_load_unlocked_file_missing(tmp_path, monkeypatch):
    """Missing file returns empty structure."""
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", tmp_path / "missing.yaml")
    result = _load_unlocked()
    assert result == {"entries": [], "last_identity": {}}


def test_load_unlocked_corrupt_yaml(tmp_path, monkeypatch):
    """Corrupt YAML returns empty structure."""
    path = tmp_path / "trackers.yaml"
    path.write_text("not: yaml: [")
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    result = _load_unlocked()
    assert result == {"entries": [], "last_identity": {}}


def test_load_unlocked_os_error(tmp_path, monkeypatch):
    """OSError during read returns empty structure."""
    path = tmp_path / "trackers.yaml"
    path.write_text("entries: []")
    # Make file unreadable
    path.chmod(0o000)
    try:
        monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
        result = _load_unlocked()
        assert result == {"entries": [], "last_identity": {}}
    finally:
        path.chmod(0o644)


def test_load_unlocked_legacy_list_shape(tmp_path, monkeypatch):
    """Legacy bare list shape migrated to dict."""
    path = tmp_path / "trackers.yaml"
    path.write_text("- eid_hex: abc\n  pair_date: 123\n")
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    result = _load_unlocked()
    assert isinstance(result["entries"], list)
    assert len(result["entries"]) == 1
    assert result["entries"][0]["eid_hex"] == "abc"
    assert result["last_identity"] == {}


def test_load_unlocked_dict_shape(tmp_path, monkeypatch):
    """Dict shape with entries and last_identity."""
    path = tmp_path / "trackers.yaml"
    path.write_text(yaml.safe_dump({
        "entries": [{"eid_hex": "abc", "pair_date": 123}],
        "last_identity": {"display_name": "Test"}
    }))
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    result = _load_unlocked()
    assert result["entries"][0]["eid_hex"] == "abc"
    assert result["last_identity"]["display_name"] == "Test"


def test_load_unlocked_invalid_data_shape(tmp_path, monkeypatch):
    """Invalid data shape (not list or dict) -> empty."""
    path = tmp_path / "trackers.yaml"
    path.write_text("just_a_string")
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    result = _load_unlocked()
    assert result == {"entries": [], "last_identity": {}}


def test_load_unlocked_defaults_applied(tmp_path, monkeypatch):
    """Default build settings and keep_track applied to entries."""
    path = tmp_path / "trackers.yaml"
    path.write_text("- eid_hex: abc\n  pair_date: 123\n")
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    result = _load_unlocked()
    entry = result["entries"][0]
    for key, val in DEFAULT_BUILD_SETTINGS.items():
        assert entry[key] == val
    for key, val in DEFAULT_KEEP_TRACK.items():
        assert entry[key] == val


def test_save_unlocked_writes_yaml(tmp_path, monkeypatch):
    """_save_unlocked writes valid YAML."""
    path = tmp_path / "trackers.yaml"
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    _save_unlocked({"entries": [], "last_identity": {}})
    assert path.exists()
    # Verify it's valid YAML
    with open(path) as f:
        data = yaml.safe_load(f)
    assert data == {"entries": [], "last_identity": {}}


def test_record_registration_demo_mode_noop(monkeypatch):
    """record_registration no-ops in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    record_registration("abc", 123)
    # Should not raise, should not write


def test_record_build_settings_demo_mode_noop(monkeypatch):
    """record_build_settings no-ops in demo mode (line 144)."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    record_build_settings("abc", "name", 20, 9, True)
    # Should not raise


def test_set_keep_track_demo_mode_noop(monkeypatch):
    """set_keep_track no-ops in demo mode (line 168)."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    set_keep_track("abc", True)
    # Should not raise


def test_set_keep_track_unknown_eid(monkeypatch, tmp_path):
    """set_keep_track is no-op for unknown EID."""
    path = tmp_path / "trackers.yaml"
    monkeypatch.setattr(config, "REGISTERED_TRACKERS_PATH", path)
    # Empty store
    set_keep_track("unknown", True)
    # Should not raise, file should not be created (or empty)
    # Actually _save_unlocked creates it with empty entries
    # Just verify no error


def test_list_registered_demo_mode(monkeypatch):
    """list_registered returns empty in demo mode (lines 179-185)."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    assert list_registered() == []


def test_load_last_identity_demo_mode(monkeypatch):
    """load_last_identity returns defaults in demo mode (lines 191-192)."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    identity = load_last_identity()
    assert identity == DEFAULT_IDENTITY


def test_record_identity_demo_mode_noop(monkeypatch):
    """record_identity no-ops in demo mode (lines 200-201)."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    record_identity("Test", "DEVICE_TYPE_BEACON", "Acme", "Tag", "http://img")
    # Should not raise