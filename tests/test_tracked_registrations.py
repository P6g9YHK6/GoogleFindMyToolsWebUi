"""Tests for webui/tracked_registrations.py - matching locally-tracked
registrations against a live Google device list for the Firmware page's
Tracked Registrations panel."""

from webui.tracked_registrations import _device_type_id, _matches, resolve_tracked_status


def test_device_type_id_known():
    """Known enum names translate to numeric IDs."""
    assert _device_type_id("DEVICE_TYPE_KEYS") == 3
    assert _device_type_id("DEVICE_TYPE_WATCH") == 4
    assert _device_type_id("DEVICE_TYPE_BEACON") == 1
    assert _device_type_id("DEVICE_TYPE_PHONE") == 20


def test_device_type_id_unknown():
    """Unrecognized or empty names return None (skipped, not a mismatch)."""
    assert _device_type_id("") is None
    assert _device_type_id("NOT_A_REAL_TYPE") is None
    # DEVICE_TYPE_UNKNOWN is a valid enum value (0), so it returns 0 not None
    assert _device_type_id("DEVICE_TYPE_UNKNOWN") == 0


def test_matches_exact():
    """All fields match -> True."""
    entry = {
        "display_name": "My Keys",
        "manufacturer_name": "Acme",
        "model_name": "Tag v2",
        "device_type": "DEVICE_TYPE_KEYS",
    }
    device = {
        "is_phone": False,
        "name": "My Keys",
        "manufacturer": "Acme",
        "model": "Tag v2",
        "type_id": 3,  # DEVICE_TYPE_KEYS
    }
    assert _matches(entry, device) is True


def test_matches_name_mismatch():
    """Different name -> False."""
    entry = {"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2"}
    device = {"is_phone": False, "name": "Other Keys", "manufacturer": "Acme", "model": "Tag v2"}
    assert _matches(entry, device) is False


def test_matches_manufacturer_mismatch():
    """Different manufacturer -> False."""
    entry = {"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2"}
    device = {"is_phone": False, "name": "My Keys", "manufacturer": "OtherCorp", "model": "Tag v2"}
    assert _matches(entry, device) is False


def test_matches_model_mismatch():
    """Different model -> False."""
    entry = {"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2"}
    device = {"is_phone": False, "name": "My Keys", "manufacturer": "Acme", "model": "Tag v3"}
    assert _matches(entry, device) is False


def test_matches_skips_phone():
    """Phone devices never match."""
    entry = {"display_name": "My Phone", "manufacturer_name": "Acme", "model_name": "Pixel"}
    device = {"is_phone": True, "name": "My Phone", "manufacturer": "Acme", "model": "Pixel"}
    assert _matches(entry, device) is False


def test_matches_device_type_mismatch():
    """Device type mismatch (when entry has a recognized type) -> False."""
    entry = {"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2", "device_type": "DEVICE_TYPE_KEYS"}
    device = {"is_phone": False, "name": "My Keys", "manufacturer": "Acme", "model": "Tag v2", "type_id": 1}  # DEVICE_TYPE_BEACON
    assert _matches(entry, device) is False


def test_matches_device_type_skipped_when_unknown():
    """Unrecognized device_type in entry is skipped (not a mismatch)."""
    entry = {"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2", "device_type": "NOT_REAL"}
    device = {"is_phone": False, "name": "My Keys", "manufacturer": "Acme", "model": "Tag v2", "type_id": 99}
    assert _matches(entry, device) is True


def test_matches_device_type_skipped_when_none():
    """None/missing device_type in entry is skipped."""
    entry = {"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2", "device_type": ""}
    device = {"is_phone": False, "name": "My Keys", "manufacturer": "Acme", "model": "Tag v2", "type_id": 99}
    assert _matches(entry, device) is True


def test_resolve_tracked_status_not_found():
    """No matching device -> status not_found."""
    entries = [{"display_name": "Ghost", "manufacturer_name": "Acme", "model_name": "Tag"}]
    devices = [{"is_phone": False, "name": "Real", "manufacturer": "Acme", "model": "Tag"}]
    results = resolve_tracked_status(entries, devices)
    assert results[0]["status"] == "not_found"


def test_resolve_tracked_status_found():
    """Exactly one match -> status found with device."""
    entries = [{"display_name": "My Keys", "manufacturer_name": "Acme", "model_name": "Tag v2"}]
    devices = [{"is_phone": False, "name": "My Keys", "manufacturer": "Acme", "model": "Tag v2", "type_id": 0}]
    results = resolve_tracked_status(entries, devices)
    assert results[0]["status"] == "found"
    assert results[0]["device"]["name"] == "My Keys"


def test_resolve_tracked_status_ambiguous():
    """Multiple matches -> status ambiguous with all matches."""
    entries = [{"display_name": "Ambiguous", "manufacturer_name": "Acme", "model_name": "Tag v2"}]
    devices = [
        {"is_phone": False, "name": "Ambiguous", "manufacturer": "Acme", "model": "Tag v2", "canonic_id": "1"},
        {"is_phone": False, "name": "Ambiguous", "manufacturer": "Acme", "model": "Tag v2", "canonic_id": "2"},
    ]
    results = resolve_tracked_status(entries, devices)
    assert results[0]["status"] == "ambiguous"
    assert len(results[0]["devices"]) == 2
    assert results[0]["devices"][0]["canonic_id"] == "1"
    assert results[0]["devices"][1]["canonic_id"] == "2"


def test_resolve_tracked_status_multiple_entries():
    """Each entry processed independently."""
    entries = [
        {"display_name": "Found", "manufacturer_name": "A", "model_name": "M"},
        {"display_name": "NotFound", "manufacturer_name": "B", "model_name": "N"},
        {"display_name": "Ambiguous", "manufacturer_name": "C", "model_name": "O"},
    ]
    devices = [
        {"is_phone": False, "name": "Found", "manufacturer": "A", "model": "M"},
        {"is_phone": False, "name": "Ambiguous", "manufacturer": "C", "model": "O", "canonic_id": "1"},
        {"is_phone": False, "name": "Ambiguous", "manufacturer": "C", "model": "O", "canonic_id": "2"},
    ]
    results = resolve_tracked_status(entries, devices)
    assert results[0]["status"] == "found"
    assert results[1]["status"] == "not_found"
    assert results[2]["status"] == "ambiguous"
    assert len(results[2]["devices"]) == 2


def test_resolve_tracked_status_preserves_entry_fields():
    """Original entry fields are preserved in results."""
    entries = [{"display_name": "Test", "manufacturer_name": "Mfr", "model_name": "Mod", "device_type": "DEVICE_TYPE_KEYS", "keep_track": True}]
    devices = [{"is_phone": False, "name": "Test", "manufacturer": "Mfr", "model": "Mod"}]
    results = resolve_tracked_status(entries, devices)
    r = results[0]
    assert r["display_name"] == "Test"
    assert r["manufacturer_name"] == "Mfr"
    assert r["model_name"] == "Mod"
    assert r["device_type"] == "DEVICE_TYPE_KEYS"
    assert r["keep_track"] is True