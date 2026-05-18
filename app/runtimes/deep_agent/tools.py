"""Tool assembly for the Deep Agent runtime."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools.base import BaseTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.features.weread.operations import (
    DocumentSourceFilter,
    ensure_weread_book_notes_document as ensure_weread_book_notes_document_operation,
    present_weread_book_document as present_weread_book_document_operation,
    read_weread_document_outline as read_weread_document_outline_operation,
    read_weread_document_section as read_weread_document_section_operation,
    search_weread_notes as search_weread_notes_operation,
)
from app.features.weread.tools import build_weread_tools
from app.infrastructure.logging import get_logger
from app.runtimes.shared_tools.clarification_tool import ask_clarification_tool
from app.runtimes.shared_tools.web_search_tool import duckduckgo_search_tool
from app.features.weread.services.weread import WeReadDomainError
from app.features.weread.services.auth import WeReadBindingRequiredError, WeReadReauthRequiredError
from app.features.weread.services.browser_login import weread_browser_service

logger = get_logger(__name__)


def _extract_runtime_user_id(runtime: ToolRuntime | None) -> int | None:
    if runtime is None or runtime.context is None:
        return None
    context = runtime.context
    if isinstance(context, dict):
        user_id = context.get("user_id")
    else:
        user_id = getattr(context, "user_id", None)
    return user_id if isinstance(user_id, int) else None


def _serialize_payload(payload: Any) -> str:
    content = payload.model_dump(mode="json", exclude_none=True) if isinstance(payload, BaseModel) else payload
    return json.dumps(content, ensure_ascii=False, indent=2)


def _serialize_error(status: str, message: str) -> str:
    return json.dumps({"status": status, "message": message}, ensure_ascii=False, indent=2)


async def _run_deep_weread_tool(
    *,
    tool_name: str,
    runtime: ToolRuntime,
    operation_factory: Callable[[int], Awaitable[Any]],
) -> str:
    user_id = _extract_runtime_user_id(runtime)
    if user_id is None:
        logger.info("deep_weread_tool_missing_user_context", tool_name=tool_name)
        return _serialize_error("auth_required", "Authenticated user context is missing.")

    try:
        logger.info("deep_weread_tool_invoked", user_id=user_id, tool_name=tool_name)
        payload = await operation_factory(user_id)
        logger.info("deep_weread_tool_completed", user_id=user_id, tool_name=tool_name)
        return _serialize_payload(payload)
    except WeReadBindingRequiredError:
        logger.info("deep_weread_tool_binding_required", user_id=user_id, tool_name=tool_name)
        return _serialize_error("binding_required", "WeRead account is not connected for this user.")
    except WeReadReauthRequiredError:
        logger.info("deep_weread_tool_reauth_required", user_id=user_id, tool_name=tool_name)
        return _serialize_error("reauth_required", "WeRead authorization expired. Ask the user to reconnect WeRead.")
    except WeReadDomainError as exc:
        logger.exception("deep_weread_tool_domain_error", user_id=user_id, tool_name=tool_name, error=str(exc))
        return _serialize_error("weread_domain_error", str(exc))
    except Exception as exc:
        logger.exception("deep_weread_tool_unexpected_error", user_id=user_id, tool_name=tool_name, error=str(exc))
        return _serialize_error("unexpected_error", "Unexpected WeRead tool failure.")


@tool("ensure_weread_book_document")
async def ensure_weread_book_document(
    title_or_book_id: Annotated[str, Field(description="书名关键词或微信读书 book_id。", min_length=1)],
    source_type: Annotated[
        DocumentSourceFilter,
        Field(description="要确保存在的本地文档类型：marks、reviews 或 both。"),
    ] = "both",
    freshness_check: Annotated[
        bool,
        Field(description="是否用远端笔记数量检查本地文档是否需要刷新；默认 false，避免不必要 API 调用。"),
    ] = False,
    *,
    runtime: ToolRuntime,
) -> str:
    """Ensure local WeRead markdown documents exist for one book."""

    async def operation(user_id: int) -> dict[str, Any]:
        return await ensure_weread_book_notes_document_operation(
            user_id=user_id,
            title_or_book_id=title_or_book_id,
            source_type=source_type,
            freshness_check=freshness_check,
        )

    return await _run_deep_weread_tool(
        tool_name="ensure_weread_book_document",
        runtime=runtime,
        operation_factory=operation,
    )


@tool("read_weread_document_outline")
async def read_weread_document_outline(
    doc_id: Annotated[str, Field(description="Local WeRead document id returned by ensure_weread_book_document.")],
    *,
    runtime: ToolRuntime,
) -> str:
    """Return chapter/section outline for a local WeRead markdown document."""

    async def operation(user_id: int) -> dict[str, Any]:
        return await read_weread_document_outline_operation(user_id=user_id, doc_id=doc_id)

    return await _run_deep_weread_tool(
        tool_name="read_weread_document_outline",
        runtime=runtime,
        operation_factory=operation,
    )


@tool("read_weread_document_section")
async def read_weread_document_section(
    doc_id: Annotated[str, Field(description="Local WeRead document id returned by ensure_weread_book_document.")],
    section_id: Annotated[
        str,
        Field(description="Section id from read_weread_document_outline, such as chapter:1. Use all for all sections."),
    ],
    max_chars: Annotated[int, Field(description="Maximum returned characters.", ge=500, le=20000)] = 8000,
    *,
    runtime: ToolRuntime,
) -> str:
    """Read one bounded section from a local WeRead markdown document."""

    async def operation(user_id: int) -> dict[str, Any]:
        return await read_weread_document_section_operation(
            user_id=user_id,
            doc_id=doc_id,
            section_id=section_id,
            max_chars=max_chars,
        )

    return await _run_deep_weread_tool(
        tool_name="read_weread_document_section",
        runtime=runtime,
        operation_factory=operation,
    )


@tool("search_weread_notes")
async def search_weread_notes(
    title_or_book_id: Annotated[str, Field(description="书名关键词或微信读书 book_id。", min_length=1)],
    query: Annotated[str, Field(description="要在本书划线/想法中检索的关键词或主题。", min_length=1)],
    source_type: Annotated[DocumentSourceFilter, Field(description="检索 marks、reviews 或 both。")] = "both",
    top_k: Annotated[int, Field(description="最多返回多少条命中。", ge=1, le=20)] = 5,
    *,
    runtime: ToolRuntime,
) -> str:
    """Search one book's local WeRead notes with FTS/vector hybrid retrieval."""

    async def operation(user_id: int) -> dict[str, Any]:
        return await search_weread_notes_operation(
            user_id=user_id,
            title_or_book_id=title_or_book_id,
            query=query,
            source_type=source_type,
            top_k=top_k,
        )

    return await _run_deep_weread_tool(
        tool_name="search_weread_notes",
        runtime=runtime,
        operation_factory=operation,
    )


