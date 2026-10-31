"""Periodic EID upload for regular FMDN trackers, standing in for the
owner's Android phone (see SpotApi/UploadPrecomputedPublicKeyIds/
upload_precomputed_public_key_ids.py's refresh_owned_trackers for what's
uploaded and why). Off by default, set EID_UPLOAD=dry-run to only log what
would be sent, or EID_UPLOAD=on to actually upload. Started from
webui/main.py's lifespan alongside the staleness sweep.

Fetches the device list itself rather than going through
webui/device_list_cache.py: this runs a couple of times a day at most, and
a day-old cached list could miss a newly paired tracker. Each tracker is
located first so its clock can be calibrated from the reports Google still
holds (see estimate_clock_zero), since pairDate alone was hours off for
some trackers.
"""

import asyncio
import logging

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


async def _clock_zero(canonic_id: str, name: str) -> int | None:
    try:
        _, capture = await locate_device_with_capture(canonic_id, name, timeout=config.LOCATE_TIMEOUT_S)
    except Exception as e:
        logger.info("Couldn't locate %s to calibrate its clock, using pairDate: %s", name, e)
        return None
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
    zeros = await asyncio.gather(*(_clock_zero(canonic_id, name) for canonic_id, name in trackers))
    clock_zeros = {canonic_id: zero for (canonic_id, _), zero in zip(trackers, zeros) if zero is not None}

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
