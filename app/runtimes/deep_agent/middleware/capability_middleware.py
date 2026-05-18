"""Middleware for translating blocking tool capability states into thread state.

Deep Agent variant: does NOT interrupt the agent loop (no goto="end"),
so the LLM can autonomously call connect_weread when it sees a binding error.
"""

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from app.infrastructure.logging import get_logger

if TYPE_CHECKING:
    from app.models.weread_binding import WeReadBinding

logger = get_logger(__name__)


class CapabilityMiddleware(AgentMiddleware):
    """Handle provider capability requirements such as missing WeRead bindings.

    Unlike the create_agent variant, this does NOT call goto="end" on
    capability errors, so the LLM sees the tool result and can respond
    by calling connect_weread autonomously.
    """

    @staticmethod
    def _parse_tool_payload(message: ToolMessage) -> dict[str, Any] | None:
        content = message.content
        if not isinstance(content, str) or not content.strip():
            return None
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _build_capability_requirement(
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        status = payload.get("status")
        if not isinstance(status, str):
            return None
        if "weread" not in tool_name:
            return None
        if status == "binding_required":
            return {
                "provider": "weread",
                "reason": "binding_required",
                "message": "需要先连接微信读书后才能继续查询。",
                "action": "connect_weread",
                "source_tool": tool_name,
            }
        if status == "reauth_required":
            return {
                "provider": "weread",
                "reason": "reauth_required",
                "message": "微信读书登录已失效，请重新连接。",
                "action": "connect_weread",
                "source_tool": tool_name,
            }
        return None

    async def _get_weread_binding(self, user_id: int) -> "WeReadBinding | None":
        from app.features.weread.stores.binding_store import weread_binding_store

        return await weread_binding_store.get_weread_binding(user_id)

    async def _clear_resolved_capability_requirement(self, state, runtime) -> dict[str, Any] | None:
        pending = state.get("pending_capability_requirement")
        if not pending:
            return None
        provider = pending.get("provider")
        if provider != "weread":
            return None
        user_id = getattr(getattr(runtime, "context", None), "user_id", None)
        if user_id is None:
            return None
        binding = await self._get_weread_binding(user_id)
        if binding is None or binding.status != "active":
            return None
        logger.info("pending_capability_requirement_cleared", provider=provider, user_id=user_id)
        return {"pending_capability_requirement": None}

    @staticmethod
    def _repair_missing_tool_messages(request: ModelRequest) -> ModelRequest:
        pending_tool_call_ids: list[str] = []
        satisfied_tool_call_ids: set[str] = set()

        for message in request.messages:
            if isinstance(message, dict):
                role = message.get("role")
                if role == "assistant":
                    tool_calls = message.get("tool_calls") or []
                    if isinstance(tool_calls, list):
                        for tool_call in tool_calls:
                            if not isinstance(tool_call, dict):
                                continue
                            tool_call_id = tool_call.get("id")
                            if isinstance(tool_call_id, str) and tool_call_id:
                                pending_tool_call_ids.append(tool_call_id)
                    continue
                if role == "tool":
                    tool_call_id = message.get("tool_call_id")
                    if isinstance(tool_call_id, str) and tool_call_id:
                        satisfied_tool_call_ids.add(tool_call_id)
                    continue

            if isinstance(message, AIMessage):
                tool_calls = getattr(message, "tool_calls", None) or []
                for tool_call in tool_calls:
                    tool_call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
                    if isinstance(tool_call_id, str) and tool_call_id:
                        pending_tool_call_ids.append(tool_call_id)
                continue

            if isinstance(message, ToolMessage):
                tool_call_id = getattr(message, "tool_call_id", "")
                if isinstance(tool_call_id, str) and tool_call_id:
                    satisfied_tool_call_ids.add(tool_call_id)

        missing_tool_call_ids = [
            tid for tid in pending_tool_call_ids if tid not in satisfied_tool_call_ids
        ]
        if not missing_tool_call_ids:
            return request

        logger.warning(
            "missing_tool_messages_recovered",
            missing_count=len(missing_tool_call_ids),
            missing_tool_call_ids=missing_tool_call_ids,
        )
        repaired_messages = list(request.messages)
        for tool_call_id in missing_tool_call_ids:
            repaired_messages.append(
                ToolMessage(
                    content=json.dumps(
                        {
                            "status": "middleware_recovered_missing_tool_message",
                            "tool_call_id": tool_call_id,
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=tool_call_id,
                    name="middleware_recovery",
                )
            )
        return request.override(messages=repaired_messages)

    @override
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        _ = (state, runtime)
        return None

    @override
    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        return await self._clear_resolved_capability_requirement(state, runtime)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        logger.info(
            "capability_middleware_model_call",
            message_count=len(request.messages),
            has_system_prompt=bool(request.system_prompt),
        )
        return handler(self._repair_missing_tool_messages(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        logger.info(
            "capability_middleware_model_call",
            message_count=len(request.messages),
            has_system_prompt=bool(request.system_prompt),
        )
        return await handler(self._repair_missing_tool_messages(request))

    def _handle_tool_result(
        self,
        tool_name: str,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        if not isinstance(result, ToolMessage):
            return result
        payload = self._parse_tool_payload(result)
        if payload is None:
            return result
        requirement = self._build_capability_requirement(tool_name, payload)
        if requirement is None:
            return result

        logger.info(
            "capability_requirement_detected",
            provider=requirement["provider"],
            reason=requirement["reason"],
            source_tool=tool_name,
        )
        # NOTE: no goto="end" — let the LLM see the error and respond
        return Command(
            update={
                "messages": [result],
                "pending_capability_requirement": requirement,
            },
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        return self._handle_tool_result(request.tool_call.get("name", ""), result)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        return self._handle_tool_result(request.tool_call.get("name", ""), result)
