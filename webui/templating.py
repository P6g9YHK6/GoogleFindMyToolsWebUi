import pathlib
import time
from datetime import datetime

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from webui import config, demo_mode, scheduler, staleness
from webui.colors import location_color

BASE_DIR = pathlib.Path(__file__).parent


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "< 1m"


def _format_build_date(raw: str) -> str:
    """"14:32 · 04 Oct 2026" from the UTC timestamp CI stamps into
    .build_info (see config.py's _read_build_info) - converted to the
    server's own local time first, same as every other timestamp in the UI
    (ping_time, etc.), so it doesn't read as the odd one out just because
    the source happened to be UTC. Blank for a local dev build, which never
    sets GFMT_BUILD_DATE at all; shown as-is if it's somehow unparsable
    rather than just disappearing."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone()
    except ValueError:
        return raw
    return dt.strftime("%H:%M · %d %b %Y")


def _local_utc_offset_min() -> int:
    """The server's own local UTC offset, in minutes - .astimezone() on a
    naive datetime always attaches a real tzinfo, so utcoffset() is never
    actually None here; this just satisfies mypy without an assert."""
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def _build_info(request: Request) -> dict:
    """Runs on every template render (see the Jinja2Templates call below) so
    the footer in base.html doesn't need every router's TemplateResponse call
    to pass this along individually."""
    return {
        "build_sha": config.GFMT_BUILD_SHA,
        "build_sha_short": config.GFMT_BUILD_SHA if config.GFMT_BUILD_SHA == "dev" else config.GFMT_BUILD_SHA[:7],
        "build_date": _format_build_date(config.GFMT_BUILD_DATE),
        "build_branch": config.GFMT_BUILD_BRANCH,
        "uptime_str": _format_uptime(time.monotonic() - config.APP_START_TIME),
        # The footer clock is meant to catch a misconfigured TZ env var at a
        # glance - showing the viewer's own browser time instead would mask
        # exactly that. now_str is just the initial paint; base.html's own
        # script ticks it live from now_ts/now_utc_offset_min below (a UTC
        # epoch plus the server's own UTC offset, not a local wall-clock
        # string) so it keeps reading as the *server's* clock even as it
        # drifts away from whatever moment this request rendered at.
        "now_str": datetime.now().strftime("%H:%M:%S · %d %b %Y"),
        "now_ts": time.time(),
        "now_utc_offset_min": _local_utc_offset_min(),
        # Small footer flag, not a banner - the one piece of UI chrome demo
        # mode adds anywhere (see base.html). Deliberately DEMO_MODE=1 only,
        # not devices_placeholder_active() too - that trigger is scoped to
        # the Devices page alone (see webui/demo_mode.py), and this context
        # processor runs on every page. Showing "Demo" in, say, the Settings
        # page's footer just because no account is signed in yet would be
        # actively misleading there, since that page shows its normal
        # not-signed-in state, not fake data. Also what auth/login.html and
        # firmware/page.html read to disable their own real-action buttons,
        # with no router needing to pass this along by hand.
        "demo_mode": demo_mode.is_demo_mode(),
    }


# One entry per top-level page base.html's nav links to - lets it highlight
# the current page (and pre-open the Settings group when landing directly on
# one of its three pages) purely from the request path, the same
# derive-it-once-in-a-context-processor approach _build_info uses above,
# instead of every one of those six routers' TemplateResponse calls passing
# an identical "which page is this" value along by hand.
_NAV_PAGES = {
    "/": "devices",
    "/firmware": "firmware",
    "/settings": "settings",
    "/logs": "logs",
    "/auth": "auth",
}


def _active_page(request: Request) -> dict:
    return {"active_page": _NAV_PAGES.get(request.url.path)}


def ping_time(ts: int | float | None) -> str:
    """"14:32 · 04 Oct 2026" - the hover title on a ping row (see
    devices/_locate_cell.html). Time first since that's usually the part
    worth a glance; the month name instead of a numeric one sidesteps the
    DD/MM vs MM/DD ambiguity a plain "14:32 04/10/26" would have."""
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%H:%M · %d %b %Y")


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"), context_processors=[_build_info, _active_page])
# Lets the schedule editor's cron preview render inline on the initial page
# load (settings/_endpoint_fields.html calls cron_preview(cron_value)
# directly) without threading a computed value through every route that
# renders that partial - the live htmx update in webui/routers/settings.py
# calls the exact same function, so the two can never disagree.
templates.env.globals["cron_preview"] = scheduler.cron_preview
# Lets devices/_locate_cell.html color each location's swatch the same as
# its map pin (see webui/colors.py) without every caller threading the
# color through by hand.
templates.env.globals["location_color"] = location_color
templates.env.globals["ping_time"] = ping_time
# "2 days"/"18 hours"/... for tooltips built from a staleness/alert status's
# age_s/threshold_s (see devices/_table.html) - same wording the alert
# message itself uses, so a card's tooltip never disagrees with its own
# notification text.
templates.env.globals["format_duration"] = staleness.format_duration
