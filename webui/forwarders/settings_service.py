"""Business logic behind the Settings page's routes
(webui/routers/settings.py): fetching+merging device rows, YAML<->form
conversion, and parsing the posted endpoints form. Split out of the router
itself so it stays thin, matching every other router in the app - this used
to be 681 lines of routing and parsing/conversion logic in one file.
"""

import json
from typing import Any

import yaml
from croniter import croniter
from starlette.datastructures import FormData

from NovaApi.ListDevices.nbe_list_devices import request_device_list
from ProtoDecoders.decoder import get_device_details, parse_device_list_protobuf
from webui import demo_data, demo_mode, device_location_store, scheduler
from webui.deps import run_blocking
from webui.device_list_cache import device_list_cache
from webui.forwarders import build_context, config_store, device_label_variables, latest_values_store, policy
from webui.routers.devices import device_type_plain_label


def device_meta_from_detail(detail: dict) -> dict:
    """The subset of get_device_details' per-device dict worth persisting
    into devices.yaml so the poll loop can read it at forward time (it never
    talks to Google's device-list API itself). shared_with is comma-joined,
    not a list, since it's substituted directly into request text."""
    shared_with = ", ".join(a["email"] for a in detail["access"] if not a["this_account"])
    return {
        "manufacturer": detail["manufacturer"] or "",
        "model": detail["model"] or "",
        "type": device_type_plain_label(detail["device_type"], detail["is_phone"]) or "",
        # "is not None", not "or \"\"" - 0 (DEVICE_TYPE_UNKNOWN) is legitimate.
        "type_id": detail["type_id"] if detail["type_id"] is not None else "",
        "image_url": detail["image_url"] or "",
        "carrier": detail["carrier"] or "",
        "codename": detail["codename"] or "",
        "imei": detail["imei"] or "",
        "registered_at": detail["registered_at"] or "",
        "shared_with": shared_with,
    }


def preview_values_json_for(canonic_id: str, google_name: str, device_meta: dict | None) -> str:
    """Real last-known values the Preview panel should prefer over its
    hardcoded placeholders (endpoint_fields.js's SAMPLE_VALUES) - built from
    the same build_context() an actual send uses, so it can't drift. Fields
    with nothing real known are left out entirely, so the client-side merge
    falls through to its own placeholder for just those. "</" is escaped so
    a value can't end _device_form.html's embedding <script> block early."""
    last = device_location_store.get_last_location(canonic_id)
    fix = None
    if last and last.get("locations"):
        candidates = device_location_store.most_recent_only(last["locations"])
        if candidates and not candidates[0].get("is_semantic"):
            fix = candidates[0]

    ctx = build_context({}, fix or {}, google_name or "", tracker_id=canonic_id, device_meta=device_meta)
    # Already handled correctly client-side (endpoint_fields.js's blockVars).
    skip = {"device_name", "device_alias", "endpoint_alias", "current_timestamp", "fix_timestamp"}
    values = {}
    for key, value in ctx.items():
        if key in skip:
            continue
        if key == "own_report":
            # bool(None) == False too - only tell the two apart by whether a
            # real fix was found, not by own_report's own truthiness.
            if fix is not None:
                values[key] = value
            continue
        if value not in (None, ""):
            values[key] = value
    return json.dumps(values).replace("</", "<\\/")


