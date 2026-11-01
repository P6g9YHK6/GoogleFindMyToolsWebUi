"""Tests for webui/templating.py - template helpers and context processors."""


from webui.templating import (
    _format_build_date,
    _format_uptime,
    _format_utc_offset,
    _local_utc_offset_min,
    ping_time,
)


def test_format_uptime():
    assert _format_uptime(30) == "< 1m"
    assert _format_uptime(60) == "1m"
    assert _format_uptime(61) == "1m"
    assert _format_uptime(3599) == "59m"
    assert _format_uptime(3600) == "1h 0m"
    assert _format_uptime(7200) == "2h 0m"
    assert _format_uptime(86400) == "1d 0h"
    assert _format_uptime(86400 + 3600) == "1d 1h"
    assert _format_uptime(172800 + 7200) == "2d 2h"


def test_format_build_date_utc_iso():
    # UTC timestamp with Z suffix
    raw = "2026-10-01T14:32:00Z"
    result = _format_build_date(raw)
    # Result is in local time, so exact output depends on TZ
    # Just verify it parses and produces a reasonable format
    assert result != ""
    assert len(result) > 5


def test_format_build_date_empty():
    assert _format_build_date("") == ""


def test_format_build_date_invalid():
    # Unparsable input returned as-is
    assert _format_build_date("not-a-date") == "not-a-date"


def test_format_build_date_with_offset():
    # ISO format with explicit offset
    raw = "2026-10-01T14:32:00+00:00"
    result = _format_build_date(raw)
    assert result != ""


def test_local_utc_offset_min():
    offset = _local_utc_offset_min()
    # Should be an integer (could be negative, zero, or positive)
    assert isinstance(offset, int)


def test_format_utc_offset():
    assert _format_utc_offset(0) == "UTC+00:00"
    assert _format_utc_offset(60) == "UTC+01:00"
    assert _format_utc_offset(-60) == "UTC-01:00"
    assert _format_utc_offset(120) == "UTC+02:00"
    assert _format_utc_offset(-330) == "UTC-05:30"  # India
    assert _format_utc_offset(570) == "UTC+09:30"   # Australia Central


def test_ping_time_none():
    assert ping_time(None) == ""
    assert ping_time(0) == ""


def test_ping_time_valid():
    # 2026-10-01 14:32:00 UTC
    ts = 1790865120
    result = ping_time(ts)
    # Format: "HH:MM · DD Mon YYYY"
    assert " · " in result
    parts = result.split(" · ")
    assert len(parts) == 2
    time_part, date_part = parts
    assert ":" in time_part
    assert " " in date_part


def test_ping_time_different_values():
    # Test a few different timestamps
    ts1 = 1700000000  # 2023-11-14
    ts2 = 1800000000  # 2027-01-15
    
    r1 = ping_time(ts1)
    r2 = ping_time(ts2)
    
    assert r1 != r2
    assert "Nov" in r1 or "11" in r1
    assert "Jan" in r2 or "01" in r2