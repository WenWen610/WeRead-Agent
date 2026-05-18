"""This file contains the schemas for the application."""

from app.schemas.auth import Token
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatTurnResponse,
    Message,
    StreamResponse,
)
from app.schemas.weread import (
    WeReadBindCookieRequest,
    WeReadBindingMutationResponse,
    WeReadBindingResponse,
    WeReadRecentBooksResponse,
)

__all__ = [
    "Token",
    "ChatRequest",
    "ChatResponse",
    "ChatTurnResponse",
    "Message",
    "StreamResponse",
    "WeReadBindCookieRequest",
    "WeReadBindingMutationResponse",
    "WeReadBindingResponse",
    "WeReadRecentBooksResponse",
]
