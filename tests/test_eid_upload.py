import asyncio
import time

from FMDNCrypto.eid_generator import ROTATION_PERIOD, generate_eid
from FMDNCrypto.foreign_tracker_cryptor import encrypt
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
    # When clock_zero == pair_date, timestamps should be pair_date + beacon_offset
    start = PAIR_DATE + 10 * ROTATION_PERIOD + 100
    eids = upload.get_rotating_eids(EIK, PAIR_DATE, PAIR_DATE, start, duration_seconds=3 * ROTATION_PERIOD)

    # The iterator aligns to rotation periods based on clock_zero
    # start = PAIR_DATE + 10*RP + 100, clock_zero = PAIR_DATE
    # min_k = ceil((10*RP + 100) / RP) = 11
    # max_k = floor((start + 3*RP) / RP) = floor((10*RP + 100 + 3*RP) / RP) = 13
    # So k = 11, 12, 13
    assert [e.timestamp.seconds for e in eids] == [PAIR_DATE + i * ROTATION_PERIOD for i in range(11, 14)]
    for i, e in zip(range(11, 14), eids):
        assert e.publicKeyId.truncatedEid == generate_eid(EIK, i * ROTATION_PERIOD)[:10]


def test_rotating_eids_drift_slots_span_neighbouring_periods():
    # With clock_zero == pair_date, drift slots span neighbouring periods
    start = PAIR_DATE + 10 * ROTATION_PERIOD
    eids = upload.get_rotating_eids(EIK, PAIR_DATE, PAIR_DATE, start, duration_seconds=0, drift_slots=2)

    assert len(eids) == 5
    assert [e.timestamp.seconds for e in eids] == [
        PAIR_DATE + i * ROTATION_PERIOD for i in range(8, 13)
    ]
    assert [e.publicKeyId.truncatedEid for e in eids] == [
        generate_eid(EIK, i * ROTATION_PERIOD)[:10] for i in range(8, 13)
    ]


def test_rotating_eids_never_go_before_pairing():
    # With clock_zero == pair_date, no timestamps before pair_date
    eids = upload.get_rotating_eids(EIK, PAIR_DATE, PAIR_DATE, PAIR_DATE, duration_seconds=0, drift_slots=2)

    assert [e.timestamp.seconds for e in eids] == [
        PAIR_DATE + i * ROTATION_PERIOD for i in range(0, 3)
    ]
    assert [e.publicKeyId.truncatedEid for e in eids] == [
        generate_eid(EIK, i * ROTATION_PERIOD)[:10] for i in range(0, 3)
    ]


def test_rotating_eids_with_clock_offset():
    # When clock_zero != pair_date, timestamps are shifted by the offset
    clock_zero = PAIR_DATE + 5 * ROTATION_PERIOD
    start = PAIR_DATE + 10 * ROTATION_PERIOD
    eids = upload.get_rotating_eids(EIK, clock_zero, PAIR_DATE, start, duration_seconds=2 * ROTATION_PERIOD)

    # At real_time = start = PAIR_DATE + 10*RP, tracker_clock = 5*RP
    # beacon_counter = 5*RP, timestamp = pair_date + 5*RP = PAIR_DATE + 5*RP
    # Next period: real_time = PAIR_DATE + 11*RP, tracker_clock = 6*RP, timestamp = PAIR_DATE + 6*RP
    assert [e.timestamp.seconds for e in eids] == [
        PAIR_DATE + i * ROTATION_PERIOD for i in range(5, 8)
    ]
    for i, e in zip(range(5, 8), eids):
        assert e.publicKeyId.truncatedEid == generate_eid(EIK, i * ROTATION_PERIOD)[:10]


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
    # pairDate is always the registered pairDate
    assert calibrated.pairDate == PAIR_DATE
    # Timestamps are adjusted so that timestamp - pairDate = tracker_clock
    first = calibrated.clientList.publicKeyIdInfo[0]
    # Verify the invariant: counter = timestamp - pairDate is a multiple of RP
    # and the EID matches generate_eid(EIK, counter)
    counter = first.timestamp.seconds - calibrated.pairDate
    assert counter >= 0
    assert counter % ROTATION_PERIOD == 0
    assert first.publicKeyId.truncatedEid == generate_eid(EIK, counter)[:10]
    
    first = fallback.clientList.publicKeyIdInfo[0]
    assert fallback.pairDate == PAIR_DATE
    counter = first.timestamp.seconds - fallback.pairDate
    assert counter >= 0
    assert counter % ROTATION_PERIOD == 0
    assert first.publicKeyId.truncatedEid == generate_eid(EIK, counter)[:10]


