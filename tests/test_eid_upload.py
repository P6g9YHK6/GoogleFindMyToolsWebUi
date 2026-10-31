import asyncio

from FMDNCrypto.eid_generator import ROTATION_PERIOD, generate_eid
from ProtoDecoders import DeviceUpdate_pb2
from SpotApi.CreateBleDevice.config import mcu_fast_pair_model_id
from SpotApi.UploadPrecomputedPublicKeyIds import upload_precomputed_public_key_ids as upload
from webui import config, eid_upload

EIK = bytes(range(32))
PAIR_DATE = 1_700_000_000


def _device(device_list, name, canonic_id, *, phone=False, mcu=False, with_key=True):
    device = device_list.deviceMetadata.add()
    device.userDefinedDeviceName = name
    device.identifierInformation.type = (
        DeviceUpdate_pb2.IDENTIFIER_ANDROID if phone else DeviceUpdate_pb2.IDENTIFIER_SPOT
    )
    device.identifierInformation.canonicIds.canonicId.add().id = canonic_id
    registration = device.information.deviceRegistration
    registration.pairDate = PAIR_DATE
    if mcu:
        registration.fastPairModelId = mcu_fast_pair_model_id
    if with_key:
        registration.encryptedUserSecrets.encryptedIdentityKey = b"\x01" * 48
    return device


# --- get_rotating_eids --------------------------------------------------

def test_rotating_eids_follow_tracker_clock():
    start = PAIR_DATE + 10 * ROTATION_PERIOD + 100
    eids = upload.get_rotating_eids(EIK, PAIR_DATE, start, duration_seconds=3 * ROTATION_PERIOD)

    assert [e.timestamp.seconds for e in eids] == [PAIR_DATE + i * ROTATION_PERIOD for i in range(10, 14)]
    for i, e in zip(range(10, 14), eids):
        assert e.publicKeyId.truncatedEid == generate_eid(EIK, i * ROTATION_PERIOD)[:10]


def test_rotating_eids_drift_slots_share_timestamp():
    start = PAIR_DATE + 10 * ROTATION_PERIOD
    eids = upload.get_rotating_eids(EIK, PAIR_DATE, start, duration_seconds=0, drift_slots=2)

    assert len(eids) == 5
    assert {e.timestamp.seconds for e in eids} == {PAIR_DATE + 10 * ROTATION_PERIOD}
    assert [e.publicKeyId.truncatedEid for e in eids] == [
        generate_eid(EIK, i * ROTATION_PERIOD)[:10] for i in range(8, 13)
    ]


def test_rotating_eids_never_go_before_pairing():
    eids = upload.get_rotating_eids(EIK, PAIR_DATE, PAIR_DATE, duration_seconds=0, drift_slots=2)

    assert [e.publicKeyId.truncatedEid for e in eids] == [
        generate_eid(EIK, i * ROTATION_PERIOD)[:10] for i in range(0, 3)
    ]


# --- refresh_owned_trackers ---------------------------------------------

def _patch(monkeypatch, sent):
    monkeypatch.setattr(upload, "retrieve_identity_key", lambda registration: EIK)
    monkeypatch.setattr(upload, "spot_request", lambda scope, data: sent.append((scope, data)))
    monkeypatch.setattr(upload, "max_truncated_eid_seconds_server", ROTATION_PERIOD)


def test_refresh_owned_trackers_skips_phones_and_mcus(monkeypatch):
    sent = []
    _patch(monkeypatch, sent)
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")
    _device(device_list, "Phone", "phone-1", phone=True)
    _device(device_list, "ESP32", "mcu-1", mcu=True)
    _device(device_list, "Keyless", "tracker-2", with_key=False)

    assert upload.refresh_owned_trackers(device_list, drift_slots=0) == 1

    assert len(sent) == 1
    scope, data = sent[0]
    assert scope == "UploadPrecomputedPublicKeyIds"
    request = DeviceUpdate_pb2.UploadPrecomputedPublicKeyIdsRequest()
    request.ParseFromString(data)
    assert [d.canonicId.id for d in request.deviceEids] == ["tracker-1"]
    assert request.deviceEids[0].pairDate == PAIR_DATE
    assert request.deviceEids[0].clientList.publicKeyIdInfo


def test_refresh_owned_trackers_dry_run_sends_nothing(monkeypatch):
    sent = []
    _patch(monkeypatch, sent)
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")

    assert upload.refresh_owned_trackers(device_list, dry_run=True) == 1
    assert sent == []


def test_refresh_owned_trackers_skips_undecryptable_key(monkeypatch):
    sent = []
    _patch(monkeypatch, sent)

    def _fail(registration):
        raise ValueError("bad key")

    monkeypatch.setattr(upload, "retrieve_identity_key", _fail)
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")

    assert upload.refresh_owned_trackers(device_list) == 0
    assert sent == []


# --- webui loop ---------------------------------------------------------

