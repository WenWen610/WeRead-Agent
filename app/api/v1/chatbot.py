"""Chatbot API endpoints for handling chat interactions.

This module provides endpoints for chat interactions, including regular chat,
streaming chat, persistent thread management, message history management, and
chat history clearing.
"""

import json
import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import StreamingResponse

from app.api.v1.auth import get_current_session
from app.infrastructure.config import settings
from app.runtimes.deep_agent.client import DeepAgentClient
from app.infrastructure.limiter import limiter
from app.infrastructure.logging import get_logger
from app.infrastructure.metrics import llm_stream_duration_seconds
from app.models.session import Session
from app.features.chat.store import chat_store
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatThreadCreateRequest,
    ChatThreadListResponse,
    ChatThreadSummary,
    ChatThreadUpdateRequest,
    ChatTimelineItem,
    ChatTimelineResponse,
    ChatTurnResponse,
    StreamEvent,
)

logger = get_logger(__name__)
router = APIRouter()
agent_client = DeepAgentClient()


def _resolve_thread_id(chat_request: ChatRequest, session: Session) -> str:
    """Resolve the effective thread id for the current request."""
    thread_id = chat_request.thread_id or session.id
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id_required")
    return str(thread_id)


def _build_thread_summary(thread) -> ChatThreadSummary:
    """Convert a persistent chat thread model into an API summary."""
    return ChatThreadSummary(
        thread_id=thread.id,
        title=thread.title,
        last_item_preview=thread.last_item_preview,
        last_item_type=thread.last_item_type,
        last_message_at=thread.last_message_at,
        item_count=thread.item_count,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        archived_at=thread.archived_at,
    )


def _build_timeline_item(item) -> ChatTimelineItem:
    """Convert a persistent chat item model into an API response item."""
    return ChatTimelineItem(
        id=item.id,
        thread_id=item.thread_id,
        seq=item.seq,
        item_type=item.item_type,
        content=item.content,
        metadata=item.item_metadata,
        created_at=item.created_at,
    )


async def _persist_chat_turn_result(
    *,
    thread_id: str,
    user_id: int,
    user_message: str,
    result: ChatTurnResponse,
) -> None:
    """Persist a completed non-stream chat turn into the UI timeline tables."""
    await chat_store.create_chat_item(
        thread_id=thread_id,
        user_id=user_id,
        item_type="user",
        content=user_message,
    )

    if result.type == "answer":
        content = str(result.data.get("content", ""))
        if not content:
            return
        await chat_store.create_chat_item(
            thread_id=thread_id,
            user_id=user_id,
            item_type="assistant",
            content=content,
        )
        return

    if result.type == "capability_required":
        await chat_store.create_chat_item(
            thread_id=thread_id,
            user_id=user_id,
            item_type="capability_notice",
            content=str(result.data.get("message", "")),
            item_metadata=result.data,
        )
        return

    await chat_store.create_chat_item(
        thread_id=thread_id,
        user_id=user_id,
        item_type="clarification",
        content=str(result.data.get("question", "")),
        item_metadata=result.data,
    )


