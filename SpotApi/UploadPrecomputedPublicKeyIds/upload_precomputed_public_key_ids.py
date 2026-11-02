#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#
import logging
import time

from FMDNCrypto.eid_generator import ROTATION_PERIOD, generate_eid
from FMDNCrypto.foreign_tracker_cryptor import decrypt_with_counter_search
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
    side of that estimate are uploaded too, each under its own timestamp,
    to absorb the remaining error.

    The server validates that the uploaded pairDate matches the device's
    registered pairing date. To make decryption work, we must set timestamps
    so that timestamp - pairDate equals the tracker's clock (beacon counter).
    Since tracker_clock = real_time - clock_zero, we set:
        timestamp = pairDate + round((real_time - clock_zero) / ROTATION_PERIOD) * ROTATION_PERIOD

    Phones and µC trackers are
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
        raw_clock_zero = (clock_zeros or {}).get(canonic_id, registration.pairDate)
        pair_date = registration.pairDate
        now = int(time.time())

        new_truncated_ids = UploadPrecomputedPublicKeyIdsRequest.DevicePublicKeyIds()
        new_truncated_ids.pairDate = pair_date
        new_truncated_ids.canonicId.id = canonic_id

        next_eids = get_rotating_eids(identity_key, raw_clock_zero, pair_date, start_date,
                                      duration_seconds=max_truncated_eid_seconds_server, drift_slots=drift_slots, now=now)
        new_truncated_ids.clientList.publicKeyIdInfo.extend(next_eids)
        request.deviceEids.append(new_truncated_ids)

        logger.info("%s EIDs for %s (clock zero pairDate%+ds, %d entries, first %s)",
                    "Would upload" if dry_run else "Uploading", name, raw_clock_zero - pair_date,
                    len(next_eids), next_eids[0].publicKeyId.truncatedEid.hex() if next_eids else "-")

    if request.deviceEids and not dry_run:
        spot_request("UploadPrecomputedPublicKeyIds", request.SerializeToString())

    return len(request.deviceEids)


def estimate_clock_zero(device_update: DeviceUpdate, identity_key: bytes | None = None) -> int | None:
    """Unix time a tracker's clock read zero, from the location reports in a
    LocateTracker response, or None without any. Each report carries the
    tracker clock it was encrypted under (deviceTimeOffset, rounded down to
    a rotation period) next to its own timestamp, which is never earlier
    than the sighting, so timestamp - deviceTimeOffset is an upper bound on
    clock zero that's at most one rotation period plus the reporting delay
    too late. Takes the tightest bound and backs off half a period.

    deviceTimeOffset can itself be wrong (see decrypt_with_counter_search), so
    given identity_key, each report's counter is the one it really decrypts
    under, and reports that decrypt under none are left out."""
    reports = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    pairs = list(zip(reports.networkLocations, reports.networkLocationTimestamps))
    if reports.HasField("recentLocation"):
        pairs.append((reports.recentLocation, reports.recentLocationTimestamp))
    bounds = []
    for location, timestamp in pairs:
        counter = location.geoLocation.deviceTimeOffset
        if not counter:
            continue
        report = location.geoLocation.encryptedReport
        if identity_key and report.publicKeyRandom:
            try:
                _, counter = decrypt_with_counter_search(
                    identity_key, report.encryptedLocation, report.publicKeyRandom, counter,
                )
            except ValueError:
                continue
        bounds.append(timestamp.seconds - counter)
    return min(bounds) - ROTATION_PERIOD // 2 if bounds else None


def get_rotating_eids(eik: bytes, clock_zero: int, pair_date: int, start_date: int, duration_seconds: int,
                      drift_slots: int = 0, now: int | None = None) -> list[PublicKeyIdList.PublicKeyIdInfo]:
    """Like get_next_eids, but for a tracker whose EID actually rotates:
    each entry's EID is derived from the tracker's own clock (seconds since
    clock_zero, see FMDNCrypto/eid_generator.py) rather than a fixed static
    one. The server validates pairDate against the registered pairing date,
    so we upload with pairDate = pair_date but set timestamps so that
    timestamp - pairDate equals the tracker's clock (beacon counter).
    The tracker broadcasts at times T = clock_zero + k * ROTATION_PERIOD.
    At that time, beacon_counter = k * ROTATION_PERIOD, and we set
    timestamp = pair_date + beacon_counter so the server derives the
    correct counter for decryption.
    With drift_slots > 0, neighbouring rotation periods (k + slot) are
    uploaded too. Those slots absorb an estimate that is off by whole
    rotation periods.
    If `now` is provided, entries for real times in the future are omitted
    (the tracker hasn't broadcast those EIDs yet)."""
    duration_seconds = int(duration_seconds)
    public_key_id_list = []
    eid_cache: dict[int, bytes] = {}
    seen_offsets: set[int] = set()

    max_real_time = now if now is not None else (1 << 63) - 1
    end_real_time = min(start_date + duration_seconds, max_real_time)

    # Beacon counter k where real_time = clock_zero + k * ROTATION_PERIOD
    # Range of k for real times in [start_date, end_real_time]
    min_k = max(0, (start_date - clock_zero + ROTATION_PERIOD - 1) // ROTATION_PERIOD)
    max_k = (end_real_time - clock_zero) // ROTATION_PERIOD

    for k in range(min_k, max_k + 1):
        base_beacon = k * ROTATION_PERIOD

        for slot in range(-drift_slots, drift_slots + 1):
            beacon_offset = base_beacon + slot * ROTATION_PERIOD
            if beacon_offset < 0:
                continue
            if beacon_offset in seen_offsets:
                continue
            # Timestamp must satisfy: timestamp - pair_date = beacon_offset
            timestamp = pair_date + beacon_offset
            seen_offsets.add(beacon_offset)
            if beacon_offset not in eid_cache:
                eid_cache[beacon_offset] = generate_eid(eik, beacon_offset)[:10]

            info = PublicKeyIdList.PublicKeyIdInfo()
            info.timestamp.seconds = timestamp
            info.publicKeyId.truncatedEid = eid_cache[beacon_offset]
            public_key_id_list.append(info)

    return public_key_id_list
