"""Chat thread model for web conversation history."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, List

from sqlmodel import Field, Relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.chat_item import ChatItem
    from app.models.user import User


class ChatThread(BaseModel, table=True):
    """Persistent chat thread shown in the web sidebar."""

    __tablename__ = "chat_threads"

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    title: str = Field(default="", max_length=200)
    last_item_preview: str = Field(default="", max_length=500)
    last_item_type: str | None = Field(default=None, max_length=50, index=True)
    last_message_at: datetime | None = Field(default=None, index=True)
    item_count: int = Field(default=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    archived_at: datetime | None = Field(default=None, index=True)

    user: "User" = Relationship(back_populates="chat_threads")
    items: List["ChatItem"] = Relationship(back_populates="thread")
