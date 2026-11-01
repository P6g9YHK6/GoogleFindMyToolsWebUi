"""Periodic EID upload for regular FMDN trackers, standing in for the
owner's Android phone (see SpotApi/UploadPrecomputedPublicKeyIds/
upload_precomputed_public_key_ids.py's refresh_owned_trackers for what's
uploaded and why). On by default, set EID_UPLOAD=dry-run to only log what
would be sent, or EID_UPLOAD=off to disable it. Started from
webui/main.py's lifespan alongside the staleness sweep.

Fetches the device list itself rather than going through
webui/device_list_cache.py: this runs a couple of times a day at most, and
a day-old cached list could miss a newly paired tracker. Each tracker is
located first so its clock can be calibrated from the reports Google still
holds (see estimate_clock_zero), since pairDate alone was hours off for
some trackers.

Clock zeros are persisted to data/clock_zeros.yaml to survive restarts
and avoid re-locating on every deploy. Only re-calibrated if >24h old
or tracker reports changed.
"""

import asyncio
import logging
import time

from NovaApi.ListDevices.nbe_list_devices import request_device_list
from ProtoDecoders.decoder import parse_device_list_protobuf, parse_device_update_protobuf
from ProtoDecoders.DeviceUpdate_pb2 import IDENTIFIER_ANDROID
from SpotApi.UploadPrecomputedPublicKeyIds.upload_precomputed_public_key_ids import (
    estimate_clock_zero,
    refresh_owned_trackers,
)
from webui import config
from webui.auth_state import is_logged_in
from webui.deps import locate_device_with_capture, run_blocking

logger = logging.getLogger("webui.eid_upload")

MODES = ("off", "dry-run", "on")

# Clock zero persistence - survives container restarts
CLOCK_ZERO_TTL_S = 24 * 3600  # re-calibrate if older than 24h
CLOCK_ZEROS_PATH = config.DATA_DIR / "clock_zeros.yaml"


def _load_clock_zeros() -> dict[str, dict]:
    """Load persisted clock zeros with timestamps."""
    if not CLOCK_ZEROS_PATH.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(CLOCK_ZEROS_PATH.read_text()) or {}
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, dict) and "clock_zero" in v}
    except Exception as e:
        logger.warning("Failed to load clock zeros from %s: %s", CLOCK_ZEROS_PATH, e)
    return {}


def _save_clock_zeros(clock_zeros: dict[str, int]):
    """Persist clock zeros with timestamps."""
    try:
        import yaml
        data = {
            canonic_id: {"clock_zero": zero, "saved_at": int(time.time())}
            for canonic_id, zero in clock_zeros.items()
        }
        CLOCK_ZEROS_PATH.write_text(yaml.safe_dump(data))
    except Exception as e:
        logger.warning("Failed to save clock zeros to %s: %s", CLOCK_ZEROS_PATH, e)


async def _clock_zero(canonic_id: str, name: str, persisted: dict[str, dict]) -> int | None:
    # Check persisted clock zero first (if < 24h old)
    now = time.time()
    if canonic_id in persisted:
        entry = persisted[canonic_id]
        saved_at = entry.get("saved_at", 0)
        if now - saved_at < CLOCK_ZERO_TTL_S:
            clock_zero = entry["clock_zero"]
            logger.debug("Using persisted clock zero for %s: %d (saved %ds ago)", name, clock_zero, int(now - saved_at))
            return clock_zero
        logger.debug("Persisted clock zero for %s expired (%ds old), re-calibrating", name, int(now - saved_at))

    # The raw response is captured before decryption, so a tracker whose
    # current reports still fail their MAC check can be calibrated from it
    # anyway - which matters exactly when a bad upload needs correcting.
    capture: dict = {}
    try:
        await locate_device_with_capture(
            canonic_id, name, timeout=config.LOCATE_TIMEOUT_S, capture=capture,
        )
    except Exception as e:
        if "raw_hex" not in capture:
            logger.info("Couldn't locate %s to calibrate its clock, using pairDate: %s", name, e)
            return None
        logger.info("Reports for %s didn't decrypt, calibrating from the raw response: %s", name, e)
    if "raw_hex" not in capture:
        return None
    return estimate_clock_zero(parse_device_update_protobuf(capture["raw_hex"]))


async def upload_once(dry_run: bool) -> int:
    if not is_logged_in():
        logger.info("Skipping EID upload, no account signed in yet.")
        return 0
    device_list = parse_device_list_protobuf(await run_blocking(request_device_list))

    trackers = [
        (device.identifierInformation.canonicIds.canonicId[0].id, device.userDefinedDeviceName)
        for device in device_list.deviceMetadata
        if device.identifierInformation.type != IDENTIFIER_ANDROID
    ]

    # Load persisted clock zeros and re-calibrate only if expired
    persisted = _load_clock_zeros()
    new_zeros = {}
    for canonic_id, name in trackers:
        zero = await _clock_zero(canonic_id, name, persisted)
        if zero is not None:
            new_zeros[canonic_id] = zero

    # Merge: keep fresh persisted values, add new ones
    clock_zeros = {}
    for canonic_id, _ in trackers:
        if canonic_id in new_zeros:
            clock_zeros[canonic_id] = new_zeros[canonic_id]
        elif canonic_id in persisted:
            entry = persisted[canonic_id]
            if time.time() - entry.get("saved_at", 0) < CLOCK_ZERO_TTL_S:
                clock_zeros[canonic_id] = entry["clock_zero"]

    # Save updated clock zeros
    if clock_zeros:
        _save_clock_zeros(clock_zeros)

    return await run_blocking(
        refresh_owned_trackers, device_list,
        drift_slots=config.EID_UPLOAD_DRIFT_SLOTS, dry_run=dry_run, clock_zeros=clock_zeros,
    )


async def upload_loop():
    mode = config.EID_UPLOAD
    if mode not in MODES:
        logger.warning("Unknown EID_UPLOAD value %r, expected one of %s. EID upload stays off.", mode, MODES)
        return
    if mode == "off":
        return

    logger.info("EID upload enabled (%s), every %ss.", mode, config.EID_UPLOAD_INTERVAL_S)
    while True:
        try:
            count = await upload_once(mode == "dry-run")
            logger.info("EID upload %s: %d tracker(s).", "dry run done" if mode == "dry-run" else "done", count)
        except Exception:
            logger.exception("EID upload failed")
        await asyncio.sleep(max(3600, config.EID_UPLOAD_INTERVAL_S))
