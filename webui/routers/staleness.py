from datetime import datetime

from fastapi import APIRouter, Request

from NovaApi.ListDevices.nbe_list_devices import request_device_list
from ProtoDecoders.decoder import get_device_details, parse_device_list_protobuf
from webui import demo_data, demo_mode, staleness
from webui.auth_state import login_required
from webui.deps import run_blocking
from webui.device_list_cache import device_list_cache
from webui.forwarders import config_store, latest_values_store
from webui.templating import templates

router = APIRouter()

_TEMPLATE_CONTEXT = {
    "duration_presets": staleness.DURATION_PRESETS,
    "duration_preset_values": staleness.DURATION_PRESET_VALUES,
    "repeat_off": staleness.REPEAT_OFF,
}


async def _rows() -> list[dict]:
    """One row per device on the account, not just ones already opted into
    staleness tracking - turning tracking *on* is the whole point here."""
    if demo_mode.is_demo_mode():
        device_details = demo_data.demo_device_details()
    else:
        def _fetch():
            result_hex = request_device_list()
            device_list = parse_device_list_protobuf(result_hex)
            return get_device_details(device_list)

        device_details = await run_blocking(device_list_cache.get_or_fetch, _fetch)
    devices = config_store.all_devices()

    rows = []
    for detail in device_details:
        google_name, canonic_id = detail["name"], detail["canonic_id"]
        device_cfg = devices.get(canonic_id) or {}
        alias = device_cfg.get("display_name") or google_name
        staleness_cfg = {**staleness.default_staleness(), **latest_values_store.get_device_staleness(canonic_id)}
        status = staleness.compute_status(canonic_id, staleness_cfg)
        rows.append({
            "canonic_id": canonic_id,
            "name": alias,
            "google_name": google_name,
            "staleness": staleness_cfg,
            "status": status,
            # Raw last_fix_time also passed through (see status above) for
            # the client-side live "X ago" ticker to recompute each second.
            "last_fix_str": (
                datetime.fromtimestamp(status["last_fix_time"]).strftime("%Y-%m-%d %H:%M:%S")
                if status["last_fix_time"] else None
            ),
        })
    return rows


@router.get("/staleness")
@login_required
async def staleness_page(request: Request):
    return templates.TemplateResponse(request, "staleness/list.html", {**_TEMPLATE_CONTEXT})


@router.get("/staleness/table")
@login_required
async def staleness_table(request: Request):
    return templates.TemplateResponse(request, "staleness/_table.html", {
        "rows": await _rows(), **_TEMPLATE_CONTEXT,
    })


def _parse_duration_field(form, base_name: str, allow_off: bool) -> int | None:
    """(preset select + "Custom" hours input) -> seconds, or None. allow_off
    additionally recognizes staleness.REPEAT_OFF ("alert once, don't
    repeat"), never offered on the threshold field itself."""
    preset = (form.get(f"{base_name}_preset", "") or "").strip()
    if allow_off and preset == staleness.REPEAT_OFF:
        return None
    if preset:
        try:
            return int(preset)
        except ValueError:
            pass
    custom_hours = (form.get(f"{base_name}_custom_hours", "") or "").strip()
    try:
        hours = float(custom_hours)
    except ValueError:
        return None
    return int(hours * 3600) if hours > 0 else None


@router.post("/staleness/devices/{canonic_id}")
@login_required
async def update_device_staleness(request: Request, canonic_id: str):
    form = await request.form()
    existing = {**staleness.default_staleness(), **latest_values_store.get_device_staleness(canonic_id)}

    new_enabled = form.get("enabled", "0") == "1"
    new_threshold = _parse_duration_field(form, "threshold", allow_off=False)
    new_repeat = _parse_duration_field(form, "repeat", allow_off=True)
    new_template = str(form.get("message_template", "") or "").strip() or staleness.DEFAULT_MESSAGE_TEMPLATE
    new_muted = form.get("muted", "0") == "1"

    staleness_cfg = dict(existing)
    staleness_cfg["enabled"] = new_enabled
    staleness_cfg["threshold_s"] = new_threshold
    staleness_cfg["repeat_s"] = new_repeat
    staleness_cfg["message_template"] = new_template
    staleness_cfg["muted"] = new_muted
    # Turning tracking off (or muting it) also clears any in-flight alert
    # streak, rather than leaving a stale "alert_active" flag that would
    # otherwise immediately fire a "back online" recovery notice the moment
    # it's turned back on again for a device that, in the meantime, never
    # actually recovered.
    if not new_enabled or new_muted:
        staleness_cfg["alert_active"] = False
        staleness_cfg["last_alert_sent_at"] = None

    latest_values_store.set_device_staleness(canonic_id, staleness_cfg)

    return templates.TemplateResponse(request, "staleness/_table.html", {
        "rows": await _rows(), **_TEMPLATE_CONTEXT,
    })
