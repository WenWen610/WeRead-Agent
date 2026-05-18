"""Tests for MarkdownMemoryMiddleware static methods."""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
import pytest

from app.features.markdown_memory.jobs import MarkdownMemoryJobStore
from app.features.markdown_memory.manager import markdown_memory_manager
from app.infrastructure.config import settings
from app.runtimes.deep_agent.middleware.markdown_memory_middleware import MarkdownMemoryMiddleware


def _make_middleware() -> MarkdownMemoryMiddleware:
    return MarkdownMemoryMiddleware()


class TestExtractText:
    def test_string(self):
        assert MarkdownMemoryMiddleware._extract_text("  hello  ") == "hello"

    def test_list_with_text_dicts(self):
        content = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
        assert MarkdownMemoryMiddleware._extract_text(content) == "hello world"

    def test_list_with_non_text(self):
        content = [{"type": "image_url", "image_url": {"url": "..."}}]
        assert MarkdownMemoryMiddleware._extract_text(content) == ""

    def test_none(self):
        assert MarkdownMemoryMiddleware._extract_text(None) == ""

    def test_empty_string(self):
        assert MarkdownMemoryMiddleware._extract_text("") == ""


class TestBuildQuery:
    def test_single_human_message(self):
        mw = _make_middleware()
        messages = [HumanMessage(content="what was my reading goal?")]
        result = mw._build_query(messages)
        assert "reading goal" in result

    def test_multiple_messages_only_human(self):
        mw = _make_middleware()
        messages = [
            HumanMessage(content="first query"),
            AIMessage(content="response"),
            HumanMessage(content="second query"),
        ]
        result = mw._build_query(messages)
        assert "second query" in result
        assert "response" not in result

    def test_truncates_to_240_chars(self):
        mw = _make_middleware()
        long_text = "word " * 100
        messages = [HumanMessage(content=long_text)]
        result = mw._build_query(messages)
        assert len(result) <= 240

    def test_empty_messages(self):
        mw = _make_middleware()
        assert mw._build_query([]) == ""

    def test_no_human_messages(self):
        mw = _make_middleware()
        messages = [AIMessage(content="response"), ToolMessage(content="result", tool_call_id="123")]
        assert mw._build_query(messages) == ""


class TestShouldSkip:
    def test_short_queries(self):
        mw = _make_middleware()
        assert mw._should_skip("ab") is True
        assert mw._should_skip("") is True
        assert mw._should_skip("  ") is True

    def test_filler_queries(self):
        mw = _make_middleware()
        for filler in ("好", "嗯", "谢谢", "继续", "ok", "yes", "no"):
            assert mw._should_skip(filler) is True, f"'{filler}' should be skipped"

    def test_normal_queries(self):
        mw = _make_middleware()
        assert mw._should_skip("what was my reading goal?") is False
        assert mw._should_skip("帮我查一下划线") is False

    def test_case_insensitive_filler(self):
        mw = _make_middleware()
        assert mw._should_skip("OK") is True
        assert mw._should_skip("Yes") is True


class TestResolveUserId:
    @staticmethod
    def _make_request(runtime_context=None):
        """Build a minimal ModelRequest-like object."""
        runtime = type("rt", (), {"context": runtime_context})()
        return type("req", (), {"runtime": runtime})()

    def test_valid_user_id(self):
        request = self._make_request(type("ctx", (), {"user_id": 42})())
        assert MarkdownMemoryMiddleware._resolve_user_id(request) == 42

    def test_none_user_id(self):
        request = self._make_request(type("ctx", (), {"user_id": None})())
        assert MarkdownMemoryMiddleware._resolve_user_id(request) is None

    def test_no_context(self):
        request = self._make_request(runtime_context=None)
        assert MarkdownMemoryMiddleware._resolve_user_id(request) is None


class TestSerializeMessage:
    def test_ai_message(self):
        msg = AIMessage(content="hello")
        result = MarkdownMemoryMiddleware._serialize_message(msg)
        assert result == {"role": "assistant", "content": "hello"}

    def test_human_message(self):
        msg = HumanMessage(content="query")
        result = MarkdownMemoryMiddleware._serialize_message(msg)
        assert result == {"role": "user", "content": "query"}

    def test_tool_message(self):
        msg = ToolMessage(content="result", tool_call_id="123")
        result = MarkdownMemoryMiddleware._serialize_message(msg)
        assert result is None

    def test_empty_content(self):
        msg = AIMessage(content="")
        result = MarkdownMemoryMiddleware._serialize_message(msg)
        assert result is None

    def test_human_with_text_list(self):
        msg = HumanMessage(content=[{"type": "text", "text": "multi part"}])
        result = MarkdownMemoryMiddleware._serialize_message(msg)
        assert result == {"role": "user", "content": "multi part"}