def test_uploaded_pair_date_matches_eid_counter(monkeypatch):
    # The server derives a reported beacon counter as timestamp - pairDate and
    # decryption rebuilds the EID from that counter, so every uploaded entry
    # must be consistent with the pairDate sent alongside it.
    sent = []
    _patch(monkeypatch, sent)
    monkeypatch.setattr(upload, "max_truncated_eid_seconds_server", 5 * ROTATION_PERIOD)
    monkeypatch.setattr(upload.time, "time", lambda: PAIR_DATE + 100 * ROTATION_PERIOD)
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")

    upload.refresh_owned_trackers(device_list, drift_slots=2,
                                  clock_zeros={"tracker-1": PAIR_DATE + 137})

    request = DeviceUpdate_pb2.UploadPrecomputedPublicKeyIdsRequest()
    request.ParseFromString(sent[0][1])
    device = request.deviceEids[0]
    assert device.clientList.publicKeyIdInfo
    for entry in device.clientList.publicKeyIdInfo:
        counter = entry.timestamp.seconds - device.pairDate
        assert counter >= 0
        assert counter % ROTATION_PERIOD == 0
        assert entry.publicKeyId.truncatedEid == generate_eid(EIK, counter)[:10]


def test_upload_once_calibrates_each_tracker(monkeypatch):
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")
    _device(device_list, "Bag", "tracker-2")
    _device(device_list, "Phone", "phone-1", phone=True)
    raw = {"tracker-1": _device_update([(PAIR_DATE + 2000, ROTATION_PERIOD)]).SerializeToString().hex()}
    located, received = [], {}

    async def _locate(canonic_id, name, timeout, capture):
        located.append(canonic_id)
        capture.update({"raw_hex": raw[canonic_id]} if canonic_id in raw else {"timed_out": True})
        return [], capture

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


