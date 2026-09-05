"""The one seam every demo-mode-aware module imports through, so DEMO_MODE
and "no account configured yet" (see devices_placeholder_active) can't get
tangled together by accident. See webui/demo_data.py for the actual fake
dataset, and webui/config.py's DEMO_MODE for the env var that drives this.
"""

from webui import config
from webui.auth_state import is_logged_in


def is_demo_mode() -> bool:
    """DEMO_MODE=1 - the full public-showcase mode: every page shows fake
    data, real Google login and all outbound network calls are disabled."""
    return config.DEMO_MODE


def devices_placeholder_active() -> bool:
    """True only for a normal (DEMO_MODE unset) instance that has no Google
    account signed in yet - shows the same fake device dataset on the
    Devices page as an onboarding placeholder, so a fresh self-hosted
    install isn't just an empty table. Deliberately used nowhere except
    webui/routers/devices.py: real login stays fully live, and Settings/
    Staleness/Logs/Firmware/write-actions behave exactly as they always
    have - this must never leak into any of those, or into the global
    footer context (see webui/templating.py's _build_info, which uses
    is_demo_mode() only, never this)."""
    return not is_demo_mode() and not is_logged_in()
