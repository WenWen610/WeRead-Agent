"""Middleware that blocks deepagents built-in filesystem tools from the main agent."""

from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.infrastructure.logging import get_logger
from app.runtimes.deep_agent.state import ThreadState

logger = get_logger(__name__)

_EXCLUDED_TOOLS = frozenset(
    {
        "glob",
        "grep",
        "edit_file",
        "execute",
        "write_todos",
    }
)


class ToolExclusionMiddleware(AgentMiddleware[ThreadState]):
    """Block deepagents built-in filesystem/shell/todo tools from the main agent."""

    state_schema = ThreadState

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        tool_name = request.tool_call.get("name", "")
        if tool_name not in _EXCLUDED_TOOLS:
            return handler(request)

        logger.info("tool_exclusion_blocked", tool_name=tool_name)
        return ToolMessage(
            content=f"Error: '{tool_name}' is not available to this agent. Use the provided WeRead tools instead.",
            tool_call_id=request.tool_call.get("id", ""),
            name=tool_name,
        )

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = request.tool_call.get("name", "")
        if tool_name not in _EXCLUDED_TOOLS:
            return await handler(request)

        logger.info("tool_exclusion_blocked", tool_name=tool_name)
        return ToolMessage(
            content=f"Error: '{tool_name}' is not available to this agent. Use the provided WeRead tools instead.",
            tool_call_id=request.tool_call.get("id", ""),
            name=tool_name,
        )
