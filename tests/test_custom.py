"""Tests for webui/forwarders/custom.py - generic HTTP forwarder."""

import httpx
import pytest

from webui.forwarders import custom


def test_render_empty_template():
    """Empty template returns empty string (line 33)."""
    assert custom._render("", {"x": "y"}) == ""
    assert custom._render(None, {"x": "y"}) == ""


def test_render_known_variable():
    """Known variable substituted."""
    assert custom._render("lat={{latitude}}", {"latitude": 1.5}) == "lat=1.5"


def test_render_known_variable_none():
    """Known variable with None value -> empty string + warning (logged)."""
    # The function returns empty string for None value
    result = custom._render("lat={{latitude}}", {"latitude": None})
    assert result == "lat="


def test_render_unknown_variable():
    """Unknown variable left in place."""
    assert custom._render("x={{unknown}}", {"known": 1}) == "x={{unknown}}"


def test_render_multiple_variables():
    """Multiple variables in one template."""
    ctx = {"lat": "1", "lon": "2"}
    assert custom._render("{{lat}},{{lon}}", ctx) == "1,2"


def test_build_context_all_fields():
    """All context fields populated."""
    location = {
        "latitude": 1.0, "longitude": 2.0, "altitude": 3.0, "accuracy": 4.0,
        "status": "CROWDSOURCED", "status_id": 2, "is_semantic": False,
        "semantic_name": "", "is_own_report": True, "time": 1234567890,
    }
    device_meta = {"manufacturer": "Acme", "model": "Tag", "type": "keys", "type_id": 3, "image_url": "img"}
    ctx = custom.build_context({}, location, "My Device", "My Alias", "tracker123", device_meta)

    assert ctx["latitude"] == 1.0
    assert ctx["longitude"] == 2.0
    assert ctx["altitude_m"] == 3.0
    assert ctx["accuracy_m"] == 4.0
    assert ctx["status"] == "CROWDSOURCED"
    assert ctx["status_id"] == 2
    assert ctx["is_semantic"] is False
    assert ctx["semantic_name"] == ""
    assert ctx["own_report"] is True
    assert ctx["google_timestamp"] == 1234567890
    assert ctx["fix_timestamp"] == 1234567890
    assert ctx["device_name"] == "My Device"
    assert ctx["device_alias"] == "My Alias"
    assert ctx["tracker_id"] == "tracker123"
    assert ctx["manufacturer"] == "Acme"
    assert ctx["model"] == "Tag"
    assert ctx["type"] == "keys"
    assert ctx["type_id"] == 3
    assert ctx["image_url"] == "img"


def test_build_context_device_alias_fallback():
    """device_alias falls back to device_name."""
    ctx = custom.build_context({}, {"latitude": 1, "longitude": 2}, "My Device", None, "", {})
    assert ctx["device_alias"] == "My Device"


def test_build_context_meta_named_keys():
    """NAMED_DEVICE_META_KEYS get their own {{name}}."""
    meta = {"manufacturer": "A", "model": "B", "type": "C", "type_id": 0, "image_url": "D", "extra": "E"}
    ctx = custom.build_context({}, {"latitude": 1, "longitude": 2}, "D", "A", "", meta)
    assert ctx["manufacturer"] == "A"
    assert ctx["model"] == "B"
    assert ctx["type"] == "C"
    assert ctx["type_id"] == 0
    assert ctx["image_url"] == "D"
    assert ctx["label_extra"] == "E"


def test_build_context_meta_none_values():
    """None values in meta -> empty string."""
    meta = {"manufacturer": None, "carrier": None}
    ctx = custom.build_context({}, {"latitude": 1, "longitude": 2}, "D", "A", "", meta)
    assert ctx["manufacturer"] == ""
    assert ctx["label_carrier"] == ""


def test_build_context_legacy_variables():
    """Legacy variables table merged into context."""
    endpoint_cfg = {"variables": {"custom_var": "custom_val"}}
    ctx = custom.build_context(endpoint_cfg, {"latitude": 1, "longitude": 2}, "D", "A", "", {})
    assert ctx["custom_var"] == "custom_val"


def test_forward_to_custom_no_location():
    """No coordinates -> False, nothing sent."""
    assert custom.forward_to_custom({}, {"latitude": None}) is False


def test_forward_to_custom_no_url():
    """No URL -> False."""
    assert custom.forward_to_custom({"url": ""}, {"latitude": 1}) is False


def test_forward_to_custom_demo_mode(monkeypatch):
    """Demo mode returns True without sending."""
    monkeypatch.setattr("webui.config.DEMO_MODE", True)
    assert custom.forward_to_custom({"url": "http://x"}, {"latitude": 1}) is True


def test_forward_to_custom_body_type_json(monkeypatch):
    """body_type=json sends JSON body."""
    called = {}

    def fake_request(method, url, headers, timeout, content, data):
        called["method"] = method
        called["url"] = url
        called["content"] = content
        called["headers"] = headers
        class Resp:
            status_code = 200
            text = "ok"
            def raise_for_status(self): pass
        return Resp()

    monkeypatch.setattr("webui.forwarders.custom.httpx.request", fake_request)
    custom.forward_to_custom(
        {"url": "http://x", "body_type": "json", "body": '{"lat": "{{latitude}}"}', "method": "POST"},
        {"latitude": 1.5, "longitude": 2.5},
        "Device", None, "", {}
    )
    assert called["method"] == "POST"
    assert called["content"] == '{"lat": "1.5"}'
    assert called["headers"]["Content-Type"] == "application/json"


