"""Process-wide, low-level backstop for demo mode: once installed, every
outbound HTTP/WebSocket call this process could possibly make raises instead
of touching the network. This is deliberately independent of, and in
addition to, every service-layer short-circuit elsewhere (webui/deps.py,
webui/forwarders/custom.py, webui/routers/vnc_proxy.py, ...) - a bug or a
future call site missed by one of those still can't leak a real request. A
no-op with zero behavior change when demo mode is off.

Deliberately does not import webui.config - install() takes the already-
resolved flag as a plain bool, so this module has nothing to do with when
demo mode is decided, only with enforcing it once it has been. Called once,
from the bottom of webui/config.py (imported before virtually everything
else) - safe regardless of import order, since only *calls* made after
install() runs are affected, not anything about when requests/httpx/
websockets themselves get imported.

Doesn't cover Auth/fcm_receiver.py's raw MCS socket (not requests/httpx/
websockets-based) - that's fine, since it's only ever reachable via
locate/sound/the real login flow, all three already cut off at the
webui/deps.py / webui/browser_provisioning.py boundary before FCM is ever
touched in demo mode. Also doesn't cover webui/esp_idf_provisioning.py's
`git clone`/toolchain-installer subprocess calls (not network-library-based
either) - those are blocked instead by never starting a Firmware Build in
the first place (see webui/firmware_build.py's own demo-mode guard), a
surer block than a network-layer patch could be since it stops the
subprocess from ever spawning at all. See webui/demo_mode.py for the rest
of the picture.
"""

import httpx
import requests
import websockets


class DemoNetworkBlocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    # Deliberately a plain sync function, not `async def`, for all four
    # targets below - `await client.request(...)` and
    # `async with websockets.connect(...)` both evaluate the call itself
    # (raising this) before there's anything to await/enter, so one
    # synchronous raise covers the sync and async call sites alike.
    raise DemoNetworkBlocked(
        "Network egress is disabled in demo mode (DEMO_MODE=1) - this call should never have been reached."
    )


def install(enabled: bool):
    if not enabled:
        return
    requests.sessions.Session.request = _blocked  # type: ignore[method-assign]
    httpx.Client.request = _blocked
    httpx.AsyncClient.request = _blocked
    websockets.connect = _blocked
