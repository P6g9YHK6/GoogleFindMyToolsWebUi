#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import (
    _status_name,
    decrypt_location_response_locations,
)
from ProtoDecoders import Common_pb2, DeviceUpdate_pb2


def test_status_name_resolves_known_statuses():
    assert _status_name(Common_pb2.Status.LAST_KNOWN) == "LAST_KNOWN"
    assert _status_name(Common_pb2.Status.SEMANTIC) == "SEMANTIC"


def test_status_name_survives_an_unrecognized_status():
    # Google occasionally rolls out new Status values before this enum is
    # updated to match (same situation as SpotDeviceType, see
    # tests/test_decoder.py) - one unrecognized status shouldn't take down
    # location decoding for the rest of the device's reports.
    assert _status_name(99) == "STATUS_UNKNOWN_99"


def _crowdsourced_update(offsets):
    device_update = DeviceUpdate_pb2.DeviceUpdate()
    registration = device_update.deviceMetadata.information.deviceRegistration
    registration.encryptedUserSecrets.encryptedIdentityKey = b"k" * 48
    reports = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    for i, offset in enumerate(offsets):
        location = reports.networkLocations.add()
        location.status = Common_pb2.Status.CROWDSOURCED
        location.geoLocation.deviceTimeOffset = offset
        location.geoLocation.encryptedReport.publicKeyRandom = b"\x01" * 20
        location.geoLocation.encryptedReport.encryptedLocation = b"ciphertext"
        reports.networkLocationTimestamps.add().seconds = 1_700_000_000 + i * 1024
    return device_update


def _patch_decrypt(monkeypatch, decrypt_fn):
    import NovaApi.ExecuteAction.LocateTracker.decrypt_locations as decrypt_locations

    monkeypatch.setattr(decrypt_locations, "retrieve_identity_key", lambda registration: b"e" * 32)
    monkeypatch.setattr(decrypt_locations, "decrypt", decrypt_fn)


def test_one_undecryptable_report_does_not_hide_the_others(monkeypatch):
    # A report matched to an older, wrongly-based EID upload used to abort
    # the whole locate; it should just be skipped while the rest come back.
    def _decrypt(identity_key, encrypted, public_key_random, counter):
        if counter == 111:
            raise ValueError("MAC check failed")
        return DeviceUpdate_pb2.Location(latitude=1234, longitude=5678).SerializeToString()

    _patch_decrypt(monkeypatch, _decrypt)

    results = decrypt_location_response_locations(_crowdsourced_update([111, 222]))

    assert len(results) == 1
    assert results[0]["latitude"] == 1234 / 1e7
    assert results[0]["longitude"] == 5678 / 1e7


def test_all_undecryptable_reports_still_raise(monkeypatch):
    # Nothing could be decoded: the caller should still see the failure (so
    # the scheduler keeps alerting), not a silent empty result.
    def _decrypt(identity_key, encrypted, public_key_random, counter):
        raise ValueError("MAC check failed")

    _patch_decrypt(monkeypatch, _decrypt)

    try:
        decrypt_location_response_locations(_crowdsourced_update([111, 222]))
    except ValueError as e:
        assert str(e) == "MAC check failed"
    else:
        raise AssertionError("expected the MAC failure to be raised")
