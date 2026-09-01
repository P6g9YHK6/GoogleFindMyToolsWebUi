"""Forwarding policy: the skip-if-close/skip-if-stale gates, the generic
dispatch-and-log call every endpoint goes through, and the "did this
destination just start failing" escalation. Deliberately decoupled from
*when* any of this runs - that's webui/scheduler.py's job, the cron loop
built on top of everything here.
"""

import json
import logging
import time

from webui.forwarders.custom import forward_to_custom
from webui.geo import haversine_distance_m
from webui.url_redact import url_origin

logger = logging.getLogger("webui.forwarders.policy")

# The subset of this module's underscore-prefixed functions webui/scheduler.py
# imports directly - still "private" to callers outside this package, but a
# deliberate, tested seam between "how to forward" (here) and "when" (there).
# Keep this list in sync with scheduler.py's import block.
__all__ = [
    "_dispatch_forward",
    "_endpoint_target",
    "_format_response_for_log",
    "_forward_one",
    "_record_forward_result",
    "_serialize_location",
]

DEFAULT_MIN_MOVEMENT_M = 50
DEFAULT_MIN_UPDATE_GAP_M = 30
DEFAULT_MAX_ACCURACY_M = 100
# Common.proto's Status enum plus SEMANTIC (a named-location reading, not a
# GPS/WiFi/cellular fix - see semantic_map.py). Roughly best-to-worst, for
# the settings UI's checkbox list.
STATUS_CHOICES: list[tuple[str, str]] = [
    ("LAST_KNOWN", "GPS"),
    ("CROWDSOURCED", "WiFi/Cellular"),
    ("AGGREGATED", "Coarse/low-accuracy"),
    ("SEMANTIC", "Named location"),
]
# A fix this recent is a live update, not Google re-serving a stale cached
# report - always sent regardless of the stale-duplicate gate below.
FRESH_FIX_AGE_S = 120
# Escalates a destination's ongoing failures via logger.error every Nth
# consecutive failure (picked up by the System Log/Apprise), instead of only
# ever showing in the Forwarding Log.
FORWARD_FAILURE_ESCALATION_THRESHOLD = 3

# Six per-endpoint skip gates below, each reading its own toggle off
# endpoint_cfg. skip_if_close/skip_if_stale/filter_by_status/skip_if_inaccurate
# default to *off* when absent; skip_if_already_seen/only_most_recent default
# to *on* - see webui/routers/settings.py's parse_endpoints_form. A semantic
# reading with no mapped coordinates has no latitude and skips every
# distance/accuracy-based gate automatically; one with mapped coordinates is
# treated the same as a real fix throughout.


def _skip_already_seen(endpoint_cfg: dict, already_seen: bool) -> bool:
    """This reading was already reported by Google in an earlier fetch (see
    device_location_store.py's first_seen tracking), not a new observation."""
    return already_seen and endpoint_cfg.get("skip_if_already_seen", True)


def _skip_not_most_recent(endpoint_cfg: dict, is_most_recent: bool) -> bool:
    """Not the reading with the latest "time" in this fetch's batch - Google
    can return several in one response, and most endpoints only want the
    newest."""
    return not is_most_recent and endpoint_cfg.get("only_most_recent", True)


def _too_close_to_bother(endpoint_cfg: dict, location: dict) -> bool:
    """Under the configured minimum-movement threshold from the last
    position actually sent (see webui/geo.py)."""
    if not endpoint_cfg.get("skip_if_close"):
        return False
    if location.get("latitude") is None:
        return False
    last_lat, last_lon = endpoint_cfg.get("last_sent_lat"), endpoint_cfg.get("last_sent_lon")
    if last_lat is None or last_lon is None:
        return False  # nothing sent yet for this endpoint - always send the first fix
    threshold = endpoint_cfg.get("min_movement_m") or DEFAULT_MIN_MOVEMENT_M
    return haversine_distance_m(last_lat, last_lon, location["latitude"], location["longitude"]) < threshold


