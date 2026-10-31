#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#
import logging
import time

from FMDNCrypto.eid_generator import ROTATION_PERIOD, generate_eid
from ProtoDecoders.DeviceUpdate_pb2 import (
    IDENTIFIER_ANDROID,
    DevicesList,
    DeviceUpdate,
    PublicKeyIdList,
    UploadPrecomputedPublicKeyIdsRequest,
)
from SpotApi.CreateBleDevice.config import max_truncated_eid_seconds_server
from SpotApi.CreateBleDevice.util import hours_to_seconds
from SpotApi.identity_key import is_mcu_tracker, retrieve_identity_key
from SpotApi.spot_request import spot_request

logger = logging.getLogger(__name__)


def refresh_custom_trackers(device_list: DevicesList):

    request = UploadPrecomputedPublicKeyIdsRequest()
    needs_upload = False

    for device in device_list.deviceMetadata:

        # This is a microcontroller
        if is_mcu_tracker(device.information.deviceRegistration):

            needs_upload = True

            new_truncated_ids = UploadPrecomputedPublicKeyIdsRequest.DevicePublicKeyIds()
            new_truncated_ids.pairDate = device.information.deviceRegistration.pairDate
            new_truncated_ids.canonicId.id = device.identifierInformation.canonicIds.canonicId[0].id

            identity_key = retrieve_identity_key(device.information.deviceRegistration)
            next_eids = get_next_eids(identity_key, new_truncated_ids.pairDate, int(time.time() - hours_to_seconds(3)), duration_seconds=max_truncated_eid_seconds_server)

            for next_eid in next_eids:
                new_truncated_ids.clientList.publicKeyIdInfo.append(next_eid)

            request.deviceEids.append(new_truncated_ids)

    if needs_upload:
        logger.info("Updating your registered µC devices...")
        try:
            bytes_data = request.SerializeToString()
            spot_request("UploadPrecomputedPublicKeyIds", bytes_data)
        except Exception as e:
            logger.error("Failed to refresh custom trackers. Please file a bug report. Continuing... %s", e)


def get_next_eids(eik: bytes, pair_date: int, start_date: int, duration_seconds: int) -> list[PublicKeyIdList.PublicKeyIdInfo]:
    duration_seconds = int(duration_seconds)
    public_key_id_list = []

    start_offset = start_date - pair_date
    current_time_offset = start_offset - (start_offset % ROTATION_PERIOD)

    static_eid = generate_eid(eik, 0)

    while current_time_offset <= start_offset + duration_seconds:
        time = pair_date + current_time_offset

        info = PublicKeyIdList.PublicKeyIdInfo()
        info.timestamp.seconds = time
        info.publicKeyId.truncatedEid = static_eid[:10]

        public_key_id_list.append(info)

        current_time_offset += ROTATION_PERIOD

    return public_key_id_list


