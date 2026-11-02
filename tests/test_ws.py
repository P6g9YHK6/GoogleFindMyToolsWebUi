"""Tests for webui/ws.py - WebSocket connection manager."""

import asyncio
import json

from webui.ws import ConnectionManager


class MockWebSocket:
    """Mock FastAPI WebSocket for testing."""
    
    def __init__(self):
        self.accepted = False
        self.closed = False
        self.sent_messages = []
        self.close_code = None
    
    async def accept(self, subprotocol=None):
        self.accepted = True
    
    async def close(self, code=1000):
        self.closed = True
        self.close_code = code
    
    async def send_text(self, message: str):
        if self.closed:
            raise RuntimeError("WebSocket is closed")
        self.sent_messages.append(message)
    
    async def receive_text(self):
        raise asyncio.CancelledError("Mock receive_text not implemented")


async def test_connection_manager_connect():
    """connect() accepts the socket and adds it to the set."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    
    await manager.connect(ws)
    
    assert ws.accepted is True
    assert ws in manager._connections


async def test_connection_manager_disconnect():
    """disconnect() removes the socket from the set."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    
    await manager.connect(ws)
    await manager.disconnect(ws)
    
    assert ws not in manager._connections


async def test_connection_manager_disconnect_idempotent():
    """disconnect() on an unknown socket is a no-op (no error)."""
    manager = ConnectionManager()
    ws = MockWebSocket()
    
    await manager.disconnect(ws)  # Should not raise
    assert len(manager._connections) == 0


async def test_connection_manager_broadcast():
    """broadcast() sends the message to all connected sockets."""
    manager = ConnectionManager()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    
    await manager.connect(ws1)
    await manager.connect(ws2)
    
    await manager.broadcast({"type": "test", "value": 42})
    
    expected = json.dumps({"type": "test", "value": 42})
    assert ws1.sent_messages == [expected]
    assert ws2.sent_messages == [expected]


async def test_connection_manager_broadcast_empty():
    """broadcast() with no connections is a no-op."""
    manager = ConnectionManager()
    
    await manager.broadcast({"type": "test"})  # Should not raise


async def test_connection_manager_broadcast_removes_failed():
    """broadcast() disconnects sockets that fail to send."""
    manager = ConnectionManager()
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    ws2.closed = True  # Simulate a broken connection
    
    await manager.connect(ws1)
    await manager.connect(ws2)
    
    await manager.broadcast({"type": "test"})
    
    assert ws1 in manager._connections
    assert ws2 not in manager._connections  # Failed socket was removed


async def test_connection_manager_concurrent_access():
    """connect/disconnect/broadcast are safe under concurrent access."""
    manager = ConnectionManager()
    
    async def connector():
        ws = MockWebSocket()
        await manager.connect(ws)
        await asyncio.sleep(0.001)
        await manager.disconnect(ws)
    
    async def broadcaster():
        for _ in range(10):
            await manager.broadcast({"tick": True})
            await asyncio.sleep(0.001)
    
    await asyncio.gather(*(connector() for _ in range(5)), broadcaster())
    
    assert len(manager._connections) == 0


def test_multiple_managers_independent():
    """Each manager instance has its own connection set."""
    m1 = ConnectionManager()
    m2 = ConnectionManager()
    
    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    
    asyncio.run(m1.connect(ws1))
    asyncio.run(m2.connect(ws2))
    
    assert len(m1._connections) == 1
    assert len(m2._connections) == 1
    assert ws1 in m1._connections
    assert ws2 in m2._connections