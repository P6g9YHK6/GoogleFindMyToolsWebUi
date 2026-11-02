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

Clock zeros are persisted per-device in devices.yaml (under the "clock_zero"
sub-key) to survive restarts and avoid re-locating on every deploy.
Re-calibrated once they're >24h old, keeping the old value if that fails.
"""

import asyncio
import logging
import time

from NovaApi.ListDevices.nbe_list_devices import request_device_list
from ProtoDecoders.decoder import parse_device_list_protobuf, parse_device_update_protobuf
from ProtoDecoders.DeviceUpdate_pb2 import IDENTIFIER_ANDROID
from SpotApi.identity_key import retrieve_identity_key
from SpotApi.UploadPrecomputedPublicKeyIds.upload_precomputed_public_key_ids import (
    estimate_clock_zero,
    refresh_owned_trackers,
)
from webui import config
from webui.auth_state import is_logged_in
from webui.deps import locate_device_with_capture, run_blocking
from webui.device_store import load, mutate_device

logger = logging.getLogger("webui.eid_upload")

MODES = ("off", "dry-run", "on")

# Clock zero persistence - survives container restarts
CLOCK_ZERO_TTL_S = 24 * 3600  # re-calibrate if older than 24h


def _get_persisted_clock_zero(canonic_id: str) -> tuple[int, int] | None:
    """Get persisted clock_zero and saved_at for a device from devices.yaml.
    Returns (clock_zero, saved_at) or None if not found/expired."""
    data = load()
    device = data.get("devices", {}).get(canonic_id, {})
    clock_zero_data = device.get("clock_zero")
    if isinstance(clock_zero_data, dict):
        clock_zero = clock_zero_data.get("clock_zero")
        saved_at = clock_zero_data.get("saved_at", 0)
        if isinstance(clock_zero, int) and isinstance(saved_at, int):
            return clock_zero, saved_at
    return None


def _estimate_from_raw_response(raw_hex: str, name: str) -> int | None:
    device_update = parse_device_update_protobuf(raw_hex)
    registration = device_update.deviceMetadata.information.deviceRegistration
    identity_key = None
    if registration.encryptedUserSecrets.encryptedIdentityKey:
        try:
            identity_key = retrieve_identity_key(registration)
        except Exception as e:
            logger.info("Calibrating %s from its reported counters, no identity key: %s", name, e)
    return estimate_clock_zero(device_update, identity_key)


async def _clock_zero(canonic_id: str, name: str) -> int | None:
    # Check persisted clock zero first (if < 24h old)
    now = time.time()
    persisted = _get_persisted_clock_zero(canonic_id)
    stale = None
    if persisted:
        clock_zero, saved_at = persisted
        if now - saved_at < CLOCK_ZERO_TTL_S:
            logger.debug("Using persisted clock zero for %s: %d (saved %ds ago)", name, clock_zero, int(now - saved_at))
            return clock_zero
        logger.debug("Persisted clock zero for %s expired (%ds old), re-calibrating", name, int(now - saved_at))
        stale = clock_zero

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
            logger.info("Couldn't locate %s to calibrate its clock: %s", name, e)
            return stale
        logger.info("Reports for %s didn't decrypt, calibrating from the raw response: %s", name, e)
    if "raw_hex" not in capture:
        return stale
    estimate = await run_blocking(_estimate_from_raw_response, capture["raw_hex"], name)
    if estimate is None:
        return stale
    # Only a fresh estimate restarts the TTL, or it would never expire.
    mutate_device(canonic_id, lambda entry: entry.setdefault("clock_zero", {}).update(
        {"clock_zero": estimate, "saved_at": int(time.time())}
    ))
    return estimate


async def upload_once(dry_run: bool) -> int:
    if not is_logged_in():
        logger.info("Skipping EID upload, no account signed in yet.")
        return 0
    device_list = parse_device_list_protobuf(await run_blocking(request_device_list))

    trackers = []
    for device in device_list.deviceMetadata:
        if device.identifierInformation.type == IDENTIFIER_ANDROID:
            continue
        # Some non-Android devices may not have a canonicId (e.g. certain
        # accessories or legacy devices). Skip those to avoid IndexError.
        canonic_ids = device.identifierInformation.canonicIds.canonicId
        if not canonic_ids:
            logger.debug("Skipping device %s: no canonicId", device.userDefinedDeviceName)
            continue
        trackers.append((canonic_ids[0].id, device.userDefinedDeviceName))

    # Calibrate clock zeros (use persisted if fresh, otherwise re-calibrate)
    clock_zeros = {}
    for canonic_id, name in trackers:
        zero = await _clock_zero(canonic_id, name)
        if zero is not None:
            clock_zeros[canonic_id] = zero

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