def test_upload_loop_off_returns_immediately(monkeypatch):
    monkeypatch.setattr(config, "EID_UPLOAD", "off")
    monkeypatch.setattr(eid_upload, "upload_once", lambda dry_run: (_ for _ in ()).throw(AssertionError))
    asyncio.run(asyncio.wait_for(eid_upload.upload_loop(), timeout=1))


def test_upload_loop_unknown_mode_stays_off(monkeypatch, caplog):
    monkeypatch.setattr(config, "EID_UPLOAD", "yes")
    asyncio.run(asyncio.wait_for(eid_upload.upload_loop(), timeout=1))
    assert "Unknown EID_UPLOAD value" in caplog.text


def test_upload_once_skips_when_logged_out(monkeypatch):
    monkeypatch.setattr(eid_upload, "is_logged_in", lambda: False)
    monkeypatch.setattr(eid_upload, "request_device_list", lambda: (_ for _ in ()).throw(AssertionError))
    assert asyncio.run(eid_upload.upload_once(dry_run=True)) == 0


# --- clock calibration --------------------------------------------------

def _device_update(reports):
    device_update = DeviceUpdate_pb2.DeviceUpdate()
    wrapper = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    for seen_at, device_time_offset in reports:
        wrapper.networkLocations.add().geoLocation.deviceTimeOffset = device_time_offset
        wrapper.networkLocationTimestamps.add().seconds = seen_at
    return device_update


def test_estimate_clock_zero_uses_tightest_report():
    zero = PAIR_DATE + 12_000
    device_update = _device_update([
        (zero + 50 * ROTATION_PERIOD + 300, 50 * ROTATION_PERIOD),
        (zero + 60 * ROTATION_PERIOD + 900, 60 * ROTATION_PERIOD),
    ])

    assert upload.estimate_clock_zero(device_update) == zero + 300 - ROTATION_PERIOD // 2


def test_estimate_clock_zero_includes_recent_location():
    zero = PAIR_DATE + 12_000
    device_update = _device_update([(zero + 60 * ROTATION_PERIOD + 900, 60 * ROTATION_PERIOD)])
    wrapper = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    wrapper.recentLocation.geoLocation.deviceTimeOffset = 70 * ROTATION_PERIOD
    wrapper.recentLocationTimestamp.seconds = zero + 70 * ROTATION_PERIOD + 100

    assert upload.estimate_clock_zero(device_update) == zero + 100 - ROTATION_PERIOD // 2


def test_estimate_clock_zero_without_reports():
    assert upload.estimate_clock_zero(_device_update([])) is None
    assert upload.estimate_clock_zero(_device_update([(PAIR_DATE, 0)])) is None


def test_refresh_owned_trackers_uses_calibrated_clock(monkeypatch):
    sent = []
    _patch(monkeypatch, sent)
    monkeypatch.setattr(upload.time, "time", lambda: PAIR_DATE + 100 * ROTATION_PERIOD)
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")
    _device(device_list, "Bag", "tracker-2")
    zero = PAIR_DATE + 5 * ROTATION_PERIOD

    upload.refresh_owned_trackers(device_list, drift_slots=0, clock_zeros={"tracker-1": zero})

    request = DeviceUpdate_pb2.UploadPrecomputedPublicKeyIdsRequest()
    request.ParseFromString(sent[0][1])
    calibrated, fallback = request.deviceEids
    assert calibrated.pairDate == PAIR_DATE
    first = calibrated.clientList.publicKeyIdInfo[0]
    assert first.publicKeyId.truncatedEid == generate_eid(EIK, first.timestamp.seconds - zero)[:10]
    first = fallback.clientList.publicKeyIdInfo[0]
    assert first.publicKeyId.truncatedEid == generate_eid(EIK, first.timestamp.seconds - PAIR_DATE)[:10]


def test_upload_once_calibrates_each_tracker(monkeypatch):
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")
    _device(device_list, "Bag", "tracker-2")
    _device(device_list, "Phone", "phone-1", phone=True)
    raw = {"tracker-1": _device_update([(PAIR_DATE + 2000, ROTATION_PERIOD)]).SerializeToString().hex()}
    located, received = [], {}

    async def _locate(canonic_id, name, timeout):
        located.append(canonic_id)
        return [], ({"raw_hex": raw[canonic_id]} if canonic_id in raw else {"timed_out": True})

    def _refresh(device_list, drift_slots, dry_run, clock_zeros):
        received.update(clock_zeros)
        return 2

    monkeypatch.setattr(eid_upload, "is_logged_in", lambda: True)
    monkeypatch.setattr(eid_upload, "request_device_list", lambda: device_list.SerializeToString().hex())
    monkeypatch.setattr(eid_upload, "locate_device_with_capture", _locate)
    monkeypatch.setattr(eid_upload, "refresh_owned_trackers", _refresh)

    assert asyncio.run(eid_upload.upload_once(dry_run=True)) == 2
    assert sorted(located) == ["tracker-1", "tracker-2"]
    assert received == {"tracker-1": PAIR_DATE + 2000 - ROTATION_PERIOD - ROTATION_PERIOD // 2}
