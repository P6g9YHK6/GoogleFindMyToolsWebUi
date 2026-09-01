import json

import yaml
from croniter import croniter
from fastapi import APIRouter, Form, HTTPException, Request

from webui import demo_mode, scheduler
from webui.auth_state import login_required
from webui.forwarders import (
    BUILTIN_VARIABLES_FROM_APP,
    BUILTIN_VARIABLES_FROM_FIX,
    PRESETS,
    config_store,
    device_label_variables,
    latest_values_store,
    policy,
    settings_service,
)
from webui.forwarders import blank_endpoint as new_blank_endpoint
from webui.templating import templates

router = APIRouter()

# "</" escaped so a preset value containing "</script" can't end the
# forwarding.html <script> block early.
_PRESETS_JSON = json.dumps(PRESETS).replace("</", "<\\/")

_TEMPLATE_CONTEXT = {
    "presets": PRESETS, "presets_json": _PRESETS_JSON,
    "builtin_variables_from_fix": BUILTIN_VARIABLES_FROM_FIX,
    "builtin_variables_from_app": BUILTIN_VARIABLES_FROM_APP,
    "cron_presets": scheduler.CRON_PRESETS, "cron_preset_values": {value for _, value in scheduler.CRON_PRESETS},
    "status_choices": policy.STATUS_CHOICES,
}


@router.get("/settings")
@login_required
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings/forwarding.html", {
        "rows": await settings_service.rows(), **_TEMPLATE_CONTEXT,
    })


def _form_response(request, row):
    return templates.TemplateResponse(request, "settings/_device_form.html", {"row": row, **_TEMPLATE_CONTEXT})


def _yaml_response(request, canonic_id, name, google_name, alias, yaml_text, label_variables, error=None):
    return templates.TemplateResponse(request, "settings/_device_yaml.html", {
        "canonic_id": canonic_id, "name": name, "google_name": google_name, "alias": alias,
        "yaml_text": yaml_text, "label_variables": label_variables, "error": error, **_TEMPLATE_CONTEXT,
    })


@router.get("/settings/devices/{canonic_id}")
@login_required
async def device_form_route(request: Request, canonic_id: str):
    """Loads the saved config as the structured form - the "Edit as form"
    button uses device_form_preview_route below instead, to keep unsaved
    edits."""
    row = await settings_service.row(canonic_id)
    return _form_response(request, row)


@router.get("/settings/devices/{canonic_id}/yaml")
@login_required
async def device_yaml_route(request: Request, canonic_id: str):
    """Loads the saved config as YAML - the "Edit as YAML" button uses
    device_yaml_preview_route below instead, to keep unsaved edits."""
    row = await settings_service.row(canonic_id)
    yaml_text = yaml.safe_dump(
        settings_service.to_yaml_doc(row["config"].get("display_name", ""), row["config"].get("endpoints", [])),
        sort_keys=False, allow_unicode=True,
    )
    return _yaml_response(
        request, canonic_id, row["name"], row["google_name"],
        row["config"].get("display_name") or "", yaml_text, row["label_variables"],
    )


@router.post("/settings/devices/{canonic_id}/yaml/preview")
@login_required
async def device_yaml_preview_route(request: Request, canonic_id: str, display_name: str = Form("")):
    """The "Edit as YAML" button's target: renders the form's current,
    not-yet-saved field values as YAML, entirely in memory."""
    form = await request.form()
    existing = config_store.get_device_config(canonic_id) or {"endpoints": []}
    endpoints, _errors = settings_service.parse_endpoints_form(form, existing.get("endpoints", []))
    yaml_text = yaml.safe_dump(settings_service.to_yaml_doc(display_name, endpoints), sort_keys=False, allow_unicode=True)
    google_name = existing.get("google_name") or ""
    name = google_name or display_name.strip() or canonic_id
    return _yaml_response(
        request, canonic_id, name, google_name, display_name.strip(), yaml_text,
        device_label_variables(existing.get("device_meta")),
    )


@router.post("/settings/devices/{canonic_id}/form/preview")
@login_required
async def device_form_preview_route(request: Request, canonic_id: str, yaml_text: str = Form(...)):
    """The "Edit as form" button's target: parses the YAML textarea's
    current content back into the form, without saving it."""
    existing = config_store.get_device_config(canonic_id) or {}
    endpoints, display_name, error = settings_service.from_yaml_doc(yaml_text)
    if error:
        google_name = existing.get("google_name") or ""
        name = google_name or existing.get("display_name") or canonic_id
        return _yaml_response(
            request, canonic_id, name, google_name, existing.get("display_name") or "", yaml_text,
            device_label_variables(existing.get("device_meta")), error=error,
        )

    device_cfg = {"display_name": display_name, "endpoints": endpoints}
    row = await settings_service.row(canonic_id, overrides={canonic_id: {"config": device_cfg, "error": None}})
    return _form_response(request, row)


