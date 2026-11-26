"""FCM/GCM registration against a fake HTTP session that answers the way
Google's endpoints do (a protobuf check-in, a "token=..." form reply, the
Firebase installation JSON). Pins how those replies are read, so a format
change on Google's side shows up here instead of as a silent "no pushes"."""

import json

import pytest

from Auth.firebase_messaging import fcmregister
from Auth.firebase_messaging.const import (
    FCM_INSTALLATION,
    FCM_REGISTRATION,
    FCM_SEND_URL,
    GCM_CHECKIN_URL,
    GCM_REGISTER_URL,
)
from Auth.firebase_messaging.fcmregister import FcmRegister, FcmRegisterConfig
from Auth.firebase_messaging.proto.checkin_pb2 import AndroidCheckinRequest, AndroidCheckinResponse

ANDROID_ID = 4_000_000_000_000_000_001
SECURITY_TOKEN = 5_000_000_000_000_000_002


class _Response:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._body = body if isinstance(body, bytes) else body.encode()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return self._body

    async def text(self):
        return self._body.decode()

    async def json(self):
        return json.loads(self._body)


class _Session:
    """Answers each POST from the queue registered for its URL prefix."""

    def __init__(self):
        self.replies: dict[str, list] = {}
        self.requests: list[dict] = []

    def answer(self, url_prefix, *replies):
        self.replies.setdefault(url_prefix, []).extend(replies)

    def post(self, url, headers=None, data=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "data": data})
        for prefix, queue in self.replies.items():
            if url.startswith(prefix) and queue:
                reply = queue.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise AssertionError(f"unexpected POST to {url}")

    def sent_to(self, url_prefix):
        return [r for r in self.requests if r["url"].startswith(url_prefix)]


def _checkin_reply():
    reply = AndroidCheckinResponse()
    reply.stats_ok = True
    reply.android_id = ANDROID_ID
    reply.security_token = SECURITY_TOKEN
    return _Response(200, reply.SerializeToString())


def _install_reply(fid="fid-1"):
    return _Response(200, json.dumps({
        "fid": fid,
        "refreshToken": "refresh-1",
        "authToken": {"token": "install-token", "expiresIn": "604800s"},
    }))


def _config(**overrides):
    return FcmRegisterConfig(project_id="proj", app_id="1:2:android:3", api_key="key", messaging_sender_id="2",
                             **overrides)


@pytest.fixture
def session(monkeypatch):
    async def _no_sleep(seconds):
        pass

    monkeypatch.setattr(fcmregister.asyncio, "sleep", _no_sleep)
    return _Session()


# --- GCM -----------------------------------------------------------------

async def test_checkin_reads_the_android_id_and_token(session):
    session.answer(GCM_CHECKIN_URL, _checkin_reply())

    result = await FcmRegister(_config(), http_client_session=session).gcm_check_in()

    assert int(result["androidId"]) == ANDROID_ID
    assert int(result["securityToken"]) == SECURITY_TOKEN


async def test_checkin_retries_as_a_new_device_after_a_refusal(session):
    session.answer(GCM_CHECKIN_URL, _Response(400, "bad"), _checkin_reply())

    result = await FcmRegister(_config(), http_client_session=session).gcm_check_in(ANDROID_ID, SECURITY_TOKEN)

    assert result is not None
    first, second = (AndroidCheckinRequest.FromString(r["data"]) for r in session.sent_to(GCM_CHECKIN_URL))
    assert first.id == ANDROID_ID and first.security_token == SECURITY_TOKEN
    assert not second.id and not second.security_token


async def test_gcm_register_reads_the_token(session):
    session.answer(GCM_REGISTER_URL, _Response(200, "token=gcm-token-1"))

    result = await FcmRegister(_config(), http_client_session=session).gcm_register(
        {"androidId": str(ANDROID_ID), "securityToken": str(SECURITY_TOKEN)},
    )

    assert result["token"] == "gcm-token-1"
    assert result["android_id"] == str(ANDROID_ID)
    sent = session.sent_to(GCM_REGISTER_URL)[0]
    assert sent["headers"]["Authorization"] == f"AidLogin {ANDROID_ID}:{SECURITY_TOKEN}"
    assert sent["data"]["X-subtype"] == result["app_id"]


