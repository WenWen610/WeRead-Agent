"""Personal WeChat (微信) channel using HTTP long-poll API.

Uses the ilinkai.weixin.qq.com API.  No WebSocket, no local WeChat client
needed — just HTTP requests with a bot token obtained via QR code login.

Protocol reverse-engineered from ``@tencent-weixin/openclaw-weixin`` v1.0.3.
Reference implementation: nanobot/weixin.py.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import tempfile
import time
import uuid
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import qrcode

from app.channels.base import BaseChannel
from app.channels.bus import MessageBus, OutboundMessage
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

# ── Protocol constants ────────────────────────────────────────────────────

MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2

WEIXIN_CHANNEL_VERSION = "2.1.1"
ILINK_APP_ID = "bot"

ERRCODE_SESSION_EXPIRED = -14
SESSION_PAUSE_DURATION_S = 60 * 60

MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30
RETRY_DELAY_S = 2

DEFAULT_POLL_TIMEOUT_S = 35
WEIXIN_MAX_MESSAGE_LEN = 4000


def _build_client_version(version: str) -> int:
    parts = version.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


ILINK_APP_CLIENT_VERSION = _build_client_version(WEIXIN_CHANNEL_VERSION)
BASE_INFO: dict[str, str] = {"channel_version": WEIXIN_CHANNEL_VERSION}

# Media upload type codes
UPLOAD_MEDIA_IMAGE = 1
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3
UPLOAD_MEDIA_VOICE = 4

ITEM_TEXT = 1
ITEM_IMAGE = 2

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})

# ── AES helpers for CDN media ─────────────────────────────────────────────


def _aes_pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes_pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data or len(data) % block_size != 0:
        return data
    pad_byte = data[-1]
    if pad_byte < 1 or pad_byte > block_size:
        return data
    return data[:-pad_byte]


def _aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(_aes_pkcs7_pad(data))


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    from Crypto.Cipher import AES
    cipher = AES.new(key, AES.MODE_ECB)
    return _aes_pkcs7_unpad(cipher.decrypt(data))


# ── WeChat Channel ──────────────────────────────────────────────────────────


class WeixinChannel(BaseChannel):
    """Personal WeChat via ilink HTTP long-poll."""

    name = "weixin"

    def __init__(
        self,
        bus: MessageBus,
        *,
        token: str = "",
        base_url: str = "https://ilinkai.weixin.qq.com",
        cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c",
        state_dir: str = "",
    ) -> None:
        super().__init__(bus)
        self.base_url = base_url.rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")

        self._state_dir = Path(state_dir) if state_dir else Path("/app/data/weixin")
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._account_path = self._state_dir / "account.json"

        self._bot_token: str = token or self._load_token()
        self._route_tag: str = ""
        self._seen_msg_ids: OrderedDict[str, bool] = OrderedDict()
        self._context_token: str = ""
        self._context_tokens: dict[str, str] = {}
        self._get_updates_buf: str = ""
        self._polling = False

        self.qr_data: dict[str, Any] = {}

    # ── Lifecycle ───────────────────────────────────────────────────────

    @property
    def has_token(self) -> bool:
        return bool(self._bot_token)

    async def start(self) -> None:
        if not self._bot_token:
            logger.warning("weixin_no_token_start_skipped")
            return

        await self._fetch_context_token()
        self._running = True
        self._polling = True
        logger.info("weixin_channel_started", base_url=self.base_url)

        asyncio.create_task(self._run_bot())

    async def _run_bot(self) -> None:
        """Auto-reconnect loop: restart polling if it crashes."""
        while self._running:
            try:
                await self._poll_loop()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("weixin_bot_loop_crashed")
            if self._running:
                logger.info("weixin_reconnecting", delay=RETRY_DELAY_S)
                await asyncio.sleep(RETRY_DELAY_S)

    async def stop(self) -> None:
        self._running = False
        self._polling = False
        logger.info("weixin_channel_stopped")

    async def send(self, msg: OutboundMessage) -> None:
        if not self._bot_token:
            return
        try:
            await self._send_text(msg.chat_id or "default", msg.content)
        except Exception:
            logger.exception("weixin_send_failed", chat_id=msg.chat_id)

    # ── Media sending ───────────────────────────────────────────────────

    async def send_media(self, openid: str, file_path: str | Path, *, media_type: int = 1) -> bool:
        path = Path(file_path)
        if not path.is_file():
            return False
        ctx_token = self._context_tokens.get(openid, self._context_token)
        try:
            await self._send_media_file(openid, path, ctx_token)
            return True
        except Exception:
            logger.exception("weixin_send_media_failed", path=str(path))
            return False

    async def send_image_base64(self, openid: str, b64_data: str, *, media_type: int = 1) -> bool:
        import base64 as _b64
        try:
            data = _b64.b64decode(b64_data)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            try:
                return await self.send_media(openid, tmp_path, media_type=media_type)
            finally:
                with suppress(OSError):
                    tmp_path.unlink()
        except Exception:
            logger.exception("weixin_send_image_b64_failed")
            return False

    async def _send_media_file(
        self,
        to_user_id: str,
        media_path: Path,
        context_token: str,
    ) -> None:
        p = Path(media_path)
        raw_data = p.read_bytes()
        raw_size = len(raw_data)
        raw_md5 = hashlib.md5(raw_data).hexdigest()

        ext = p.suffix.lower()
        if ext in _IMAGE_EXTS:
            upload_type = UPLOAD_MEDIA_IMAGE
            item_type = ITEM_IMAGE
            item_key = "image_item"
        else:
            upload_type = UPLOAD_MEDIA_FILE
            item_type = ITEM_IMAGE
            item_key = "image_item"

        aes_key_raw = os.urandom(16)
        aes_key_hex = aes_key_raw.hex()
        padded_size = ((raw_size + 1 + 15) // 16) * 16
        file_key = os.urandom(16).hex()

        upload_body: dict[str, Any] = {
            "filekey": file_key,
            "media_type": upload_type,
            "to_user_id": to_user_id,
            "rawsize": raw_size,
            "rawfilemd5": raw_md5,
            "filesize": padded_size,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
        }

        upload_resp = await self._api_post("ilink/bot/getuploadurl", upload_body)
        upload_full_url = str(upload_resp.get("upload_full_url", "") or "").strip()
        upload_param = str(upload_resp.get("upload_param", "") or "")
        if not upload_full_url and not upload_param:
            raise RuntimeError(f"No upload URL returned: {upload_resp}")

        encrypted_data = _aes_ecb_encrypt(raw_data, aes_key_raw)

        if upload_full_url:
            cdn_upload_url = upload_full_url
        else:
            cdn_upload_url = (
                f"{self.cdn_base_url}/upload"
                f"?encrypted_query_param={quote(upload_param)}"
                f"&filekey={quote(file_key)}"
            )

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            cdn_resp = await client.post(cdn_upload_url, content=encrypted_data,
                                         headers={"Content-Type": "application/octet-stream"})
            cdn_resp.raise_for_status()
            download_param = cdn_resp.headers.get("x-encrypted-param", "")
            if not download_param:
                raise RuntimeError("CDN upload missing x-encrypted-param header")

        cdn_aes_key_b64 = base64.b64encode(aes_key_hex.encode()).decode()

        media_item: dict[str, Any] = {
            "media": {
                "encrypt_query_param": download_param,
                "aes_key": cdn_aes_key_b64,
                "encrypt_type": 1,
            },
        }
        if item_type == ITEM_IMAGE:
            media_item["mid_size"] = padded_size

        client_id = f"agent-{uuid.uuid4().hex[:12]}"
        weixin_msg: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": MESSAGE_TYPE_BOT,
            "message_state": MESSAGE_STATE_FINISH,
            "item_list": [{"type": item_type, item_key: media_item}],
        }
        if context_token:
            weixin_msg["context_token"] = context_token

        await self._api_post(
            "ilink/bot/sendmessage",
            {"msg": weixin_msg, "base_info": BASE_INFO},
        )
        logger.info("weixin_media_sent", to_user_id=to_user_id)

    # ── QR Code Login ───────────────────────────────────────────────────

    # ── HTTP helpers ────────────────────────────────────────────────────

    @staticmethod
    def _random_wechat_uin() -> str:
        uint32 = int.from_bytes(os.urandom(4), "big")
        return base64.b64encode(str(uint32).encode()).decode()

    def _make_headers(self, *, auth: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-WECHAT-UIN": self._random_wechat_uin(),
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }
        if auth and self._bot_token:
            headers["Authorization"] = f"Bearer {self._bot_token}"
        if self._route_tag:
            headers["SKRouteTag"] = self._route_tag
        return headers

    async def _api_get(self, path: str, params: dict | None = None, *, timeout: float = 60.0, auth: bool = True) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.get(url, params=params, headers=self._make_headers(auth=auth))
            resp.raise_for_status()
            result: dict = resp.json()
            errcode = result.get("errcode", 0)
            if errcode != 0 and errcode is not None:
                errmsg = result.get("errmsg", result.get("message", ""))
                logger.warning("weixin_api_error", path=path, errcode=errcode, errmsg=errmsg)
            return result

    async def _api_post(self, path: str, data: dict, *, timeout: float = 60.0) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(url, json=data, headers=self._make_headers())
        resp.raise_for_status()
        result: dict = resp.json()
        errcode = result.get("errcode", 0)
        if errcode != 0 and errcode is not None:
            errmsg = result.get("errmsg", result.get("message", ""))
            logger.warning("weixin_api_error", path=path, errcode=errcode, errmsg=errmsg)
        return result

    async def login(self) -> dict[str, Any]:
        """Start QR code login flow. Returns QR data for frontend display."""
        result = await self._api_get("ilink/bot/get_bot_qrcode", {"bot_type": "3"}, auth=False)
        qrcode_id = result.get("qrcode", "")
        qrcode_url = result.get("qrcode_img_content", "")
        if not qrcode_id:
            raise RuntimeError(f"Failed to get QR code: {result}")

        img = qrcode.make(qrcode_url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        qrcode_b64 = base64.b64encode(buf.getvalue()).decode()

        self.qr_data = {
            "qrcode_id": qrcode_id,
            "qrcode_img_content": qrcode_b64,
        }
        logger.info("weixin_qr_code_obtained", qrcode_id=qrcode_id)
        return self.qr_data

    async def check_login_status(self) -> dict[str, Any]:
        """Poll QR login status."""
        qrcode_id = self.qr_data.get("qrcode_id", "")
        if not qrcode_id:
            return {"status": "no_qr", "message": "No QR code generated yet."}

        logger.info("weixin_check_status_api_call", qrcode_id=qrcode_id)
        result = await self._api_get(
            "ilink/bot/get_qrcode_status",
            {"qrcode": qrcode_id},
            auth=False,
        )
        logger.info("weixin_check_status_api_response", result=str(result)[:200])
        status = str(result.get("status", "wait"))

        if status == "confirmed":
            self._bot_token = str(result.get("bot_token", ""))
            ilink_bot_id = str(result.get("ilink_bot_id", ""))
            ilink_user_id = str(result.get("ilink_user_id", ""))
            route_tag = str(result.get("route_tag", ""))
            baseurl = result.get("baseurl", "")
            if baseurl:
                self.base_url = str(baseurl).rstrip("/")
            if self._bot_token:
                self._save_token(self._bot_token, ilink_bot_id, ilink_user_id, route_tag, self.base_url)
                self.qr_data["ilink_user_id"] = ilink_user_id
                return {"status": "success", "message": "WeChat login successful."}
            return {"status": "waiting", "message": "Waiting for confirmation..."}
        elif status == "expired":
            return {"status": "expired", "message": "QR code expired. Request a new one."}
        elif status == "scaned_but_redirect":
            redirect_host = result.get("redirect_host", "")
            if redirect_host:
                self.base_url = f"https://{redirect_host.lstrip('/')}"
                logger.info("weixin_redirect_base_url", base_url=self.base_url)
            return {"status": "waiting", "message": "QR code scanned, waiting for confirmation..."}
        else:
            return {"status": "waiting", "message": f"Waiting for scan (status={status})"}

    # ── Message Polling ─────────────────────────────────────────────────

    async def _fetch_context_token(self) -> None:
        try:
            result = await self._api_post("ilink/bot/getconfig", {})
            self._context_token = str(result.get("context_token", ""))
        except Exception:
            logger.exception("weixin_fetch_config_failed")

    async def _process_message(self, msg: dict) -> None:
        """Process a single inbound WeChat message from getupdates."""
        if msg.get("message_type") == MESSAGE_TYPE_BOT:
            return

        msg_id = str(msg.get("message_id", "") or msg.get("seq", ""))
        if not msg_id:
            msg_id = f"{msg.get('from_user_id', '')}_{msg.get('create_time_ms', '')}"
        if msg_id in self._seen_msg_ids:
            return
        self._seen_msg_ids[msg_id] = True
        if len(self._seen_msg_ids) > 1000:
            self._seen_msg_ids.popitem(last=False)

        from_user_id = str(msg.get("from_user_id", ""))
        if not from_user_id:
            return

        ctx_token = msg.get("context_token", "")
        if ctx_token:
            self._context_tokens[from_user_id] = str(ctx_token)
            self._save_state()

        item_list: list[dict] = msg.get("item_list") or []
        content_parts: list[str] = []

        for item in item_list:
            if item.get("type") == ITEM_TEXT:
                text_item = item.get("text_item") or {}
                text = str(text_item.get("text", ""))
                if text.strip():
                    content_parts.append(text)

        text = "".join(content_parts).strip()
        if text:
            logger.info("weixin_message_received", from_user_id=from_user_id, preview=text[:80])
            await self._handle_message(from_user_id, from_user_id, text)

    async def _poll_loop(self) -> None:
        consecutive_failures = 0

        while self._polling:
            try:
                updates = await self._api_post(
                    "ilink/bot/getupdates",
                    {
                        "get_updates_buf": self._get_updates_buf,
                        "base_info": BASE_INFO,
                    },
                    timeout=float(DEFAULT_POLL_TIMEOUT_S + 10),
                )

                errcode = updates.get("errcode", 0)
                if errcode == ERRCODE_SESSION_EXPIRED:
                    logger.warning("weixin_session_expired")
                    await asyncio.sleep(30)
                    continue

                self._context_token = str(updates.get("context_token", self._context_token))
                new_buf = str(updates.get("get_updates_buf", ""))
                if new_buf and new_buf != self._get_updates_buf:
                    self._get_updates_buf = new_buf
                    self._save_state()
                consecutive_failures = 0

                msgs: list[dict] = updates.get("msgs", []) or []
                for msg in msgs:
                    with suppress(Exception):
                        await self._process_message(msg)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("weixin_poll_error")
                consecutive_failures += 1
                delay = BACKOFF_DELAY_S if consecutive_failures >= MAX_CONSECUTIVE_FAILURES else RETRY_DELAY_S
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
                await asyncio.sleep(delay)

    # ── Sending ─────────────────────────────────────────────────────────

    async def _send_text(self, chat_id: str, text: str) -> None:
        if not text.strip():
            return

        ctx_token = self._context_tokens.get(chat_id, self._context_token)
        client_id = f"agent-{uuid.uuid4().hex[:12]}"

        chunks: list[str] = []
        if len(text) <= WEIXIN_MAX_MESSAGE_LEN:
            chunks = [text]
        else:
            for i in range(0, len(text), WEIXIN_MAX_MESSAGE_LEN):
                chunks.append(text[i:i + WEIXIN_MAX_MESSAGE_LEN])

        for chunk in chunks:
            if not chunk.strip():
                continue

            weixin_msg: dict[str, Any] = {
                "from_user_id": "",
                "to_user_id": chat_id,
                "client_id": client_id,
                "message_type": MESSAGE_TYPE_BOT,
                "message_state": MESSAGE_STATE_FINISH,
                "item_list": [{"type": ITEM_TEXT, "text_item": {"text": chunk}}],
            }
            if ctx_token:
                weixin_msg["context_token"] = ctx_token

            await self._api_post(
                "ilink/bot/sendmessage",
                {"msg": weixin_msg, "base_info": BASE_INFO},
            )

    async def _send_typing(self, chat_id: str) -> None:
        try:
            await self._api_post(
                "ilink/bot/sendtyping",
                {"chat_id": chat_id, "context_token": self._context_token, "type": 1},
            )
        except Exception:
            pass

    # ── Persistence ─────────────────────────────────────────────────────

    def _save_state(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._account_path.write_text(
            json.dumps({
                "bot_token": self._bot_token,
                "base_url": self.base_url,
                "route_tag": self._route_tag,
                "get_updates_buf": self._get_updates_buf,
                "context_tokens": self._context_tokens,
                "updated_at": int(time.time()),
            }),
            encoding="utf-8",
        )

    def _save_token(self, token: str, ilink_bot_id: str = "", ilink_user_id: str = "", route_tag: str = "", base_url: str = "") -> None:
        self._save_state()

    def _load_token(self) -> str:
        try:
            data = json.loads(self._account_path.read_text(encoding="utf-8"))
            token = str(data.get("bot_token", ""))
            if token:
                saved_base_url = data.get("base_url", "")
                if saved_base_url:
                    self.base_url = saved_base_url.rstrip("/")
                self._route_tag = str(data.get("route_tag", ""))
                self._get_updates_buf = str(data.get("get_updates_buf", ""))
                saved_ct = data.get("context_tokens", {})
                if isinstance(saved_ct, dict):
                    self._context_tokens = saved_ct
                logger.info("weixin_token_loaded")
            return token
        except (FileNotFoundError, json.JSONDecodeError):
            return ""
