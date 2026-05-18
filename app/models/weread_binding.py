"""WeRead credential binding model."""

from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field

from app.models.base import BaseModel


class WeReadBindingStatus(str, Enum):
    """Lifecycle states for a user's WeRead binding."""

    ACTIVE = "active"
    EXPIRED = "expired"
    REAUTH_REQUIRED = "reauth_required"


class WeReadBindingSource(str, Enum):
    """Source of the saved WeRead credential."""

    MANUAL = "manual"
    COOKIE_CLOUD = "cookie_cloud"
    QR = "qr"


class WeReadBinding(BaseModel, table=True):
    """Encrypted WeRead credential persisted per user."""

    __tablename__ = "weread_bindings"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)
    encrypted_cookie: str
    status: str = Field(default=WeReadBindingStatus.ACTIVE.value, index=True)
    source: str = Field(default=WeReadBindingSource.MANUAL.value)
    last_error: str | None = Field(default=None, max_length=500)
    last_validated_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