async def test_gcm_register_retries_an_error_reply_then_gives_up(session):
    session.answer(GCM_REGISTER_URL, *[_Response(200, "Error=PHONE_REGISTRATION_ERROR")] * 3)

    result = await FcmRegister(_config(), http_client_session=session).gcm_register(
        {"androidId": "1", "securityToken": "2"}, retries=3,
    )

    assert result is None
    assert len(session.sent_to(GCM_REGISTER_URL)) == 3


# --- Firebase installation and registration ------------------------------

async def test_install_reads_tokens_and_expiry(session):
    session.answer(FCM_INSTALLATION, _install_reply())

    result = await FcmRegister(_config(), http_client_session=session).fcm_install()

    assert result["token"] == "install-token"
    assert result["expires_in"] == 604800
    assert result["refresh_token"] == "refresh-1"
    assert result["fid"] == "fid-1"


async def test_install_failure_returns_none(session):
    session.answer(FCM_INSTALLATION, _Response(403, "API key not valid"))
    assert await FcmRegister(_config(), http_client_session=session).fcm_install() is None


async def test_android_restriction_headers_are_normalized(session):
    session.answer(FCM_INSTALLATION, _install_reply())
    config = _config(android_package="com.google.android.gms", android_cert_sha1="38:91:8A:45" + ":00" * 16)

    await FcmRegister(config, http_client_session=session).fcm_install()

    headers = session.sent_to(FCM_INSTALLATION)[0]["headers"]
    assert headers["X-Android-Package"] == "com.google.android.gms"
    assert headers["X-Android-Cert"] == "38918a45" + "00" * 16


def test_a_malformed_cert_fingerprint_is_rejected():
    with pytest.raises(ValueError):
        fcmregister._normalize_sha1_fingerprint("not-a-fingerprint")


async def test_fcm_register_leaves_out_the_default_vapid_key(session):
    session.answer(FCM_REGISTRATION, _Response(200, json.dumps({"token": "fcm-token"})))
    register = FcmRegister(_config(), http_client_session=session)
    keys = register.generate_keys()

    result = await register.fcm_register({"token": "gcm-token-1"}, {"token": "install-token"}, keys)

    assert result == {"token": "fcm-token"}
    sent = session.sent_to(FCM_REGISTRATION)[0]
    web = json.loads(sent["data"])["web"]
    assert web["applicationPubKey"] is None
    assert web["endpoint"] == FCM_SEND_URL + "gcm-token-1"
    assert web["p256dh"] == keys["public"] and web["auth"] == keys["secret"]
    assert sent["headers"]["x-goog-firebase-installations-auth"] == "install-token"


# --- full flow -----------------------------------------------------------

def _answer_full_registration(session):
    session.answer(GCM_CHECKIN_URL, _checkin_reply())
    session.answer(GCM_REGISTER_URL, _Response(200, "token=gcm-token-1"))
    session.answer(FCM_INSTALLATION, _install_reply())
    session.answer(FCM_REGISTRATION, _Response(200, json.dumps({"token": "fcm-token"})))


async def test_register_builds_the_credentials_the_push_client_needs(session):
    _answer_full_registration(session)

    credentials = await FcmRegister(_config(), http_client_session=session).register()

    assert credentials["gcm"]["token"] == "gcm-token-1"
    assert credentials["fcm"]["registration"] == {"token": "fcm-token"}
    assert credentials["fcm"]["installation"]["fid"] == "fid-1"
    assert set(credentials["keys"]) == {"public", "private", "secret"}
    assert credentials["config"]["project_id"] == "proj"


async def test_checkin_or_register_keeps_working_credentials(session):
    session.answer(GCM_CHECKIN_URL, _checkin_reply())
    existing = {"gcm": {"android_id": ANDROID_ID, "security_token": SECURITY_TOKEN}}
    saved = []

    result = await FcmRegister(_config(), existing, saved.append, http_client_session=session).checkin_or_register()

    assert result is existing
    assert saved == []


async def test_checkin_or_register_registers_from_scratch_and_saves(session):
    _answer_full_registration(session)
    saved = []

    result = await FcmRegister(_config(), None, saved.append, http_client_session=session).checkin_or_register()

    assert saved == [result]
    assert result["gcm"]["token"] == "gcm-token-1"


async def test_register_fails_loudly_when_firebase_refuses(session):
    session.answer(GCM_CHECKIN_URL, _checkin_reply())
    session.answer(GCM_REGISTER_URL, _Response(200, "token=gcm-token-1"))
    session.answer(FCM_INSTALLATION, _Response(403, "API key not valid"))

    with pytest.raises(RuntimeError, match="fcm"):
        await FcmRegister(_config(), http_client_session=session).register()
