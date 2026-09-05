"""Fixed, deterministic dataset shown whenever demo mode is active (see
webui/demo_mode.py) - a public showcase account with fake devices, so a
public-facing instance never touches a real Google account or sends a real
network request. Every builder function here returns the exact same shape
the real code produces (documented on each), so call sites are drop-in
replacements and no template needs to know the difference.

Deliberately hardcoded rather than randomly generated: a small, curated,
always-identical list *is* "fixed seed, deterministic" - there's no random
number generator whose output has to be kept in sync across processes or
platforms this way. Coordinates are real, well-known Bay Area locations (a
nod to this project's own subject matter) rather than placeholder/fictional
ones, so the map looks authentic.

Every timestamp below is stored as a relative offset ("seen 3 minutes ago")
and turned into an absolute epoch second fresh on every call - see
demo_devices_store() - not cached anywhere, so a demo container left running
for days never drifts into looking abandoned even though the *positions*
themselves never move (see the "static, no movement" demo requirement).
webui/device_store.py calls demo_devices_store() fresh on every load() too,
so this is also what makes a visitor's "write" (a Locate click, a Settings
save) show up for that one response only: the very next read starts over
from this same fixed seed.
"""

import time

from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import create_map_links

OWNER_USERNAME = "demo@example.invalid"
OWNER_DISPLAY_NAME = "Demo User"

# One dict per device - deliberately a plain list of dicts, not tuples, since
# there are now enough fields (get_device_details()'s full shape, plus this
# module's own staleness/endpoint knobs) that positional tuples would be
# unreadable. device_type is None for the phone (is_phone devices never
# carry one - see ProtoDecoders/decoder.py:get_device_details) and one of
# webui/routers/devices.py's _DEVICE_TYPE_LABELS keys for everything else,
# picked to fit the persona where one already exists in that map (Beacon,
# Umbrella).
_DEVICES: list[dict] = [
    {
        "name": "Reverse-Engineered Pixel", "canonic_id": "demo-reverse-engineered-pixel", "alias": None,
        "is_phone": True, "device_type": None, "manufacturer": "Google", "model": "Pixel 9",
        "carrier": None, "codename": "tokay", "imei": None, "registered_ago_s": 400 * 86400,
        "lat": 37.4220, "lon": -122.0841, "altitude": 32, "accuracy": 12, "seen_ago_s": 180,
        "staleness_threshold_s": None,
    },
    {
        "name": "The Decoy Keys", "canonic_id": "demo-the-decoy-keys", "alias": "House Keys",
        "is_phone": False, "device_type": "DEVICE_TYPE_KEYS", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.3861, "lon": -122.0839, "altitude": 25, "accuracy": 8, "seen_ago_s": 720,
        "staleness_threshold_s": None,
    },
    {
        "name": "Schrodinger's Backpack", "canonic_id": "demo-schrodingers-backpack", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_BAG", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.4043, "lon": -122.0748, "altitude": 15, "accuracy": 15, "seen_ago_s": 2820,
        "staleness_threshold_s": None,
    },
    {
        # Deliberately stale (see staleness_threshold_s below) - the name's
        # too good a fit not to use for the one device that's demonstrably
        # not being tracked right now.
        "name": "Totally Not Tracked Wallet", "canonic_id": "demo-totally-not-tracked-wallet", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_WALLET", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.4419, "lon": -122.1430, "altitude": 45, "accuracy": 10, "seen_ago_s": 7080,
        "staleness_threshold_s": 3600,
    },
    {
        "name": "FMDN Test Tag", "canonic_id": "demo-fmdn-test-tag", "alias": "Backpack Tag",
        "is_phone": False, "device_type": "DEVICE_TYPE_BADGE", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.3688, "lon": -122.0363, "altitude": 28, "accuracy": 20, "seen_ago_s": 360,
        "staleness_threshold_s": None,
    },
    {
        "name": "Beacon of Freedom (ESP32)", "canonic_id": "demo-beacon-of-freedom-esp32", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_BEACON", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.3230, "lon": -122.0322, "altitude": 60, "accuracy": 30, "seen_ago_s": 1320,
        "staleness_threshold_s": None,
    },
    {
        "name": "Self-Hosted Bike", "canonic_id": "demo-self-hosted-bike", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_BIKE", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.4030, "lon": -122.1141, "altitude": 90, "accuracy": 18, "seen_ago_s": 10800,
        "staleness_threshold_s": 6 * 3600,  # 3h old, 6h threshold - shows Fresh
    },
    {
        "name": "Firmware Flasher Bag", "canonic_id": "demo-firmware-flasher-bag", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_LUGGAGE", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.3541, "lon": -121.9552, "altitude": 40, "accuracy": 22, "seen_ago_s": 540,
        "staleness_threshold_s": None,
    },
    {
        "name": "Open Source Tablet", "canonic_id": "demo-open-source-tablet", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_TABLET", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.4852, "lon": -122.2364, "altitude": 12, "accuracy": 14, "seen_ago_s": 1980,
        "staleness_threshold_s": None,
    },
    {
        # Deliberately stale too (5h old, 3h threshold) - fittingly, for the
        # one device that's supposedly lost.
        "name": '"Lost" Umbrella', "canonic_id": "demo-lost-umbrella", "alias": None,
        "is_phone": False, "device_type": "DEVICE_TYPE_UMBRELLA", "manufacturer": None, "model": None,
        "carrier": None, "codename": None, "imei": None, "registered_ago_s": None,
        "lat": 37.4275, "lon": -122.1697, "altitude": 55, "accuracy": 35, "seen_ago_s": 18000,
        "staleness_threshold_s": 3 * 3600,
    },
]

