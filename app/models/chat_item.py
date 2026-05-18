"""Chat timeline item model for web conversation history."""

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.chat_thread import ChatThread
    from app.models.user import User


class ChatItemType(StrEnum):
    """Supported persistent UI timeline item types."""

    USER = "user"
    ASSISTANT = "assistant"
    CLARIFICATION = "clarification"
    CAPABILITY_NOTICE = "capability_notice"
    MILESTONE = "milestone"


class ChatItem(BaseModel, table=True):
    """Persistent UI timeline item shown in the chat panel."""

    __tablename__ = "chat_items"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(foreign_key="chat_threads.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    seq: int = Field(index=True)
    item_type: str = Field(default=ChatItemType.ASSISTANT.value, index=True, max_length=50)
    content: str
    item_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )

    thread: "ChatThread" = Relationship(back_populates="items")
    user: "User" = Relationship(back_populates="chat_items")