@router.post("/settings/devices/{canonic_id}/yaml")
@login_required
async def save_device_yaml_route(request: Request, canonic_id: str, yaml_text: str = Form(...)):
    row = await settings_service.row(canonic_id)
    endpoints, display_name, error = settings_service.from_yaml_doc(yaml_text)

    def _error(msg: str):
        return _yaml_response(
            request, canonic_id, row["name"], row["google_name"],
            row["config"].get("display_name") or "", yaml_text, row["label_variables"], error=msg,
        )

    if error:
        return _error(error)

    # The form path (update_device_settings below) has always rejected an
    # invalid cron rather than saving it - this path let one through
    # unchecked, silently breaking that endpoint's polling.
    cron_errors = [
        f"endpoints[{i}]: \"{ep.get('cron', '')}\" is not a valid cron expression"
        for i, ep in enumerate(endpoints)
        if not croniter.is_valid(str(ep.get("cron", "")))
    ]
    if cron_errors:
        return _error("; ".join(cron_errors))

    # google_name/device_meta aren't part of what this editor shows (see
    # to_yaml_doc) - carry both forward from what's already on disk instead
    # of losing them.
    existing = config_store.get_device_config(canonic_id)
    device_cfg = {"display_name": display_name, "endpoints": endpoints}
    if existing and existing.get("google_name"):
        device_cfg["google_name"] = existing["google_name"]
    if existing and existing.get("device_meta"):
        device_cfg["device_meta"] = existing["device_meta"]

    try:
        config_store.set_device_config(canonic_id, device_cfg)
        scheduler.restart_device(canonic_id)
    except Exception as e:
        return _error(f"Failed to save: {e}")
    latest_values_store.prune_to_urls(canonic_id, {ep["url"] for ep in endpoints})

    # In demo mode set_device_config() was a no-op - echo back what was just
    # typed instead of re-reading the fixed seed.
    saved_overrides = {canonic_id: {"config": device_cfg, "error": None}} if demo_mode.is_demo_mode() else None
    fresh_row = await settings_service.row(canonic_id, overrides=saved_overrides, saved=True)
    return _form_response(request, fresh_row)


@router.post("/settings/cron-preview")
async def cron_preview_route(request: Request):
    """Live "next run" preview for the schedule editor - reads whatever
    single field got posted, since the real field name is namespaced
    per-endpoint ("ep-{idx}-cron")."""
    form = await request.form()
    cron = next(iter(form.values()), "")
    preview = scheduler.cron_preview(str(cron))
    return templates.TemplateResponse(request, "settings/_cron_preview.html", {"preview": preview})


@router.get("/settings/devices/{canonic_id}/endpoints/blank")
@login_required
async def blank_endpoint_route(request: Request, canonic_id: str):
    blank = new_blank_endpoint(scheduler.DEFAULT_CRON)
    # Rendered outside the per-device-row loop, so `row` is a bare stub
    # carrying just label_variables for the "From the location fix" chips.
    device_meta = (config_store.get_device_config(canonic_id) or {}).get("device_meta")
    return templates.TemplateResponse(request, "settings/_endpoint_fields.html", {
        "endpoint": blank, "idx": "__NEW__", "is_new": True,
        "row": {"label_variables": device_label_variables(device_meta)},
        **_TEMPLATE_CONTEXT,
    })


@router.post("/settings/devices/{canonic_id}/endpoints/{index}/send-now")
@login_required
async def send_now_route(request: Request, canonic_id: str, index: int):
    send_error = None
    try:
        endpoint = await scheduler.forward_now(canonic_id, index)
    except Exception as e:
        # Only a failure to persist the result (e.g. a disk write error) -
        # forward_now already turns a failed send itself into an "error: ..."
        # last_forward_status string.
        endpoint = None
        send_error = f"Send failed: {e}"

    if endpoint is None:
        if send_error is None:
            raise HTTPException(status_code=404, detail="No such device or endpoint")
        device_cfg = config_store.get_device_config(canonic_id) or {}
        endpoints = device_cfg.get("endpoints", [])
        if not (0 <= index < len(endpoints)):
            raise HTTPException(status_code=404, detail="No such device or endpoint")
        endpoint = endpoints[index]

    # Rendered outside the per-device-row loop, so `row`/`idx` aren't in
    # scope the normal way - pass them explicitly instead.
    device_meta = (config_store.get_device_config(canonic_id) or {}).get("device_meta")
    return templates.TemplateResponse(request, "settings/_endpoint_fields.html", {
        "endpoint": endpoint,
        "row": {"canonic_id": canonic_id, "label_variables": device_label_variables(device_meta)},
        "idx": str(index), "send_error": send_error, **_TEMPLATE_CONTEXT,
    })


@router.post("/settings/devices/{canonic_id}")
@login_required
async def update_device_settings(
    request: Request,
    canonic_id: str,
    display_name: str = Form(""),
):
    form = await request.form()
    existing = config_store.get_device_config(canonic_id) or {"endpoints": []}
    endpoints, errors = settings_service.parse_endpoints_form(form, existing.get("endpoints", []))

    # google_name/device_meta aren't fields this form edits - carry them
    # forward from disk, or a plain alias save would wipe them.
    device_cfg = {"display_name": display_name, "endpoints": endpoints}
    if existing.get("google_name"):
        device_cfg["google_name"] = existing["google_name"]
    if existing.get("device_meta"):
        device_cfg["device_meta"] = existing["device_meta"]

    if errors:
        row = await settings_service.row(canonic_id, overrides={canonic_id: {"config": device_cfg, "error": "; ".join(errors)}})
        return _form_response(request, row)

    try:
        config_store.set_device_config(canonic_id, device_cfg)
        scheduler.restart_device(canonic_id)
    except Exception as e:
        row = await settings_service.row(canonic_id, overrides={canonic_id: {"config": device_cfg, "error": f"Failed to save: {e}"}})
        return _form_response(request, row)
    latest_values_store.prune_to_urls(canonic_id, {ep["url"] for ep in endpoints})

    # In demo mode set_device_config() was a no-op - echo back what was just
    # typed instead of re-reading the fixed seed.
    saved_overrides = {canonic_id: {"config": device_cfg, "error": None}} if demo_mode.is_demo_mode() else None
    row = await settings_service.row(canonic_id, overrides=saved_overrides, saved=True)
    return _form_response(request, row)
