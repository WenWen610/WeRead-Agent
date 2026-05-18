"""This file contains the chat schema for the application."""

import re
from datetime import datetime
from typing import Any
from typing import (
    List,
    Literal,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


class Message(BaseModel):
    """Message model for chat endpoint.

    Attributes:
        role: The role of the message sender (user or assistant).
        content: The content of the message.
    """

    model_config = {"extra": "ignore"}

    role: Literal["user", "assistant", "system"] = Field(..., description="The role of the message sender")
    content: str = Field(..., description="The content of the message", min_length=1)

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate the message content.

        Args:
            v: The content to validate

        Returns:
            str: The validated content

        Raises:
            ValueError: If the content contains disallowed patterns
        """
        # Check for potentially harmful content
        if re.search(r"<script.*?>.*?</script>", v, re.IGNORECASE | re.DOTALL):
            raise ValueError("Content contains potentially harmful script tags")

        # Check for null bytes
        if "\0" in v:
            raise ValueError("Content contains null bytes")

        return v

    @model_validator(mode="after")
    def validate_content_length(self) -> "Message":
        """Apply role-aware content length limits."""
        if self.role == "system":
            if len(self.content) > 20000:
                raise ValueError("System message content exceeds maximum length of 20000 characters")
            return self

        if len(self.content) > 3000:
            raise ValueError("Message content exceeds maximum length of 3000 characters")

        return self


class ChatRequest(BaseModel):
    """Request model for chat endpoint.

    Attributes:
        message: The latest user message for this turn.
        thread_id: Optional thread identifier. Defaults to the current session id.
    """

    message: str = Field(
        ...,
        description="The latest user message for this conversation turn",
        min_length=1,
        max_length=3000,
    )
    thread_id: str | None = Field(
        default=None,
        description="Optional conversation thread id. If omitted, the current session id is used.",
    )
    web_search_enabled: bool | None = Field(
        default=None,
        description="Optional per-request web search toggle for Deep Agent routes. If omitted, server default applies.",
    )


class ChatResponse(BaseModel):
    """Response model for chat endpoint.

    Attributes:
        messages: List of messages in the conversation.
    """

    messages: List[Message] = Field(..., description="List of messages in the conversation")


class ChatThreadCreateRequest(BaseModel):
    """Request model for creating a persistent chat thread."""

    title: str = Field(default="", description="Optional initial thread title", max_length=200)


class ChatThreadUpdateRequest(BaseModel):
    """Request model for updating a persistent chat thread."""

    title: str = Field(..., description="Updated thread title", min_length=1, max_length=200)


class ChatThreadSummary(BaseModel):
    """Sidebar summary for a persistent chat thread."""

    thread_id: str = Field(..., description="Unique chat thread id")
    title: str = Field(default="", description="Thread title")
    last_item_preview: str = Field(default="", description="Preview of the latest persisted timeline item")
    last_item_type: str | None = Field(default=None, description="Type of the latest persisted timeline item")
    last_message_at: datetime | None = Field(default=None, description="Timestamp of the latest persisted item")
    item_count: int = Field(default=0, description="Number of persisted timeline items")
    created_at: datetime = Field(..., description="When the thread was created")
    updated_at: datetime = Field(..., description="When the thread was last updated")
    archived_at: datetime | None = Field(default=None, description="When the thread was archived")


class ChatThreadListResponse(BaseModel):
    """Response model for listing persistent chat threads."""

    threads: List[ChatThreadSummary] = Field(default_factory=list, description="Persistent chat threads")


class ChatTimelineItem(BaseModel):
    """Persistent UI timeline item for the chat panel."""

    id: int = Field(..., description="Persistent item id")
    thread_id: str = Field(..., description="Owning thread id")
    seq: int = Field(..., description="Stable per-thread sequence number")
    item_type: Literal["user", "assistant", "clarification", "capability_notice", "milestone"] = Field(
        ...,
        description="Persistent UI timeline item type",
    )
    content: str = Field(..., description="Display content for the timeline item")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional structured item payload")
    created_at: datetime = Field(..., description="When the timeline item was created")


class ChatTimelineResponse(BaseModel):
    """Response model for a thread's persistent timeline."""

    items: List[ChatTimelineItem] = Field(default_factory=list, description="Persistent timeline items")


class ChatTurnResponse(BaseModel):
    """Unified non-stream chat response aligned with stream semantics."""

    type: Literal["answer", "clarification", "capability_required"] = Field(
        ...,
        description="The final chat result type",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Final chat payload",
    )


class StreamResponse(BaseModel):
    """Response model for streaming chat endpoint.

    Attributes:
        content: The content of the current chunk.
        done: Whether the stream is complete.
    """

    content: str = Field(default="", description="The content of the current chunk")
    done: bool = Field(default=False, description="Whether the stream is complete")


class StreamEvent(BaseModel):
    """Structured stream event for SSE responses."""

    type: Literal[
        "chunk",
        "status",
        "clarification",
        "capability_required",
        "artifact",
        "todo_update",
        "tool_call",
        "tool_result",
        "subagent_start",
        "subagent_update",
        "subagent_end",
        "final",
        "error",
        "done",
    ] = Field(
        ...,
        description="The stream event type",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event payload for the current stream item",
    )
