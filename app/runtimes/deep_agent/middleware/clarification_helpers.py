"""Shared helpers for building clarification state and interrupt commands."""

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.types import Command

from app.runtimes.deep_agent.middleware.clarification_types import ClarificationType, coerce_clarification_type
from app.runtimes.deep_agent.state import BookReferenceState


CLARIFICATION_TYPE_LABELS = {
    ClarificationType.MISSING_INFO: "需要补充信息",
    ClarificationType.AMBIGUOUS_REQUIREMENT: "需要确认需求",
    ClarificationType.CANDIDATE_SELECTION: "需要确认目标对象",
    ClarificationType.RISK_CONFIRMATION: "需要确认操作",
    ClarificationType.SUGGESTION: "需要确认建议",
}


def build_pending_clarification(
    *,
    clarification_type: str | ClarificationType,
    question: str,
    source_tool: str,
    context: str | None = None,
    options: list[str] | None = None,
    candidates: list[BookReferenceState] | None = None,
) -> dict[str, Any]:
    normalized_clarification_type = coerce_clarification_type(clarification_type)
    normalized_question = question.strip()
    normalized_context = (context or "").strip() or None
    normalized_options = [str(option).strip() for option in (options or []) if str(option).strip()]

    pending_clarification: dict[str, Any] = {
        "clarification_type": normalized_clarification_type,
        "question": normalized_question,
        "source_tool": source_tool,
    }
    if normalized_context:
        pending_clarification["context"] = normalized_context
    if normalized_options:
        pending_clarification["options"] = normalized_options
    if candidates:
        pending_clarification["candidates"] = candidates
    return pending_clarification


def format_clarification_message(
    *,
    clarification_type: str | ClarificationType,
    question: str,
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    normalized_clarification_type = coerce_clarification_type(clarification_type)
    normalized_question = question.strip()
    normalized_context = (context or "").strip() or None
    normalized_options = [str(option).strip() for option in (options or []) if str(option).strip()]

    lines: list[str] = [CLARIFICATION_TYPE_LABELS.get(normalized_clarification_type, "需要澄清")]
    if normalized_context:
        lines.append(normalized_context)
    if normalized_question:
        lines.append(normalized_question)
    if normalized_options:
        lines.append("可选项：")
        lines.extend(f"{index}. {option}" for index, option in enumerate(normalized_options, start=1))
    return "\n".join(lines)


def build_clarification_command(
    *,
    clarification_type: str | ClarificationType,
    question: str,
    source_tool: str,
    tool_call_id: str,
    context: str | None = None,
    options: list[str] | None = None,
    candidates: list[BookReferenceState] | None = None,
    message_name: str = "ask_clarification",
    additional_updates: dict[str, Any] | None = None,
) -> Command:
    pending_clarification = build_pending_clarification(
        clarification_type=clarification_type,
        question=question,
        source_tool=source_tool,
        context=context,
        options=options,
        candidates=candidates,
    )
    message_content = format_clarification_message(
        clarification_type=clarification_type,
        question=question,
        context=context,
        options=options,
    )

    update: dict[str, Any] = {
        "pending_clarification": pending_clarification,
        "messages": [
            ToolMessage(
                content=message_content,
                name=message_name,
                tool_call_id=tool_call_id,
            )
        ],
    }
    if additional_updates:
        update.update(additional_updates)

    return Command(update=update, goto=END)
