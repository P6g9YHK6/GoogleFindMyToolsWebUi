import pytest
import requests

from Auth import token_retrieval as token_retrieval_module
from Auth.token_retrieval import request_token


class FakeFcmReceiver:
    def get_android_id(self):
        return "1234"


HTML_502_RESPONSE = {
    "<!DOCTYPE html>": "",
    "<html lang": "en>",
    "  <p><b>502.</b> <ins>That’s an error.</ins>": "",
}


@pytest.fixture(autouse=True)
def stub_deps(monkeypatch):
    monkeypatch.setattr(token_retrieval_module, "get_aas_token", lambda: "fake-aas-token")
    monkeypatch.setattr(token_retrieval_module, "FcmReceiver", FakeFcmReceiver)
    # Retry tests below don't want to actually wait out the real backoff.
    monkeypatch.setattr(token_retrieval_module.time, "sleep", lambda seconds: None)


def test_request_token_returns_auth_on_success(monkeypatch):
    monkeypatch.setattr(
        token_retrieval_module.gpsoauth, "perform_oauth",
        lambda *a, **kw: {"Auth": "the-token"},
    )
    assert request_token("user@example.com", "someScope") == "the-token"


def test_request_token_raises_immediately_on_real_rejection(monkeypatch):
    monkeypatch.setattr(
        token_retrieval_module.gpsoauth, "perform_oauth",
        lambda *a, **kw: {"Error": "BadAuthentication"},
    )
    with pytest.raises(RuntimeError, match="Google rejected the sign-in"):
        request_token("user@example.com", "someScope")


def test_request_token_retries_transient_error_then_succeeds(monkeypatch):
    calls = []

    def fake_perform_oauth(*a, **kw):
        calls.append(1)
        if len(calls) < 3:
            return dict(HTML_502_RESPONSE)
        return {"Auth": "the-token"}

    monkeypatch.setattr(token_retrieval_module.gpsoauth, "perform_oauth", fake_perform_oauth)
    assert request_token("user@example.com", "someScope") == "the-token"
    assert len(calls) == 3


def test_request_token_raises_clear_error_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(
        token_retrieval_module.gpsoauth, "perform_oauth",
        lambda *a, **kw: dict(HTML_502_RESPONSE),
    )
    with pytest.raises(RuntimeError, match="transient error"):
        request_token("user@example.com", "someScope")


def test_request_token_retries_transient_connection_error_then_succeeds(monkeypatch):
    calls = []

    def fake_perform_oauth(*a, **kw):
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed "
                "connection without response'))"
            )
        return {"Auth": "the-token"}

    monkeypatch.setattr(token_retrieval_module.gpsoauth, "perform_oauth", fake_perform_oauth)
    assert request_token("user@example.com", "someScope") == "the-token"
    assert len(calls) == 3


def test_request_token_raises_after_exhausting_connection_error_retries(monkeypatch):
    calls = []

    def fake_perform_oauth(*a, **kw):
        calls.append(1)
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(token_retrieval_module.gpsoauth, "perform_oauth", fake_perform_oauth)
    with pytest.raises(requests.exceptions.ConnectionError):
        request_token("user@example.com", "someScope")
    assert len(calls) == token_retrieval_module.TOKEN_REQUEST_RETRIES + 1