def test_forward_to_custom_body_type_raw(monkeypatch):
    """body_type=raw sends raw body (line 140)."""
    called = {}

    def fake_request(method, url, headers, timeout, content, data):
        called["method"] = method
        called["content"] = content
        class Resp:
            status_code = 200
            text = "ok"
            def raise_for_status(self): pass
        return Resp()

    monkeypatch.setattr("webui.forwarders.custom.httpx.request", fake_request)
    custom.forward_to_custom(
        {"url": "http://x", "body_type": "raw", "body": "raw:{{latitude}}", "method": "POST"},
        {"latitude": 1.5, "longitude": 2.5},
        "Device", None, "", {}
    )
    assert called["method"] == "POST"
    assert called["content"] == "raw:1.5"


def test_forward_to_custom_body_type_form(monkeypatch):
    """body_type=form sends form data (lines 143-147)."""
    called = {}

    def fake_request(method, url, headers, timeout, content, data):
        called["data"] = data
        class Resp:
            status_code = 200
            text = "ok"
            def raise_for_status(self): pass
        return Resp()

    monkeypatch.setattr("webui.forwarders.custom.httpx.request", fake_request)
    custom.forward_to_custom(
        {"url": "http://x", "body_type": "form", "body": "a=1\nb={{latitude}}", "method": "POST"},
        {"latitude": 1.5, "longitude": 2.5},
        "Device", None, "", {}
    )
    assert called["data"]["a"] == "1"
    assert called["data"]["b"] == "1.5"


def test_forward_to_custom_response_out(monkeypatch):
    """response_out populated with status and body."""

    class Resp:
        status_code = 201
        text = "created"
        def raise_for_status(self): pass

    def fake_request(*a, **kw):
        return Resp()

    monkeypatch.setattr("webui.forwarders.custom.httpx.request", fake_request)
    out = {}
    custom.forward_to_custom(
        {"url": "http://x"}, {"latitude": 1, "longitude": 2},
        "Device", None, "", {}, response_out=out
    )
    assert out["status_code"] == 201
    assert out["body"] == "created"


def test_forward_to_custom_truncates_long_response(monkeypatch):
    """Long response body truncated."""

    long_body = "x" * 3000

    class Resp:
        status_code = 200
        text = long_body
        def raise_for_status(self): pass

    def fake_request(*a, **kw):
        return Resp()

    monkeypatch.setattr("webui.forwarders.custom.httpx.request", fake_request)
    out = {}
    custom.forward_to_custom(
        {"url": "http://x"}, {"latitude": 1, "longitude": 2},
        "Device", None, "", {}, response_out=out
    )
    assert len(out["body"]) == custom.MAX_LOGGED_RESPONSE_CHARS + len("... (truncated)")
    assert out["body"].endswith("... (truncated)")


def test_forward_to_custom_raise_for_status(monkeypatch):
    """Non-2xx raises after capturing response."""
    import httpx

    class Resp:
        status_code = 400
        text = "bad request"
        def raise_for_status(self):
            raise httpx.HTTPStatusError("400", request=None, response=self)

    def fake_request(*a, **kw):
        return Resp()

    monkeypatch.setattr("webui.forwarders.custom.httpx.request", fake_request)
    out = {}
    with pytest.raises(httpx.HTTPStatusError):
        custom.forward_to_custom(
            {"url": "http://x"}, {"latitude": 1, "longitude": 2},
            "Device", None, "", {}, response_out=out
        )
    assert out["status_code"] == 400
    assert out["body"] == "bad request"


def test_preview_request():
    """preview_request returns dry-run dict (lines 168-173)."""
    endpoint_cfg = {
        "url": "http://x?lat={{latitude}}",
        "method": "POST",
        "headers": {"X-Test": "{{device_name}}"},
        "body_type": "json",
        "body": '{"lat": "{{latitude}}"}',
    }
    location = {"latitude": 1.5, "longitude": 2.5, "time": 123}
    result = custom.preview_request(endpoint_cfg, location, "My Device")

    assert result["method"] == "POST"
    assert result["url"] == "http://x?lat=1.5"
    assert result["headers"]["X-Test"] == "My Device"
    assert result["body_type"] == "json"
    assert result["body"] == '{"lat": "1.5"}'


def test_preview_request_default_method():
    """Default method is GET."""
    result = custom.preview_request({"url": "http://x"}, {"latitude": 1})
    assert result["method"] == "GET"


def test_preview_request_body_type_none():
    """body_type=none -> empty body."""
    result = custom.preview_request(
        {"url": "http://x", "body_type": "none", "body": "should not appear"},
        {"latitude": 1}
    )
    assert result["body"] == ""


def test_forward_to_custom_sends_a_non_ascii_header_as_utf8(monkeypatch):
    """A header templated from a tracker's name mustn't raise before sending."""
    received = {}

    def handler(request):
        received["headers"] = request.headers.raw
        return httpx.Response(200)

    def request_via_mock(method, url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.request(method, url, **kwargs)

    monkeypatch.setattr(custom.httpx, "request", request_via_mock)
    assert custom.forward_to_custom(
        {"method": "POST", "url": "http://x/", "headers": {"X-Limit-D": "{{device_alias}}"}},
        {"latitude": 1.0, "longitude": 2.0}, device_alias="Clés",
    ) is True
    assert (b"X-Limit-D", "Clés".encode()) in received["headers"]
