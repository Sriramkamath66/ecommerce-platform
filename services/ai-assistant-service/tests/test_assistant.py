"""
Tests for the AI Assistant Service.

Run with:  pytest tests/ -v --asyncio-mode=auto
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

USER_ID = "user-123"
SESSION_ID = "session-abc"


# ---------------------------------------------------------------------------
# MockStream — simulates the Anthropic async streaming context manager
# ---------------------------------------------------------------------------

class _TextStreamGen:
    """Async generator that yields pre-configured text chunks."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class MockStream:
    """Minimal mock for anthropic_client.messages.stream() context manager."""

    def __init__(self, chunks: list[str], stop_reason: str = "end_turn") -> None:
        self._chunks = chunks
        self._stop_reason = stop_reason
        self.text_stream = _TextStreamGen(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get_final_message(self):
        msg = MagicMock()
        msg.stop_reason = self._stop_reason
        # Build content blocks for a simple text response
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "".join(self._chunks)
        msg.content = [text_block]
        return msg


class MockStreamToolUse:
    """
    First call returns stop_reason='tool_use' with a tool_use block.
    Subsequent calls return stop_reason='end_turn'.
    """

    _call_count: int = 0

    def __init__(self, tool_name: str, tool_input: dict, tool_id: str = "tool-1") -> None:
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._tool_id = tool_id
        MockStreamToolUse._call_count = 0

    # Re-use the same instance as a callable via __call__ so it can be patched
    # on the anthropic_client.messages.stream side.

    def build_stream(self):
        MockStreamToolUse._call_count += 1
        call_num = MockStreamToolUse._call_count

        class _ToolStream:
            def __init__(self_, chunks, stop_reason, content_blocks):
                self_.text_stream = _TextStreamGen(chunks)
                self_._stop_reason = stop_reason
                self_._content_blocks = content_blocks

            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *args):
                pass

            async def get_final_message(self_):
                msg = MagicMock()
                msg.stop_reason = self_._stop_reason
                msg.content = self_._content_blocks
                return msg

        if call_num == 1:
            tool_block = MagicMock()
            tool_block.type = "tool_use"
            tool_block.name = self._tool_name
            tool_block.input = self._tool_input
            tool_block.id = self._tool_id
            tool_block.model_dump.return_value = {
                "type": "tool_use",
                "name": self._tool_name,
                "input": self._tool_input,
                "id": self._tool_id,
            }
            return _ToolStream([], "tool_use", [tool_block])
        else:
            text_block = MagicMock()
            text_block.type = "text"
            text_block.text = "Here are the results!"
            return _ToolStream(["Here are the results!"], "end_turn", [text_block])


# ===========================================================================
# session_service tests
# ===========================================================================

@pytest.mark.asyncio
async def test_create_session():
    """create_session stores metadata and returns a UUID string."""
    from app.services.session_service import create_session

    mock_redis = AsyncMock()
    session_id = await create_session(mock_redis, USER_ID, session_ttl=3600)

    assert isinstance(session_id, str)
    assert len(session_id) == 36  # UUID4 format

    # Verify SET was called at least twice (meta + history keys)
    assert mock_redis.set.call_count == 2
    calls = [c.args[0] for c in mock_redis.set.call_args_list]
    assert any(session_id in k and "meta" in k for k in calls)
    assert any(session_id in k and "history" in k for k in calls)


@pytest.mark.asyncio
async def test_get_history_empty():
    """get_history returns [] when the Redis key does not exist."""
    from app.services.session_service import get_history

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # key absent

    result = await get_history(mock_redis, SESSION_ID)
    assert result == []


