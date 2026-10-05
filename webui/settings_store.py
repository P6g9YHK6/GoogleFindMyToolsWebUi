"""Persisted app-wide settings (config.yaml) - the query throttle and Apprise
notification settings that used to be env-var-only (see webui/config.py).
A value here overrides its env-var default; nothing set here just falls
back to the env var/hardcoded default, so upgrading with no config.yaml
present yet changes nothing.
"""

import os
import threading

from webui import config, demo_mode
from webui.yaml_io import read_yaml_dict, write_yaml_dict

_lock = threading.Lock()


def _defaults() -> dict:
    return {
        "query_throttle_max": config.QUERY_THROTTLE_MAX,
        "query_throttle_window_s": config.QUERY_THROTTLE_WINDOW_S,
        "query_min_spread_s": config.QUERY_MIN_SPREAD_S,
        "apprise_urls": os.environ.get("APPRISE_URLS", ""),
        "apprise_notify_level": os.environ.get("APPRISE_NOTIFY_LEVEL", "WARNING"),
        # Fixed coordinates for named SEMANTIC locations (e.g. "Nest Mini -
        # Living Room") - Google never reports lat/lon for these, so without
        # an entry here they're skipped by every forwarder (see
        # webui/forwarders/semantic_map.py). Global rather than per-device:
        # a named smart-home device's position doesn't change depending on
        # which tracker reports being near it. {name: {"latitude": float,
        # "longitude": float, "altitude": float (optional),
        # "match_mode": "full"|"partial"}}. match_mode defaults to "full"
        # (exact match) when absent, altitude to None, so entries saved
        # before either field existed keep behaving the same way.
        "semantic_location_map": {},
    }


def load() -> dict:
    defaults = _defaults()
    if demo_mode.is_demo_mode():
        # Never reads config.yaml - a visitor's App Settings save (see
        # webui/routers/auth.py) is echoed back to that one request's
        # response only, never actually persisted (see save() below).
        return defaults
    with _lock:
        data, ok = read_yaml_dict(config.APP_SETTINGS_PATH)
        if not ok:
            return defaults
        defaults.update(data)
        return defaults


def save(data: dict):
    if demo_mode.is_demo_mode():
        return  # hard no-op - see load() above
    with _lock:
        write_yaml_dict(config.APP_SETTINGS_PATH, data)


def apprise_env() -> dict:
    """The current Apprise settings, shaped as the env-var dict
    webui.notify.configure_apprise_logging() expects."""
    settings = load()
    return {
        "APPRISE_URLS": settings["apprise_urls"],
        "APPRISE_NOTIFY_LEVEL": settings["apprise_notify_level"],
    }
