import pytest
import requests

from NovaApi import nova_request as nova_request_module
from NovaApi.nova_request import nova_request


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.content = b"\xde\xad\xbe\xef"


@pytest.fixture(autouse=True)
def stub_auth(monkeypatch):
    monkeypatch.setattr(nova_request_module, "get_username", lambda: "user@example.com")
    monkeypatch.setattr(nova_request_module, "get_adm_token", lambda username: "fake-token")
    # Retry tests below don't want to actually wait out the real backoff.
    monkeypatch.setattr(nova_request_module.time, "sleep", lambda seconds: None)


def test_nova_request_returns_hex_content_on_success(monkeypatch):
    monkeypatch.setattr(
        nova_request_module.requests, "post", lambda *a, **kw: FakeResponse(200),
    )
    assert nova_request("someScope", "00") == "deadbeef"


def test_nova_request_raises_with_a_clear_message_on_failure(monkeypatch):
    monkeypatch.setattr(
        nova_request_module.requests, "post",
        lambda *a, **kw: FakeResponse(403, "<html><body>Forbidden</body></html>"),
    )
    with pytest.raises(RuntimeError, match="403"):
        nova_request("someScope", "00")


def test_nova_request_retries_transient_connection_error_then_succeeds(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end closed "
                "connection without response'))"
            )
        return FakeResponse(200)

    monkeypatch.setattr(nova_request_module.requests, "post", fake_post)
    assert nova_request("someScope", "00") == "deadbeef"
    assert len(calls) == 3


def test_nova_request_raises_after_exhausting_retries(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(nova_request_module.requests, "post", fake_post)
    with pytest.raises(requests.exceptions.ConnectionError):
        nova_request("someScope", "00")
    assert len(calls) == nova_request_module.NOVA_REQUEST_RETRIES + 1


GOOGLE_500_PAGE = (
    "<html><head><title>Internal Server Error</title></head><body>\n\n\n"
    "<h1>Internal Server Error</h1>\n<h2>Error 500</h2></body></html>"
)


def test_nova_request_retries_5xx_then_succeeds(monkeypatch):
    responses = [FakeResponse(500, GOOGLE_500_PAGE), FakeResponse(200)]
    monkeypatch.setattr(
        nova_request_module.requests, "post", lambda *a, **kw: responses.pop(0),
    )
    assert nova_request("someScope", "00") == "deadbeef"
    assert responses == []


def test_nova_request_raises_clean_message_after_exhausting_5xx_retries(monkeypatch, caplog):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        return FakeResponse(500, GOOGLE_500_PAGE)

    monkeypatch.setattr(nova_request_module.requests, "post", fake_post)
    with pytest.raises(RuntimeError) as excinfo:
        nova_request("someScope", "00")
    assert len(calls) == nova_request_module.NOVA_REQUEST_RETRIES + 1
    assert str(excinfo.value) == (
        "Nova request to someScope failed with 500: Internal Server Error Error 500"
    )
    # Callers log the raised error; logging a WARNING here too sent a
    # second notification for the same failure.
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_nova_request_does_not_retry_4xx(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        return FakeResponse(404, "Not Found")

    monkeypatch.setattr(nova_request_module.requests, "post", fake_post)
    with pytest.raises(RuntimeError, match="404"):
        nova_request("someScope", "00")
    assert len(calls) == 1