@tool("present_weread_book_document", return_direct=True)
async def present_weread_book_document(
    title_or_book_id: Annotated[str, Field(description="书名关键词或微信读书 book_id。", min_length=1)],
    source_type: Annotated[
        DocumentSourceFilter,
        Field(description="要展示的 markdown 类型：marks、reviews 或 both。"),
    ] = "both",
    force_refresh: Annotated[
        bool,
        Field(description="是否强制重新同步远端内容；默认 false，会优先展示已有本地 markdown。"),
    ] = False,
    reverse_order: Annotated[
        bool,
        Field(description="是否反转排序（从早到晚）；默认 true。LLM 可根据用户需求设为 false 展示最新内容。"),
    ] = True,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    runtime: ToolRuntime,
) -> Command:
    """Ensure local WeRead markdown and emit artifact metadata for frontend display."""

    async def operation(user_id: int) -> dict[str, Any]:
        return await present_weread_book_document_operation(
            user_id=user_id,
            title_or_book_id=title_or_book_id,
            source_type=source_type,
            force_refresh=force_refresh,
            reverse_order=reverse_order,
        )

    raw = await _run_deep_weread_tool(
        tool_name="present_weread_book_document",
        runtime=runtime,
        operation_factory=operation,
    )

    try:
        payload: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return Command(update={"messages": [ToolMessage(content=raw, tool_call_id=tool_call_id)]})

    artifacts: list[dict[str, Any]] = payload.get("artifacts", [])
    status: str = payload.get("status", "")
    book_title: str = payload.get("book_title", title_or_book_id)
    item_count: int = payload.get("item_count", 0)

    if status == "presented" and artifacts:
        source_label = "划线 + 想法" if source_type == "both" else ("划线" if source_type == "marks" else "想法")
        msg = f"《{book_title}》的{source_label}内容已在前端展示，共 {item_count} 条。"
    elif status == "ready" and artifacts:
        msg = f"《{book_title}》的文档已就绪，共 {item_count} 条。"
    else:
        msg = payload.get("message", raw)

    return Command(
        update={
            "messages": [ToolMessage(content=msg, tool_call_id=tool_call_id)],
            "artifacts": artifacts,
        }
    )


