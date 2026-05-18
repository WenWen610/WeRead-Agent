"""Message bus that decouples channels from the agent runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class InboundMessage:
    """A message received from a chat platform."""
    channel: str
    sender_id: str
    chat_id: str
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    # Non-text media URLs (images, files, voice transcripts, etc.)
    media_urls: list[str] = field(default_factory=list)


@dataclass
class OutboundMessage:
    """A response to send back to a chat platform."""
    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MessageBus:
    """Simple async queue pair between channels and the agent."""

    inbound: asyncio.Queue[InboundMessage]
    outbound: asyncio.Queue[OutboundMessage]

    def __init__(self) -> None:
        self.inbound = asyncio.Queue(maxsize=512)
        self.outbound = asyncio.Queue(maxsize=512)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        return await self.outbound.get()
