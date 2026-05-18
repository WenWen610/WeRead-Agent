"""Runtime client for the Deep Agent reading assistant."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Optional

import aiosqlite
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from app.infrastructure.config import settings
from app.runtimes.deep_agent.agent import get_deep_agent_key, make_deep_agent
from app.runtimes.deep_agent.context import DeepAgentContext
from app.infrastructure.logging import get_logger
from app.schemas.chat import ChatTurnResponse, Message, StreamEvent

logger = get_logger(__name__)


@dataclass(frozen=True)
class _DeepAgentRuntime:
    agent: Any
    key: tuple[str, str]


class DeepAgentClient:
    """Create, stream, and inspect the Deep Agent runtime."""

    def __init__(self) -> None:
        """Initialize lazy runtime, checkpointer, and store handles."""
        self._runtime: Optional[_DeepAgentRuntime] = None
        self._sqlite_conn: Optional[aiosqlite.Connection] = None
        self._checkpointer: Optional[AsyncSqliteSaver] = None
        self._store_conn: aiosqlite.Connection | None = None
        self._store: AsyncSqliteStore | None = None

    async def _get_checkpointer(self) -> AsyncSqliteSaver:
        if self._checkpointer is not None:
            return self._checkpointer

        db_path = settings.LANGGRAPH_CHECKPOINT_SQLITE_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()
        self._sqlite_conn = conn
        self._checkpointer = checkpointer
        return checkpointer

    async def _get_store(self) -> AsyncSqliteStore:
        if self._store is not None:
            return self._store

        db_path = settings.DEEP_AGENT_STORE_SQLITE_PATH
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(db_path))
        store = AsyncSqliteStore(conn)
        await store.setup()
        await conn.commit()
        self._store_conn = conn
        self._store = store
        return store

    @staticmethod
    def _build_callbacks() -> list[Any]:
        return []

    def _build_config(self, thread_id: str, user_id: int | None) -> RunnableConfig:
        return RunnableConfig(
            configurable={"thread_id": thread_id},
            callbacks=self._build_callbacks(),
            metadata={
                "thread_id": thread_id,
                "session_id": thread_id,
                "user_id": user_id,
                "runtime": "deep_agent",
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
            },
            recursion_limit=settings.DEEP_AGENT_RECURSION_LIMIT,
        )

    @staticmethod
    def _resolve_web_search_enabled(value: bool | None) -> bool:
        return settings.DEEP_AGENT_WEB_SEARCH_ENABLED if value is None else value

    async def _ensure_agent(self, config: RunnableConfig, *, web_search_enabled: bool = False):
        key = get_deep_agent_key(config, web_search_enabled=web_search_enabled)
        if self._runtime is not None and self._runtime.key == key:
            return self._runtime.agent

        agent = make_deep_agent(
            config,
            checkpointer=await self._get_checkpointer(),
            store=await self._get_store(),
            context_schema=DeepAgentContext,
            web_search_enabled=web_search_enabled,
        )
        self._runtime = _DeepAgentRuntime(agent=agent, key=key)
        return agent

    @staticmethod
    def _extract_text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    @staticmethod
    def _is_assistant_text_chunk(message_chunk: Any) -> bool:
        message_type = str(getattr(message_chunk, "type", "")).lower()
        if message_type in {"tool", "toolmessagechunk"}:
            return False
        if message_type in {"ai", "aimessagechunk"}:
            return True
        class_name = type(message_chunk).__name__.lower()
        if "toolmessage" in class_name:
            return False
        return "aimessage" in class_name

    @staticmethod
    def _has_tool_call_payload(message_chunk: Any) -> bool:
        tool_call_chunks = getattr(message_chunk, "tool_call_chunks", None)
        if tool_call_chunks:
            return True
        tool_calls = getattr(message_chunk, "tool_calls", None)
        if tool_calls:
            return True
        additional_kwargs = getattr(message_chunk, "additional_kwargs", None)
        return isinstance(additional_kwargs, dict) and bool(additional_kwargs.get("tool_calls"))

    @staticmethod
    def _iter_update_items(chunk: Any) -> list[tuple[str, dict[str, Any]]]:
        if not isinstance(chunk, dict):
            return []
        return [(str(node_name), update) for node_name, update in chunk.items() if isinstance(update, dict)]

    @staticmethod
    def _process_messages(messages: list[BaseMessage]) -> list[Message]:
        history: list[Message] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                content = DeepAgentClient._extract_text_content(message.content)
                if content:
                    history.append(Message(role="user", content=content))
                continue
            if isinstance(message, AIMessage):
                content = DeepAgentClient._extract_text_content(message.content)
                if content:
                    history.append(Message(role="assistant", content=content))
        return history

    @staticmethod
    def _extract_artifacts(update: dict[str, Any], seen_artifact_ids: set[str]) -> list[dict[str, Any]]:
        raw_artifacts = update.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raw_artifacts = []
            raw_messages = update.get("messages")
            if isinstance(raw_messages, list):
                for message in raw_messages:
                    content = getattr(message, "content", "")
                    if not isinstance(content, str) or not content.strip().startswith("{"):
                        continue
                    try:
                        payload = json.loads(content)
                    except json.JSONDecodeError:
                        continue
                    payload_artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
                    if isinstance(payload_artifacts, list):
                        raw_artifacts.extend(payload_artifacts)
        if not raw_artifacts:
            return []
        artifacts: list[dict[str, Any]] = []
        for artifact in raw_artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_id = artifact.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id or artifact_id in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact_id)
            artifacts.append(artifact)
        return artifacts

    @staticmethod
    def _extract_todos(update: dict[str, Any]) -> list[dict[str, Any]] | None:
        for key in ("todos", "todo", "todo_list"):
            value = update.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return None

    @staticmethod
    def _build_clarification_event(update: dict[str, Any]) -> StreamEvent | None:
        pending = update.get("pending_clarification")
        if not isinstance(pending, dict):
            return None

        question = pending.get("question")
        if not isinstance(question, str) or not question:
            return None

        raw_options = pending.get("options")
        options = [str(option) for option in raw_options] if isinstance(raw_options, list) else []
        return StreamEvent(
            type="clarification",
            data={
                "question": question,
                "options": options,
                "clarification_type": pending.get("clarification_type"),
            },
        )

    @staticmethod
    def _build_capability_required_event(update: dict[str, Any]) -> StreamEvent | None:
        pending = update.get("pending_capability_requirement")
        if not isinstance(pending, dict):
            return None

        provider = pending.get("provider")
        reason = pending.get("reason")
        message = pending.get("message")
        if not isinstance(provider, str) or not provider:
            return None
        if not isinstance(reason, str) or not reason:
            return None
        if not isinstance(message, str) or not message:
            return None

        data = {
            "provider": provider,
            "reason": reason,
            "message": message,
        }
        action = pending.get("action")
        if isinstance(action, str) and action:
            data["action"] = action
        source_tool = pending.get("source_tool")
        if isinstance(source_tool, str) and source_tool:
            data["source_tool"] = source_tool
        return StreamEvent(type="capability_required", data=data)

    @staticmethod
    def _build_book_resolved_status(update: dict[str, Any]) -> StreamEvent | None:
        current_book = update.get("current_book")
        if not isinstance(current_book, dict):
            return None

        book_id = current_book.get("book_id")
        title = current_book.get("title")
        if not isinstance(book_id, str) or not book_id or not isinstance(title, str) or not title:
            return None

        return StreamEvent(
            type="status",
            data={
                "stage": "book_resolved",
                "message": f"已确认目标书籍：《{title}》",
                "book_id": book_id,
                "title": title,
            },
        )

    async def chat(
        self,
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> ChatTurnResponse:
        """Run one non-streaming chat turn and collect streamed events."""
        text_parts: list[str] = []
        artifacts: list[dict[str, Any]] = []
        seen_artifact_ids: set[str] = set()
        error_payload: dict[str, Any] | None = None
        clarification_payload: dict[str, Any] | None = None
        capability_payload: dict[str, Any] | None = None

        async for event in self.stream(message, thread_id, user_id, web_search_enabled=web_search_enabled):
            if event.type == "chunk":
                text_parts.append(str(event.data.get("content", "")))
                continue
            if event.type == "clarification":
                clarification_payload = event.data
                continue
            if event.type == "capability_required":
                capability_payload = event.data
                continue
            if event.type == "artifact":
                raw_artifacts = event.data.get("artifacts")
                if isinstance(raw_artifacts, list):
                    for artifact in raw_artifacts:
                        if not isinstance(artifact, dict):
                            continue
                        artifact_id = artifact.get("artifact_id")
                        if not isinstance(artifact_id, str) or artifact_id in seen_artifact_ids:
                            continue
                        seen_artifact_ids.add(artifact_id)
                        artifacts.append(artifact)
                continue
            if event.type == "error":
                error_payload = event.data
                continue
            if event.type == "done":
                break

        content = "".join(text_parts)
        if clarification_payload is not None:
            return ChatTurnResponse(type="clarification", data=clarification_payload)
        if capability_payload is not None:
            return ChatTurnResponse(type="capability_required", data=capability_payload)
        if error_payload is not None and not content:
            content = str(error_payload.get("message") or "Deep Agent request failed.")
        data: dict[str, Any] = {"content": content}
        if artifacts:
            data["artifacts"] = artifacts
        if error_payload is not None:
            data["error"] = error_payload
        return ChatTurnResponse(type="answer", data=data)

    async def get_chat_history(self, thread_id: str) -> list[Message]:
        """Return visible user/assistant messages for a thread."""
        config = self._build_config(thread_id, None)
        agent = await self._ensure_agent(config)
        state = await agent.aget_state(config)
        values = state.values or {}
        messages = values.get("messages")
        if not isinstance(messages, list):
            return []
        return self._process_messages(messages)

    async def get_thread_artifacts(self, thread_id: str) -> list[dict[str, Any]]:
        """Return artifacts stored in a thread state."""
        config = self._build_config(thread_id, None)
        agent = await self._ensure_agent(config)
        state = await agent.aget_state(config)
        values = state.values or {}
        artifacts = values.get("artifacts")
        if not isinstance(artifacts, list):
            return []
        return [artifact for artifact in artifacts if isinstance(artifact, dict)]

    async def clear_chat_history(self, thread_id: str) -> None:
        """Delete LangGraph checkpoint history for a thread."""
        checkpointer = await self._get_checkpointer()
        await checkpointer.adelete_thread(str(thread_id))
        logger.info("deep_agent_checkpoint_thread_deleted", thread_id=thread_id)

    async def stream(
        self,
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream model, tool, clarification, and artifact events for a thread."""
        config = self._build_config(thread_id, user_id)
        resolved_web_search_enabled = self._resolve_web_search_enabled(web_search_enabled)
        agent = await self._ensure_agent(config, web_search_enabled=resolved_web_search_enabled)
        emitted_artifact_ids: set[str] = set()
        tool_status_emitted = False
        summarization_status_emitted = False
        clarification_key: tuple[str, tuple[str, ...]] | None = None
        capability_requirement_key: tuple[str, str, str] | None = None
        resolved_book_id: str | None = None

        try:
            async for mode, chunk in agent.astream(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                context=DeepAgentContext(user_id=user_id, thread_id=thread_id),
                stream_mode=["messages", "updates"],
            ):
                if mode == "messages":
                    if not isinstance(chunk, tuple) or len(chunk) != 2:
                        continue
                    message_chunk, metadata = chunk
                    if metadata.get("lc_source") == "summarization":
                        if not summarization_status_emitted:
                            summarization_status_emitted = True
                            yield StreamEvent(
                                type="status",
                                data={
                                    "stage": "compacting",
                                    "message": "上下文较长，正在自动压缩历史对话…",
                                },
                            )
                        continue
                    if self._has_tool_call_payload(message_chunk) and not tool_status_emitted:
                        tool_status_emitted = True
                        yield StreamEvent(type="tool_call", data={"message": "正在调用工具或 subagent"})
                    if not self._is_assistant_text_chunk(message_chunk):
                        continue
                    text = self._extract_text_content(getattr(message_chunk, "content", ""))
                    if text:
                        yield StreamEvent(type="chunk", data={"content": text})
                    continue

                if mode != "updates":
                    continue

                for _node_name, update in self._iter_update_items(chunk):
                    capability_event = self._build_capability_required_event(update)
                    if capability_event is not None:
                        provider = str(capability_event.data.get("provider", ""))
                        reason = str(capability_event.data.get("reason", ""))
                        message_text = str(capability_event.data.get("message", ""))
                        event_key = (provider, reason, message_text)
                        if event_key != capability_requirement_key:
                            capability_requirement_key = event_key
                            yield capability_event
                        continue

                    clarification_event = self._build_clarification_event(update)
                    if clarification_event is not None:
                        question = clarification_event.data.get("question", "")
                        raw_options = clarification_event.data.get("options", [])
                        options = tuple(str(option) for option in raw_options) if isinstance(raw_options, list) else ()
                        event_key = (str(question), options)
                        if event_key != clarification_key:
                            clarification_key = event_key
                            yield clarification_event
                        continue

                    todos = self._extract_todos(update)
                    if todos is not None:
                        yield StreamEvent(type="todo_update", data={"todos": todos})

                    artifacts = self._extract_artifacts(update, emitted_artifact_ids)
                    if artifacts:
                        yield StreamEvent(type="artifact", data={"artifacts": artifacts})

                    book_status_event = self._build_book_resolved_status(update)
                    if book_status_event is not None:
                        book_id = book_status_event.data.get("book_id")
                        if isinstance(book_id, str) and book_id != resolved_book_id:
                            resolved_book_id = book_id
                            yield book_status_event

        except Exception as exc:
            logger.exception("deep_agent_stream_failed", thread_id=thread_id, user_id=user_id, error=str(exc))
            yield StreamEvent(
                type="error",
                data={
                    "message": "Deep Agent request failed.",
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                },
            )
        yield StreamEvent(type="done", data={})

    @staticmethod
    def encode_sse(event: StreamEvent) -> str:
        """Serialize one stream event as SSE data."""
        return f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