def test_upload_once_calibrates_from_raw_reports_when_decryption_fails(monkeypatch):
    # A tracker whose current reports fail their MAC check used to give up
    # calibration (and keep uploading the wrong base) because decrypting the
    # response raised after the raw payload had already been captured.
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Bag", "tracker-1")
    raw = _device_update([(PAIR_DATE + 2000, ROTATION_PERIOD)]).SerializeToString().hex()
    received = {}

    async def _locate(canonic_id, name, timeout, capture):
        capture["raw_hex"] = raw
        raise ValueError("MAC check failed")

    def _refresh(device_list, drift_slots, dry_run, clock_zeros):
        received.update(clock_zeros)
        return 1

    monkeypatch.setattr(eid_upload, "is_logged_in", lambda: True)
    monkeypatch.setattr(eid_upload, "request_device_list", lambda: device_list.SerializeToString().hex())
    monkeypatch.setattr(eid_upload, "locate_device_with_capture", _locate)
    monkeypatch.setattr(eid_upload, "refresh_owned_trackers", _refresh)

    assert asyncio.run(eid_upload.upload_once(dry_run=True)) == 1
    assert received == {"tracker-1": PAIR_DATE + 2000 - ROTATION_PERIOD - ROTATION_PERIOD // 2}


def _stub_clock_store(monkeypatch, entry):
    devices = {"tracker-1": {"clock_zero": entry} if entry else {}}
    monkeypatch.setattr(eid_upload, "load", lambda: {"devices": devices})

    def _mutate(canonic_id, fn):
        fn(devices[canonic_id])

    monkeypatch.setattr(eid_upload, "mutate_device", _mutate)
    return devices


def test_clock_zero_reused_while_fresh_without_restarting_its_ttl(monkeypatch):
    saved_at = int(time.time()) - 3600
    devices = _stub_clock_store(monkeypatch, {"clock_zero": 123, "saved_at": saved_at})

    async def _locate(*args, **kwargs):
        raise AssertionError("a fresh clock zero shouldn't need a locate")

    monkeypatch.setattr(eid_upload, "locate_device_with_capture", _locate)

    assert asyncio.run(eid_upload._clock_zero("tracker-1", "Keys")) == 123
    assert devices["tracker-1"]["clock_zero"]["saved_at"] == saved_at


def test_clock_zero_recalibrated_once_expired(monkeypatch):
    expired = int(time.time()) - eid_upload.CLOCK_ZERO_TTL_S - 1
    devices = _stub_clock_store(monkeypatch, {"clock_zero": 123, "saved_at": expired})
    raw = _device_update([(PAIR_DATE + 2000, ROTATION_PERIOD)]).SerializeToString().hex()

    async def _locate(canonic_id, name, timeout, capture):
        capture["raw_hex"] = raw
        return [], capture

    monkeypatch.setattr(eid_upload, "locate_device_with_capture", _locate)

    expected = PAIR_DATE + 2000 - ROTATION_PERIOD - ROTATION_PERIOD // 2
    assert asyncio.run(eid_upload._clock_zero("tracker-1", "Keys")) == expected
    assert devices["tracker-1"]["clock_zero"]["clock_zero"] == expected
    assert devices["tracker-1"]["clock_zero"]["saved_at"] > expired


def test_clock_zero_keeps_expired_value_when_recalibration_fails(monkeypatch):
    expired = int(time.time()) - eid_upload.CLOCK_ZERO_TTL_S - 1
    devices = _stub_clock_store(monkeypatch, {"clock_zero": 123, "saved_at": expired})

    async def _locate(canonic_id, name, timeout, capture):
        capture["timed_out"] = True
        return [], capture

    monkeypatch.setattr(eid_upload, "locate_device_with_capture", _locate)

    assert asyncio.run(eid_upload._clock_zero("tracker-1", "Keys")) == 123
    assert devices["tracker-1"]["clock_zero"]["saved_at"] == expired


def test_estimate_clock_zero_uses_the_counter_a_report_really_decrypts_under():
    zero = PAIR_DATE + 12_000
    true_counter = 60 * ROTATION_PERIOD
    encrypted, sx = encrypt(b"location", b"\x07" * 32, generate_eid(EIK, true_counter))
    device_update = _device_update([(zero + true_counter + 100, true_counter + 1885)])
    wrapper = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    report = wrapper.networkLocations[0].geoLocation.encryptedReport
    report.encryptedLocation = encrypted
    report.publicKeyRandom = sx

    assert upload.estimate_clock_zero(device_update, EIK) == zero + 100 - ROTATION_PERIOD // 2


def test_estimate_clock_zero_leaves_out_reports_that_never_decrypt():
    device_update = _device_update([(PAIR_DATE + 2000, ROTATION_PERIOD)])
    wrapper = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    report = wrapper.networkLocations[0].geoLocation.encryptedReport
    report.encryptedLocation = b"\x00" * 29
    report.publicKeyRandom = generate_eid(EIK, 0)

    assert upload.estimate_clock_zero(device_update, EIK) is None


def _stub_upload(monkeypatch, responses):
    sent = []

    def _spot_request(api_scope, payload):
        request = DeviceUpdate_pb2.UploadPrecomputedPublicKeyIdsRequest()
        request.ParseFromString(payload)
        sent.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response

    monkeypatch.setattr(upload, "spot_request", _spot_request)
    monkeypatch.setattr(upload, "retrieve_identity_key", lambda registration: EIK)
    return sent


def _latest_eid_real_time(request, clock_zero):
    entries = request.deviceEids[0].clientList.publicKeyIdInfo
    return max(entry.timestamp.seconds for entry in entries) - PAIR_DATE + clock_zero


def test_upload_covers_the_time_until_the_next_upload(monkeypatch):
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")
    clock_zero = int(time.time()) - 100 * ROTATION_PERIOD
    sent = _stub_upload(monkeypatch, [None])

    assert upload.refresh_owned_trackers(device_list, clock_zeros={"tracker-1": clock_zero}) == 1

    assert len(sent) == 1
    assert _latest_eid_real_time(sent[0], clock_zero) > time.time() + config.EID_UPLOAD_INTERVAL_S


def test_upload_drops_upcoming_eids_only_if_refused(monkeypatch):
    device_list = DeviceUpdate_pb2.DevicesList()
    _device(device_list, "Keys", "tracker-1")
    clock_zero = int(time.time()) - 100 * ROTATION_PERIOD
    sent = _stub_upload(monkeypatch, [RuntimeError("failed with 400: INVALID_ARGUMENT"), None])

    assert upload.refresh_owned_trackers(device_list, drift_slots=0, clock_zeros={"tracker-1": clock_zero}) == 1

    assert len(sent) == 2
    assert _latest_eid_real_time(sent[0], clock_zero) > time.time()
    assert _latest_eid_real_time(sent[1], clock_zero) <= time.time()
