"""Process-wide, low-level backstop for demo mode: once installed, every
outbound HTTP/WebSocket call raises instead of touching the network - on top
of, not instead of, the service-layer short-circuits elsewhere (webui/deps.py,
webui/forwarders/custom.py, webui/routers/vnc_proxy.py, ...), so a bug or a
missed call site still can't leak a real request. No-op when demo mode is off.

Doesn't cover Auth/fcm_receiver.py's raw MCS socket (not requests/httpx/
websockets-based) - unreachable in demo mode anyway, since locate/sound/login
are already cut off before FCM is touched. Doesn't cover
webui/esp_idf_provisioning.py's subprocess calls either - blocked instead by
never starting a Firmware Build in the first place (webui/firmware_build.py).
"""

import httpx
import requests
import websockets


class DemoNetworkBlocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    # Sync, not async: raises before there's anything to await/enter, which
    # covers both the sync and async call sites below with one function.
    raise DemoNetworkBlocked(
        "Network egress is disabled in demo mode (DEMO_MODE=1) - this call should never have been reached."
    )


def install(enabled: bool):
    if not enabled:
        return
    requests.sessions.Session.request = _blocked  # type: ignore[method-assign]
    httpx.Client.request = _blocked  # type: ignore[method-assign]
    httpx.AsyncClient.request = _blocked  # type: ignore[method-assign]
    websockets.connect = _blocked  # type: ignore[misc,assignment]
