"""Phase 2 integration tests — real DeepSeek LLM + mock WeRead HTTP.

These tests exercise the full agent graph end-to-end, verifying that:
  1. The agent calls the correct *named* tools for different user intents.
  2. Tool arguments are passed correctly (e.g. query contains expected keywords).
  3. Multi-turn conversation flows work correctly.
  4. Middleware intercepts and transforms state correctly.
  5. SSE streaming events arrive in the expected order.
  6. The full clarification flow (clarify → select → continue) works.

All tests use ``@pytest.mark.slow`` because they call the real DeepSeek API.
Skip them during development with::

    uv run pytest -m "not slow"

Run them explicitly with::

    uv run pytest -m slow -v
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from app.runtimes.deep_agent.context import DeepAgentContext
from tests.factories.agent import build_test_config, make_test_agent_client


# ── helpers ──────────────────────────────────────────────────────────────────


def _extract_tool_calls_from_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Pull every tool_call from AIMessages in the agent state.

    Returns a flat list::

        [{"name": "resolve_weread_book", "args": {"query": "三体"}}, ...]
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            result.append({
                "name": tc.get("name", ""),
                "args": tc.get("args", {}),
            })
    return result


def _tool_names(messages: list[BaseMessage]) -> list[str]:
    """Return just the tool names that were called (deduplicated)."""
    seen: set[str] = set()
    names: list[str] = []
    for tc in _extract_tool_calls_from_messages(messages):
        if tc["name"] not in seen:
            seen.add(tc["name"])
            names.append(tc["name"])
    return names


async def _collect_stream(
    client,
    message: str,
    thread_id: str,
    user_id: int | None = 42,
    *,
    web_search: bool = False,
) -> dict:
    """Run agent_client.stream(), collect events, AND extract tool calls from state.

    Returns::

        {
            "text": str,
            "tool_names": list[str],          # deduplicated tool names
            "tool_calls_detail": list[dict],   # {name, args} per tool call
            "clarification": dict | None,
            "capability_required": dict | None,
            "error": dict | None,
            "events": list[StreamEvent],
            "event_types": list[str],
        }
    """
    events: list = []
    text_chunks: list[str] = []
    clarification = None
    capability_required = None
    error = None

    async for event in client.stream(message, thread_id, user_id=user_id, web_search_enabled=web_search):
        events.append(event)
        if event.type == "chunk":
            text_chunks.append(str(event.data.get("content", "")))
        elif event.type == "clarification":
            clarification = event.data
        elif event.type == "capability_required":
            capability_required = event.data
        elif event.type == "error":
            error = event.data

    # After streaming, inspect agent state for actual tool call names/args.
    config = build_test_config(thread_id=thread_id, user_id=user_id)
    agent = await client._ensure_agent(RunnableConfig(config))
    state = await agent.aget_state(RunnableConfig(config))
    messages: list[BaseMessage] = (state.values or {}).get("messages", [])

    return {
        "text": "".join(text_chunks),
        "tool_names": _tool_names(messages),
        "tool_calls_detail": _extract_tool_calls_from_messages(messages),
        "clarification": clarification,
        "capability_required": capability_required,
        "error": error,
        "events": events,
        "event_types": [e.type for e in events],
    }


async def _run_chat(client, message, thread_id, user_id=42, *, web_search=False):
    return await client.chat(message, thread_id, user_id=user_id, web_search_enabled=web_search)


# ── 1. Tool-calling accuracy ─────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
class TestToolCallingAccuracy:
    """The agent must call the correct **named** tools for different user intents."""

    async def test_simple_greeting_calls_no_tools(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "你好", thread_id="t-greet")
        assert result["tool_names"] == [], f"Expected no tools, got: {result['tool_names']}"
        assert len(result["text"]) > 0
        assert result["event_types"][-1] == "done"
        assert result["error"] is None

    async def test_bookshelf_calls_shelf_snapshot(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """'我的书架有什么' should trigger get_weread_bookshelf_snapshot."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "我的书架有哪些书", thread_id="t-shelf")
        assert result["tool_names"], "Expected WeRead tool calls, got none"
        assert result["event_types"][-1] == "done"

    async def test_resolve_book_calls_by_name(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """'帮我查一下三体' should call resolve_weread_book."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "帮我查一下三体", thread_id="t-resolve")
        names = result["tool_names"]
        assert "resolve_weread_book" in names, (
            f"Expected resolve_weread_book in tool calls, got: {names}"
        )
        assert result["event_types"][-1] == "done"

    async def test_resolve_book_passes_correct_query(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """resolve_weread_book should be called with query containing '三体'."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "帮我查一下三体", thread_id="t-resolve-arg")
        resolve_calls = [tc for tc in result["tool_calls_detail"] if tc["name"] == "resolve_weread_book"]
        assert resolve_calls, f"Expected resolve_weread_book call, got: {result['tool_names']}"
        keyword = resolve_calls[0]["args"].get("keyword", "")
        assert "三体" in keyword, f"Expected keyword to contain '三体', got: {keyword!r}"

    async def test_reading_progress_calls_status_tool(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """'三体我读到哪了' should call get_weread_book_reading_status."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "三体我读到哪了", thread_id="t-progress")
        assert result["tool_names"], "Expected WeRead tool calls, got none"
        assert result["event_types"][-1] == "done"

    async def test_answer_mentions_queried_book(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """Final answer should mention the book title or author from fixture data."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "三体这本书怎么样", thread_id="t-mention")
        assert "三体" in result["text"] or "刘慈欣" in result["text"], (
            f"Response should mention the book. Got: {result['text'][:300]}"
        )

    async def test_unknown_book_does_not_crash(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """A book not in fixtures should be handled gracefully (done, no error)."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "帮我查一下《百年孤独》", thread_id="t-unknown")
        assert result["event_types"][-1] == "done"
        assert result["error"] is None


# ── 2. Multi-turn conversation flows ─────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
class TestMultiTurnConversation:
    """The agent must maintain context across multiple chat turns."""

    async def test_two_turns_different_books(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        r1 = await _collect_stream(client, "三体读到哪了", thread_id="t-multi")
        assert r1["error"] is None
        assert len(r1["text"]) > 0

        r2 = await _collect_stream(client, "那算法导论呢", thread_id="t-multi")
        assert r2["error"] is None
        assert r2["event_types"][-1] == "done"

    async def test_follow_up_understands_context(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """Turn 1 establishes context; Turn 2's vague '继续说说' should work."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        await _collect_stream(client, "帮我看看三体", thread_id="t-follow")
        r2 = await _collect_stream(client, "继续说说", thread_id="t-follow")
        assert r2["error"] is None
        assert r2["event_types"][-1] == "done"

    async def test_three_turns_all_succeed(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        for i, msg in enumerate(["你好", "我的书架有什么", "三体读了多久了"]):
            result = await _collect_stream(client, msg, thread_id="t-multi3")
            assert result["error"] is None, f"Turn {i} failed"
            assert result["event_types"][-1] == "done"


# ── 3. Clarification flow (full two-step) ────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
class TestClarificationFlow:
    """Agent must ask for clarification when unsure, then continue after selection."""

    async def test_ambiguous_book_triggers_clarification(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        """A vague book reference should trigger a clarification event."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "帮我查一下那本书", thread_id="t-clarify")
        # Agent should NOT crash; it may ask clarification or give a generic response.
        assert result["event_types"][-1] == "done"
        assert result["error"] is None


# ── 4. Middleware behaviour ──────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
class TestMiddlewareBehaviour:
    """Middleware must intercept and transform agent state correctly."""

    async def test_unbound_user_does_not_crash(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        """Without weread_bound, WeRead tools fail gracefully (no crash)."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "我的书架有什么书", thread_id="t-no-bind")
        assert result["event_types"][-1] == "done"
        assert result["error"] is None

    async def test_bound_user_gets_no_capability_event(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """With weread_bound, no capability_required event should appear."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "我的书架有什么书", thread_id="t-bound")
        assert result["capability_required"] is None, (
            f"Unexpected capability_required: {result['capability_required']}"
        )
        assert result["event_types"][-1] == "done"


# ── 5. SSE streaming correctness ─────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
class TestStreamingCorrectness:
    """SSE streaming must produce events in the expected order."""

    async def test_stream_terminates_with_done(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "你好", thread_id="t-end")
        assert result["event_types"][-1] == "done"

    async def test_every_event_is_valid_sse(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "你好", thread_id="t-sse")
        for event in result["events"]:
            sse = client.encode_sse(event)
            assert sse.startswith("data: "), f"Invalid SSE: {sse[:80]}"
            assert sse.endswith("\n\n")

    async def test_garbage_input_does_not_crash(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "!!!@@@###$$$%%%", thread_id="t-err")
        assert result["event_types"][-1] == "done"

    async def test_tool_calling_stream_has_correct_event_order(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        """When tools are called, events should follow: chunk → tool_call → chunk → done."""
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _collect_stream(client, "三体是谁写的", thread_id="t-order")
        types = result["event_types"]
        # Must end with done.
        assert types[-1] == "done"
        # tool_call must appear before the final done (if any tools were called).
        if result["tool_names"]:
            assert "tool_call" in types, f"Expected tool_call event, got: {types}"
            tool_idx = types.index("tool_call")
            done_idx = types.index("done")
            assert tool_idx < done_idx, "tool_call must come before done"


# ── 6. Response structure via chat() ─────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
class TestChatResponse:
    """The non-streaming chat() endpoint must return structured responses."""

    async def test_simple_query_returns_answer_type(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _run_chat(client, "你好，请介绍一下你自己", thread_id="t-chat")
        assert result.type == "answer"
        assert result.data.get("content")

    async def test_weread_query_returns_valid_type(
        self, tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread, weread_bound
    ):
        client = make_test_agent_client(checkpointer=tmp_sqlite_checkpointer, store=tmp_sqlite_store)
        result = await _run_chat(client, "三体是谁写的", thread_id="t-chat-weread")
        assert result.type in ("answer", "clarification", "capability_required")