# Same plain-label strings webui/routers/devices.py's device_type_plain_label
# would compute for each device above - hardcoded here rather than imported
# from that module, which would create an import cycle (routers/devices.py
# itself imports this module for its own demo short-circuit).
_TYPE_PLAIN_LABELS = {
    "DEVICE_TYPE_KEYS": "Keys", "DEVICE_TYPE_BAG": "Bag", "DEVICE_TYPE_WALLET": "Wallet",
    "DEVICE_TYPE_BADGE": "Badge", "DEVICE_TYPE_BEACON": "Beacon", "DEVICE_TYPE_BIKE": "Bike",
    "DEVICE_TYPE_LUGGAGE": "Luggage", "DEVICE_TYPE_TABLET": "Tablet", "DEVICE_TYPE_UMBRELLA": "Umbrella",
}

# canonic_id -> example forwarding endpoints, obviously-fake ".invalid" hosts
# (RFC 2606 - guaranteed to never resolve, so even a bypassed egress guard
# hits nothing real) - only a handful of devices, so the Forwarding Settings
# page shows a realistic mix of configured/unconfigured rather than every
# device looking identical.
_ENDPOINTS: dict[str, list[dict]] = {
    "demo-reverse-engineered-pixel": [{
        "alias": "Home Traccar",
        "method": "GET",
        "url": (
            "http://traccar.example.invalid:5055/?id=demo-pixel&lat={{latitude}}&lon={{longitude}}"
            "&timestamp={{google_timestamp}}&altitude={{altitude_m}}&accuracy={{accuracy_m}}"
        ),
        "cron": "*/5 * * * *",
        "last_forward_status": "ok",
        "last_forward_ago_s": 200,
    }],
    "demo-the-decoy-keys": [{
        "alias": "Nextcloud PhoneTrack",
        "method": "GET",
        "url": (
            "https://nc.example.invalid/apps/phonetrack/logGet/demo-token/{{device_alias}}"
            "?lat={{latitude}}&lon={{longitude}}&timestamp={{google_timestamp}}&alt={{altitude_m}}&acc={{accuracy_m}}"
        ),
        "cron": "*/15 * * * *",
        "last_forward_status": "ok",
        "last_forward_ago_s": 740,
    }],
    "demo-fmdn-test-tag": [{
        "alias": "Debug webhook",
        "method": "POST",
        "url": "https://hooks.example.invalid/demo-tag",
        "headers": {"Content-Type": "application/json"},
        "body_type": "json",
        "body": '{"lat": {{latitude}}, "lon": {{longitude}}, "ts": {{google_timestamp}}}',
        "cron": "*/10 * * * *",
        "last_forward_status": "ok",
        "last_forward_ago_s": 400,
    }],
    "demo-beacon-of-freedom-esp32": [{
        "alias": "Backup Traccar",
        "method": "GET",
        "url": (
            "http://traccar-backup.example.invalid:5055/?id=demo-esp32&lat={{latitude}}&lon={{longitude}}"
            "&timestamp={{google_timestamp}}&altitude={{altitude_m}}&accuracy={{accuracy_m}}"
        ),
        "cron": "*/30 * * * *",
        "skip_if_close": True,
        "min_movement_m": 50,
        "last_forward_status": "skipped: moved less than 50m",
        "last_forward_ago_s": 1340,
    }],
}


