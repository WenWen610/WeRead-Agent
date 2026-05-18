"""Markdown memory retrieval middleware for Deep Agent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage

from app.features.markdown_memory.manager import markdown_memory_manager
from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

_FILLER_QUERIES = frozenset(
    {
        "好的",
        "好",
        "嗯",
        "嗯嗯",
        "谢谢",
        "继续",
        "ok",
        "okay",
        "thanks",
        "yes",
        "no",
    }
)

_EXPLICIT_MEMORY_SIGNALS = frozenset(
    {
        "记住",
        "记一下",
        "记录一下",
        "以后我",
        "我的偏好",
        "我偏好",
        "remember",
        "make a note",
        "note that",
        "my preference",
    }
)


@dataclass(frozen=True)
class _AutoMemoryPlan:
    start_index: int
    end_index: int
    reason: str


class MarkdownMemoryMiddleware(AgentMiddleware):
    """Inject relevant Markdown memory and schedule background memory writes."""

    def __init__(self) -> None:
        """Initialize middleware instance."""

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts).strip()
        return ""

    def _build_query(self, messages: list[AnyMessage]) -> str:
        query_parts: list[str] = []
        total = 0
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            text = self._extract_text(message.content)
            if not text:
                continue
            remaining = 240 - total
            if remaining <= 0:
                break
            query_parts.insert(0, text[-remaining:])
            total += min(len(text), remaining)
        return " ".join(query_parts).strip()

    @staticmethod
    def _should_skip(query: str) -> bool:
        normalized = query.strip()
        if len(normalized) < 3:
            return True
        return normalized.casefold() in _FILLER_QUERIES or normalized in _FILLER_QUERIES

    @staticmethod
    def _resolve_user_id(request: ModelRequest) -> int | None:
        context = getattr(request.runtime, "context", None)
        user_id = getattr(context, "user_id", None)
        return user_id if isinstance(user_id, int) else None

    async def _augment_messages(self, request: ModelRequest) -> list[AnyMessage]:
        messages = list(request.messages)
        if not settings.MARKDOWN_MEMORY_ENABLED:
            return messages

        query = self._build_query(messages)
        if self._should_skip(query):
            return messages

        user_id = self._resolve_user_id(request)
        try:
            results = await markdown_memory_manager.search(
                user_id,
                query,
                max_results=settings.MARKDOWN_MEMORY_SEARCH_MAX_RESULTS,
                min_score=settings.MARKDOWN_MEMORY_SEARCH_MIN_SCORE,
            )
        except Exception as exc:
            logger.exception("markdown_memory_search_failed", user_id=user_id, error=str(exc))
            return messages

        if not results:
            return messages

        content = markdown_memory_manager.format_search_results(results)
        if len(content) > settings.MARKDOWN_MEMORY_MAX_INJECT_CHARS:
            content = content[: settings.MARKDOWN_MEMORY_MAX_INJECT_CHARS].rstrip() + "\n...(truncated)"

        tool_call_id = f"memory_search_{uuid.uuid4().hex}"
        ai_message = AIMessage(
            content="Searching memory for relevant reading context...",
            tool_calls=[
                {
                    "id": tool_call_id,
                    "name": "memory_search",
                    "args": {
                        "query": query,
                        "max_results": settings.MARKDOWN_MEMORY_SEARCH_MAX_RESULTS,
                        "min_score": settings.MARKDOWN_MEMORY_SEARCH_MIN_SCORE,
                    },
                }
            ],
        )
        tool_message = ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name="memory_search",
        )
        logger.info(
            "markdown_memory_context_injected",
            user_id=user_id,
            result_count=len(results),
        )
        return [*messages, ai_message, tool_message]

    @staticmethod
    def _resolve_context(runtime: Any) -> tuple[int | None, str | None]:
        context = getattr(runtime, "context", None)
        user_id = getattr(context, "user_id", None)
        thread_id = getattr(context, "thread_id", None) or getattr(context, "session_id", None)
        return (
            user_id if isinstance(user_id, int) else None,
            thread_id if isinstance(thread_id, str) and thread_id else None,
        )

    @classmethod
    def _serialize_message(cls, message: BaseMessage) -> dict[str, str] | None:
        content = cls._extract_text(getattr(message, "content", ""))
        if not content:
            return None
        message_type = str(getattr(message, "type", "")).lower()
        role = "assistant" if message_type in {"ai", "assistant"} else "user" if message_type in {"human", "user"} else ""
        if not role:
            return None
        return {"role": role, "content": content}

    @staticmethod
    def _count_user_turns(messages: list[BaseMessage]) -> int:
        return sum(1 for message in messages if isinstance(message, HumanMessage))

    @staticmethod
    def _message_id(message: BaseMessage) -> str | None:
        message_id = getattr(message, "id", None)
        return message_id if isinstance(message_id, str) and message_id else None

    @classmethod
    def _boundary_message_id(cls, messages: list[BaseMessage], index: int) -> str | None:
        if index <= 0 or index > len(messages):
            return None
        return cls._message_id(messages[index - 1])

    @classmethod
    def _find_index_after_message_id(cls, messages: list[BaseMessage], message_id: str) -> int | None:
        for index, message in enumerate(messages):
            if cls._message_id(message) == message_id:
                return index + 1
        return None

    @classmethod
    def _resolve_completed_index(cls, thread_id: str, user_id: int, messages: list[BaseMessage]) -> int:
        cursor = markdown_memory_manager.job_store.get_cursor(user_id, thread_id)
        if cursor is None:
            return 0
        if cursor.completed_message_id:
            resolved = cls._find_index_after_message_id(messages, cursor.completed_message_id)
            if resolved is not None:
                return resolved
            logger.info(
                "markdown_memory_cursor_message_id_not_found",
                user_id=user_id,
                thread_id=thread_id,
                completed_message_id=cursor.completed_message_id,
                message_count=len(messages),
            )
        return max(0, min(cursor.completed_index, len(messages)))

    @classmethod
    def _window_contains_explicit_memory_signal(cls, messages: list[BaseMessage]) -> bool:
        for message in messages:
            if not isinstance(message, HumanMessage):
                continue
            text = cls._extract_text(getattr(message, "content", "")).casefold()
            if any(signal in text for signal in _EXPLICIT_MEMORY_SIGNALS):
                return True
        return False

    async def _plan_auto_memory(
        self,
        thread_id: str,
        user_id: int,
        state: dict[str, Any],
        messages: list[BaseMessage],
    ) -> _AutoMemoryPlan | None:
        interval = max(0, settings.MARKDOWN_MEMORY_AUTO_INTERVAL)
        if interval <= 0:
            return None

        cursor = await asyncio.to_thread(self._resolve_completed_index, thread_id, user_id, messages)
        if cursor >= len(messages):
            return None

        event = state.get("_summarization_event")
        if isinstance(event, dict):
            cutoff = event.get("cutoff_index")
            if isinstance(cutoff, int) and cutoff > cursor:
                return _AutoMemoryPlan(
                    start_index=cursor,
                    end_index=max(0, min(cutoff, len(messages))),
                    reason="summarization_event",
                )

        new_messages = messages[cursor:]
        if self._count_user_turns(new_messages) >= max(interval, 1):
            return _AutoMemoryPlan(start_index=cursor, end_index=len(messages), reason="interval")

        if self._window_contains_explicit_memory_signal(new_messages):
            return _AutoMemoryPlan(start_index=cursor, end_index=len(messages), reason="explicit_memory_signal")

        return None

    @classmethod
    def _message_hash(
        cls,
        *,
        thread_id: str,
        start_index: int,
        end_index: int,
        messages: list[BaseMessage],
        serialized: list[dict[str, str]],
    ) -> str:
        ids = [cls._message_id(message) for message in messages[start_index:end_index]]
        payload = {
            "thread_id": thread_id,
            "start_index": start_index,
            "end_index": end_index,
            "ids": ids,
            "messages": serialized,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

    async def _enqueue_auto_memory_job(
        self,
        *,
        user_id: int,
        thread_id: str,
        plan: _AutoMemoryPlan,
        raw_messages: list[BaseMessage],
        messages: list[dict[str, str]],
    ) -> None:
        try:
            message_hash = self._message_hash(
                thread_id=thread_id,
                start_index=plan.start_index,
                end_index=plan.end_index,
                messages=raw_messages,
                serialized=messages,
            )
            await markdown_memory_manager.enqueue_auto_memory_job(
                messages=messages,
                user_id=user_id,
                thread_id=thread_id,
                day_path=f"memory/{datetime.now(timezone.utc).date().isoformat()}.md",
                start_message_id=self._boundary_message_id(raw_messages, plan.start_index),
                end_message_id=self._boundary_message_id(raw_messages, plan.end_index),
                start_index=plan.start_index,
                end_index=plan.end_index,
                message_hash=message_hash,
                reason=plan.reason,
            )
        except Exception as exc:
            logger.exception("markdown_memory_auto_memory_failed", user_id=user_id, thread_id=thread_id, error=str(exc))

    @staticmethod
    def _should_inject(request: ModelRequest) -> bool:
        """Only inject memory when the conversation has just started (last message is HumanMessage)."""
        messages = request.messages
        if not messages:
            return False
        return isinstance(messages[-1], HumanMessage)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if self._should_inject(request):
            messages = await self._augment_messages(request)
            return await handler(request.override(messages=messages))
        return await handler(request)

    @override
    async def aafter_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        if not settings.MARKDOWN_MEMORY_ENABLED:
            return None
        user_id, thread_id = self._resolve_context(runtime)
        if user_id is None or not thread_id:
            return None
        raw_messages = state.get("messages") or []
        messages = [message for message in raw_messages if isinstance(message, BaseMessage)]
        if not messages:
            return None
        plan = await self._plan_auto_memory(thread_id, user_id, state, messages)
        if plan is None or plan.end_index <= plan.start_index:
            return None
        serialized = [
            item
            for item in (self._serialize_message(message) for message in messages[plan.start_index : plan.end_index])
            if item is not None
        ]
        if not serialized:
            return None
        asyncio.create_task(
            self._enqueue_auto_memory_job(
                user_id=user_id,
                thread_id=thread_id,
                plan=plan,
                raw_messages=messages,
                messages=serialized,
            )
        )
        logger.info(
            "markdown_memory_auto_memory_scheduled",
            user_id=user_id,
            thread_id=thread_id,
            message_count=len(serialized),
            reason=plan.reason,
            start_index=plan.start_index,
            end_index=plan.end_index,
        )
        return None