def _extract_user_id_for_tool(runtime: ToolRuntime | None) -> int | None:
    if runtime is None or runtime.context is None:
        return None
    ctx = runtime.context
    return ctx.user_id if isinstance(getattr(ctx, "user_id", None), int) else None


@tool("connect_weread")
async def connect_weread(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    runtime: ToolRuntime,
) -> Command:
    """Generate a WeRead QR code for binding.

    Call this when a WeRead tool returns `status: "binding_required"` or
    `status: "reauth_required"`. Launches a browser to obtain a QR code;
    the user scans it with WeChat to complete the login.
    """
    user_id = _extract_user_id_for_tool(runtime)
    if user_id is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps({"status": "auth_required", "message": "User context is missing."}, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    try:
        session = await weread_browser_service.start_qr_login_session(user_id)
        ready = await weread_browser_service.wait_for_qr_ready(user_id, session.id)
        if ready is None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps({"status": "failed", "message": "Failed to create QR login session."}, ensure_ascii=False),
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

        if ready.status != "qr_ready":
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps({"status": "failed", "message": f"QR login session ended with status {ready.status}."}, ensure_ascii=False),
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

        if not ready.qr_image_base64:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=json.dumps({"status": "failed", "message": "QR code image is empty."}, ensure_ascii=False),
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

        content = json.dumps(
            {
                "status": "qr_ready",
                "session_id": ready.id,
                "expires_at": ready.expires_at.isoformat() if ready.expires_at else None,
                "message": "请使用微信扫描二维码绑定微信读书。",
            },
            ensure_ascii=False,
        )
        return Command(
            update={
                "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
                "artifacts": [
                    {
                        "artifact_id": f"weread_qr_{ready.id}",
                        "kind": "weread_qr_code",
                        "name": "WeRead 二维码",
                        "qr_image_base64": ready.qr_image_base64,
                        "session_id": ready.id,
                        "expires_at": ready.expires_at.isoformat() if ready.expires_at else None,
                    }
                ],
            }
        )
    except Exception as exc:
        logger.exception("connect_weread_failed", user_id=user_id, error=str(exc))
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps({"status": "failed", "message": f"QR login failed: {exc}"}, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )


def build_deep_agent_tools(
    user_id: int | None = None,
    *,
    web_search_enabled: bool = False,
) -> list[BaseTool]:
    """Build the small high-level tool set for the main Deep Agent."""
    weread_tools_by_name = {tool.name: tool for tool in build_weread_tools(user_id=user_id)}
    tools = [
        ask_clarification_tool,
        connect_weread,
        weread_tools_by_name["resolve_weread_book"],
        weread_tools_by_name["get_weread_bookshelf_snapshot"],
        weread_tools_by_name["search_weread_bookshelf"],
        weread_tools_by_name["get_weread_book_reading_status"],
        weread_tools_by_name["get_weread_book_note_counts"],
        search_weread_notes,
        present_weread_book_document,
    ]
    if web_search_enabled:
        tools.append(duckduckgo_search_tool)
    return tools


def build_weread_analysis_tools(user_id: int | None = None) -> list[BaseTool]:
    """Build tools for subagents that analyze local WeRead documents."""
    weread_tools_by_name = {tool.name: tool for tool in build_weread_tools(user_id=user_id)}
    return [
        weread_tools_by_name["resolve_weread_book"],
        ensure_weread_book_document,
        read_weread_document_outline,
        read_weread_document_section,
    ]