@pytest.mark.asyncio
async def test_append_message_sliding_window():
    """Sliding window trims history to MAX_HISTORY_TURNS * 2 messages."""
    from app.services.session_service import append_message

    # Pre-populate with exactly MAX_HISTORY_TURNS * 2 messages (the limit)
    max_turns = 3  # small number for the test
    existing = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
        for i in range(max_turns * 2)
    ]
    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps(existing)

    # Append one more message — should trigger truncation
    await append_message(
        mock_redis,
        SESSION_ID,
        role="user",
        content="new message",
        max_turns=max_turns,
        session_ttl=3600,
    )

    # Capture what was written back
    written_raw = mock_redis.set.call_args_list[0].args[1]
    written = json.loads(written_raw)

    assert len(written) == max_turns * 2
    # Last message is the one we just appended
    assert written[-1]["content"] == "new message"


# ===========================================================================
# AssistantService tests
# ===========================================================================

@pytest.mark.asyncio
async def test_process_message_simple():
    """process_message yields text chunks from the Anthropic stream."""
    from app.services.assistant_service import AssistantService

    mock_rag = AsyncMock()
    mock_rag.retrieve_context.return_value = []
    mock_rag.format_context.return_value = "No products found."

    stream_instance = MockStream(["Hello ", "world!"], stop_reason="end_turn")
    mock_anthropic = MagicMock()
    mock_anthropic.messages.stream.return_value = stream_instance

    mock_settings = MagicMock()
    mock_settings.LLM_MODEL = "claude-test"

    service = AssistantService(mock_anthropic, mock_rag, AsyncMock(), mock_settings)

    gen = await service.process_message(SESSION_ID, USER_ID, "hello", [])
    chunks = [chunk async for chunk in gen]

    assert chunks == ["Hello ", "world!"]


@pytest.mark.asyncio
async def test_process_message_with_tool_use():
    """process_message handles tool_use stop_reason, calls execute_tool, then continues."""
    from app.services.assistant_service import AssistantService

    mock_rag = AsyncMock()
    mock_rag.retrieve_context.return_value = []
    mock_rag.format_context.return_value = "No products found."

    tool_stream_factory = MockStreamToolUse("search_products", {"query": "shoes"})
    call_count = [0]

    def make_stream(**kwargs):
        return tool_stream_factory.build_stream()

    mock_anthropic = MagicMock()
    mock_anthropic.messages.stream.side_effect = make_stream

    mock_settings = MagicMock()
    mock_settings.LLM_MODEL = "claude-test"
    mock_settings.ORDER_SERVICE_URL = "http://order-service"
    mock_settings.INVENTORY_SERVICE_URL = "http://inventory-service"

    service = AssistantService(mock_anthropic, mock_rag, AsyncMock(), mock_settings)

    gen = await service.process_message(SESSION_ID, USER_ID, "find me shoes", [])
    chunks = [chunk async for chunk in gen]

    # Second iteration yields text
    assert "Here are the results!" in chunks
    # Anthropic was called twice: once for tool_use, once for end_turn
    assert mock_anthropic.messages.stream.call_count == 2


@pytest.mark.asyncio
async def test_execute_tool_search_products():
    """execute_tool('search_products') calls RAG and returns a formatted string."""
    from app.services.assistant_service import AssistantService

    mock_rag = AsyncMock()
    mock_rag.retrieve_context.return_value = [
        {
            "product_id": "p1",
            "name": "Running Shoe",
            "description": "Fast shoe",
            "price": 99.99,
            "score": 0.95,
        }
    ]
    mock_rag.format_context.return_value = "1. Running Shoe (ID: p1) - $99.99\n   Fast shoe\n   Similarity: 0.95"

    service = AssistantService(MagicMock(), mock_rag, AsyncMock(), MagicMock())
    result = await service.execute_tool("search_products", {"query": "shoes", "limit": 3}, USER_ID)

    assert "Running Shoe" in result
    mock_rag.retrieve_context.assert_awaited_once_with("shoes", 3)


