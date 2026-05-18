"""WeChat channel identity binding model."""

from datetime import UTC, datetime

from sqlmodel import Field

from app.models.base import BaseModel


class WeixinBinding(BaseModel, table=True):
    """Bind a WeChat openid to an app user and chat thread."""

    __tablename__ = "weixin_bindings"

    openid: str = Field(primary_key=True, max_length=191)
    user_id: int = Field(foreign_key="user.id", index=True)
    thread_id: str = Field(index=True, max_length=128)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
