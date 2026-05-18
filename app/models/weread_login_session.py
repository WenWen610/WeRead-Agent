"""Temporary WeRead QR login session model."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlmodel import Field

from app.models.base import BaseModel


class WeReadQrLoginSessionStatus(StrEnum):
    """Lifecycle states for a temporary QR login session."""

    PENDING = "pending"
    QR_READY = "qr_ready"
    SUCCESS = "success"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WeReadLoginSession(BaseModel, table=True):
    """Temporary server-side browser session for WeRead QR login."""

    __tablename__ = "weread_login_sessions"

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    status: str = Field(default=WeReadQrLoginSessionStatus.PENDING.value, index=True)
    qr_image_base64: str | None = Field(default=None)
    last_error: str | None = Field(default=None, max_length=500)
    expires_at: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=3), index=True)
    completed_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
