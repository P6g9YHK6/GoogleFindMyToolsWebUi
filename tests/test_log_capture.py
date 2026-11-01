"""Tests for webui/log_capture.py - system log capture handler."""

import logging
import logging.handlers

from webui import system_log_store
from webui.log_capture import _SystemLogHandler, configure_log_capture


def test_system_log_handler_emit_stores_entry(monkeypatch):
    """emit() stores the formatted record in system_log_store."""
    captured = []

    def fake_append(level, logger_name, message, when):
        captured.append((level, logger_name, message, when))

    monkeypatch.setattr(system_log_store, "append", fake_append)

    handler = _SystemLogHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    assert len(captured) == 1
    level, logger_name, message, when = captured[0]
    assert level == "INFO"
    assert logger_name == "test.logger"
    assert message == "INFO: Test message"
    assert isinstance(when, int)


def test_system_log_handler_skips_noisy_loggers(monkeypatch):
    """emit() skips loggers in _NOISY_LOGGERS."""
    captured = []
    monkeypatch.setattr(system_log_store, "append", lambda *a, **k: captured.append((*a, *k.values())))

    handler = _SystemLogHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET /health 200",
        args=(),
        exc_info=None,
    )

    handler.emit(record)

    assert len(captured) == 0


def test_system_log_handler_format_error(monkeypatch):
    """emit() calls handleError on format exception."""
    handler = _SystemLogHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # A record that will raise during format (e.g. bad % formatting)
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Test %s",
        args=(),  # Missing argument - will raise in format
        exc_info=None,
    )

    # Should not raise, handleError is called internally
    handler.emit(record)


def test_configure_log_capture_idempotent():
    """configure_log_capture() can be called multiple times without duplicating handlers."""
    root = logging.getLogger()
    
    # Clean up any existing handlers from previous tests
    for h in list(root.handlers):
        if isinstance(h, _SystemLogHandler):
            root.removeHandler(h)
    
    h1 = configure_log_capture(logging.INFO)
    assert h1 in root.handlers
    assert len([h for h in root.handlers if isinstance(h, _SystemLogHandler)]) == 1

    h2 = configure_log_capture(logging.INFO)
    assert h2 in root.handlers
    assert len([h for h in root.handlers if isinstance(h, _SystemLogHandler)]) == 1
    # Handler is replaced each call (not accumulated), so may be different object
    # but only one exists at a time
    assert len([h for h in root.handlers if isinstance(h, _SystemLogHandler)]) == 1

    # Cleanup
    root.removeHandler(h2)


def test_configure_log_capture_custom_level():
    """configure_log_capture() respects the level argument."""
    root = logging.getLogger()
    
    for h in list(root.handlers):
        if isinstance(h, _SystemLogHandler):
            root.removeHandler(h)

    handler = configure_log_capture(logging.DEBUG)
    assert handler.level == logging.DEBUG

    root.removeHandler(handler)


def test_configure_log_capture_replaces_existing():
    """configure_log_capture() replaces any existing _SystemLogHandler."""
    root = logging.getLogger()
    
    for h in list(root.handlers):
        if isinstance(h, _SystemLogHandler):
            root.removeHandler(h)

    h1 = configure_log_capture(logging.INFO)
    assert h1.level == logging.INFO

    h2 = configure_log_capture(logging.WARNING)
    assert h2.level == logging.WARNING
    # Only one handler exists at a time (old replaced by new)
    assert len([h for h in root.handlers if isinstance(h, _SystemLogHandler)]) == 1

    root.removeHandler(h2)