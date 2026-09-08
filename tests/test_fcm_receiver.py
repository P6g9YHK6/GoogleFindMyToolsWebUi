"""Regression test for a real production incident: FcmReceiver.
register_for_location_updates()/get_fcm_token()/get_android_id() used to
check `self._listening` and call _start_listener_in_background() with no
lock guarding the two steps - a classic check-then-act race. Two devices'
polls landing close together (the common case right after a restart, when
every device's schedule is freshly evaluated at once) could both see
`_listening == False` and both start a listener concurrently against the
same shared FcmPushClient, corrupting its connection state. Observed in
production as "readexactly() called while another coroutine is already
waiting for incoming data", crashing the listener and timing out every
locate that was waiting on it.

Uses real threads and a real (short) delay inside the guarded section,
same approach as tests/test_query_throttle.py's concurrency test - a
single-threaded test can't tell "two callers both started the listener"
apart from "only one did", since nothing else is running concurrently to
race with it.
"""

import threading
import time

import pytest

from Auth.firebase_messaging import FcmPushClientRunState


@pytest.fixture
def receiver(monkeypatch):
    """A real FcmReceiver instance with __init__'s network-touching bits
    (get_cached_value, FcmPushClient) stubbed out, and _listening starting
    False so _ensure_listening() actually has something to guard. pc.run_state
    defaults to STARTED - the healthy, already-listening state - since
    _listener_dead() now reads it too."""
    from Auth import fcm_receiver as fcm_receiver_module

    monkeypatch.setattr(fcm_receiver_module, "get_cached_value", lambda name: {"gcm": {"android_id": "abc123"}})
    monkeypatch.setattr(fcm_receiver_module.FcmPushClient, "__init__", lambda self, *a, **kw: None)

    fcm_receiver_module.FcmReceiver._instance = None
    r = fcm_receiver_module.FcmReceiver()
    r._listening = False
    r.pc.run_state = FcmPushClientRunState.STARTED
    yield r
    fcm_receiver_module.FcmReceiver._instance = None


def test_ensure_listening_only_starts_the_listener_once_under_concurrency(receiver, monkeypatch):
    start_calls = []
    entered = threading.Event()
    release = threading.Event()

    def fake_start_listener_in_background():
        # Simulates the real method's own real work taking a moment - long
        # enough for a second concurrent caller to reach its own
        # self._listening check while the first is still mid-start, which
        # is exactly the window the old unguarded code raced in.
        start_calls.append(1)
        entered.set()
        release.wait(timeout=2)
        receiver._listening = True
        return "abc123"

    monkeypatch.setattr(receiver, "_start_listener_in_background", fake_start_listener_in_background)

    t1 = threading.Thread(target=receiver._ensure_listening)
    t1.start()
    assert entered.wait(timeout=2)  # t1 is now inside the guarded start

    t2 = threading.Thread(target=receiver._ensure_listening)
    t2.start()
    # t2 must block behind t1's lock, not race it - give it every chance to
    # (wrongly) start a second listener before we let t1 finish.
    time.sleep(0.1)
    assert len(start_calls) == 1

    release.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert len(start_calls) == 1
    assert receiver._listening is True


def test_ensure_listening_is_a_no_op_once_already_listening(receiver, monkeypatch):
    receiver._listening = True
    receiver.credentials = {"gcm": {"android_id": "already-listening-id"}}

    start_calls = []
    monkeypatch.setattr(receiver, "_start_listener_in_background", lambda: start_calls.append(1))

    assert receiver._ensure_listening() == "already-listening-id"
    assert start_calls == []


def test_register_for_location_updates_and_get_fcm_token_use_the_guard(receiver, monkeypatch):
    calls = []
    monkeypatch.setattr(receiver, "_ensure_listening", lambda: calls.append(1))
    receiver.credentials = {"fcm": {"registration": {"token": "tok"}}}

    receiver.register_for_location_updates(lambda hex_string: None)
    receiver.get_fcm_token()

    assert calls == [1, 1]


@pytest.mark.parametrize("dead_state", [FcmPushClientRunState.STOPPING, FcmPushClientRunState.STOPPED])
def test_ensure_listening_restarts_after_the_push_client_dies(receiver, monkeypatch, dead_state):
    """Regression test: FcmPushClient shuts itself down after 3 sequential
    connection errors (see fcmpushclient.py's abort_on_sequential_error_count)
    and never recovers on its own - _listening alone stayed True forever
    after the first successful start, so every locate after such a crash
    used to sit waiting on a listener that was actually long dead."""
    receiver._listening = True
    receiver.pc.run_state = dead_state

    start_calls = []
    monkeypatch.setattr(receiver, "_start_listener_in_background", lambda: start_calls.append(1) or "restarted-id")

    assert receiver._ensure_listening() == "restarted-id"
    assert start_calls == [1]


def test_get_android_id_only_guards_when_credentials_are_missing(receiver, monkeypatch):
    calls = []
    monkeypatch.setattr(receiver, "_ensure_listening", lambda: calls.append(1) or "started-id")

    receiver.credentials = None
    assert receiver.get_android_id() == "started-id"
    assert calls == [1]

    receiver.credentials = {"gcm": {"android_id": "cached-id"}}
    assert receiver.get_android_id() == "cached-id"
    assert calls == [1]  # unchanged - already-known credentials skip the guard entirely


def test_concurrent_creation_waits_for_full_init(monkeypatch):
    """Regression test for "'FcmReceiver' object has no attribute
    '_start_lock'". __new__ publishes the singleton to _instance before
    __init__ finishes, and __init__ used to set _initialized=True first and
    then do slow work (file reads, FcmPushClient construction) before ever
    creating _start_lock. A second thread calling FcmReceiver() in that
    window - e.g. two devices' locates landing together right after a
    restart or a "Clear credentials" - grabbed the half-built instance,
    early-returned from __init__, and blew up on `with self._start_lock` in
    _ensure_listening."""
    from Auth import fcm_receiver as fcm_receiver_module

    entered_init = threading.Event()
    release_init = threading.Event()

    def slow_get_cached_value(name):
        entered_init.set()
        release_init.wait(timeout=2)
        return {"gcm": {"android_id": "abc123"}}

    monkeypatch.setattr(fcm_receiver_module, "get_cached_value", slow_get_cached_value)
    monkeypatch.setattr(fcm_receiver_module.FcmPushClient, "__init__", lambda self, *a, **kw: None)
    # Keep _ensure_listening from going deeper than the _start_lock guard on
    # the second caller - this test is about the lock existing at all.
    monkeypatch.setattr(fcm_receiver_module.FcmReceiver, "_listener_dead", lambda self: False)
    fcm_receiver_module.FcmReceiver._listening = True

    fcm_receiver_module.FcmReceiver._instance = None

    t1 = threading.Thread(target=fcm_receiver_module.FcmReceiver)
    t1.start()
    assert entered_init.wait(timeout=2)  # t1 is mid-init, _start_lock not yet created

    errors = []

    def second_caller():
        try:
            r = fcm_receiver_module.FcmReceiver()
            r._ensure_listening()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t2 = threading.Thread(target=second_caller)
    t2.start()
    time.sleep(0.1)
    assert not errors, f"second caller must block, not fail, while init is running: {errors}"

    release_init.set()
    t1.join(timeout=2)
    t2.join(timeout=2)

    assert not errors, f"second caller must work once init has finished: {errors}"

    fcm_receiver_module.FcmReceiver._instance = None
    fcm_receiver_module.FcmReceiver._listening = False
