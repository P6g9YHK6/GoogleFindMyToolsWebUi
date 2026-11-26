"""End-to-end checks for the EID upload against a simulated tracker.

Each scenario builds a tracker whose clock started at a known time, has it
"broadcast" while the crowd reports sightings (encrypted under the counter
the tracker really used, timestamped the way Google timestamps them), runs
those reports through estimate_clock_zero and refresh_owned_trackers, and
checks that the upload holds the EID the tracker is broadcasting now and
until the next upload, under the timestamp Google decrypts it with.

Most past EID upload bugs only showed up as "reports stopped arriving" on a
real account. These pin down the whole chain instead of one function each.
"""

from dataclasses import dataclass

import pytest

from FMDNCrypto.eid_generator import ROTATION_PERIOD, generate_eid
from FMDNCrypto.foreign_tracker_cryptor import encrypt
from ProtoDecoders import DeviceUpdate_pb2
from SpotApi.UploadPrecomputedPublicKeyIds import upload_precomputed_public_key_ids as upload
from webui import config

EIK = bytes(range(32))
PAIR_DATE = 1_700_000_000
CANONIC_ID = "tracker-1"
HOUR = 3600


@dataclass
class Scenario:
    # How long after pairing the tracker's clock read zero (a reset or
    # battery swap restarts it).
    clock_offset: int
    # Hours before "now" each sighting happened.
    sightings_h_ago: tuple[float, ...] = (2, 6, 20)
    # How long after a sighting Google timestamps the report.
    report_delay: int = 60
    # Constant error on the counter Google reports back (deviceTimeOffset).
    counter_error: int = 0
    # How long ago pairing was.
    paired_days_ago: int = 120


SCENARIOS = {
    "clock started at pairing": Scenario(clock_offset=0),
    "clock restarted hours after pairing": Scenario(clock_offset=5 * HOUR + 137),
    "battery swapped days after pairing": Scenario(clock_offset=3 * 24 * HOUR + 611),
    "slow crowd reports": Scenario(clock_offset=2 * HOUR, report_delay=900),
    "reports later than a whole period": Scenario(clock_offset=2 * HOUR, report_delay=1500),
    "Google's counter off by +1885s": Scenario(clock_offset=4 * HOUR, counter_error=1885),
    "single old sighting": Scenario(clock_offset=7 * HOUR + 3, sightings_h_ago=(30,)),
    "sighting right on a rotation boundary": Scenario(clock_offset=0, sightings_h_ago=(ROTATION_PERIOD * 10 / HOUR,)),
}


def _counter_at(clock_zero: int, real_time: int) -> int:
    return (real_time - clock_zero) // ROTATION_PERIOD * ROTATION_PERIOD


def _reports(scenario: Scenario, clock_zero: int, now: int) -> DeviceUpdate_pb2.DeviceUpdate:
    device_update = DeviceUpdate_pb2.DeviceUpdate()
    wrapper = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    for i, hours_ago in enumerate(scenario.sightings_h_ago):
        seen_at = int(now - hours_ago * HOUR)
        counter = _counter_at(clock_zero, seen_at)
        encrypted, sx = encrypt(b"location", bytes([i + 1]) * 32, generate_eid(EIK, counter))
        location = wrapper.networkLocations.add()
        location.geoLocation.deviceTimeOffset = counter + scenario.counter_error
        location.geoLocation.encryptedReport.encryptedLocation = encrypted
        location.geoLocation.encryptedReport.publicKeyRandom = sx
        wrapper.networkLocationTimestamps.add().seconds = seen_at + scenario.report_delay
    return device_update


def _device_list() -> DeviceUpdate_pb2.DevicesList:
    device_list = DeviceUpdate_pb2.DevicesList()
    device = device_list.deviceMetadata.add()
    device.userDefinedDeviceName = "Keys"
    device.identifierInformation.type = DeviceUpdate_pb2.IDENTIFIER_SPOT
    device.identifierInformation.canonicIds.canonicId.add().id = CANONIC_ID
    registration = device.information.deviceRegistration
    registration.pairDate = PAIR_DATE
    registration.encryptedUserSecrets.encryptedIdentityKey = b"\x01" * 48
    return device_list


def _upload(monkeypatch, now: int, clock_zeros: dict[str, int], responses=(None,)):
    sent = []
    responses = list(responses)

    def _spot_request(api_scope, payload):
        request = DeviceUpdate_pb2.UploadPrecomputedPublicKeyIdsRequest()
        request.ParseFromString(payload)
        sent.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response

    monkeypatch.setattr(upload, "spot_request", _spot_request)
    monkeypatch.setattr(upload, "retrieve_identity_key", lambda registration: EIK)
    monkeypatch.setattr(upload.time, "time", lambda: now)
    upload.refresh_owned_trackers(_device_list(), drift_slots=config.EID_UPLOAD_DRIFT_SLOTS, clock_zeros=clock_zeros)
    return sent


