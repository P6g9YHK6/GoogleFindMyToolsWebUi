"""Wire format of the Nova requests we send and the locate round trip
(request out through nova_request, answer back through an FCM push).
Nothing here talks to Google: nova_request and the FCM receiver are faked
at the module boundary, the protobufs are the real ones."""

import pytest

from NovaApi.ExecuteAction import nbe_execute_action
from NovaApi.ExecuteAction.LocateTracker import location_request
from NovaApi.ExecuteAction.LocateTracker.location_request import create_location_request
from NovaApi.ExecuteAction.PlaySound import sound_action
from NovaApi.ExecuteAction.PlaySound.sound_request import create_sound_request
from NovaApi.ListDevices.nbe_list_devices import create_device_list_request
from NovaApi.scopes import NOVA_ACTION_API_SCOPE
from ProtoDecoders import DeviceUpdate_pb2


def _action(hex_payload) -> DeviceUpdate_pb2.ExecuteActionRequest:
    request = DeviceUpdate_pb2.ExecuteActionRequest()
    request.ParseFromString(bytes.fromhex(hex_payload))
    return request


# --- request builders ---------------------------------------------------

def test_action_request_targets_the_device():
    request = nbe_execute_action.create_action_request("canonic-1", "fcm-token", request_uuid="req-1")

    assert request.scope.type == DeviceUpdate_pb2.SPOT_DEVICE
    assert request.scope.device.canonicId.id == "canonic-1"
    assert request.requestMetadata.requestUuid == "req-1"
    assert request.requestMetadata.gcmRegistrationId.id == "fcm-token"
    assert request.requestMetadata.fmdClientUuid == nbe_execute_action.client_id


def test_action_requests_get_a_fresh_uuid_each():
    first = nbe_execute_action.create_action_request("canonic-1", "fcm-token")
    second = nbe_execute_action.create_action_request("canonic-1", "fcm-token")
    assert first.requestMetadata.requestUuid != second.requestMetadata.requestUuid


def test_location_request_asks_for_every_crowdsourced_location():
    request = _action(create_location_request("canonic-1", "fcm-token", "req-1"))

    assert request.action.HasField("locateTracker")
    assert request.action.locateTracker.contributorType == DeviceUpdate_pb2.FMDN_ALL_LOCATIONS
    assert request.requestMetadata.requestUuid == "req-1"


@pytest.mark.parametrize("should_start, field, other", [(True, "startSound", "stopSound"),
                                                        (False, "stopSound", "startSound")])
def test_sound_request_starts_or_stops(should_start, field, other):
    request = _action(create_sound_request(should_start, "canonic-1", "fcm-token"))
    assert request.action.HasField(field)
    assert not request.action.HasField(other)
    assert request.scope.device.canonicId.id == "canonic-1"


def test_play_sound_sends_through_the_action_scope(monkeypatch):
    sent = []

    class _Receiver:
        def get_fcm_token(self):
            return "fcm-token"

    monkeypatch.setattr(sound_action, "FcmReceiver", _Receiver)
    monkeypatch.setattr(sound_action, "nova_request", lambda scope, payload: sent.append((scope, payload)))

    assert sound_action.play_sound("canonic-1", True) == {"ok": True, "should_start": True}
    scope, payload = sent[0]
    assert scope == NOVA_ACTION_API_SCOPE
    assert _action(payload).requestMetadata.gcmRegistrationId.id == "fcm-token"


def test_device_list_request_asks_for_spot_devices():
    first = DeviceUpdate_pb2.DevicesListRequest()
    first.ParseFromString(bytes.fromhex(create_device_list_request()))
    second = DeviceUpdate_pb2.DevicesListRequest()
    second.ParseFromString(bytes.fromhex(create_device_list_request()))

    assert first.deviceListRequestPayload.type == DeviceUpdate_pb2.SPOT_DEVICE
    assert first.deviceListRequestPayload.id != second.deviceListRequestPayload.id


# --- locate round trip --------------------------------------------------

class _FakeReceiver:
    """Stands in for Auth.fcm_receiver.FcmReceiver; `pushes` decides what
    FCM delivers once the request has gone out."""

    def __init__(self):
        self.callbacks = []

    def register_for_location_updates(self, callback):
        self.callbacks.append(callback)
        return "fcm-token"

    def unregister_callback(self, callback):
        self.callbacks.remove(callback)


def _push(request_uuid: str) -> str:
    update = DeviceUpdate_pb2.DeviceUpdate()
    update.fcmMetadata.requestUuid = request_uuid
    return update.SerializeToString().hex()


@pytest.fixture
def locate(monkeypatch):
    receiver = _FakeReceiver()
    state = {"pushes": lambda request_uuid: [_push(request_uuid)], "error": None, "sent": []}

    def _nova_request(scope, hex_payload):
        state["sent"].append(scope)
        if state["error"]:
            raise state["error"]
        request_uuid = _action(hex_payload).requestMetadata.requestUuid
        for push in state["pushes"](request_uuid):
            for callback in list(receiver.callbacks):
                callback(push)

    monkeypatch.setattr(location_request, "FcmReceiver", lambda: receiver)
    monkeypatch.setattr(location_request, "nova_request", _nova_request)
    monkeypatch.setattr(location_request, "decrypt_location_response_locations",
                        lambda update, name: [{"from": update.fcmMetadata.requestUuid}])
    state["receiver"] = receiver
    return state


def test_locate_returns_the_matching_answer(locate):
    capture = {}
    result = location_request.get_location_data_for_device("canonic-1", "Keys", timeout=1, capture=capture)

    assert locate["sent"] == [NOVA_ACTION_API_SCOPE]
    assert len(result) == 1
    assert "raw_hex" in capture and "device_update_text" in capture
    assert locate["receiver"].callbacks == []


def test_locate_ignores_answers_to_other_requests(locate):
    # Several locates share one FCM connection, so a push for someone else's
    # request must neither be returned nor end the wait.
    locate["pushes"] = lambda request_uuid: [_push("someone-else"), _push(request_uuid)]

    result = location_request.get_location_data_for_device("canonic-1", "Keys", timeout=1)

    assert result[0]["from"] != "someone-else"


def test_locate_times_out_without_an_answer(locate):
    locate["pushes"] = lambda request_uuid: [_push("someone-else")]
    capture = {}

    assert location_request.get_location_data_for_device("canonic-1", "Keys", timeout=0.05, capture=capture) == []
    assert capture.get("timed_out") is True
    assert "raw_hex" not in capture
    assert locate["receiver"].callbacks == []


def test_locate_unregisters_when_the_request_fails(locate):
    locate["error"] = RuntimeError("Nova request failed with 403")

    with pytest.raises(RuntimeError):
        location_request.get_location_data_for_device("canonic-1", "Keys", timeout=1)
    assert locate["receiver"].callbacks == []