def _location_for(spec: dict) -> dict:
    """Same shape as NovaApi/ExecuteAction/LocateTracker/decrypt_locations.py's
    decrypt_location_response_locations() produces for one non-semantic
    reading - map_links included, via the exact same helper, so the "Map"
    column's links are real working deep-links, not placeholders."""
    lat, lon = spec["lat"], spec["lon"]
    fix_time = int(time.time()) - spec["seen_ago_s"]
    return {
        "latitude": lat, "longitude": lon, "altitude": spec["altitude"],
        "time": fix_time, "is_semantic": False, "semantic_name": None,
        "status": "LAST_KNOWN", "accuracy": spec["accuracy"], "is_own_report": True,
        "map_links": create_map_links(lat, lon),
    }


def _device_meta_for(spec: dict) -> dict:
    """Same shape webui/forwarders/settings_service.py's
    device_meta_from_detail() persists into a device's config - computed by
    hand here (see _TYPE_PLAIN_LABELS above for why) rather than by calling
    that function, so this module has no import back into anything that
    itself imports this one."""
    now = int(time.time())
    type_label = "Phone" if spec["is_phone"] else (_TYPE_PLAIN_LABELS.get(spec["device_type"] or "") or "")
    registered_ago_s = spec["registered_ago_s"]
    return {
        "manufacturer": spec["manufacturer"] or "", "model": spec["model"] or "",
        "type": type_label, "type_id": "",
        "image_url": "", "carrier": spec["carrier"] or "", "codename": spec["codename"] or "",
        "imei": spec["imei"] or "", "registered_at": (now - registered_ago_s) if registered_ago_s else "",
        "shared_with": "",  # every demo device is owned outright, nothing shared
    }


def _endpoints_for(canonic_id: str) -> list[dict]:
    now = int(time.time())
    endpoints = []
    for ep in _ENDPOINTS.get(canonic_id, []):
        ep = dict(ep)
        ago = ep.pop("last_forward_ago_s", None)
        if ago is not None:
            ep["last_forward_time"] = now - ago
        endpoints.append(ep)
    return endpoints


def _staleness_for(spec: dict) -> dict | None:
    threshold_s = spec["staleness_threshold_s"]
    if threshold_s is None:
        return None
    return {
        "enabled": True, "threshold_s": threshold_s, "repeat_s": None,
        "message_template": "No update from {{device_name}} in over {{threshold}}",
        "muted": False, "alert_active": False, "last_alert_sent_at": None,
    }


def demo_device_details() -> list[dict]:
    """Same shape as ProtoDecoders.decoder.get_device_details()'s return
    value. Used in place of a real Nova device-list fetch by every page that
    normally makes one (webui/routers/devices.py, webui/forwarders/
    settings_service.py, webui/routers/staleness.py) when demo mode
    applies."""
    now = int(time.time())
    return [
        {
            "name": spec["name"], "canonic_id": spec["canonic_id"], "last_seen": None,
            "is_phone": spec["is_phone"], "image_url": None, "device_type": spec["device_type"],
            "type_id": None, "manufacturer": spec["manufacturer"], "model": spec["model"],
            "carrier": spec["carrier"], "codename": spec["codename"], "imei": spec["imei"],
            "registered_at": (now - spec["registered_ago_s"]) if spec["registered_ago_s"] else None,
            "access": [{"email": OWNER_USERNAME, "has_access": True, "is_owner": True, "this_account": True}],
        }
        for spec in _DEVICES
    ]


def demo_devices_store() -> dict:
    """Same shape as webui/device_store.py's on-disk devices.yaml - the seed
    webui/device_store.py's demo-mode load()/mutate_device()/mutate_devices()
    hand back a fresh copy of on every call. Deliberately doesn't seed an
    "endpoint_state" sub-key - each endpoint's last_forward_status/time is
    baked directly into its "config" entry instead (see _endpoints_for), and
    webui/forwarders/latest_values_store.get_endpoint_state() only ever
    *adds* to what's already there, never replaces it, so an empty/missing
    endpoint_state leaves those baked-in values displaying correctly."""
    devices = {}
    for spec in _DEVICES:
        canonic_id = spec["canonic_id"]
        location = _location_for(spec)
        entry = {
            "config": {
                "display_name": spec["alias"] or "",
                "google_name": spec["name"],
                "device_meta": _device_meta_for(spec),
                "endpoints": _endpoints_for(canonic_id),
            },
            "location": {"locations": [location], "fetched_at": location["time"]},
        }
        staleness_cfg = _staleness_for(spec)
        if staleness_cfg is not None:
            entry["staleness"] = staleness_cfg
        devices[canonic_id] = entry
    return {"schema_version": 1, "devices": devices}


