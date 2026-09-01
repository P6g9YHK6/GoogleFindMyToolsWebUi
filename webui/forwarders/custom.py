"""The one forwarder every endpoint sends through, generic HTTP with
{{variable}} templating - Traccar and Nextcloud PhoneTrack are just presets
that pre-fill this (see presets.py), not separate code paths."""

import logging
import re
import time

import httpx

from webui import demo_mode

logger = logging.getLogger("webui.forwarders.custom")

TIMEOUT_S = 10
# Bounded so one misconfigured destination's oversized error page can't blow
# up the forwarding log file.
MAX_LOGGED_RESPONSE_CHARS = 2000
_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")


def _render(template: str, ctx: dict) -> str:
    """Substitutes {{name}} tokens from ctx. An unresolved token (e.g. a
    typo) is left in place rather than dropped, so a bad template is obvious
    in the request itself. A token that resolves to an empty string logs a
    warning instead - easy to miss otherwise, since nothing looks broken."""
    if not template:
        return ""

    def repl(m: re.Match) -> str:
        key = m.group(1)
        value = ctx.get(key)
        if value is None:
            return m.group(0)
        rendered = str(value)
        if rendered == "":
            logger.warning("Template variable {{%s}} resolved to an empty value", key)
        return rendered

    return _TOKEN_RE.sub(repl, template)


# device_meta fields with a dedicated {{name}} - everything else becomes
# {{label_<key>}} instead (see build_context below). Also used by
# presets.py's device_label_variables() to know which keys to skip.
NAMED_DEVICE_META_KEYS = ("manufacturer", "model", "type", "type_id", "image_url")


def build_context(
    endpoint_cfg: dict, location: dict, device_name: str, device_alias: str | None = None,
    tracker_id: str = "", device_meta: dict | None = None,
) -> dict:
    """Every {{variable}} available to this endpoint's templates.

    device_alias falls back to device_name when not given (google_name vs
    display_name - see webui/scheduler.py for where the two get told apart).
    device_meta is what get_device_details knows about the device
    (manufacturer, model, category, photo, phone-only info); its
    NAMED_DEVICE_META_KEYS fields get their own {{name}}, everything else
    becomes {{label_<key>}}."""
    ctx = {
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "altitude_m": location.get("altitude"),
        "accuracy_m": location.get("accuracy"),
        # Common.proto's Status enum, as a string name - SEMANTIC for a
        # named-location reading, LAST_KNOWN/CROWDSOURCED/AGGREGATED for a
        # real fix. status_id is the same thing as its raw numeric value.
        "status": location.get("status") or "",
        "status_id": location.get("status_id") if location.get("status_id") is not None else "",
        "is_semantic": bool(location.get("is_semantic")),
        "semantic_name": location.get("semantic_name") or "",
        "own_report": bool(location.get("is_own_report")),
        "google_timestamp": location.get("time"),
        "current_timestamp": int(time.time()),
        # google_timestamp's old name - not offered as a chip anymore, but
        # kept resolving so an endpoint saved before the rename still works.
        "fix_timestamp": location.get("time"),
        "device_name": device_name or "",
        "device_alias": (device_alias if device_alias is not None else device_name) or "",
        "endpoint_alias": endpoint_cfg.get("alias") or "",
        "tracker_id": tracker_id or "",
    }
    meta = device_meta or {}
    for key in NAMED_DEVICE_META_KEYS:
        # "is not None", not "or \"\"" - type_id can legitimately be 0.
        value = meta.get(key)
        ctx[key] = value if value is not None else ""
    for key, value in meta.items():
        if key not in NAMED_DEVICE_META_KEYS:
            ctx[f"label_{key}"] = value if value is not None else ""
    # Legacy per-endpoint "variables" table, dropped from the UI but still
    # honored for anything saved before that change.
    ctx.update(endpoint_cfg.get("variables") or {})
    return ctx


def forward_to_custom(
    endpoint_cfg: dict, location: dict, device_name: str = "", device_alias: str | None = None,
    tracker_id: str = "", device_meta: dict | None = None, response_out: dict | None = None,
) -> bool:
    """response_out, if given, is filled with the destination's actual
    status_code/body (truncated) - the return value only says whether the
    request was *sent*, which can't tell apart a destination that answers
    200 while silently rejecting the point."""
    if location.get("latitude") is None:
        # A real fix with no coordinates, or a SEMANTIC reading with no
        # configured mapping - either way there's nothing to send.
        return False

    url_template = (endpoint_cfg.get("url") or "").strip()
    if not url_template:
        return False

    if demo_mode.is_demo_mode():
        return True

    ctx = build_context(endpoint_cfg, location, device_name, device_alias, tracker_id, device_meta)
    # Query params live in the URL itself (a literal "?key=value"), not a
    # separate table - passing a `params=` kwarg to httpx would replace
    # rather than merge with it, silently discarding a typed-in querystring.
    url = _render(url_template, ctx)
    headers = {k: _render(v, ctx) for k, v in (endpoint_cfg.get("headers") or {}).items()}
    method = (endpoint_cfg.get("method") or "GET").strip().upper() or "GET"

    body_type = endpoint_cfg.get("body_type") or "none"
    body_raw = endpoint_cfg.get("body") or ""
    content: str | None = None
    data: dict[str, str] | None = None

    if body_type == "json" and body_raw.strip():
        content = _render(body_raw, ctx)
        headers.setdefault("Content-Type", "application/json")
    elif body_type == "raw" and body_raw.strip():
        content = _render(body_raw, ctx)
    elif body_type == "form" and body_raw.strip():
        # One "key=value" pair per line, same {{variable}} substitution.
        data = {}
        for line in body_raw.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = _render(v.strip(), ctx)

    response = httpx.request(method, url, headers=headers, timeout=TIMEOUT_S, content=content, data=data)
    if response_out is not None:
        # Captured before raise_for_status() - a non-2xx's body is exactly
        # what's worth logging, not just its status.
        body = response.text
        if len(body) > MAX_LOGGED_RESPONSE_CHARS:
            body = body[:MAX_LOGGED_RESPONSE_CHARS] + "... (truncated)"
        response_out["status_code"] = response.status_code
        response_out["body"] = body
    response.raise_for_status()
    return True


def preview_request(
    endpoint_cfg: dict, location: dict, device_name: str = "", device_alias: str | None = None,
    tracker_id: str = "", device_meta: dict | None = None,
) -> dict:
    """Non-sending dry-run of forward_to_custom, for the "Send now"
    confirmation - kept alongside it so the two can't drift on templating."""
    ctx = build_context(endpoint_cfg, location, device_name, device_alias, tracker_id, device_meta)
    url = _render((endpoint_cfg.get("url") or "").strip(), ctx)
    headers = {k: _render(v, ctx) for k, v in (endpoint_cfg.get("headers") or {}).items()}
    body_type = endpoint_cfg.get("body_type") or "none"
    body = _render(endpoint_cfg.get("body") or "", ctx) if body_type != "none" else ""
    return {
        "method": (endpoint_cfg.get("method") or "GET").strip().upper() or "GET",
        "url": url, "headers": headers, "body_type": body_type, "body": body,
    }


__all__ = ["forward_to_custom", "build_context", "preview_request", "TIMEOUT_S"]
