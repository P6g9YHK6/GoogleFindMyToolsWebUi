#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import datetime
import hashlib
import logging
import threading
import time

from cryptography.exceptions import InvalidTag

from FMDNCrypto.foreign_tracker_cryptor import decrypt_with_counter_search
from KeyBackup.cloud_key_decryptor import decrypt_aes_gcm
from NovaApi.ExecuteAction.LocateTracker.decrypted_location import WrappedLocation
from ProtoDecoders import Common_pb2, DeviceUpdate_pb2
from ProtoDecoders.decoder import parse_device_update_protobuf
from SpotApi.identity_key import is_mcu_tracker, retrieve_identity_key

logger = logging.getLogger(__name__)

# Track MAC failures for hourly summary log (reduces log spam)
_mac_failures_lock = threading.Lock()
_mac_failure_count = 0
_mac_failure_window_start = time.time()

# Corrections already logged, so a tracker with a constant error logs it once
# rather than on every poll.
_logged_counter_corrections: set[int] = set()


def create_map_links(latitude, longitude):
    """One link per major map provider for a single location, OpenStreetMap
    first (the default/primary one) - returned as a dict rather than a
    single URL so callers can offer a choice instead of being locked into
    whichever provider used to be hardcoded here. None of these need an API
    key; each is just a deep-link URL built straight from the coordinates.
    Returns {} for invalid coordinates instead of a link, rather than the
    old behavior of quietly handing back an error string as if it were one."""
    try:
        latitude = float(latitude)
        longitude = float(longitude)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Invalid latitude or longitude values.")
    except ValueError:
        return {}

    return {
        "OSM": f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=17/{latitude}/{longitude}",
        "Google": f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}",
        "Apple": f"https://maps.apple.com/?ll={latitude},{longitude}&q=Location",
        "Bing": f"https://www.bing.com/maps?cp={latitude}~{longitude}&lvl=16",
        "Waze": f"https://waze.com/ul?ll={latitude},{longitude}&navigate=yes",
    }

def _status_name(status_value):
    """Common_pb2.Status.Name(), tolerant of values the enum doesn't define
    yet. Same situation as SpotDeviceType in ProtoDecoders/decoder.py -
    Google can roll out a new Status value before this enum is updated to
    match, and one unrecognized status shouldn't take down location
    decoding for the rest of the device's reports."""
    try:
        return Common_pb2.Status.Name(status_value)
    except ValueError:
        return f"STATUS_UNKNOWN_{status_value}"


def decrypt_location_response_locations(device_update_protobuf):

    device_registration = device_update_protobuf.deviceMetadata.information.deviceRegistration

    identity_key = retrieve_identity_key(device_registration)
    locations_proto = device_update_protobuf.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    is_mcu = is_mcu_tracker(device_registration)

    # At All Areas Reports or Own Reports
    recent_location = locations_proto.recentLocation
    recent_location_time = locations_proto.recentLocationTimestamp

    # High Traffic Reports
    network_locations = list(locations_proto.networkLocations)
    network_locations_time = list(locations_proto.networkLocationTimestamps)

    if locations_proto.HasField("recentLocation"):
        network_locations.append(recent_location)
        network_locations_time.append(recent_location_time)

    location_time_array = []
    undecryptable = []
    for loc, ts in zip(network_locations, network_locations_time):

        if loc.status == Common_pb2.Status.SEMANTIC:
            wrapped_location = WrappedLocation(
                decrypted_location=b'',
                time=int(ts.seconds),
                accuracy=0,
                status=loc.status,
                is_own_report=True,
                name=loc.semanticLocation.locationName
            )
            location_time_array.append(wrapped_location)
        else:

            encrypted_location = loc.geoLocation.encryptedReport.encryptedLocation
            public_key_random = loc.geoLocation.encryptedReport.publicKeyRandom

            try:
                if public_key_random == b"":  # Own Report
                    identity_key_hash = hashlib.sha256(identity_key).digest()
                    decrypted_location = decrypt_aes_gcm(identity_key_hash, encrypted_location)
                else:
                    time_offset = 0 if is_mcu else loc.geoLocation.deviceTimeOffset
                    decrypted_location, counter = decrypt_with_counter_search(
                        identity_key, encrypted_location, public_key_random, time_offset,
                    )
                    if counter != time_offset:
                        _log_counter_correction(counter - time_offset)
            except (ValueError, InvalidTag) as e:
                # One undecryptable sighting (e.g. one matched to an older EID
                # upload whose clock base was wrong) shouldn't hide all the
                # others in the same response. Skipped here, but the whole
                # locate still fails if none of them decrypt (see below).
                _log_mac_failure(ts.seconds, e)
                undecryptable.append(e)
                continue

            wrapped_location = WrappedLocation(
                decrypted_location=decrypted_location,
                time=int(ts.seconds),
                accuracy=loc.geoLocation.accuracy,
                status=loc.status,
                is_own_report=loc.geoLocation.encryptedReport.isOwnReport,
                name=""
            )
            location_time_array.append(wrapped_location)

    if not location_time_array:
        if undecryptable:
            raise undecryptable[-1]
        logger.info("No locations found.")
        return []

    logger.info("Decrypted %d location report(s).", len(location_time_array))
    results = []

    for loc in location_time_array:

        is_semantic = loc.status == Common_pb2.Status.SEMANTIC
        latitude = longitude = altitude = None
        loc_time_str = datetime.datetime.fromtimestamp(loc.time).strftime("%Y-%m-%d %H:%M:%S")

        if is_semantic:
            logger.info("Semantic location %r at %s (status=%s, own_report=%s)",
                        loc.name, loc_time_str, loc.status, loc.is_own_report)
        else:
            proto_loc = DeviceUpdate_pb2.Location()
            proto_loc.ParseFromString(loc.decrypted_location)

            latitude = proto_loc.latitude / 1e7
            longitude = proto_loc.longitude / 1e7
            altitude = proto_loc.altitude

            logger.info("Location report at %s (status=%s, own_report=%s)",
                        loc_time_str, loc.status, loc.is_own_report)

        results.append({
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "time": loc.time,
            "is_semantic": is_semantic,
            "semantic_name": loc.name if is_semantic else None,
            "status": _status_name(loc.status),
            "status_id": int(loc.status),
            "accuracy": loc.accuracy,
            "is_own_report": loc.is_own_report,
            "map_links": create_map_links(latitude, longitude) if not is_semantic else None,
        })

    return results


def _log_counter_correction(correction: int):
    if correction not in _logged_counter_corrections:
        _logged_counter_corrections.add(correction)
        logger.info("Report only decrypted with its counter corrected by %+ds.", correction)


def _log_mac_failure(report_time: int, exc: Exception):
    """Track MAC failures and log an hourly summary instead of individual warnings."""
    global _mac_failure_count, _mac_failure_window_start

    now = time.time()
    with _mac_failures_lock:
        # Flush hourly summary if window expired
        if now - _mac_failure_window_start >= 3600:
            if _mac_failure_count > 0:
                logger.info("MAC failures in last hour: %d (old reports from before clock-fix)",
                            _mac_failure_count)
            _mac_failure_count = 0
            _mac_failure_window_start = now

        _mac_failure_count += 1
        # Log individual failure at DEBUG to keep WARNING clean
        logger.debug("Skipping undecryptable location report at %s: %s",
                     datetime.datetime.fromtimestamp(report_time).strftime("%Y-%m-%d %H:%M:%S"), exc)


if __name__ == '__main__':
    res = parse_device_update_protobuf("")
    decrypt_location_response_locations(res)