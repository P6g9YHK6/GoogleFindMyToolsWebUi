"""Per-endpoint forwarding runtime state - last status/time, last-sent
position, consecutive-failure streak - and per-device staleness tracking
config/state. Backed by devices.yaml (see webui/device_store.py), under this
module's own "endpoint_state" (keyed by endpoint URL) and "staleness"
sub-keys - kept separate from "config" (config_store.py) so that stays pure
configuration.

Keyed by the endpoint's own URL rather than its position in the list, so
state naturally survives reordering and starts fresh if the URL changes (a
differently-targeted request is a new "endpoint" as far as policy.py's gates
are concerned) - no separate reconciliation step needed on save.
"""

from webui import device_store

# Every field policy.py's _record_forward_result computes.
STATE_KEYS = (
    "last_forward_status", "last_forward_time",
    "last_sent_lat", "last_sent_lon", "last_sent_fix_time",
    "consecutive_failures",
)


def get_device_staleness(canonic_id: str) -> dict:
    """{} if tracking has never been configured for this device."""
    entry = device_store.load()["devices"].get(canonic_id) or {}
    return dict(entry.get("staleness") or {})


def set_device_staleness(canonic_id: str, state: dict):
    device_store.mutate_device(canonic_id, lambda entry: entry.update(staleness=state))


def get_endpoint_state(canonic_id: str, url: str) -> dict:
    """{} if nothing has ever been sent through this URL (or it just changed)."""
    if not url:
        return {}
    entry = device_store.load()["devices"].get(canonic_id) or {}
    return dict((entry.get("endpoint_state") or {}).get(url) or {})


def set_endpoint_state(canonic_id: str, url: str, state: dict):
    """Overwrites wholesale - callers build the full dict (see policy.py's
    _record_forward_result) rather than patching individual keys."""
    if not url:
        return

    def _set(entry: dict) -> None:
        entry.setdefault("endpoint_state", {})[url] = state

    device_store.mutate_device(canonic_id, _set)


def prune_to_urls(canonic_id: str, urls: set[str]):
    """Drops recorded state for URLs no longer among this device's current
    endpoints, after a save - hygiene only, not required for correctness."""

    def _prune(entry: dict) -> None:
        endpoint_state = entry.get("endpoint_state")
        if not endpoint_state:
            return
        kept = {url: state for url, state in endpoint_state.items() if url in urls}
        if kept:
            entry["endpoint_state"] = kept
        else:
            entry.pop("endpoint_state", None)

    device_store.mutate_device(canonic_id, _prune)
