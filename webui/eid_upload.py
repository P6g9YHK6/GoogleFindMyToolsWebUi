"""Periodic EID upload for regular FMDN trackers, standing in for the
owner's Android phone (see SpotApi/UploadPrecomputedPublicKeyIds/
upload_precomputed_public_key_ids.py's refresh_owned_trackers for what's
uploaded and why). Off by default, set EID_UPLOAD=dry-run to only log what
would be sent, or EID_UPLOAD=on to actually upload. Started from
webui/main.py's lifespan alongside the staleness sweep.

Fetches the device list itself rather than going through
webui/device_list_cache.py: this runs a couple of times a day at most, and
a day-old cached list could miss a newly paired tracker.
"""

import asyncio
import logging

from NovaApi.ListDevices.nbe_list_devices import request_device_list
from ProtoDecoders.decoder import parse_device_list_protobuf
from SpotApi.UploadPrecomputedPublicKeyIds.upload_precomputed_public_key_ids import refresh_owned_trackers
from webui import config
from webui.auth_state import is_logged_in
from webui.deps import run_blocking

logger = logging.getLogger("webui.eid_upload")

MODES = ("off", "dry-run", "on")


def upload_once(dry_run: bool) -> int:
    if not is_logged_in():
        logger.info("Skipping EID upload, no account signed in yet.")
        return 0
    device_list = parse_device_list_protobuf(request_device_list())
    return refresh_owned_trackers(device_list, drift_slots=config.EID_UPLOAD_DRIFT_SLOTS, dry_run=dry_run)


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
            count = await run_blocking(upload_once, mode == "dry-run")
            logger.info("EID upload %s: %d tracker(s).", "dry run done" if mode == "dry-run" else "done", count)
        except Exception:
            logger.exception("EID upload failed")
        await asyncio.sleep(max(3600, config.EID_UPLOAD_INTERVAL_S))
