import pytest

from Auth import aas_token_retrieval as aas_token_retrieval_module
from Auth.aas_token_retrieval import _generate_aas_token


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
    monkeypatch.setattr(aas_token_retrieval_module, "get_username", lambda: "user@example.com")
    monkeypatch.setattr(aas_token_retrieval_module, "FcmReceiver", FakeFcmReceiver)
    monkeypatch.setattr(
        aas_token_retrieval_module, "request_oauth_account_token_flow", lambda: "fake-oauth-token",
    )
    # Retry tests below don't want to actually wait out the real backoff.
    monkeypatch.setattr(aas_token_retrieval_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(aas_token_retrieval_module, "set_cached_value", lambda name, value: None)


def test_generate_aas_token_returns_token_on_success(monkeypatch):
    monkeypatch.setattr(
        aas_token_retrieval_module.gpsoauth, "exchange_token",
        lambda *a, **kw: {"Token": "the-aas-token"},
    )
    assert _generate_aas_token() == "the-aas-token"


def test_generate_aas_token_caches_email_on_success(monkeypatch):
    cached = {}
    monkeypatch.setattr(
        aas_token_retrieval_module, "set_cached_value",
        lambda name, value: cached.__setitem__(name, value),
    )
    monkeypatch.setattr(
        aas_token_retrieval_module.gpsoauth, "exchange_token",
        lambda *a, **kw: {"Token": "the-aas-token", "Email": "user@example.com"},
    )
    _generate_aas_token()
    assert cached[aas_token_retrieval_module.username_string] == "user@example.com"


def test_generate_aas_token_raises_immediately_on_real_rejection(monkeypatch):
    monkeypatch.setattr(
        aas_token_retrieval_module.gpsoauth, "exchange_token",
        lambda *a, **kw: {"Error": "BadAuthentication"},
    )
    with pytest.raises(RuntimeError, match="Google rejected the token exchange"):
        _generate_aas_token()


def test_generate_aas_token_retries_transient_error_then_succeeds(monkeypatch):
    calls = []

    def fake_exchange_token(*a, **kw):
        calls.append(1)
        if len(calls) < 3:
            return dict(HTML_502_RESPONSE)
        return {"Token": "the-aas-token"}

    monkeypatch.setattr(aas_token_retrieval_module.gpsoauth, "exchange_token", fake_exchange_token)
    assert _generate_aas_token() == "the-aas-token"
    assert len(calls) == 3


def test_generate_aas_token_raises_clear_error_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(
        aas_token_retrieval_module.gpsoauth, "exchange_token",
        lambda *a, **kw: dict(HTML_502_RESPONSE),
    )
    with pytest.raises(RuntimeError, match="transient"):
        _generate_aas_token()
