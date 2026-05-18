"""QQ Bot channel using Tencent's official botpy SDK.

Uses WebSocket connection managed by botpy — no long-poll, no
reverse engineering needed.

Prerequisites:
    1. Register a QQ Bot at https://q.qq.com
    2. Obtain AppID + Secret
    3. pip install qq-botpy
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.channels.base import BaseChannel
from app.channels.bus import MessageBus, OutboundMessage
from app.infrastructure.logging import get_logger

if TYPE_CHECKING:
    import botpy
    from botpy.message import C2CMessage, GroupMessage

logger = get_logger(__name__)

QQ_AVAILABLE: bool = False

try:
    import botpy  # noqa: F811
    from botpy.message import C2CMessage, GroupMessage  # noqa: F811

    QQ_AVAILABLE = True
except ImportError:
    pass
ACK_MESSAGE = "⏳ 处理中..."


def _make_bot_class(channel: QQChannel) -> type[botpy.Client]:
    """Create a botpy.Client subclass that forwards messages to the channel."""
    class _Bot(botpy.Client):
        async def on_ready(self) -> None:
            logger.info("qq_bot_ready", name=getattr(self.robot, "name", "unknown"))

        async def on_c2c_message_create(self, message: C2CMessage) -> None:
            await channel._on_message(message, is_group=False)

        async def on_group_at_message_create(self, message: GroupMessage) -> None:
            await channel._on_message(message, is_group=True)

    return _Bot


class QQChannel(BaseChannel):
    """QQ Bot via official botpy SDK."""

    name = "qq"

    def __init__(
        self,
        bus: MessageBus,
        *,
        app_id: str = "",
        secret: str = "",
    ) -> None:
        super().__init__(bus)
        self.app_id = app_id
        self.secret = secret
        self._bot: botpy.Client | None = None
        self._processed_ids: deque[str] = deque(maxlen=1000)

    @property
    def has_token(self) -> bool:
        return bool(self.app_id and self.secret)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        if not self.app_id or not self.secret:
            logger.warning("qq_channel_no_credentials")
            return

        BotClass = _make_bot_class(self)
        self._bot = BotClass()
        self._running = True
        logger.info("qq_channel_started")

        # Auto-reconnect loop: restart bot if WebSocket drops
        while self._running:
            try:
                await self._bot.start(appid=self.app_id, secret=self.secret)
            except asyncio.CancelledError:
                logger.info("qq_bot_start_cancelled")
                break
            except Exception:
                logger.exception("qq_bot_connection_lost")
            if self._running:
                logger.info("qq_bot_reconnecting", delay=5)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._bot is not None:
            try:
                await self._bot.close()
            except Exception:
                pass
            self._bot = None
        logger.info("qq_channel_stopped")

    # ── Sending ─────────────────────────────────────────────────────────

    async def send(self, msg: OutboundMessage) -> None:
        if self._bot is None:
            logger.warning("qq_send_skipped_not_connected")
            return

        openid = msg.chat_id or msg.metadata.get("openid", "")
        if not openid:
            return

        try:
            # msg_type: 0=text, 1=markdown (if supported by bot version)
            await self._bot.api.post_c2c_message(
                openid=openid,
                msg_type=0,
                content=msg.content,
                msg_id=msg.metadata.get("msg_id", ""),
            )
        except Exception:
            logger.exception("qq_send_failed", openid=openid)

    async def send_media(
        self,
        openid: str,
        file_path: str | Path,
        *,
        file_type: int = 1,  # 1=image, 4=file
    ) -> bool:
        """Send an image or file to a QQ user.

        Returns True on success, False on failure.
        """
        if self._bot is None:
            return False

        path = Path(file_path)
        if not path.is_file():
            logger.warning("qq_send_media_file_not_found", path=str(path))
            return False

        try:
            data = base64.b64encode(path.read_bytes()).decode()

            # Upload to QQ via base64 file API
            result = await self._bot.api._http.request(
                botpy.http.Route("POST", "/v2/users/{openid}/files", openid=openid),
                json={"file_type": file_type, "file_data": data},
            )

            file_info = result.get("file_info") if isinstance(result, dict) else None
            if not file_info:
                logger.error("qq_media_upload_failed_no_file_info")
                return False

            # Send as media message (msg_type=7)
            await self._bot.api.post_c2c_message(
                openid=openid,
                msg_type=7,
                media=file_info,
            )
            logger.info("qq_media_sent", openid=openid, file_type=file_type)
            return True
        except Exception:
            logger.exception("qq_send_media_failed", openid=openid)
            return False

    async def send_image_base64(
        self,
        openid: str,
        b64_data: str,
        *,
        file_type: int = 1,
    ) -> bool:
        """Send a base64-encoded image directly."""
        if self._bot is None:
            return False
        try:
            result = await self._bot.api._http.request(
                botpy.http.Route("POST", "/v2/users/{openid}/files", openid=openid),
                json={"file_type": file_type, "file_data": b64_data},
            )
            file_info = result.get("file_info") if isinstance(result, dict) else None
            if not file_info:
                return False
            await self._bot.api.post_c2c_message(
                openid=openid,
                msg_type=7,
                media=file_info,
            )
            return True
        except Exception:
            logger.exception("qq_send_image_b64_failed", openid=openid)
            return False

    # ── Message handling ────────────────────────────────────────────────

    async def _on_message(self, message: Any, *, is_group: bool = False) -> None:
        """Callback from botpy.Client when a C2C or group @ message arrives."""
        author = getattr(message, "author", None)
        openid = getattr(author, "id", "") if author else ""
        content = getattr(message, "content", "") or ""
        msg_id = getattr(message, "id", None) or ""

        if not content.strip():
            return

        # Deduplicate
        if msg_id and msg_id in self._processed_ids:
            return
        if msg_id:
            self._processed_ids.append(msg_id)

        chat_id = openid

        logger.info(
            "qq_message_received",
            openid=openid,
            is_group=is_group,
            content_preview=content[:100],
        )

        # Send ack before processing
        await self._send_ack(openid)

        await self._handle_message(openid, chat_id, content)

    async def _send_ack(self, openid: str) -> None:
        """Send a quick acknowledgment so the user knows the bot is working."""
        if self._bot is None:
            return
        with suppress(Exception):
            await self._bot.api.post_c2c_message(
                openid=openid,
                msg_type=0,
                content=ACK_MESSAGE,
                msg_id="",
            )