class TestCountUserTurns:
    def test_only_human(self):
        messages = [HumanMessage(content="a"), HumanMessage(content="b")]
        assert MarkdownMemoryMiddleware._count_user_turns(messages) == 2

    def test_mixed_messages(self):
        messages: list[BaseMessage] = [
            HumanMessage(content="a"),
            AIMessage(content="b"),
            HumanMessage(content="c"),
            ToolMessage(content="d", tool_call_id="123"),
        ]
        assert MarkdownMemoryMiddleware._count_user_turns(messages) == 2

    def test_no_human(self):
        messages = [AIMessage(content="b")]
        assert MarkdownMemoryMiddleware._count_user_turns(messages) == 0


class TestAutoMemoryPlanning:
    @pytest.mark.asyncio
    async def test_plans_interval_from_thread_cursor(self, tmp_path, monkeypatch):
        store = MarkdownMemoryJobStore(tmp_path / "memory_tasks.db")
        store.ensure_schema()
        monkeypatch.setattr(markdown_memory_manager, "job_store", store)
        monkeypatch.setattr(settings, "MARKDOWN_MEMORY_AUTO_INTERVAL", 2)

        completed_job_id, _ = store.enqueue_auto_memory_job(
            user_id=7,
            thread_id="thread-a",
            day_path="memory/2026-05-17.md",
            start_message_id=None,
            end_message_id="ai-1",
            start_index=0,
            end_index=2,
            message_hash="hash-completed",
            reason="interval",
            messages=[{"role": "user", "content": "old"}],
        )
        completed_job = store.claim_job(completed_job_id)
        assert completed_job is not None
        store.complete_job(completed_job)

        mw = _make_middleware()
        messages: list[BaseMessage] = [
            HumanMessage(content="old user", id="u-1"),
            AIMessage(content="old assistant", id="ai-1"),
            HumanMessage(content="new user 1", id="u-2"),
            AIMessage(content="new assistant 1", id="ai-2"),
            HumanMessage(content="new user 2", id="u-3"),
        ]

        plan = await mw._plan_auto_memory("thread-a", 7, {}, messages)

        assert plan is not None
        assert plan.start_index == 2
        assert plan.end_index == 5
        assert plan.reason == "interval"

    @pytest.mark.asyncio
    async def test_plans_explicit_memory_signal_before_interval(self, tmp_path, monkeypatch):
        store = MarkdownMemoryJobStore(tmp_path / "memory_tasks.db")
        store.ensure_schema()
        monkeypatch.setattr(markdown_memory_manager, "job_store", store)
        monkeypatch.setattr(settings, "MARKDOWN_MEMORY_AUTO_INTERVAL", 5)

        mw = _make_middleware()
        messages: list[BaseMessage] = [
            HumanMessage(content="记住：我喜欢先看目录再读正文", id="u-1"),
            AIMessage(content="已记录", id="ai-1"),
        ]

        plan = await mw._plan_auto_memory("thread-a", 7, {}, messages)

        assert plan is not None
        assert plan.start_index == 0
        assert plan.end_index == 2
        assert plan.reason == "explicit_memory_signal"

    @pytest.mark.asyncio
    async def test_plans_summarization_event_until_cutoff(self, tmp_path, monkeypatch):
        store = MarkdownMemoryJobStore(tmp_path / "memory_tasks.db")
        store.ensure_schema()
        monkeypatch.setattr(markdown_memory_manager, "job_store", store)
        monkeypatch.setattr(settings, "MARKDOWN_MEMORY_AUTO_INTERVAL", 5)

        mw = _make_middleware()
        messages: list[BaseMessage] = [
            HumanMessage(content="one", id="u-1"),
            AIMessage(content="two", id="ai-1"),
            HumanMessage(content="three", id="u-2"),
            AIMessage(content="four", id="ai-2"),
        ]

        plan = await mw._plan_auto_memory(
            "thread-a",
            7,
            {"_summarization_event": {"cutoff_index": 3}},
            messages,
        )

        assert plan is not None
        assert plan.start_index == 0
        assert plan.end_index == 3
        assert plan.reason == "summarization_event"
