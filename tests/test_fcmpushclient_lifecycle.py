import asyncio
import ssl

import Auth.firebase_messaging.fcmpushclient as fcmpushclient_module
from Auth.firebase_messaging.fcmpushclient import (
    ErrorType,
    FcmPushClient,
    FcmPushClientRunState,
    FcmRegisterConfig,
)


def _make_client() -> FcmPushClient:
    config = FcmRegisterConfig(
        project_id="proj", app_id="app", api_key="key", messaging_sender_id="sender",
    )
    return FcmPushClient(callback=lambda *a: None, fcm_config=config, credentials=None)


async def test_stop_before_start_does_not_raise():
    """FcmReceiver._register_for_fcm calls pc.stop() from its except-handler
    when the very first checkin/registration attempt fails - i.e. before
    pc.start() has ever run. stop() used to do `async with self.stopping_lock`
    while that lock was still None (only created in start()), raising
    "'NoneType' object does not support the asynchronous context manager
    protocol" - which surfaced up through webui.scheduler as a baffling
    "Locate failed for <device>: 'NoneType' object does not support the
    asynchronous context manager protocol" warning."""
    client = _make_client()

    # Never called client.start() - reset_lock/stopping_lock must already be
    # usable locks, not None.
    await client.stop()


async def test_checkin_or_register_closes_session_even_on_failure(monkeypatch):
    """If FcmRegister.checkin_or_register() raises (e.g. gcm checkin
    exhausts its retries), the FcmRegister's own aiohttp session used to
    never get closed - checkin_or_register() awaited self.register.close()
    on the line right after the call that raised, so it was skipped
    entirely. That leaked ClientSession/TCPConnector, logged later by
    aiohttp as "Unclosed client session" / "Unclosed connector
    connections" once garbage collected."""
    client = _make_client()

    closed = []

    async def boom(self):
        raise RuntimeError("checkin failed")

    async def fake_close(self):
        closed.append(True)

    monkeypatch.setattr(
        fcmpushclient_module.FcmRegister, "checkin_or_register", boom
    )
    monkeypatch.setattr(fcmpushclient_module.FcmRegister, "close", fake_close)

    try:
        await client.checkin_or_register()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the simulated checkin failure to propagate")

    assert closed == [True], "FcmRegister.close() must run even when checkin_or_register() raises"


class _BenignCloseReader:
    """A fake StreamReader that raises the TLS close-race SSLError exactly
    once, then blocks forever so _listen does not spin after handling it."""

    def __init__(self) -> None:
        self.raised = False

    async def readexactly(self, n: int) -> bytes:
        if not self.raised:
            self.raised = True
            err = ssl.SSLError(
                1, "[SSL: APPLICATION_DATA_AFTER_CLOSE_NOTIFY] application data after close notify"
            )
            err.reason = "APPLICATION_DATA_AFTER_CLOSE_NOTIFY"
            raise err
        await asyncio.Event().wait()
        raise AssertionError("should never reach here")


async def test_benign_ssl_close_notify_reconnects_without_abort(monkeypatch):
    """Google's MCS server sometimes sends close_notify and then a little
    more application data while we are already tearing the connection down,
    surfacing as SSLError 'APPLICATION_DATA_AFTER_CLOSE_NOTIFY'. When that
    read error arrives after run_state has moved past RESETTING it used to
    fall through to the unexpected-error branch and count toward the
    3-strike shutdown ("Shutting down push receiver due to 3 sequential
    errors of type ErrorType.CONNECTION"). It should instead reconnect via
    _reset() without advancing the abort counter."""

    async def fake_connect_with_retry(self) -> bool:
        return True

    async def fake_login(self) -> None:
        self.run_state = FcmPushClientRunState.STARTED

    monkeypatch.setattr(
        fcmpushclient_module.FcmPushClient,
        "_connect_with_retry",
        fake_connect_with_retry,
    )
    monkeypatch.setattr(
        fcmpushclient_module.FcmPushClient, "_login", fake_login
    )

    client = _make_client()
    client.config.reset_interval = 0
    client.reset_lock = asyncio.Lock()
    client.stopping_lock = asyncio.Lock()
    client.do_listen = True
    client.run_state = FcmPushClientRunState.STARTED  # deliberately NOT RESETTING
    client.first_message = False
    client.reader = _BenignCloseReader()
    client.writer = None

    resets: list[bool] = []

    async def fake_reset(self) -> None:
        resets.append(True)

    monkeypatch.setattr(fcmpushclient_module.FcmPushClient, "_reset", fake_reset)

    task = asyncio.create_task(client._listen())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert resets == [True], "benign close-notify error must trigger a reconnect"
    assert (
        client.sequential_error_counters.get(ErrorType.CONNECTION, 0) == 0
    ), "benign close-notify error must not advance the abort counter"
