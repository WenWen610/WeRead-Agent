"""Middleware for intercepting clarification requests and ending the current turn."""

from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.runtimes.deep_agent.middleware.clarification_types import coerce_clarification_type
from app.runtimes.deep_agent.middleware.clarification_helpers import build_clarification_command
from app.runtimes.deep_agent.state import ThreadState
from app.infrastructure.logging import get_logger


logger = get_logger(__name__)


class ClarificationMiddleware(AgentMiddleware[ThreadState]):
    """Intercept `ask_clarification` and turn it into an end-of-turn question."""

    state_schema = ThreadState

    def _build_interrupt_command(self, request: ToolCallRequest) -> Command:
        args = request.tool_call.get("args", {})
        tool_call_id = request.tool_call.get("id", "")

        logger.info(
            "clarification_request_intercepted",
            clarification_type=args.get("clarification_type"),
            tool_call_id=tool_call_id,
        )

        return build_clarification_command(
            clarification_type=coerce_clarification_type(args.get("clarification_type")),
            question=str(args.get("question") or ""),
            context=args.get("context"),
            options=args.get("options") if isinstance(args.get("options"), list) else None,
            source_tool="ask_clarification",
            message_name="ask_clarification",
            tool_call_id=tool_call_id,
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "ask_clarification":
            return handler(request)
        return self._build_interrupt_command(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call.get("name") != "ask_clarification":
            return await handler(request)
        return self._build_interrupt_command(request)