async def rows(overrides: dict[str, dict] | None = None, saved_id: str | None = None) -> list[dict]:
    if demo_mode.is_demo_mode():
        device_details = demo_data.demo_device_details()
    else:
        def _fetch():
            result_hex = request_device_list()
            device_list = parse_device_list_protobuf(result_hex)
            return get_device_details(device_list)

        # Same shape webui/routers/devices.py fetches - both pages share one
        # device_list_cache slot, so both have to ask for the same shape,
        # even though only name/canonic_id are actually used below.
        device_details = await run_blocking(device_list_cache.get_or_fetch, _fetch)
    devices = config_store.all_devices()

    result = []
    for detail in device_details:
        google_name, canonic_id = detail["name"], detail["canonic_id"]
        device_cfg = devices.get(canonic_id)
        if device_cfg is None:
            # Blank, not google_name - a never-saved device's alias field
            # must not look pre-filled/deliberately typed in.
            device_cfg = {"display_name": "", "endpoints": []}
        else:
            device_meta = device_meta_from_detail(detail)
            if device_cfg.get("google_name") != google_name or device_cfg.get("device_meta") != device_meta:
                # Keep the account's real name/metadata in sync on disk, for
                # devices already saved - scheduler.py's poll loop never
                # talks to Google's API itself, so this is the only place
                # {{device_name}}/{{manufacturer}}/etc. get their values from
                # at forward time.
                device_cfg = dict(device_cfg, google_name=google_name, device_meta=device_meta)
                config_store.set_device_config(canonic_id, device_cfg)
                devices[canonic_id] = device_cfg
        save_error = None
        if overrides and canonic_id in overrides:
            device_cfg = overrides[canonic_id]["config"]
            save_error = overrides[canonic_id]["error"]
        # Runtime state isn't config anymore (latest_values_store.py), so it
        # never comes back from config_store above - merge it in here,
        # display-only, for _endpoint_fields.html's "Last forward: ..." line.
        for ep in device_cfg.get("endpoints", []):
            if isinstance(ep, dict):
                state = latest_values_store.get_endpoint_state(canonic_id, ep.get("url", ""))
                if state:
                    ep.update(state)
        alias = device_cfg.get("display_name") or google_name
        result.append({
            "name": alias,
            "google_name": google_name,
            "canonic_id": canonic_id,
            "config": device_cfg,
            "save_error": save_error,
            "saved": canonic_id == saved_id,
            "label_variables": device_label_variables(device_cfg.get("device_meta")),
            "preview_values_json": preview_values_json_for(canonic_id, google_name, device_cfg.get("device_meta")),
        })
    return result


async def row(canonic_id: str, overrides: dict[str, dict] | None = None, saved: bool = False) -> dict:
    """The single row a device's own save POST should come back as - saving one
    device's form must not hand the browser the whole page's worth of forms to
    swap into that one form's slot (see _device_row.html)."""
    all_rows = await rows(overrides=overrides, saved_id=canonic_id if saved else None)
    fallback = (overrides or {}).get(canonic_id, {})
    fallback_config = fallback.get("config", {"endpoints": []})
    return next(
        (r for r in all_rows if r["canonic_id"] == canonic_id),
        {
            "name": fallback_config.get("display_name", canonic_id),
            "google_name": None,
            "canonic_id": canonic_id,
            "config": fallback_config,
            "save_error": fallback.get("error"),
            "saved": saved,
            "label_variables": device_label_variables(fallback_config.get("device_meta")),
            "preview_values_json": preview_values_json_for(canonic_id, "", fallback_config.get("device_meta")),
        },
    )


def to_yaml_doc(display_name: str, endpoints: list[dict]) -> dict:
    """The YAML editor's view of a device: alias and endpoints - no
    "google_name" (read-only, fed from Google's device list), and no
    per-endpoint "type" (always the same generic query-builder shape now)."""
    clean_endpoints = [{k: v for k, v in ep.items() if k != "type"} for ep in endpoints]
    return {"display_name": display_name, "endpoints": clean_endpoints}


def from_yaml_doc(yaml_text: str) -> tuple[list[dict], str, str | None]:
    """Inverse of to_yaml_doc: (endpoints, display_name, error). Shared by
    the save route and the live "switch to form" preview. Cron validity is
    deliberately not checked here - that's save-time only, a live preview
    should never refuse to just show you what you typed."""
    try:
        parsed = yaml.safe_load(yaml_text)
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("must be a mapping (e.g. \"endpoints: [...]\"), not a list or a bare value")
        parsed.setdefault("endpoints", [])
        if not isinstance(parsed["endpoints"], list):
            raise ValueError("\"endpoints\" must be a list")
        for i, endpoint in enumerate(parsed["endpoints"]):
            if not isinstance(endpoint, dict):
                raise ValueError(f"endpoints[{i}] must be a mapping")
        parsed.setdefault("display_name", "")
        if not isinstance(parsed["display_name"], str):
            raise ValueError("\"display_name\" must be a string")
    except (yaml.YAMLError, ValueError) as e:
        return [], "", f"Invalid YAML: {e}"

    endpoints = [{k: v for k, v in ep.items() if k != "type"} for ep in parsed["endpoints"]]
    return endpoints, parsed["display_name"], None


def parse_kv_rows(keys: list[str], values: list[str]) -> dict:
    return {k.strip(): v for k, v in zip(keys, values) if k.strip()}


