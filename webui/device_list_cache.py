"""Short-lived (single-digit-seconds) cache around the Nova device-list call
both webui/routers/devices.py and webui/routers/settings.py hit on every page
load - the slowest thing either page does. One shared, keyless, process-wide
slot: this app only ever talks to one signed-in Google account at a time.

Deliberately not in NovaApi/ListDevices/nbe_list_devices.py itself - that
module's list_devices() is also the CLI entry point and must never see stale
cached data just because a webui process happened to fetch recently.

Callers supply their own fetch closure rather than this module calling
request_device_list() itself, so each router's own monkeypatched binding
(see tests/conftest.py) keeps working untouched.
"""

import threading
import time
from collections.abc import Callable
from typing import TypeVar

from webui import config

T = TypeVar("T")


class DeviceListCache:
    """Caches whatever `fetch` returns, for `ttl_s` seconds. Single-flight
    under concurrent misses: the lock is held across the whole fetch call, so
    a burst of concurrent callers past expiry collapses into one real Nova
    call instead of a thundering herd.

    threading.Lock, not asyncio.Lock: reached from real worker threads (see
    webui/deps.py's run_blocking), not just coroutines on one event loop.
    """

    def __init__(self, ttl_s: float = 8.0, clock: Callable[[], float] = time.monotonic):
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._value: object = None
        self._fetched_at: float | None = None
        self._has_value = False

    def get_or_fetch(self, fetch: Callable[[], T]) -> T:
        with self._lock:
            now = self._clock()
            if self._has_value and self._fetched_at is not None and now - self._fetched_at < self._ttl_s:
                return self._value  # type: ignore[return-value]
            value = fetch()
            self._value = value
            self._fetched_at = self._clock()
            self._has_value = True
            return value

    def invalidate(self):
        """Forces the next get_or_fetch() to actually fetch, regardless of
        TTL - e.g. right after registering a new tracker, so it shows up on
        the very next page load."""
        with self._lock:
            self._has_value = False
            self._value = None
            self._fetched_at = None


device_list_cache = DeviceListCache(ttl_s=config.DEVICE_LIST_CACHE_TTL_S)
