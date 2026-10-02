import functools

from starlette.requests import Request

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


def login_required(handler):
    """Route decorator: renders _not_signed_in.html instead of running
    handler when logged out. `templates` is imported inside the wrapper, not
    at module level, to avoid a cycle (webui.templating -> webui.scheduler ->
    this module)."""
    @functools.wraps(handler)
    async def wrapper(request: Request, **kwargs):
        if not is_logged_in():
            from webui.templating import templates
            return templates.TemplateResponse(request, "_not_signed_in.html", {})
        return await handler(request, **kwargs)
    return wrapper
