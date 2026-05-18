"""ChannelManager: lifecycle orchestration for chat platform channels."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.channels.base import BaseChannel
from app.channels.bus import MessageBus, OutboundMessage
from app.channels.weixin import WeixinChannel
from app.channels.qq import QQChannel
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

# Map channel name → factory
_CHANNEL_FACTORIES: Mapping[str, type[BaseChannel]] = {
    "weixin": WeixinChannel,
    "qq": QQChannel,
}


class ChannelManager:
    """Start/stop channels, dispatch outbound messages."""

    def __init__(self, bus: MessageBus) -> None:
        self.bus = bus
        self._channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task[Any] | None = None

    # ── Channel lifecycle ───────────────────────────────────────────────

    def add(self, channel_name: str, **config: Any) -> BaseChannel:
        cls = _CHANNEL_FACTORIES.get(channel_name)
        if cls is None:
            raise ValueError(f"Unknown channel: {channel_name}")
        channel = cls(bus=self.bus, **config)
        self._channels[channel_name] = channel
        return channel

    async def start_all(self) -> None:
        tasks = []
        for name, channel in self._channels.items():
            if channel.has_token:
                logger.info("channel_starting", name=name)
                tasks.append(asyncio.create_task(self._start_channel(name, channel)))
            else:
                logger.warning("channel_skipped_no_token", name=name)

        if tasks:
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        try:
            await channel.start()
        except Exception:
            logger.exception("channel_start_failed", name=name)

    async def stop_all(self) -> None:
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None

        for channel in self._channels.values():
            await channel.stop()

    def get_channel(self, name: str) -> BaseChannel | None:
        return self._channels.get(name)

    def get_channels(self) -> dict[str, BaseChannel]:
        return dict(self._channels)

    # ── Outbound dispatch ───────────────────────────────────────────────

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                msg = await self.bus.consume_outbound()
                channel = self._channels.get(msg.channel)
                if channel is None:
                    logger.warning("channel_dispatch_unknown", channel=msg.channel)
                    continue
                await channel.send(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("channel_dispatch_error")

    # ── Convenience: send from agent code ───────────────────────────────

    async def send_text(self, channel: str, chat_id: str, text: str) -> None:
        await self.bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=text)
        )
