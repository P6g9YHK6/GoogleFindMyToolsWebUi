import base64
import json

from KeyBackup import vault_web_api
from KeyBackup.vault_web_api import _parse_batchexecute_response, fetch_vault_keys_via_web_app

KEY_A = bytes(range(32))
KEY_B = bytes(range(32, 64))


def _chunk(envelope, length_skew=0):
    body = json.dumps(envelope)
    return f"{len(body) + length_skew}\n{body}\n"


def _response(entries, *, length_skew=0, extra_chunks=()):
    inner = [[None, None, None, None, None, entries]]
    envelope = [["wrb.fr", "FAvFKc", json.dumps(inner), None, None, None, "generic"], ["di", 87]]
    chunks = [_chunk(c) for c in extra_chunks] + [_chunk(envelope, length_skew), _chunk([["e", 4, None, None, 512]])]
    return ")]}'\n\n" + "".join(chunks)


def _entry(key, version, domain="finder_hw", epoch=1):
    return [base64.b64encode(key).decode(), version, domain, epoch, "ignored"]


def test_parses_every_key_version():
    text = _response([_entry(KEY_A, 1, epoch=7), _entry(KEY_B, 2, epoch=9)])

    assert _parse_batchexecute_response(text) == [
        {"key": KEY_A, "version": 1, "domain": "finder_hw", "epoch": 7},
        {"key": KEY_B, "version": 2, "domain": "finder_hw", "epoch": 9},
    ]


def test_tolerates_a_declared_length_that_is_off_by_one():
    # Seen in a live capture: the declared chunk length didn't match the JSON.
    for skew in (-1, 1):
        assert len(_parse_batchexecute_response(_response([_entry(KEY_A, 1)], length_skew=skew))) == 1


def test_skips_chunks_for_other_rpcs():
    other = [["wrb.fr", "SomethingElse", "[]", None, None, None, "generic"]]
    assert len(_parse_batchexecute_response(_response([_entry(KEY_A, 1)], extra_chunks=[other]))) == 1


def test_empty_payload_means_no_keys():
    text = ")]}'\n\n" + _chunk([["wrb.fr", "FAvFKc", None, None, None, None, "generic"]])
    assert _parse_batchexecute_response(text) == []


def test_garbage_means_no_keys():
    assert _parse_batchexecute_response("<html>Sign in</html>") == []
    assert _parse_batchexecute_response(")]}'\n\n12\n{not json") == []


class _Driver:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def get(self, url):
        if self.error:
            raise self.error

    def execute_script(self, script):
        return True

    def execute_async_script(self, script):
        return self.result


def test_fetch_returns_the_parsed_keys(monkeypatch):
    driver = _Driver({"ok": True, "status": 200, "text": _response([_entry(KEY_A, 3)])})
    assert [e["version"] for e in fetch_vault_keys_via_web_app(driver)] == [3]


def test_fetch_returns_nothing_on_an_http_error():
    assert fetch_vault_keys_via_web_app(_Driver({"ok": False, "status": 401, "text": ""})) == []


def test_fetch_never_raises(monkeypatch):
    monkeypatch.setattr(vault_web_api, "WebDriverWait", lambda driver, timeout: None)
    assert fetch_vault_keys_via_web_app(_Driver(error=RuntimeError("browser gone"))) == []
