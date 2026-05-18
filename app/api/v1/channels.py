"""FastAPI routes for channel QR login and status management."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.channels.binding_store import weixin_binding_store
from app.infrastructure.limiter import limiter
from app.infrastructure.logging import get_logger

from app.api.v1.auth import get_current_session
from app.models.session import Session

logger = get_logger(__name__)

router = APIRouter(tags=["channels"])

_channel_manager: Any = None
_channel_bridge: Any = None


def set_channel_manager(manager: Any) -> None:
    """Register the process-wide channel manager for route handlers."""
    global _channel_manager
    _channel_manager = manager


def set_channel_bridge(bridge: Any) -> None:
    """Register the process-wide channel bridge for route handlers."""
    global _channel_bridge
    _channel_bridge = bridge


def _weixin_channel():
    if _channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager not initialized")
    ch = _channel_manager.get_channel("weixin")
    if ch is None:
        raise HTTPException(status_code=404, detail="Weixin channel not configured")
    return ch


def _ensure_bridge_and_dispatch() -> None:
    """Ensure the dispatch loop is running after a channel starts."""
    if _channel_manager is not None and _channel_manager._dispatch_task is None:
        _channel_manager._dispatch_task = asyncio.create_task(_channel_manager._dispatch_loop())
        logger.info("channel_dispatch_started")


# ── Models ────────────────────────────────────────────────────────────────


class QRCodeResponse(BaseModel):
    """WeChat QR code login payload."""

    qrcode_id: str
    qrcode_img_content: str


class LoginStatusResponse(BaseModel):
    """WeChat QR code login status payload."""

    status: str
    message: str


class ChannelStatusResponse(BaseModel):
    """Channel runtime status payload."""

    name: str
    running: bool
    has_token: bool


# ── Routes ────────────────────────────────────────────────────────────────


@router.get("/status", response_model=list[ChannelStatusResponse])
@limiter.limit("30/minute")
async def list_channels(request: Request):
    """Return status of all configured channels."""
    if _channel_manager is None:
        return []
    result = []
    for name, ch in _channel_manager.get_channels().items():
        result.append(
            ChannelStatusResponse(name=name, running=ch.running, has_token=ch.has_token)
        )
    return result


@router.post("/weixin/qrcode", response_model=QRCodeResponse)
@limiter.limit("10/minute")
async def start_weixin_login(request: Request):
    """Generate a WeChat QR code for user scanning."""
    ch = _weixin_channel()
    try:
        data = await ch.login()
        return QRCodeResponse(qrcode_id=data["qrcode_id"], qrcode_img_content=data["qrcode_img_content"])
    except Exception as exc:
        logger.exception("weixin_login_qr_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/weixin/qrcode/status", response_model=LoginStatusResponse)
@limiter.limit("30/minute")
async def check_weixin_login_status(
    request: Request,
    thread_id: str = "",
    session_id: str = "",
    session: Session = Depends(get_current_session),
):
    """Poll QR code login status and bind WeChat to the selected Web thread."""
    ch = _weixin_channel()
    logger.info("weixin_check_status_start", qrcode_id=ch.qr_data.get("qrcode_id", ""))
    try:
        data = await ch.check_login_status()
        logger.info("weixin_check_status_done", status=data.get("status", ""))
        if data.get("status") == "success" and ch.has_token:
            if not ch.running:
                await ch.start()
                _ensure_bridge_and_dispatch()
            if hasattr(ch, "qr_data"):
                ilink_user_id = ch.qr_data.get("ilink_user_id", "")
                if ilink_user_id:
                    resolved_thread_id = thread_id or session_id or session.id
                    await weixin_binding_store.upsert_binding(
                        openid=ilink_user_id,
                        user_id=session.user_id,
                        thread_id=resolved_thread_id,
                    )
        return LoginStatusResponse(status=data["status"], message=data.get("message", ""))
    except Exception as exc:
        logger.exception("weixin_login_check_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/weixin/start")
@limiter.limit("10/minute")
async def start_weixin_channel(request: Request):
    """Manually start the WeChat channel (requires existing token)."""
    ch = _weixin_channel()
    if not ch.has_token:
        raise HTTPException(status_code=400, detail="No bot token. Generate a QR code first.")
    if ch.running:
        return {"status": "already_running"}
    await ch.start()
    _ensure_bridge_and_dispatch()
    return {"status": "started"}


@router.post("/weixin/stop")
@limiter.limit("10/minute")
async def stop_weixin_channel(request: Request):
    """Stop the WeChat channel."""
    ch = _weixin_channel()
    await ch.stop()
    return {"status": "stopped"}


# ── QQ routes ───────────────────────────────────────────────────────────


def _qq_channel():
    if _channel_manager is None:
        raise HTTPException(status_code=503, detail="Channel manager not initialized")
    ch = _channel_manager.get_channel("qq")
    if ch is None:
        raise HTTPException(status_code=404, detail="QQ channel not configured")
    return ch


@router.get("/qq/status", response_model=ChannelStatusResponse)
@limiter.limit("30/minute")
async def get_qq_status(request: Request):
    """Return QQ channel status."""
    ch = _qq_channel()
    return ChannelStatusResponse(name=ch.name, running=ch.running, has_token=ch.has_token)


@router.post("/qq/start")
@limiter.limit("10/minute")
async def start_qq_channel(request: Request):
    """Start the QQ channel (requires configured AppID + Secret)."""
    ch = _qq_channel()
    if not ch.has_token:
        raise HTTPException(status_code=400, detail="QQ AppID and Secret not configured.")
    if ch.running:
        return {"status": "already_running"}
    await ch.start()
    return {"status": "started"}


@router.post("/qq/stop")
@limiter.limit("10/minute")
async def stop_qq_channel(request: Request):
    """Stop the QQ channel."""
    ch = _qq_channel()
    await ch.stop()
    return {"status": "stopped"}