@router.get("/threads", response_model=ChatThreadListResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def list_chat_threads(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """List persistent chat threads for the authenticated user."""
    try:
        threads = await chat_store.get_user_chat_threads(session.user_id)
        return ChatThreadListResponse(threads=[_build_thread_summary(thread) for thread in threads])
    except Exception as e:
        logger.exception("list_chat_threads_failed", session_id=session.id, user_id=session.user_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/threads", response_model=ChatThreadSummary)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def create_chat_thread(
    request: Request,
    thread_request: ChatThreadCreateRequest,
    session: Session = Depends(get_current_session),
):
    """Create a persistent chat thread for the authenticated user."""
    try:
        thread_id = str(uuid.uuid4())
        thread = await chat_store.create_chat_thread(thread_id, session.user_id, thread_request.title)
        return _build_thread_summary(thread)
    except Exception as e:
        logger.exception("create_chat_thread_failed", session_id=session.id, user_id=session.user_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads/{thread_id}/items", response_model=ChatTimelineResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_chat_thread_items(
    request: Request,
    thread_id: str,
    session: Session = Depends(get_current_session),
):
    """Get the persistent UI timeline items for a chat thread."""
    try:
        thread = await chat_store.get_chat_thread(thread_id)
        if thread is None or thread.user_id != session.user_id:
            raise HTTPException(status_code=404, detail="Chat thread not found")

        items = await chat_store.get_chat_items(thread_id)
        return ChatTimelineResponse(items=[_build_timeline_item(item) for item in items])
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "get_chat_thread_items_failed",
            session_id=session.id,
            thread_id=thread_id,
            user_id=session.user_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads/{thread_id}/artifacts")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_chat_thread_artifacts(
    request: Request,
    thread_id: str,
    session: Session = Depends(get_current_session),
):
    """Get the latest artifact list persisted in the agent thread state."""
    try:
        thread = await chat_store.get_chat_thread(thread_id)
        if thread is None or thread.user_id != session.user_id:
            raise HTTPException(status_code=404, detail="Chat thread not found")

        artifacts = await agent_client.get_thread_artifacts(thread_id)
        return {"artifacts": artifacts}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "get_chat_thread_artifacts_failed",
            session_id=session.id,
            thread_id=thread_id,
            user_id=session.user_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/threads/{thread_id}", response_model=ChatThreadSummary)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def update_chat_thread(
    request: Request,
    thread_id: str,
    thread_request: ChatThreadUpdateRequest,
    session: Session = Depends(get_current_session),
):
    """Update a persistent chat thread title."""
    try:
        thread = await chat_store.get_chat_thread(thread_id)
        if thread is None or thread.user_id != session.user_id:
            raise HTTPException(status_code=404, detail="Chat thread not found")

        updated_thread = await chat_store.update_chat_thread_title(thread_id, thread_request.title)
        return _build_thread_summary(updated_thread)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "update_chat_thread_failed",
            session_id=session.id,
            thread_id=thread_id,
            user_id=session.user_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/threads/{thread_id}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def delete_chat_thread(
    request: Request,
    thread_id: str,
    session: Session = Depends(get_current_session),
):
    """Delete a persistent chat thread and clear its runtime checkpoint history."""
    try:
        thread = await chat_store.get_chat_thread(thread_id)
        if thread is None or thread.user_id != session.user_id:
            raise HTTPException(status_code=404, detail="Chat thread not found")

        await chat_store.delete_chat_thread(thread_id)
        await agent_client.clear_chat_history(thread_id)
        return {"message": "Chat thread deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "delete_chat_thread_failed",
            session_id=session.id,
            thread_id=thread_id,
            user_id=session.user_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat", response_model=ChatTurnResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request using the runtime agent.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        ChatTurnResponse: The processed chat result.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    try:
        thread_id = _resolve_thread_id(chat_request, session)
        logger.info(
            "chat_request_received",
            session_id=session.id,
            thread_id=thread_id,
            message_length=len(chat_request.message),
        )

        result = await agent_client.chat(
            chat_request.message,
            thread_id,
            user_id=session.user_id,
            web_search_enabled=chat_request.web_search_enabled,
        )
        await _persist_chat_turn_result(
            thread_id=thread_id,
            user_id=session.user_id,
            user_message=chat_request.message,
            result=result,
        )

        logger.info(
            "chat_request_processed",
            session_id=session.id,
            thread_id=thread_id,
            response_type=result.type,
        )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("chat_request_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """Process a chat request using LangGraph with streaming response.

    Args:
        request: The FastAPI request object for rate limiting.
        chat_request: The chat request containing messages.
        session: The current session from the auth token.

    Returns:
        StreamingResponse: A streaming response of the chat completion.

    Raises:
        HTTPException: If there's an error processing the request.
    """
    try:
        thread_id = _resolve_thread_id(chat_request, session)
        logger.info(
            "stream_chat_request_received",
            session_id=session.id,
            thread_id=thread_id,
            message_length=len(chat_request.message),
        )

        # StreamingResponse pulls data from this async generator incrementally.
        async def event_generator():
            """Generate streaming events.

            Yields:
                str: Server-sent events in JSON format.

            Raises:
                Exception: If there's an error during streaming.
            """
            user_message_persisted = False
            clarification_persisted = False
            capability_persisted = False
            assistant_chunks: list[str] = []
            try:
                if not user_message_persisted:
                    await chat_store.create_chat_item(
                        thread_id=thread_id,
                        user_id=session.user_id,
                        item_type="user",
                        content=chat_request.message,
                    )
                    user_message_persisted = True

                # Measure end-to-end model streaming latency for Prometheus.
                with llm_stream_duration_seconds.labels(model=settings.DEFAULT_LLM_MODEL).time():
                    async for event in agent_client.stream(
                        chat_request.message,
                        thread_id,
                        user_id=session.user_id,
                        web_search_enabled=chat_request.web_search_enabled,
                    ):
                        if event.type == "chunk":
                            content = str(event.data.get("content", ""))
                            if content:
                                assistant_chunks.append(content)
                        elif event.type == "clarification" and not clarification_persisted:
                            clarification_persisted = True
                            await chat_store.create_chat_item(
                                thread_id=thread_id,
                                user_id=session.user_id,
                                item_type="clarification",
                                content=str(event.data.get("question", "")),
                                item_metadata=event.data,
                            )
                        elif event.type == "capability_required" and not capability_persisted:
                            capability_persisted = True
                            await chat_store.create_chat_item(
                                thread_id=thread_id,
                                user_id=session.user_id,
                                item_type="capability_notice",
                                content=str(event.data.get("message", "")),
                                item_metadata=event.data,
                            )
                        elif event.type == "done" and assistant_chunks:
                            assistant_content = "".join(assistant_chunks).strip()
                            if assistant_content:
                                await chat_store.create_chat_item(
                                    thread_id=thread_id,
                                    user_id=session.user_id,
                                    item_type="assistant",
                                    content=assistant_content,
                                )
                            assistant_chunks.clear()

                        # SSE frame format:
                        # event: <type>\n
                        # data: <json>\n\n
                        yield f"event: {event.type}\n"
                        yield f"data: {json.dumps(event.data, ensure_ascii=False)}\n\n"

            except Exception as e:
                logger.exception(
                    "stream_chat_request_failed",
                    session_id=session.id,
                    error=str(e),
                )
                error_event = StreamEvent(
                    type="status",
                    data={"stage": "error", "message": "流式请求失败，请稍后重试。", "error": str(e)},
                )
                yield f"event: {error_event.type}\n"
                yield f"data: {json.dumps(error_event.data, ensure_ascii=False)}\n\n"
                yield "event: done\n"
                yield "data: {}\n\n"

        # "text/event-stream" tells clients to consume this as SSE.
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "stream_chat_request_failed",
            session_id=session.id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_session_messages(
    request: Request,
    thread_id: str | None = Query(default=None),
    session: Session = Depends(get_current_session),
):
    """Get all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        ChatResponse: All messages in the session.

    Raises:
        HTTPException: If there's an error retrieving the messages.
    """
    try:
        effective_thread_id = thread_id or session.id
        messages = await agent_client.get_chat_history(effective_thread_id)
        return ChatResponse(messages=messages)
    except Exception as e:
        logger.exception(
            "get_messages_failed",
            session_id=session.id,
            thread_id=effective_thread_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/messages")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_chat_history(
    request: Request,
    thread_id: str | None = Query(default=None),
    session: Session = Depends(get_current_session),
):
    """Clear all messages for a session.

    Args:
        request: The FastAPI request object for rate limiting.
        session: The current session from the auth token.

    Returns:
        dict: A message indicating the chat history was cleared.
    """
    try:
        effective_thread_id = thread_id or session.id
        await agent_client.clear_chat_history(effective_thread_id)
        return {"message": "Chat history cleared successfully"}
    except Exception as e:
        logger.exception(
            "clear_chat_history_failed",
            session_id=session.id,
            thread_id=effective_thread_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))