def _stale_duplicate(endpoint_cfg: dict, location: dict, now: float | None = None) -> bool:
    """Not fresh/live (see FRESH_FIX_AGE_S) and within the configured
    minimum-update-gap of the last fix's own timestamp actually sent - i.e.
    Google re-serving the same stale report, not a new one."""
    if not endpoint_cfg.get("skip_if_stale"):
        return False
    if location.get("is_semantic") and location.get("latitude") is None:
        return False
    if location.get("time") is None:
        return False
    now = time.time() if now is None else now
    if now - location["time"] <= FRESH_FIX_AGE_S:
        return False
    last_fix_time = endpoint_cfg.get("last_sent_fix_time")
    if last_fix_time is None:
        return False  # nothing sent yet for this endpoint - always send the first fix
    gap_s = (endpoint_cfg.get("min_update_gap_m") or DEFAULT_MIN_UPDATE_GAP_M) * 60
    return abs(location["time"] - last_fix_time) < gap_s


def _skip_blocked_status(endpoint_cfg: dict, location: dict) -> bool:
    """This reading's status is one of the fix types unchecked in this
    endpoint's "filter by report type" list. A semantic reading's status is
    always "SEMANTIC" - checked the same way as any other type."""
    if not endpoint_cfg.get("filter_by_status"):
        return False
    return location.get("status") in (endpoint_cfg.get("blocked_statuses") or [])


def _skip_not_own_report(endpoint_cfg: dict, location: dict) -> bool:
    """Crowdsourced from a nearby device, not the tracker's own GPS (see
    own_report in custom.py's build_context). Always bypassed for a semantic
    reading - is_own_report is hardcoded True for those at decode time, so
    it's never a real signal there."""
    if not endpoint_cfg.get("skip_if_not_own_report"):
        return False
    if location.get("is_semantic"):
        return False
    return not location.get("is_own_report")


def _skip_inaccurate(endpoint_cfg: dict, location: dict) -> bool:
    """Google's own accuracy_m radius exceeds the configured threshold.
    Always bypassed for a semantic reading - accuracy is hardcoded 0 for
    those at decode time, so it's never a real signal there."""
    if not endpoint_cfg.get("skip_if_inaccurate"):
        return False
    if location.get("is_semantic") or location.get("accuracy") is None:
        return False
    threshold = endpoint_cfg.get("max_accuracy_m") or DEFAULT_MAX_ACCURACY_M
    return location["accuracy"] > threshold


def _dispatch_forward(
    endpoint_cfg: dict, location: dict, device_name: str = "", device_alias: str | None = None,
    tracker_id: str = "", device_meta: dict | None = None, response_out: dict | None = None,
) -> str:
    """Sends this endpoint's request with no distance/staleness gate - used
    both by the scheduled path (after it passes those) and "send now", which
    bypasses them entirely."""
    try:
        ok = forward_to_custom(endpoint_cfg, location, device_name, device_alias, tracker_id, device_meta, response_out)
        return "ok" if ok else "skipped"
    except Exception as e:
        logger.warning("Forwarding failed: %s", e)
        return f"error: {e}"


