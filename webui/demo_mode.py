"""The one seam every demo-mode-aware module imports through, so DEMO_MODE
and "no account configured yet" (devices_placeholder_active) can't get
tangled together by accident. See webui/demo_data.py for the fake dataset.
"""

from webui import config
from webui.auth_state import is_logged_in


def is_demo_mode() -> bool:
    """DEMO_MODE=1 - fake data everywhere, real login and outbound network
    calls disabled."""
    return config.DEMO_MODE


def devices_placeholder_active() -> bool:
    """A normal (DEMO_MODE unset) instance with no account signed in yet -
    shows fake devices on the Devices page only, as an onboarding
    placeholder. Must never leak into Settings/Staleness/Logs/Firmware/
    write-actions or the global footer (see webui/templating.py's
    _build_info, which uses is_demo_mode() only, never this)."""
    return not is_demo_mode() and not is_logged_in()
