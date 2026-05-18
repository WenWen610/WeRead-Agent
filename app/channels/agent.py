"""Bridge between channel messages and the DeepAgentClient.

Consumes inbound messages from the bus, streams the agent response,
accumulates text for the final answer, and forwards artifacts
(e.g. WeRead QR codes) as media attachments.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import tempfile
from pathlib import Path
from typing import Any

from app.channels.binding_store import weixin_binding_store
from app.channels.bus import MessageBus
from app.channels.manager import ChannelManager
from app.features.chat.store import chat_store
from app.runtimes.deep_agent.client import DeepAgentClient
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)


class ChannelAgentBridge:
    """Consume inbound channel messages → DeepAgentClient → publish outbound."""

    def __init__(
        self,
        bus: MessageBus,
        manager: ChannelManager,
        agent_client: DeepAgentClient,
    ) -> None:
        """Initialize the bridge with channel and agent dependencies."""
        self.bus = bus
        self.manager = manager
        self.agent_client = agent_client
        self._consumer_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Start consuming inbound channel messages."""
        self._consumer_task = asyncio.create_task(self._consume_loop())
        logger.info("channel_agent_bridge_started")

    async def stop(self) -> None:
        """Stop the inbound message consumer."""
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        logger.info("channel_agent_bridge_stopped")

    async def _consume_loop(self) -> None:
        while True:
            try:
                msg = await self.bus.consume_inbound()
                await self._handle_message(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("channel_agent_bridge_error")

    async def _handle_message(self, msg) -> None:
        session_id = f"{msg.channel}:{msg.chat_id}"
        user_id = 0
        binding = await weixin_binding_store.get_binding(msg.chat_id)
        if binding is not None:
            session_id = binding.thread_id
            user_id = binding.user_id

        logger.info(
            "channel_message_received",
            channel=msg.channel,
            sender_id=msg.sender_id,
            chat_id=msg.chat_id,
            session_id=session_id,
            user_id=user_id,
            content_preview=msg.content[:100],
        )

        accumulated_text: list[str] = []

        try:
            if user_id:
                await chat_store.create_chat_item(
                    thread_id=session_id,
                    user_id=user_id,
                    item_type="user",
                    content=msg.content,
                )

            async for event in self.agent_client.stream(msg.content, session_id, user_id=user_id if user_id else None):
                if event.type == "chunk":
                    accumulated_text.append(event.data.get("content", ""))

                elif event.type == "artifact":
                    await self._handle_artifact(msg, event.data)

                elif event.type == "capability_required":
                    # WeRead reauth/bind needed — LLM will call connect_weread
                    pass

        except Exception:
            logger.exception("channel_agent_chat_failed", session_id=session_id)
            await self.manager.send_text(msg.channel, msg.chat_id, "抱歉，处理你的消息时出错了。")
            return

        answer = "".join(accumulated_text).strip()
        if answer:
            if user_id:
                await chat_store.create_chat_item(
                    thread_id=session_id,
                    user_id=user_id,
                    item_type="assistant",
                    content=answer,
                )
            await self.manager.send_text(msg.channel, msg.chat_id, answer)
            logger.info(
                "channel_response_sent",
                channel=msg.channel,
                chat_id=msg.chat_id,
                response_preview=answer[:100],
            )

    async def _handle_artifact(self, msg, artifact_data: dict) -> None:
        """Send artifact to the chat platform.

        - QR codes (weread_qr_code) → send as image
        - Markdown documents (weread_markdown) → send file path + web link
        """
        artifacts = artifact_data.get("artifacts")
        if not isinstance(artifacts, list):
            return

        channel = self.manager.get_channel(msg.channel)
        if channel is None:
            return

        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue

            kind = artifact.get("kind", "")
            if kind == "weread_qr_code":
                await self._send_qr_code(msg, channel, artifact)

            elif kind == "weread_markdown":
                await self._send_document_notification(msg, artifact)

    async def _send_qr_code(self, msg, channel, artifact: dict) -> None:
        """Send WeRead QR code as an image to the chat platform."""
        b64 = artifact.get("qr_image_base64", "")
        if not b64:
            return

        logger.info("sending_qr_via_channel", channel=msg.channel)

        with tempfile.NamedTemporaryFile(
            suffix=".png", prefix="wechat_qr_", delete=False
        ) as tmp:
            tmp.write(base64.b64decode(b64))
            tmp_path = Path(tmp.name)

        try:
            if hasattr(channel, "send_media"):
                await channel.send_media(msg.chat_id, tmp_path)
            elif hasattr(channel, "send_image_base64"):
                await channel.send_image_base64(msg.chat_id, b64)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

        await self.manager.send_text(
            msg.channel, msg.chat_id,
            "请用微信扫描上方二维码完成登录。"
        )

    async def _send_document_notification(self, msg, artifact: dict) -> None:
        """Send a text notification about the generated markdown document."""
        name = artifact.get("name", "未知文档")
        file_path = artifact.get("file_path", "")
        book_id = artifact.get("book_id", "")
        source_type = artifact.get("source_type", "")

        parts = [f"文档已生成：{name}"]
        if file_path:
            parts.append(f"本地路径：{file_path}")
        if book_id and source_type:
            parts.append(f"在线查看：http://localhost:8000/weread/{book_id}/{source_type}")

        await self.manager.send_text(msg.channel, msg.chat_id, "\n".join(parts))