def fake_locate_result(canonic_id: str) -> list[dict]:
    """Same shape as NovaApi.ExecuteAction.LocateTracker.location_request
    .get_location_data_for_device()'s return value - a demo visitor's Locate
    click re-reports this device's one fixed position, freshly-timed ("just
    now") rather than its normal canned recency, since a manual click ought
    to look like it actually did something. Empty list (matching the real
    "timed out" shape) for a canonic_id this dataset doesn't recognize."""
    for spec in _DEVICES:
        if spec["canonic_id"] == canonic_id:
            return [_location_for({**spec, "seen_ago_s": 0})]
    return []


def fake_locate_with_capture_result(canonic_id: str) -> tuple[list[dict], dict]:
    """Same shape as webui/deps.py's locate_device_with_capture() - only
    reachable in demo mode as a defense-in-depth fallback (the one real
    caller, webui/routers/debug_export.py, is blocked outright at the router
    level before this would ever run)."""
    return fake_locate_result(canonic_id), {}


def fake_sound_result(should_start: bool) -> dict:
    """Same shape as NovaApi.ExecuteAction.PlaySound.sound_action.play_sound()'s
    return value."""
    return {"ok": True, "should_start": should_start}


def fake_register_result(**_kwargs) -> dict:
    """Same shape as SpotApi.CreateBleDevice.create_ble_device.register_esp32()'s
    return value - a plausible-looking but inert advertisement key, not tied
    to any real tracker. Accepts (and ignores) whatever identity kwargs
    webui.deps.register_tracker() was called with - the fake result doesn't
    depend on what was submitted, same as a real registration's EID never
    depends on the identity fields either."""
    return {
        "eid_hex": "de3a70d0" + "0" * 24 + "demo",
        "advertisement_key": "de3a70d0" + "0" * 24 + "demo",
        "pair_date": int(time.time()),
    }


def demo_forward_log_entries() -> list[dict]:
    """Same shape as webui/forwarders/log_store.py's recent_entries() -
    newest first, "response" field included. Each configured endpoint's
    current status, plus one older entry apiece so the Forwarding Log
    doesn't look bare on a fresh demo."""
    now = int(time.time())

    def _name_for(canonic_id: str) -> str:
        return next(spec["name"] for spec in _DEVICES if spec["canonic_id"] == canonic_id)

    entries = []
    for canonic_id, endpoints in _ENDPOINTS.items():
        device_name = _name_for(canonic_id)
        for ep in endpoints:
            target = f"{ep.get('alias')} ({ep.get('method')} {ep.get('url')})"
            status = ep.get("last_forward_status", "ok")
            level = "ok" if status == "ok" else ("error" if status.startswith("error") else "skipped")
            response = "200 OK" if status == "ok" else ""
            ago = ep.get("last_forward_ago_s", 0)
            entries.append({
                "time": now - ago, "canonic_id": canonic_id, "device_name": device_name,
                "endpoint_type": "custom", "target": target, "status": status,
                "payload": "", "response": response, "level": level,
            })
            entries.append({
                "time": now - ago - 300, "canonic_id": canonic_id, "device_name": device_name,
                "endpoint_type": "custom", "target": target, "status": "ok",
                "payload": "", "response": "200 OK", "level": "ok",
            })
    entries.sort(key=lambda e: e["time"], reverse=True)
    return entries


def demo_system_log_entries() -> list[dict]:
    """Same shape as webui/system_log_store.py's recent_entries()."""
    now = int(time.time())
    lines = [
        (30, "INFO", "webui.main", "Application startup complete."),
        (25, "INFO", "webui.scheduler", "Demo mode active - background polling disabled."),
        (20, "INFO", "webui.staleness", "Demo mode active - staleness sweep disabled."),
        (600, "INFO", "webui.forwarders.custom", "Forwarded location to Home Traccar."),
        (3600, "WARNING", "webui.forwarders.policy", "Forwarding to Backup Traccar has failed 3 times in a row"),
        (3700, "INFO", "webui.browser_provisioning", "Sign-in is disabled on this demo instance."),
    ]
    return [
        {"time": now - ago, "level": level, "logger": logger_name, "message": message}
        for ago, level, logger_name, message in lines
    ]


def demo_auth_status() -> dict:
    """Same shape as webui/routers/auth.py's _auth_status() - a fully
    "signed in" account, so the Config page reads like a real logged-in
    instance rather than logged in with every diagnostic field blank."""
    return {
        "logged_in": True,
        "username": OWNER_USERNAME,
        "shared_key_ready": True,
        "diagnostics": [
            {"name": name, "present": True}
            for name in ["username", "aas_token", "fcm_credentials", "shared_key", "owner_key"]
        ],
        "cleanup_warning": None,
    }
