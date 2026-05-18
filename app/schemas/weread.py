"""Schemas for WeRead binding APIs."""

from datetime import datetime

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
)

from app.features.weread.models import SnapshotBookItem
from app.models.weread_binding import (
    WeReadBindingSource,
    WeReadBindingStatus,
)
from app.models.weread_login_session import WeReadQrLoginSessionStatus


class WeReadBindCookieRequest(BaseModel):
    """Request body for storing a WeRead cookie."""

    cookie: SecretStr = Field(..., description="Raw WeRead cookie captured from a logged-in session.")
    source: WeReadBindingSource = Field(
        default=WeReadBindingSource.MANUAL,
        description="Credential source for audit and future flows.",
    )


class WeReadBindingResponse(BaseModel):
    """Current binding status for a user."""

    connected: bool = Field(..., description="Whether the user currently has a saved WeRead binding.")
    status: WeReadBindingStatus | None = Field(default=None, description="Current binding lifecycle state.")
    source: WeReadBindingSource | None = Field(default=None, description="How the binding was created.")
    last_error: str | None = Field(default=None, description="Last known upstream or credential error.")
    last_validated_at: datetime | None = Field(default=None, description="Last time the binding was validated.")
    updated_at: datetime | None = Field(default=None, description="Last time the binding record changed.")


class WeReadBindingMutationResponse(BaseModel):
    """Mutation result for binding operations."""

    success: bool = Field(..., description="Whether the mutation succeeded.")
    message: str = Field(..., description="Human-readable result message.")


class WeReadQrLoginSessionResponse(BaseModel):
    """Current state of a temporary QR login session."""

    session_id: str = Field(..., description="Temporary QR login session id.")
    status: WeReadQrLoginSessionStatus = Field(..., description="Current QR login session status.")
    qr_image_base64: str | None = Field(default=None, description="Base64 PNG QR image for the current session.")
    last_error: str | None = Field(default=None, description="Last QR login error, if any.")
    expires_at: datetime = Field(..., description="When the QR login session expires.")
    created_at: datetime = Field(..., description="When the QR login session was created.")
    updated_at: datetime = Field(..., description="When the QR login session was last updated.")
    completed_at: datetime | None = Field(default=None, description="When the QR login session finished.")


class WeReadRecentBooksResponse(BaseModel):
    """Recent bookshelf items sourced from the local metadata cache."""

    connected: bool = Field(..., description="Whether the user currently has an active WeRead binding.")
    refreshed: bool = Field(..., description="Whether the endpoint refreshed the local bookshelf cache just now.")
    sync_key: int | None = Field(default=None, description="Latest bookshelf sync key when available.")
    generated_at: str | None = Field(default=None, description="When the current cached snapshot was generated.")
    books: list[SnapshotBookItem] = Field(default_factory=list, description="Most recently read books.")
