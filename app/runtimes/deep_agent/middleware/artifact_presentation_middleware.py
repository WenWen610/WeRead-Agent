"""Middleware: `present_weread_book_document` uses `return_direct=True` to end the turn."""

from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.infrastructure.logging import get_logger
from app.runtimes.deep_agent.state import ThreadState

logger = get_logger(__name__)


class ArtifactPresentationMiddleware(AgentMiddleware[ThreadState]):
    """Pass-through middleware for artifact presentation.

    Turn termination is handled by `return_direct=True` on the
    `present_weread_book_document` tool definition in tools.py, not by
    this middleware.
    """

    state_schema = ThreadState

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return await handler(request)