def _forward_one(
    endpoint_cfg: dict, location: dict, device_name: str = "", device_alias: str | None = None,
    tracker_id: str = "", device_meta: dict | None = None,
    already_seen: bool = False, is_most_recent: bool = True, response_out: dict | None = None,
) -> str:
    """already_seen/is_most_recent are computed once per batch by
    webui/scheduler.py and passed in - checked first since, unlike the gates
    below, they need no distance/timing math."""
    if _skip_already_seen(endpoint_cfg, already_seen):
        return "skipped: already reported by Google (not a new reading)"
    if _skip_not_most_recent(endpoint_cfg, is_most_recent):
        return "skipped: not the most recent reading in this batch"
    if _too_close_to_bother(endpoint_cfg, location):
        threshold = endpoint_cfg.get("min_movement_m") or DEFAULT_MIN_MOVEMENT_M
        return f"skipped: moved less than {threshold:g}m"
    if _stale_duplicate(endpoint_cfg, location):
        gap = endpoint_cfg.get("min_update_gap_m") or DEFAULT_MIN_UPDATE_GAP_M
        return f"skipped: not updated in the last {gap:g}m"
    if _skip_blocked_status(endpoint_cfg, location):
        return f"skipped: fix type {location.get('status')} is unchecked in this endpoint's filter"
    if _skip_not_own_report(endpoint_cfg, location):
        return "skipped: not this tracker's own report (crowdsourced by another device)"
    if _skip_inaccurate(endpoint_cfg, location):
        threshold = endpoint_cfg.get("max_accuracy_m") or DEFAULT_MAX_ACCURACY_M
        return f"skipped: accuracy radius over {threshold:g}m"
    return _dispatch_forward(endpoint_cfg, location, device_name, device_alias, tracker_id, device_meta, response_out)


def _serialize_location(location: dict) -> str:
    """The exact location data a forward attempt worked with, for the
    forwarding log."""
    try:
        return json.dumps(location, default=str)
    except TypeError:
        return str(location)


def _format_response_for_log(response_out: dict) -> str:
    """response_out as one string for the Forwarding Log's Response column -
    blank on a skip or connection-level failure. Distinguishes a destination
    that answers 200 while silently rejecting the point from a real success,
    which the Status column alone can't."""
    if not response_out:
        return ""
    return f"{response_out.get('status_code', '')}: {response_out.get('body', '')}"


def _endpoint_target(endpoint_cfg: dict) -> str:
    """Short human-readable destination summary, for the forwarding log."""
    url = endpoint_cfg.get("url") or ""
    method = endpoint_cfg.get("method") or "GET"
    label = f"{method} {url}".strip()
    alias = endpoint_cfg.get("alias")
    return f"{alias} ({label})" if alias else label


def _redacted_endpoint_target(endpoint_cfg: dict) -> str:
    """Same as _endpoint_target, but with the URL's path/query dropped, since
    this one feeds the shared application log rather than this endpoint's own
    Forwarding Log entry."""
    host = url_origin(endpoint_cfg.get("url") or "") or "(unparseable url)"
    method = endpoint_cfg.get("method") or "GET"
    label = f"{method} {host}/...".strip()
    alias = endpoint_cfg.get("alias")
    return f"{alias} ({label})" if alias else label


def _record_forward_result(
    endpoint_cfg: dict, status: str, location: dict | None, device_display_name: str, now_ts: int | None = None,
):
    """Updates one endpoint's persisted last-forward state after an attempt,
    and escalates via logger.error every FORWARD_FAILURE_ESCALATION_THRESHOLD
    consecutive failures. A "skipped" status is neither success nor failure,
    so it doesn't touch the streak."""
    endpoint_cfg["last_forward_status"] = status
    endpoint_cfg["last_forward_time"] = now_ts if now_ts is not None else int(time.time())

    if status == "ok" and location is not None:
        endpoint_cfg["last_sent_lat"] = location["latitude"]
        endpoint_cfg["last_sent_lon"] = location["longitude"]
        endpoint_cfg["last_sent_fix_time"] = location.get("time")
        endpoint_cfg["consecutive_failures"] = 0
    elif status.startswith("error"):
        failures = endpoint_cfg.get("consecutive_failures", 0) + 1
        endpoint_cfg["consecutive_failures"] = failures
        if failures % FORWARD_FAILURE_ESCALATION_THRESHOLD == 0:
            logger.error(
                "Forwarding to %s for %s has failed %d times in a row",
                _redacted_endpoint_target(endpoint_cfg), device_display_name, failures,
            )
