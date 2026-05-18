"""Middleware for syncing WeRead tool results into thread state."""

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.runtimes.deep_agent.middleware.clarification_types import ClarificationType
from app.runtimes.deep_agent.middleware.clarification_helpers import build_clarification_command
from app.runtimes.deep_agent.state import BookReferenceState, ThreadState
from app.infrastructure.logging import get_logger


logger = get_logger(__name__)


class WeReadStateMiddleware(AgentMiddleware[ThreadState]):
    """Persist WeRead resolution results into short-term thread state."""

    state_schema = ThreadState

    _CHINESE_NUMERAL_MAP = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    _CANCEL_KEYWORDS = (
        "算了",
        "不用了",
        "不查了",
        "先不查",
        "先不看",
        "取消",
        "换个话题",
        "换一个问题",
    )
    _NEW_QUERY_KEYWORDS = (
        "帮我",
        "请帮我",
        "我想",
        "看看",
        "看下",
        "查一下",
        "查询",
        "最近",
        "哪些",
        "有没有",
        "怎么",
        "为什么",
        "总结",
        "分析",
        "推荐",
        "另外",
        "换一个",
        "换个",
        "再问",
        "顺便",
    )

    def _extract_latest_human_reply(self, state: ThreadState) -> str | None:
        messages = state.get("messages") or []
        if not messages:
            return None

        latest_message = messages[-1]
        if getattr(latest_message, "type", None) != "human":
            return None

        content = getattr(latest_message, "content", "")
        if not isinstance(content, str):
            return None

        reply = content.strip()
        return reply or None

    def _normalize_text(self, value: str) -> str:
        lowered = value.strip().lower()
        return re.sub(r"[\s\"'“”‘’《》()（）,，.。:：;；!?！？/_-]+", "", lowered)

    def _extract_choice_index(self, reply: str, candidate_count: int) -> int | None:
        digit_match = re.search(r"第?\s*(\d+)\s*(本|个|项|条|个选项|号)?", reply)
        if digit_match:
            index = int(digit_match.group(1))
            if 1 <= index <= candidate_count:
                return index - 1

        chinese_match = re.search(r"第?\s*([一二两三四五六七八九十])\s*(本|个|项|条|个选项|号)?", reply)
        if chinese_match:
            index = self._CHINESE_NUMERAL_MAP.get(chinese_match.group(1))
            if index is not None and 1 <= index <= candidate_count:
                return index - 1

        return None

    def _match_candidate_by_reply(
        self,
        reply: str,
        candidates: list[BookReferenceState],
    ) -> BookReferenceState | None:
        reply_normalized = self._normalize_text(reply)
        if not reply_normalized:
            return None

        choice_index = self._extract_choice_index(reply, len(candidates))
        if choice_index is not None:
            return candidates[choice_index]

        matches: list[BookReferenceState] = []
        for candidate in candidates:
            searchable_values = [
                candidate.get("title") or "",
                candidate.get("author") or "",
                candidate.get("translator") or "",
                self._format_candidate_option(candidate),
            ]
            normalized_values = [self._normalize_text(value) for value in searchable_values if value]

            if any(
                reply_normalized in normalized_value or normalized_value in reply_normalized
                for normalized_value in normalized_values
                if normalized_value
            ):
                matches.append(candidate)

        if len(matches) == 1:
            return matches[0]
        return None

    def _classify_pending_reply(
        self,
        reply: str,
        candidates: list[BookReferenceState],
    ) -> Literal["answer_clarification", "new_query", "cancel", "unclear"]:
        normalized_reply = self._normalize_text(reply)
        if not normalized_reply:
            return "unclear"

        if any(keyword in reply for keyword in self._CANCEL_KEYWORDS):
            return "cancel"

        if self._extract_choice_index(reply, len(candidates)) is not None:
            return "answer_clarification"

        if self._match_candidate_by_reply(reply, candidates) is not None:
            return "answer_clarification"

        if any(keyword in reply for keyword in self._NEW_QUERY_KEYWORDS):
            return "new_query"

        if ("?" in reply or "？" in reply) and len(normalized_reply) >= 6:
            return "new_query"

        if len(normalized_reply) >= 10:
            return "new_query"

        return "unclear"

    def _consume_pending_candidate_selection(self, state: ThreadState) -> dict[str, Any] | None:
        pending = state.get("pending_clarification")
        if not pending:
            return None

        reply = self._extract_latest_human_reply(state)
        if reply is None:
            return None

        clarification_type = pending.get("clarification_type")
        if clarification_type != ClarificationType.CANDIDATE_SELECTION:
            logger.info("pending_clarification_cleared_after_user_reply", clarification_type=clarification_type)
            return {"pending_clarification": None}

        candidates = pending.get("candidates") or []
        if not isinstance(candidates, list) or not candidates:
            logger.info("pending_candidate_selection_missing_candidates")
            return {"pending_clarification": None}

        reply_kind = self._classify_pending_reply(reply, candidates)
        logger.info(
            "pending_candidate_selection_classified",
            reply=reply,
            reply_kind=reply_kind,
            candidate_count=len(candidates),
        )

        if reply_kind in {"cancel", "new_query"}:
            logger.info("pending_candidate_selection_cleared", reply_kind=reply_kind)
            return {"pending_clarification": None}

        if reply_kind == "unclear":
            return None

        matched_book = self._match_candidate_by_reply(reply, candidates)
        if matched_book is None:
            logger.info(
                "pending_candidate_selection_not_matched",
                reply=reply,
                candidate_count=len(candidates),
            )
            return None

        logger.info(
            "pending_candidate_selection_resolved",
            reply=reply,
            book_id=matched_book["book_id"],
            title=matched_book.get("title"),
        )
        return {
            "current_book": matched_book,
            "recent_books": [matched_book],
            "pending_clarification": None,
        }

    def _build_current_book_prompt_patch(self, state: ThreadState) -> str | None:
        current_book = state.get("current_book")
        if not current_book:
            return None

        title = current_book.get("title") or "未知书名"
        author = current_book.get("author") or "未知作者"
        book_id = current_book.get("book_id")
        translator = current_book.get("translator")

        lines = [
            "<current_book_context>",
            "当前会话已确认的目标书籍：",
            f"- title: {title}",
            f"- author: {author}",
        ]
        if translator:
            lines.append(f"- translator: {translator}")
        if book_id:
            lines.append(f"- book_id: {book_id}")
        lines.extend(
            [
                "- 如果用户使用“这本书”“它”“这本”等指代，且没有明确提出新的书名或作者，优先指代这本书。",
                "- 如果用户明确提出新的书名、作者或新的检索目标，应以新的目标为准，不要被当前书籍误导。",
                "</current_book_context>",
            ]
        )
        return "\n".join(lines)

    def _patch_model_request(self, request: ModelRequest) -> ModelRequest:
        patch = self._build_current_book_prompt_patch(request.state)
        if patch is None:
            return request

        logger.info(
            "current_book_context_injected",
            book_id=request.state["current_book"].get("book_id"),
            title=request.state["current_book"].get("title"),
        )

        tool_call_id = f"current_book_{uuid.uuid4().hex[:8]}"
        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": tool_call_id,
                    "name": "current_book_context",
                    "args": {},
                }
            ],
        )
        tool_message = ToolMessage(
            content=patch,
            tool_call_id=tool_call_id,
            name="current_book_context",
        )
        return request.override(messages=[*list(request.messages), ai_message, tool_message])

    def _parse_tool_payload(self, message: ToolMessage) -> dict[str, Any] | None:
        content = message.content
        if not isinstance(content, str) or not content.strip():
            return None

        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            logger.info("weread_tool_payload_not_json", tool_name=message.name)
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    def _to_book_reference(self, book_payload: dict[str, Any]) -> BookReferenceState:
        book_state: BookReferenceState = {"book_id": str(book_payload["book_id"])}

        for key in ("title", "author", "translator"):
            value = book_payload.get(key)
            if value is not None:
                book_state[key] = str(value)

        return book_state

    def _format_candidate_option(self, book_state: BookReferenceState) -> str:
        title = book_state.get("title") or "未命名书籍"
        author = book_state.get("author")
        translator = book_state.get("translator")

        parts = [title]
        if author:
            parts.append(author)
        if translator:
            parts.append(f"译者：{translator}")
        return " / ".join(parts)

    def _build_multiple_candidates_command(
        self, result: ToolMessage, payload: dict[str, Any]
    ) -> ToolMessage | Command:
        candidates_payload = payload.get("candidates")
        if not isinstance(candidates_payload, list) or not candidates_payload:
            return result

        candidate_states = [
            self._to_book_reference(candidate)
            for candidate in candidates_payload
            if isinstance(candidate, dict) and candidate.get("book_id")
        ]
        if not candidate_states:
            return result

        keyword = str(payload.get("keyword") or "").strip()
        question = (
            f"我找到了多本和“{keyword}”相关的书，你想查询哪一本？"
            if keyword
            else "我找到了多本相关的书，你想查询哪一本？"
        )
        options = [self._format_candidate_option(book) for book in candidate_states]
        context = "需要先确认目标书籍，才能继续查询单本书详情。"

        logger.info(
            "resolve_weread_book_requires_clarification",
            keyword=keyword or None,
            candidate_count=len(candidate_states),
        )

        return build_clarification_command(
            clarification_type=ClarificationType.CANDIDATE_SELECTION,
            question=question,
            context=context,
            options=options,
            candidates=candidate_states,
            source_tool="resolve_weread_book",
            message_name="resolve_weread_book",
            tool_call_id=result.tool_call_id,
            additional_updates={"current_book": None},
        )

    def _update_from_resolve_result(self, result: ToolMessage) -> ToolMessage | Command:
        payload = self._parse_tool_payload(result)
        if payload is None:
            return result

        status = payload.get("status")
        if status == "multiple_candidates":
            return self._build_multiple_candidates_command(result, payload)

        if status != "resolved":
            return result

        book_payload = payload.get("book")
        if not isinstance(book_payload, dict) or not book_payload.get("book_id"):
            return result

        book_state = self._to_book_reference(book_payload)
        logger.info(
            "thread_current_book_updated",
            book_id=book_state["book_id"],
            title=book_state.get("title"),
        )
        return Command(
            update={
                "messages": [result],
                "current_book": book_state,
                "recent_books": [book_state],
                "pending_clarification": None,
            }
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        if request.tool_call.get("name") != "resolve_weread_book":
            return result
        if not isinstance(result, ToolMessage):
            return result
        return self._update_from_resolve_result(result)

    @override
    def before_agent(self, state: ThreadState, runtime) -> dict[str, Any] | None:
        _ = runtime
        return self._consume_pending_candidate_selection(state)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._patch_model_request(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if request.tool_call.get("name") != "resolve_weread_book":
            return result
        if not isinstance(result, ToolMessage):
            return result
        return self._update_from_resolve_result(result)

    @override
    async def abefore_agent(self, state: ThreadState, runtime) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._patch_model_request(request))
