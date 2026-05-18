"""User-scoped WeRead LangChain tools."""

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools.base import BaseTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.infrastructure.logging import get_logger
from app.runtimes.shared_tools.present_file_tool import build_present_files_update
from app.features.weread.services.weread import (
    MARK_PREVIEW_DEFAULT_ITEMS,
    MARKS_AND_REVIEWS_PREVIEW_DEFAULT_MARK_ITEMS,
    MARKS_AND_REVIEWS_PREVIEW_DEFAULT_REVIEW_ITEMS,
    REVIEW_PREVIEW_DEFAULT_ITEMS,
    WeReadDomainError,
    weread_service,
)
from app.features.weread.services.auth import (
    WeReadBindingRequiredError,
    WeReadReauthRequiredError,
)

logger = get_logger(__name__)


def _serialize_tool_payload(payload: Any) -> str:
    """将工具返回结果序列化为紧凑的 JSON 文本，供 LLM 消费。"""
    if isinstance(payload, BaseModel):
        content = payload.model_dump(mode="json", exclude_none=True)
    else:
        content = payload
    return json.dumps(content, ensure_ascii=False, indent=2)


def _serialize_tool_error(status: str, message: str) -> str:
    """将标准化后的工具错误序列化为 JSON 文本，供 LLM 消费。"""
    return json.dumps({"status": status, "message": message}, ensure_ascii=False, indent=2)


def _summarize_tool_payload(payload: Any) -> dict[str, Any]:
    """为工具返回结果生成安全的日志摘要，避免直接打印完整内容。"""
    content = payload.model_dump(mode="json", exclude_none=True) if isinstance(payload, BaseModel) else payload
    summary: dict[str, Any] = {"payload_type": type(payload).__name__}

    if isinstance(content, dict):
        summary["top_level_keys"] = sorted(content.keys())[:10]

        for key in (
            "status",
            "recommended_action",
            "message",
            "book_id",
            "book_title",
            "source_type",
            "mode",
            "sync_key",
            "generated_at",
            "last_updated",
            "keyword",
            "book_count",
            "pure_book_count",
            "unread_books_count",
            "reading_books_count",
            "finished_books_count",
            "mark_count",
            "review_count",
            "total_marks_count",
            "total_reviews_count",
            "returned_marks_count",
            "returned_reviews_count",
            "omitted_marks_count",
            "omitted_reviews_count",
            "preview_mode",
            "total_matches",
            "total_highlights",
            "total_notes",
            "total_reviews",
            "total_hits",
        ):
            value = content.get(key)
            if value is not None and not isinstance(value, (dict, list)):
                summary[key] = value

        for key in (
            "books",
            "recent_books",
            "currently_reading_books",
            "exported_documents",
            "highlights",
            "notes",
            "marks",
            "reviews",
            "hits",
            "chapters",
            "artifacts",
            "booklists",
        ):
            value = content.get(key)
            if isinstance(value, list):
                summary[f"{key}_count"] = len(value)

        stats = content.get("stats")
        if isinstance(stats, dict):
            total_books = stats.get("totalBooks")
            if total_books is not None:
                summary["total_books"] = total_books

        reading_status = content.get("reading_status")
        if isinstance(reading_status, dict):
            progress = reading_status.get("progress")
            if progress is not None:
                summary["progress"] = progress

        metadata = content.get("metadata")
        if isinstance(metadata, dict):
            for key in ("doc_id", "book_id", "book_title", "source_type", "file_path", "item_count"):
                value = metadata.get(key)
                if value is not None and not isinstance(value, (dict, list)):
                    summary[f"metadata_{key}"] = value
    elif isinstance(content, list):
        summary["item_count"] = len(content)
    else:
        summary["payload_preview"] = str(content)[:120]

    return summary