@pytest.mark.asyncio
async def test_execute_tool_get_order_status():
    """execute_tool('get_order_status') calls the order service and returns JSON."""
    from app.services.assistant_service import AssistantService

    order_data = {"order_id": "ord-1", "status": "shipped"}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps(order_data)
    mock_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.get.return_value = mock_response

    mock_settings = MagicMock()
    mock_settings.ORDER_SERVICE_URL = "http://order-service:8003"

    service = AssistantService(MagicMock(), AsyncMock(), mock_http, mock_settings)
    result = await service.execute_tool("get_order_status", {"order_id": "ord-1"}, USER_ID)

    assert "shipped" in result
    mock_http.get.assert_awaited_once_with(
        "http://order-service:8003/orders/ord-1", timeout=10.0
    )


@pytest.mark.asyncio
async def test_execute_tool_check_inventory():
    """execute_tool('check_inventory') calls the inventory service and returns JSON."""
    from app.services.assistant_service import AssistantService

    stock_data = {"product_id": "prod-5", "in_stock": True, "quantity": 42}
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = json.dumps(stock_data)
    mock_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.get.return_value = mock_response

    mock_settings = MagicMock()
    mock_settings.INVENTORY_SERVICE_URL = "http://inventory-service:8004"

    service = AssistantService(MagicMock(), AsyncMock(), mock_http, mock_settings)
    result = await service.execute_tool("check_inventory", {"product_id": "prod-5"}, USER_ID)

    assert "42" in result
    mock_http.get.assert_awaited_once_with(
        "http://inventory-service:8004/inventory/prod-5", timeout=10.0
    )


# ===========================================================================
# Chat router / endpoint tests
# ===========================================================================

def _build_test_app():
    """Return a FastAPI app with mocked state for endpoint tests."""
    from app.main import app

    # Override app state with test doubles — don't start real clients
    mock_redis = AsyncMock()
    mock_settings = MagicMock()
    mock_settings.SESSION_TTL = 3600
    mock_settings.MAX_HISTORY_TURNS = 20

    app.state.redis = mock_redis
    app.state.settings = mock_settings
    return app, mock_redis


def _valid_hs256_token() -> str:
    """Return a test JWT signed with HS256 / 'test-secret'."""
    import jwt
    import time

    payload = {"sub": USER_ID, "exp": int(time.time()) + 3600}
    return jwt.encode(payload, "test-secret", algorithm="HS256")


def test_chat_endpoint_streaming():
    """POST /chat/sessions/{id}/messages streams SSE events."""
    from app.main import app
    from app.services.assistant_service import AssistantService

    # --- mock Redis ---
    mock_redis = AsyncMock()
    # get_session_user → owner matches USER_ID
    mock_redis.get.side_effect = [
        # First call: meta key for ownership check
        json.dumps({"user_id": USER_ID, "created_at": "2024-01-01T00:00:00+00:00"}),
        # Second call: history for get_history
        json.dumps([]),
        # Third & fourth calls: get_history inside append_message (×2)
        json.dumps([]),
        json.dumps([{"role": "user", "content": "hi"}]),
    ]
    app.state.redis = mock_redis

    # --- mock settings ---
    mock_settings = MagicMock()
    mock_settings.SESSION_TTL = 3600
    mock_settings.MAX_HISTORY_TURNS = 20
    app.state.settings = mock_settings

    # --- mock assistant service ---
    async def fake_process_message(session_id, user_id, message, history):
        yield "Hello "
        yield "world!"

    mock_assistant = MagicMock()
    mock_assistant.process_message = AsyncMock(side_effect=fake_process_message)
    app.state.assistant_service = mock_assistant

    token = _valid_hs256_token()
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app, raise_server_exceptions=True) as client:
        with client.stream(
            "POST",
            f"/chat/sessions/{SESSION_ID}/messages",
            json={"content": "hi"},
            headers=headers,
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            raw = b"".join(resp.iter_bytes()).decode()

    # Verify SSE events are present
    assert 'text_delta' in raw
    assert 'Hello ' in raw
    assert 'world!' in raw
    assert 'message_stop' in raw