def refresh_owned_trackers(device_list: DevicesList, drift_slots: int = 2, dry_run: bool = False,
                           clock_zeros: dict[str, int] | None = None) -> int:
    """Does for regular FMDN trackers (Chipolo, Pebblebee, ...) what the
    owner's Android phone normally does every few days: precompute their
    upcoming rotating EIDs and upload them, so Google can match crowdsourced
    sightings to them. Without that upload (e.g. the only Android phone on
    the account is switched off) the server-side list runs out and every
    tracker silently stops getting new reports.

    Experimental: the tracker's own clock can't be read without a BLE
    connection. clock_zeros maps canonic id to the unix time that clock
    read zero, as estimated by estimate_clock_zero below; trackers missing
    from it fall back to pairDate, which is the same thing as of pairing
    but can be hours off by now. drift_slots extra rotation periods either
    side of that estimate are uploaded under each timestamp to absorb the
    remaining error. Phones and µC trackers are
    skipped (phones upload their own, µC trackers are handled by
    refresh_custom_trackers above). Returns how many trackers were (or in
    dry_run, would have been) uploaded."""

    request = UploadPrecomputedPublicKeyIdsRequest()
    start_date = int(time.time() - hours_to_seconds(3))

    for device in device_list.deviceMetadata:
        if device.identifierInformation.type == IDENTIFIER_ANDROID:
            continue

        registration = device.information.deviceRegistration
        if is_mcu_tracker(registration) or not registration.encryptedUserSecrets.encryptedIdentityKey:
            continue

        name = device.userDefinedDeviceName
        try:
            identity_key = retrieve_identity_key(registration)
        except Exception as e:
            logger.warning("Skipping EID upload for %s, couldn't get its identity key: %s", name, e)
            continue

        canonic_id = device.identifierInformation.canonicIds.canonicId[0].id
        clock_zero = (clock_zeros or {}).get(canonic_id, registration.pairDate)

        new_truncated_ids = UploadPrecomputedPublicKeyIdsRequest.DevicePublicKeyIds()
        new_truncated_ids.pairDate = registration.pairDate
        new_truncated_ids.canonicId.id = canonic_id

        next_eids = get_rotating_eids(identity_key, clock_zero, start_date,
                                      duration_seconds=max_truncated_eid_seconds_server, drift_slots=drift_slots)
        new_truncated_ids.clientList.publicKeyIdInfo.extend(next_eids)
        request.deviceEids.append(new_truncated_ids)

        logger.info("%s EIDs for %s (clock zero pairDate%+ds, %d entries, first %s)",
                    "Would upload" if dry_run else "Uploading", name, clock_zero - registration.pairDate,
                    len(next_eids), next_eids[0].publicKeyId.truncatedEid.hex() if next_eids else "-")

    if request.deviceEids and not dry_run:
        spot_request("UploadPrecomputedPublicKeyIds", request.SerializeToString())

    return len(request.deviceEids)


def estimate_clock_zero(device_update: DeviceUpdate) -> int | None:
    """Unix time a tracker's clock read zero, from the location reports in a
    LocateTracker response, or None without any. Each report carries the
    tracker clock it was encrypted under (deviceTimeOffset, rounded down to
    a rotation period) next to its own timestamp, which is never earlier
    than the sighting, so timestamp - deviceTimeOffset is an upper bound on
    clock zero that's at most one rotation period plus the reporting delay
    too late. Takes the tightest bound and backs off half a period."""
    reports = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    pairs = list(zip(reports.networkLocations, reports.networkLocationTimestamps))
    if reports.HasField("recentLocation"):
        pairs.append((reports.recentLocation, reports.recentLocationTimestamp))
    bounds = [
        timestamp.seconds - location.geoLocation.deviceTimeOffset
        for location, timestamp in pairs
        if location.geoLocation.deviceTimeOffset
    ]
    return min(bounds) - ROTATION_PERIOD // 2 if bounds else None


def get_rotating_eids(eik: bytes, clock_zero: int, start_date: int, duration_seconds: int,
                      drift_slots: int = 0) -> list[PublicKeyIdList.PublicKeyIdInfo]:
    """Like get_next_eids, but for a tracker whose EID actually rotates:
    each entry's EID is derived from the tracker's own clock (seconds since
    clock_zero, see FMDNCrypto/eid_generator.py) rather than a fixed static
    one.
    With drift_slots > 0, the EIDs of the neighbouring rotation periods are
    listed under the same timestamp as well."""
    duration_seconds = int(duration_seconds)
    public_key_id_list = []
    eid_cache: dict[int, bytes] = {}

    start_offset = max(0, start_date - clock_zero)
    current_time_offset = start_offset - (start_offset % ROTATION_PERIOD)

    while current_time_offset <= start_offset + duration_seconds:
        for slot in range(-drift_slots, drift_slots + 1):
            beacon_offset = current_time_offset + slot * ROTATION_PERIOD
            if beacon_offset < 0:
                continue
            if beacon_offset not in eid_cache:
                eid_cache[beacon_offset] = generate_eid(eik, beacon_offset)[:10]

            info = PublicKeyIdList.PublicKeyIdInfo()
            info.timestamp.seconds = clock_zero + current_time_offset
            info.publicKeyId.truncatedEid = eid_cache[beacon_offset]
            public_key_id_list.append(info)

        current_time_offset += ROTATION_PERIOD

    return public_key_id_list
