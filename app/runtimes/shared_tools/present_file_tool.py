"""Generic artifact presentation tool for runtime agents."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.logging import get_logger

logger = get_logger(__name__)


class PresentableArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    artifact_id: str = Field(..., description="Stable unique identifier for the artifact.")
    kind: str = Field(..., description="Artifact kind, such as weread_markdown.")
    name: str = Field(..., description="Display name shown to the user.")
    generated_at: str = Field(default="", description="Artifact generation timestamp.")
    doc_id: str | None = Field(default=None, description="Optional document identifier.")
    book_id: str | None = Field(default=None, description="Optional related book identifier.")
    book_title: str | None = Field(default=None, description="Optional related book title.")
    source_type: str | None = Field(default=None, description="Optional artifact source subtype.")


def build_present_files_update(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize artifacts into a thread-state update payload."""
    normalized_artifacts = [
        PresentableArtifact.model_validate(artifact).model_dump(mode="json", exclude_none=True)
        for artifact in artifacts
    ]
    return {"artifacts": normalized_artifacts}


def _serialize_present_files_payload(artifacts: list[dict[str, Any]]) -> str:
    return json.dumps(
        {
            "status": "presented",
            "artifacts_count": len(artifacts),
            "artifacts": artifacts,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool("present_files")
def present_files_tool(
    artifacts: Annotated[
        list[PresentableArtifact],
        Field(description="One or more local artifacts that should be shown to the user."),
    ],
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    *,
    runtime: ToolRuntime,
) -> Command:
    """Register local files or artifacts for frontend display.

    Use this tool after another tool has already generated local files and returned their artifact metadata.
    It does not read file contents. It only adds artifact references into the current thread state so the frontend
    can render file cards and open them through dedicated preview endpoints.
    """
    _ = runtime
    artifact_payload = [artifact.model_dump(mode="json", exclude_none=True) for artifact in artifacts]
    logger.info(
        "present_files_registered",
        artifacts_count=len(artifact_payload),
        artifact_ids=[artifact["artifact_id"] for artifact in artifact_payload],
    )
    # Command(update=...) 是 LangGraph state patch，不是直接发给前端的事件。
    #
    # - messages: [ToolMessage(...)] 会追加一条工具结果消息，保证 provider
    #   tool-call 协议合法：assistant tool_call 后面必须跟匹配的
    #   ToolMessage(tool_call_id=...)。
    #
    # - artifacts: [...] 是阅读助手的业务 state。LangGraph 会把它合并进
    #   ThreadState.artifacts；AgentClient 后续在 "updates" stream 里看到这个
    #   key，再转换成 StreamEvent(type="artifact")。
    return Command(
        update={
            "messages": [
                ToolMessage(
                    _serialize_present_files_payload(artifact_payload),
                    tool_call_id=tool_call_id,
                )
            ],
            **build_present_files_update(artifact_payload),
        }
    )