async def _run_weread_tool(
    user_id: int,
    tool_name: str,
    operation: Callable[[], Awaitable[Any]],
) -> str:
    """执行 WeRead 服务调用，并将失败统一转换为标准工具错误。"""
    try:
        logger.info("weread_tool_invoked", user_id=user_id, tool_name=tool_name)
        payload = await operation()
        payload_summary = _summarize_tool_payload(payload)
        logger.info("weread_tool_completed", user_id=user_id, tool_name=tool_name, **payload_summary)
        return _serialize_tool_payload(payload)
    except WeReadBindingRequiredError:
        logger.info("weread_tool_binding_required", user_id=user_id, tool_name=tool_name)
        return _serialize_tool_error(
            "binding_required",
            "WeRead account is not connected for this user. Bind WeRead before using this tool.",
        )
    except WeReadReauthRequiredError:
        logger.info("weread_tool_reauth_required", user_id=user_id, tool_name=tool_name)
        return _serialize_tool_error(
            "reauth_required",
            "WeRead authorization expired. Ask the user to reconnect WeRead and retry.",
        )
    except WeReadDomainError as exc:
        logger.exception(
            "weread_tool_domain_error",
            user_id=user_id,
            tool_name=tool_name,
            error=str(exc),
        )
        return _serialize_tool_error(
            "weread_domain_error",
            "WeRead request failed. Tell the user the upstream request did not succeed and suggest retrying.",
        )
    except Exception as exc:
        logger.exception(
            "weread_tool_unexpected_error",
            user_id=user_id,
            tool_name=tool_name,
            error=str(exc),
        )
        return _serialize_tool_error(
            "unexpected_error",
            "Unexpected WeRead tool failure. Tell the user the request failed and suggest retrying later.",
        )


def _extract_runtime_user_id(runtime: ToolRuntime | None) -> int | None:
    """从 tool runtime context 中提取当前用户 ID。"""
    if runtime is None or runtime.context is None:
        return None

    context = runtime.context
    if isinstance(context, dict):
        user_id = context.get("user_id")
    else:
        user_id = getattr(context, "user_id", None)

    return user_id if isinstance(user_id, int) else None


async def _run_weread_tool_with_runtime(
    *,
    tool_name: str,
    operation_factory: Callable[[int], Awaitable[Any]],
    runtime: ToolRuntime | None = None,
    user_id: int | None = None,
) -> str:
    """优先从 runtime context 读取 user_id，兼容旧的闭包式 user_id。"""
    resolved_user_id = user_id if user_id is not None else _extract_runtime_user_id(runtime)
    if resolved_user_id is None:
        logger.info("weread_tool_missing_user_context", tool_name=tool_name)
        return _serialize_tool_error(
            "auth_required",
            "Authenticated user context is missing. Sign in before using WeRead tools.",
        )

    return await _run_weread_tool(
        user_id=resolved_user_id,
        tool_name=tool_name,
        operation=lambda: operation_factory(resolved_user_id),
    )


