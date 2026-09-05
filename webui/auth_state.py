from Auth import token_cache
from Auth.token_cache import get_cached_value
from webui import config


def is_logged_in() -> bool:
    if config.DEMO_MODE:
        # Full public-showcase demo mode reads as a normal, already-signed-in
        # instance everywhere - see webui/demo_mode.py and webui/demo_data.py.
        # devices_placeholder_active() (webui/demo_mode.py) is the separate,
        # narrower "no account configured yet" case that must NOT go through
        # here - it deliberately keeps the real check below.
        return True
    return bool(get_cached_value("aas_token") and get_cached_value("fcm_credentials"))


def auth_store_ok() -> bool:
    """Whether auth.yaml's most recent read actually succeeded - see
    Auth.token_cache.last_load_ok(). is_logged_in() alone can't tell a
    corrupt store apart from a legitimately logged-out one."""
    if config.DEMO_MODE:
        return True
    return token_cache.last_load_ok()
