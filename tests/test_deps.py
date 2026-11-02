"""Tests for webui/deps.py - blocking call wrappers and coalesced locates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webui import config, deps


async def test_run_blocking_executes_in_executor(monkeypatch):
    """run_blocking dispatches to thread pool executor."""
    called = {}

    def sync_func(x, y=1):
        called["args"] = (x, y)
        return x + y

    result = await deps.run_blocking(sync_func, 5, y=2)
    assert result == 7
    assert called["args"] == (5, 2)


async def test_run_blocking_propagates_exception(monkeypatch):
    """run_blocking propagates exceptions from the sync function."""

    def failing_func():
        raise ValueError("sync error")

    with pytest.raises(ValueError, match="sync error"):
        await deps.run_blocking(failing_func)


async def test_locate_device_demo_mode(monkeypatch):
    """locate_device returns fake data in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    result = await deps.locate_device("canonic-1", "Test Device")
    # Should return demo data fake result
    assert result is not None


async def test_locate_device_with_capture_demo_mode(monkeypatch):
    """locate_device_with_capture returns fake data in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    locations, capture = await deps.locate_device_with_capture("canonic-1", "Test Device")
    assert locations is not None
    assert isinstance(capture, dict)


async def test_locate_device_uses_semaphore(monkeypatch):
    """locate_device uses semaphore for concurrency control."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_get_location = AsyncMock(return_value={"locations": []})
    
    # Patch the class method on the LocateCoalescer class
    with patch("webui.locate_coalescer.LocateCoalescer.get_or_fetch", new_callable=AsyncMock, return_value={"locations": []}) as mock_coalescer:
        with patch("webui.deps.get_location_data_for_device", mock_get_location):
            await deps.locate_device("canonic-1", "Test")
    
    # Should call the coalescer
    mock_coalescer.assert_called_once()


async def test_locate_device_with_capture_bypasses_coalescer(monkeypatch):
    """locate_device_with_capture bypasses coalescer (uses semaphore directly)."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_get_location = AsyncMock(return_value={"locations": []})
    
    with patch("webui.deps.get_location_data_for_device", mock_get_location):
        await deps.locate_device_with_capture("canonic-1", "Test")
    
    # Should call get_location_data_for_device directly (not through coalescer)
    mock_get_location.assert_called_once()


async def test_locate_device_with_capture_capture_dict(monkeypatch):
    """locate_device_with_capture passes capture dict to get_location_data_for_device."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    capture = {}
    mock_get_location = AsyncMock(return_value={"locations": []})
    
    with patch("webui.deps.get_location_data_for_device", mock_get_location):
        await deps.locate_device_with_capture("canonic-1", "Test", capture=capture)
    
    # Check that capture was passed as the last positional arg
    call_args = mock_get_location.call_args
    assert len(call_args[0]) == 4  # canonic_id, name, timeout, capture
    assert call_args[0][3] is not None


async def test_set_sound_demo_mode(monkeypatch):
    """set_sound returns fake data in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    result = await deps.set_sound("canonic-1", True)
    assert result is not None


async def test_register_tracker_demo_mode(monkeypatch):
    """register_tracker returns fake data in demo mode."""
    monkeypatch.setattr(config, "DEMO_MODE", True)
    result = await deps.register_tracker()
    assert result is not None
    # Check default values
    assert "eid_hex" in result


async def test_register_tracker_invalidates_cache(monkeypatch):
    """register_tracker invalidates device list cache on success."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_register = MagicMock(return_value={"eid_hex": "abc123"})
    mock_invalidate = MagicMock()
    
    with patch("webui.deps.register_esp32", mock_register), \
         patch("webui.deps.device_list_cache.invalidate", mock_invalidate):
        await deps.register_tracker()
    
    mock_register.assert_called_once()
    mock_invalidate.assert_called_once()


async def test_register_tracker_propagates_exception(monkeypatch):
    """register_tracker propagates exception from register_esp32."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    def failing_register(*args, **kwargs):
        raise RuntimeError("registration failed")
    
    with patch("webui.deps.register_esp32", failing_register):
        with pytest.raises(RuntimeError, match="registration failed"):
            await deps.register_tracker()


async def test_set_sound_normal_mode(monkeypatch):
    """set_sound calls play_sound in normal mode."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_play = MagicMock(return_value={"result": "ok"})
    
    with patch("webui.deps.play_sound", mock_play):
        result = await deps.set_sound("canonic-1", True)
    
    mock_play.assert_called_once_with("canonic-1", True)
    assert result == {"result": "ok"}


async def test_set_sound_propagates_exception(monkeypatch):
    """set_sound propagates exception from play_sound."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    def failing_play(*args, **kwargs):
        raise RuntimeError("sound failed")
    
    with patch("webui.deps.play_sound", failing_play):
        with pytest.raises(RuntimeError, match="sound failed"):
            await deps.set_sound("canonic-1", True)


async def test_register_tracker_normal_mode(monkeypatch):
    """register_tracker calls register_esp32 in normal mode."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_register = MagicMock(return_value={"eid_hex": "abc123"})
    
    with patch("webui.deps.register_esp32", mock_register):
        result = await deps.register_tracker(
            display_name="Test",
            device_type="DEVICE_TYPE_BEACON",
            manufacturer_name="Test",
            model_name="Model",
        )
    
    mock_register.assert_called_once()
    assert result["eid_hex"] == "abc123"


async def test_register_tracker_invalidates_cache_with_custom_params(monkeypatch):
    """register_tracker invalidates cache even with custom params."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_register = MagicMock(return_value={"eid_hex": "abc123"})
    mock_invalidate = MagicMock()
    
    with patch("webui.deps.register_esp32", mock_register), \
         patch("webui.deps.device_list_cache.invalidate", mock_invalidate):
        result = await deps.register_tracker(display_name="Custom")
    
    mock_register.assert_called_once()
    mock_invalidate.assert_called_once()
    assert result["eid_hex"] == "abc123"


async def test_register_tracker_uses_defaults(monkeypatch):
    """register_tracker uses default values when not provided."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    
    mock_register = MagicMock(return_value={"eid_hex": "abc123"})
    
    with patch("webui.deps.register_esp32", mock_register):
        result = await deps.register_tracker()
    
    mock_register.assert_called_once()
    call_args = mock_register.call_args
    assert result["eid_hex"] == "abc123"
    assert call_args.kwargs["display_name"] == "GoogleFindMyTools µC"
    assert call_args.kwargs["device_type"] == "DEVICE_TYPE_BEACON"
    assert call_args.kwargs["manufacturer_name"] == "GoogleFindMyTools"
    assert call_args.kwargs["model_name"] == "µC"


async def test_query_gate_is_configured(monkeypatch):
    """query_gate is configured with settings_store.load."""
    from webui.deps import query_gate, query_throttle
    
    assert query_gate is query_throttle
    assert callable(query_throttle.configure)


async def test_executor_exists():
    """_executor exists and is configured."""
    from concurrent.futures import ThreadPoolExecutor

    from webui.deps import _executor
    
    assert isinstance(_executor, ThreadPoolExecutor)
    assert _executor._max_workers == 32
    assert _executor._thread_name_prefix == "gfmt-blocking"


async def test_locate_semaphore_exists():
    """_locate_semaphore exists and is configured."""
    from asyncio import Semaphore

    from webui.deps import _locate_semaphore
    
    assert isinstance(_locate_semaphore, Semaphore)
    # Default LOCATE_CONCURRENCY is 5
    assert _locate_semaphore._value == 5