async def _run_weread_command_tool_with_runtime(
    *,
    tool_name: str,
    operation_factory: Callable[[int], Awaitable[Any]],
    tool_call_id: str,
    state_update_factory: Callable[[Any], dict[str, Any]] | None = None,
    runtime: ToolRuntime | None = None,
    user_id: int | None = None,
) -> Command:
    resolved_user_id = user_id if user_id is not None else _extract_runtime_user_id(runtime)
    if resolved_user_id is None:
        logger.info("weread_tool_missing_user_context", tool_name=tool_name)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        _serialize_tool_error(
                            "auth_required",
                            "Authenticated user context is missing. Sign in before using WeRead tools.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    try:
        logger.info("weread_tool_invoked", user_id=resolved_user_id, tool_name=tool_name)
        payload = await operation_factory(resolved_user_id)
        payload_summary = _summarize_tool_payload(payload)
        logger.info("weread_tool_completed", user_id=resolved_user_id, tool_name=tool_name, **payload_summary)
        update: dict[str, Any] = {
            "messages": [
                ToolMessage(
                    _serialize_tool_payload(payload),
                    tool_call_id=tool_call_id,
                )
            ]
        }
        if state_update_factory is not None:
            update.update(state_update_factory(payload))
        return Command(update=update)
    except WeReadBindingRequiredError:
        logger.info("weread_tool_binding_required", user_id=resolved_user_id, tool_name=tool_name)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        _serialize_tool_error(
                            "binding_required",
                            "WeRead account is not connected for this user. Bind WeRead before using this tool.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    except WeReadReauthRequiredError:
        logger.info("weread_tool_reauth_required", user_id=resolved_user_id, tool_name=tool_name)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        _serialize_tool_error(
                            "reauth_required",
                            "WeRead authorization expired. Ask the user to reconnect WeRead and retry.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    except WeReadDomainError as exc:
        logger.exception(
            "weread_tool_domain_error",
            user_id=resolved_user_id,
            tool_name=tool_name,
            error=str(exc),
        )
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        _serialize_tool_error(
                            "weread_domain_error",
                            "WeRead request failed. Tell the user the upstream request did not succeed and suggest retrying.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    except Exception as exc:
        logger.exception(
            "weread_tool_unexpected_error",
            user_id=resolved_user_id,
            tool_name=tool_name,
            error=str(exc),
        )
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        _serialize_tool_error(
                            "unexpected_error",
                            "Unexpected WeRead tool failure. Tell the user the request failed and suggest retrying later.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )


def build_weread_tools(user_id: int | None = None) -> list[BaseTool]:
    """构建当前请求可用的 WeRead 工具列表。"""

    @tool("get_weread_bookshelf_snapshot")
    async def get_weread_bookshelf_snapshot(
        force_refresh: Annotated[bool, Field(description="预留的缓存控制参数；当前一般保持 false 即可。")] = False,
        preview_limit: Annotated[
            int,
            Field(description="书架概览里最多返回多少本代表性的书。", ge=1, le=20),
        ] = 8,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """获取当前用户微信读书书架的整体概览。

        用于书架总览、最近阅读等整体统计性问题。
        默认返回精简概览，不返回完整书架明细，也不用于单本书定位或笔记详情。
        """
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_bookshelf_snapshot",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_bookshelf_overview(
                current_user_id,
                force_refresh=force_refresh,
                preview_limit=preview_limit,
            ),
        )

    @tool("search_weread_bookshelf")
    async def search_weread_bookshelf(
        keyword: Annotated[
            str, Field(description="要搜索的作者名、书名关键词、译者、分类或书单关键词。", min_length=1)
        ],
        exact_match: Annotated[bool, Field(description="是否使用精确匹配，而不是模糊包含匹配。")] = False,
        max_results: Annotated[int, Field(description="最多返回多少本匹配到的书。", ge=1, le=20)] = 10,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """在当前用户的微信读书书架内按关键词检索。

        用于按分类、作者、书名关键词、译者或书单搜索书架范围内的书。
        返回一批匹配结果，不负责唯一定位单本书；多本结果通常是正常答案。
        """
        return await _run_weread_tool_with_runtime(
            tool_name="search_weread_bookshelf",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.search_bookshelf(
                user_id=current_user_id,
                keyword=keyword,
                exact_match=exact_match,
                max_results=max_results,
            ),
        )

    @tool("resolve_weread_book")
    async def resolve_weread_book(
        keyword: Annotated[str, Field(description="要定位的书名、作者或其他识别该书的关键词。", min_length=1)],
        exact_match: Annotated[bool, Field(description="是否使用精确匹配，而不是模糊包含匹配。")] = False,
        max_candidates: Annotated[
            int, Field(description="当存在多个候选书时，最多返回多少个候选项。", ge=1, le=10)
        ] = 5,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """在调用单本书详情工具之前，先把目标书唯一定位出来。

        用于在查询单本书阅读状态、笔记、导出或全文详情前，先把目标书定位到唯一 `book_id`。
        返回的核心字段包括 `status`、`recommended_action`、`message`；只有 `status=resolved` 时才表示可以继续调用单书详情工具。
        """
        return await _run_weread_tool_with_runtime(
            tool_name="resolve_weread_book",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.resolve_book(
                user_id=current_user_id,
                keyword=keyword,
                exact_match=exact_match,
                max_candidates=max_candidates,
            ),
        )

    @tool("get_weread_book_reading_status")
    async def get_weread_book_reading_status(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        *,
        runtime: ToolRuntime,
    ) -> str:
        """根据书的 book_id，获取当前用户在该书上的阅读进度和阅读状态。

        适合回答“这本书我读到哪里了”“有没有读完”“最近有没有在看”“我有没有读过这本书”这类问题。
        """
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_book_reading_status",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_book_reading_status(current_user_id, book_id),
        )

    @tool("get_weread_book_note_counts")
    async def get_weread_book_note_counts(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        *,
        runtime: ToolRuntime,
    ) -> str:
        """根据书的 book_id，获取当前用户在该书上的笔记数量统计，包含划线数量和想法/点评数量。"""
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_book_note_counts",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_weread_book_note_counts(
                current_user_id, book_id
            ),
        )

    @tool("get_weread_book_marks_and_reviews")
    # Routing detail lives in skills/public/weread/SKILL.md.
    # Keep runtime tool schema minimal; retain the previous longer wording here:
    # - "默认返回多少条带原文摘录的想法/点评预览。值越大，上下文开销越高；需要全文时优先改用导出或笔记检索。"
    # - "在想法/点评预览之外，额外返回多少条划线预览。需要完整笔记时优先改用导出工具。"
    # - 当前实际逻辑是按现有顺序取前几条 review / mark，再截断每条文本长度，不做“代表性”挑选。
    async def get_weread_book_marks_and_reviews(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        include_chapter: Annotated[
            bool, Field(description="是否按章节组织返回划线和想法/点评；为 false 时返回平铺列表。")
        ] = True,
        highlight_style: Annotated[
            int | None, Field(description="可选的划线高亮样式筛选；不传则返回全部样式。")
        ] = None,
        max_review_items: Annotated[
            int,
            Field(
                description="返回前多少条带原文摘录的想法/点评预览。",
                ge=1,
                le=10,
            ),
        ] = MARKS_AND_REVIEWS_PREVIEW_DEFAULT_REVIEW_ITEMS,
        max_mark_items: Annotated[
            int,
            Field(
                description="在想法/点评预览之外，额外返回前多少条划线预览。",
                ge=0,
                le=10,
            ),
        ] = MARKS_AND_REVIEWS_PREVIEW_DEFAULT_MARK_ITEMS,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """根据 `book_id` 获取当前用户在该书上的前几条紧凑笔记预览。"""
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_book_marks_and_reviews",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_book_marks_and_reviews(
                user_id=current_user_id,
                book_id=book_id,
                include_chapter=include_chapter,
                highlight_style=highlight_style,
                max_review_items=max_review_items,
                max_mark_items=max_mark_items,
            ),
        )

    @tool("export_weread_book_notes_to_markdown")
    async def export_weread_book_notes_to_markdown(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        source_type: Annotated[
            Literal["marks", "reviews", "both"],
            Field(description="要导出到 markdown 的内容类型，可选 marks、reviews、both。"),
        ] = "both",
        include_chapter: Annotated[
            bool, Field(description="是否按章节组织写入本地 markdown；为 false 时写入平铺结构。")
        ] = True,
        highlight_style: Annotated[
            int | None, Field(description="仅当 source_type 包含 marks 时生效；可选的划线高亮样式筛选。")
        ] = None,
        present_to_user: Annotated[
            bool, Field(description="导出完成后是否将生成的 markdown 文件登记为可展示 artifact。")
        ] = True,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
        *,
        runtime: ToolRuntime,
    ) -> Command:
        """将当前用户某本书的划线、想法或两者一起导出为本地 markdown 文件。

        用于导出或同步本地 markdown 文件，返回导出状态和文件元数据，不返回正文内容。
        当 `present_to_user=true` 时，还会把生成出的文件登记为当前会话的 artifact，供前端直接展示。
        """
        return await _run_weread_command_tool_with_runtime(
            tool_name="export_weread_book_notes_to_markdown",
            runtime=runtime,
            user_id=user_id,
            tool_call_id=tool_call_id,
            operation_factory=lambda current_user_id: weread_service.export_weread_book_notes_to_markdown(
                user_id=current_user_id,
                book_id=book_id,
                source_type=source_type,
                include_chapter=include_chapter,
                highlight_style=highlight_style,
            ),
            state_update_factory=(
                (
                    lambda payload: build_present_files_update(
                        [
                            artifact.model_dump(mode="json", exclude_none=True)
                            for artifact in payload.artifacts
                        ]
                    )
                )
                if present_to_user
                else None
            ),
        )

    @tool("query_weread_book_notes")
    async def query_weread_book_notes(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        query: Annotated[str, Field(description="要在这本书的本地笔记中查找的内容、主题或关键词。", min_length=1)],
        source_type: Annotated[
            Literal["marks", "reviews", "both"],
            Field(description="要查询的笔记类型，可选 marks、reviews、both。"),
        ] = "both",
        top_k: Annotated[int, Field(description="最多返回多少条最相关的本地命中片段。", ge=1, le=20)] = 5,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """在当前用户某本书的本地笔记中检索相关片段。

        用于在单本书的本地笔记中查找相关命中片段，适合主题检索和模糊回忆式查找。
        它优先使用本地 markdown 和 SQLite 索引，只返回相关片段，不返回全文。
        如果本地还没有对应的 marks/reviews，会先同步并建索引，再执行查询。
        """
        return await _run_weread_tool_with_runtime(
            tool_name="query_weread_book_notes",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.query_weread_book_notes(
                user_id=current_user_id,
                book_id=book_id,
                source_type=source_type,
                query=query,
                top_k=top_k,
            ),
        )

    @tool("get_weread_book_reviews")
    # Routing detail lives in skills/public/weread/SKILL.md.
    # Keep runtime tool schema minimal; retain the previous longer wording here:
    # - "默认返回多少条代表性的想法/点评预览。需要完整内容时优先改用导出工具。"
    # - 当前实际逻辑是按现有顺序取前几条 review，再截断每条文本长度，不做“代表性”挑选。
    async def get_weread_book_reviews(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        include_chapter: Annotated[bool, Field(description="是否返回章节信息。")] = True,
        max_items: Annotated[
            int,
            Field(
                description="返回前多少条想法/点评预览。",
                ge=1,
                le=10,
            ),
        ] = REVIEW_PREVIEW_DEFAULT_ITEMS,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """根据 `book_id` 获取当前用户在该书上的前几条想法/点评预览。"""
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_book_reviews",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_weread_book_reviews(
                user_id=current_user_id,
                book_id=book_id,
                include_chapter=include_chapter,
                max_items=max_items,
            ),
        )

    @tool("get_weread_book_marks")
    # Routing detail lives in skills/public/weread/SKILL.md.
    # Keep runtime tool schema minimal; retain the previous longer wording here:
    # - "默认返回多少条代表性的划线预览。需要完整内容时优先改用导出工具。"
    # - 当前实际逻辑是按现有顺序取前几条 mark，再截断每条文本长度，不做“代表性”挑选。
    async def get_weread_book_marks(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        include_chapter: Annotated[bool, Field(description="是否返回章节分组信息。")] = True,
        highlight_style: Annotated[
            int | None, Field(description="可选的划线高亮样式筛选；不传则返回全部样式。")
        ] = None,
        max_items: Annotated[
            int,
            Field(
                description="返回前多少条划线预览。",
                ge=1,
                le=10,
            ),
        ] = MARK_PREVIEW_DEFAULT_ITEMS,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """根据 `book_id` 获取当前用户在该书上的前几条划线预览。"""
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_book_marks",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_weread_book_marks(
                user_id=current_user_id,
                book_id=book_id,
                include_chapter=include_chapter,
                highlight_style=highlight_style,
                max_items=max_items,
            ),
        )

    @tool("get_weread_book_best_reviews")
    async def get_weread_book_best_reviews(
        book_id: Annotated[
            str, Field(description="微信读书中的书籍 ID。通常先通过 resolve_weread_book 获得。", min_length=1)
        ],
        count: Annotated[int, Field(description="最多获取多少条热门公开点评。", ge=1, le=30)] = 10,
        max_idx: Annotated[int, Field(description="上游接口使用的分页游标。", ge=0)] = 0,
        synckey: Annotated[int, Field(description="上游接口使用的同步键。", ge=0)] = 0,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """根据书的 book_id，获取其他用户对该书的热门公开点评。"""
        return await _run_weread_tool_with_runtime(
            tool_name="get_weread_book_best_reviews",
            runtime=runtime,
            user_id=user_id,
            operation_factory=lambda current_user_id: weread_service.get_book_best_reviews(
                user_id=current_user_id,
                book_id=book_id,
                count=count,
                max_idx=max_idx,
                synckey=synckey,
            ),
        )

    return [
        get_weread_bookshelf_snapshot,
        search_weread_bookshelf,
        resolve_weread_book,
        get_weread_book_reading_status,
        get_weread_book_note_counts,
        query_weread_book_notes,
        get_weread_book_marks,
        get_weread_book_reviews,
        export_weread_book_notes_to_markdown,
        get_weread_book_marks_and_reviews,
        get_weread_book_best_reviews,
    ]