def _uploaded_counters(request) -> dict[bytes, int]:
    """Truncated EID -> the counter Google derives for it (timestamp - pairDate)."""
    device = request.deviceEids[0]
    return {
        entry.publicKeyId.truncatedEid: entry.timestamp.seconds - device.pairDate
        for entry in device.clientList.publicKeyIdInfo
    }


def _assert_covers(request, clock_zero: int, real_times):
    uploaded = _uploaded_counters(request)
    for real_time in real_times:
        counter = _counter_at(clock_zero, real_time)
        eid = generate_eid(EIK, counter)[:10]
        assert eid in uploaded, f"EID broadcast at {real_time} (counter {counter}) wasn't uploaded"
        assert uploaded[eid] == counter, "uploaded under a timestamp that decrypts with the wrong counter"


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_upload_covers_what_the_tracker_broadcasts(monkeypatch, scenario):
    now = PAIR_DATE + scenario.paired_days_ago * 24 * HOUR
    clock_zero = PAIR_DATE + scenario.clock_offset

    estimate = upload.estimate_clock_zero(_reports(scenario, clock_zero, now), EIK)
    assert estimate is not None
    sent = _upload(monkeypatch, now, {CANONIC_ID: estimate})

    assert len(sent) == 1
    _assert_covers(sent[0], clock_zero, [now, now + config.EID_UPLOAD_INTERVAL_S])


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_estimate_lands_within_the_drift_slots(scenario):
    now = PAIR_DATE + scenario.paired_days_ago * 24 * HOUR
    clock_zero = PAIR_DATE + scenario.clock_offset

    estimate = upload.estimate_clock_zero(_reports(scenario, clock_zero, now), EIK)

    assert abs(estimate - clock_zero) <= config.EID_UPLOAD_DRIFT_SLOTS * ROTATION_PERIOD


def test_past_only_retry_still_covers_now(monkeypatch):
    scenario = SCENARIOS["clock restarted hours after pairing"]
    now = PAIR_DATE + scenario.paired_days_ago * 24 * HOUR
    clock_zero = PAIR_DATE + scenario.clock_offset
    estimate = upload.estimate_clock_zero(_reports(scenario, clock_zero, now), EIK)

    sent = _upload(monkeypatch, now, {CANONIC_ID: estimate},
                   responses=(RuntimeError("failed with 400: INVALID_ARGUMENT"), None))

    assert len(sent) == 2
    _assert_covers(sent[1], clock_zero, [now - HOUR, now])


def test_uncalibrated_tracker_is_covered_only_if_its_clock_started_at_pairing(monkeypatch):
    now = PAIR_DATE + 120 * 24 * HOUR

    sent = _upload(monkeypatch, now, {})
    _assert_covers(sent[0], PAIR_DATE, [now])

    # A restarted clock is exactly why calibration exists: the pairDate
    # fallback misses it.
    restarted = PAIR_DATE + 5 * HOUR
    uploaded = _uploaded_counters(sent[0])
    assert generate_eid(EIK, _counter_at(restarted, now))[:10] not in uploaded


def test_reports_from_another_tracker_dont_skew_the_estimate():
    # A report that doesn't decrypt under this tracker's key (wrong key,
    # corrupted payload) must be left out rather than pull the estimate.
    scenario = SCENARIOS["clock restarted hours after pairing"]
    now = PAIR_DATE + scenario.paired_days_ago * 24 * HOUR
    clock_zero = PAIR_DATE + scenario.clock_offset
    device_update = _reports(scenario, clock_zero, now)
    wrapper = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    foreign = wrapper.networkLocations.add()
    foreign_counter = _counter_at(clock_zero, now - HOUR)
    encrypted, sx = encrypt(b"location", b"\x09" * 32, generate_eid(bytes(32), foreign_counter))
    foreign.geoLocation.deviceTimeOffset = foreign_counter
    foreign.geoLocation.encryptedReport.encryptedLocation = encrypted
    foreign.geoLocation.encryptedReport.publicKeyRandom = sx
    # Timestamped as if it pinned clock zero far too early.
    wrapper.networkLocationTimestamps.add().seconds = now - HOUR - 20 * ROTATION_PERIOD

    assert upload.estimate_clock_zero(device_update, EIK) == upload.estimate_clock_zero(
        _reports(scenario, clock_zero, now), EIK,
    )
