"""This file contains the user model for the application."""

from typing import (
    TYPE_CHECKING,
    List,
)

import bcrypt
from sqlmodel import (
    Field,
    Relationship,
)

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.chat_item import ChatItem
    from app.models.chat_thread import ChatThread
    from app.models.session import Session


class User(BaseModel, table=True):
    """User model for storing user accounts and related app data."""

    id: int = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    sessions: List["Session"] = Relationship(back_populates="user")
    chat_threads: List["ChatThread"] = Relationship(back_populates="user")
    chat_items: List["ChatItem"] = Relationship(back_populates="user")

    def verify_password(self, password: str) -> bool:
        """Verify if the provided password matches the hash."""
        return bcrypt.checkpw(password.encode("utf-8"), self.hashed_password.encode("utf-8"))

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# Avoid circular imports
from app.models.chat_item import ChatItem  # noqa: E402
from app.models.chat_thread import ChatThread  # noqa: E402
from app.models.session import Session  # noqa: E402