def parse_endpoints_form(form: FormData, existing_endpoints: list[dict]) -> tuple[list[dict], list[str]]:
    """(endpoints, errors) straight off a posted device form - shared by the
    save route and the "switch to YAML view" preview, so both reflect the
    same not-yet-saved values.

    Fields are namespaced "ep-{idx}-{field}" (idx = saved position, or a
    fresh client-generated id for a new block - see endpoint_fields.js), so
    each block's own variable-length headers table can't bleed into
    another's."""
    ep_order = form.getlist("ep_order")

    endpoints: list[dict[str, Any]] = []
    errors = []
    for idx in ep_order:
        def field(name: str, default: str = "") -> str:
            # Every field here is a plain text input, never a file, so
            # form.get() (typed str | UploadFile | None) is always a str.
            value = form.get(f"ep-{idx}-{name}", default)
            return str(value) if value else default

        def field_list(name: str) -> list[str]:
            return [str(v) for v in form.getlist(f"ep-{idx}-{name}")]

        url = field("url").strip()
        if not url:
            continue  # unfilled "+ Add endpoint" block, drop it silently

        # The Preset dropdown (presets.py) is a one-time template, never a
        # saved property - ignored here even if posted. No "type" either -
        # every endpoint saved through this form is the same generic shape.
        entry: dict[str, Any] = {
            "method": (field("method", "GET").strip().upper() or "GET"),
            "url": url,
        }

        # Only write these keys when non-empty - every reader already
        # treats missing the same as present-but-empty.
        headers = parse_kv_rows(field_list("header_key"), field_list("header_value"))
        if headers:
            entry["headers"] = headers

        body_type = field("body_type", "none").strip() or "none"
        if body_type != "none":
            entry["body_type"] = body_type

        body = field("body")
        if body:
            entry["body"] = body

        alias = field("alias").strip()
        if alias:
            entry["alias"] = alias

        cron_expr = field("cron").strip()
        if not cron_expr or not croniter.is_valid(cron_expr):
            errors.append(f"Endpoint {len(endpoints) + 1}: \"{cron_expr}\" is not a valid cron expression")
        entry["cron"] = cron_expr or scheduler.DEFAULT_CRON

        if field("skip_if_close", "0") == "1":
            entry["skip_if_close"] = True
            try:
                entry["min_movement_m"] = float(field("min_movement_m") or policy.DEFAULT_MIN_MOVEMENT_M)
            except ValueError:
                entry["min_movement_m"] = policy.DEFAULT_MIN_MOVEMENT_M

        if field("skip_if_stale", "0") == "1":
            entry["skip_if_stale"] = True
            try:
                entry["min_update_gap_m"] = float(field("min_update_gap_m") or policy.DEFAULT_MIN_UPDATE_GAP_M)
            except ValueError:
                entry["min_update_gap_m"] = policy.DEFAULT_MIN_UPDATE_GAP_M

        # These two default *on* (policy._skip_already_seen/_skip_not_most_recent),
        # opposite the other toggles here - "off" needs a real persisted False.
        if field("skip_if_already_seen", "1") != "1":
            entry["skip_if_already_seen"] = False

        if field("only_most_recent", "1") != "1":
            entry["only_most_recent"] = False

        # Each status checkbox defaults to checked once visible; only ones
        # actively unchecked get persisted (policy._skip_blocked_status).
        if field("filter_by_status", "0") == "1":
            entry["filter_by_status"] = True
            blocked_statuses = [code for code, _ in policy.STATUS_CHOICES if field(f"status_{code}", "1") != "1"]
            if blocked_statuses:
                entry["blocked_statuses"] = blocked_statuses

        if field("skip_if_not_own_report", "0") == "1":
            entry["skip_if_not_own_report"] = True

        if field("skip_if_inaccurate", "0") == "1":
            entry["skip_if_inaccurate"] = True
            try:
                entry["max_accuracy_m"] = float(field("max_accuracy_m") or policy.DEFAULT_MAX_ACCURACY_M)
            except ValueError:
                entry["max_accuracy_m"] = policy.DEFAULT_MAX_ACCURACY_M

        # Best-effort carry-forward of legacy "variables" (no field left to
        # re-post one) if this still looks like the same logical endpoint.
        position = len(endpoints)
        if position < len(existing_endpoints) and existing_endpoints[position].get("url") == url:
            if "variables" in existing_endpoints[position]:
                entry["variables"] = existing_endpoints[position]["variables"]

        endpoints.append(entry)

    return endpoints, errors
