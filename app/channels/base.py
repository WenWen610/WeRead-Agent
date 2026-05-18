"""Abstract base class for chat platform channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.channels.bus import InboundMessage, MessageBus, OutboundMessage


class BaseChannel(ABC):
    """Contract every chat platform integration must fulfill.

    Subclass and implement start/stop/send.  Call self._handle_message()
    for each inbound message received from the platform.
    """

    def __init__(self, bus: MessageBus, config: dict[str, Any] | None = None) -> None:
        self.bus = bus
        self.config = config or {}
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Connect to the platform and start listening for messages.

        Must set self._running = True.  Should be a long-running task.
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and clean up.  Set self._running = False."""
        ...

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """Deliver an outbound message through the platform's native API."""
        ...

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        media_urls: list[str] | None = None,
    ) -> None:
        """Validate and publish an inbound message to the bus."""
        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                metadata=metadata or {},
                media_urls=media_urls or [],
            )
        )

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique channel identifier (e.g. 'weixin', 'telegram')."""
        ...
