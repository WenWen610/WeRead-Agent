"""Persistent identity bindings for chat channels."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.infrastructure.database import DatabaseService, database_service
from app.infrastructure.logging import get_logger
from app.models.weixin_binding import WeixinBinding

logger = get_logger(__name__)


class WeixinBindingStore:
    """Store WeChat openid to app user/thread bindings."""

    def __init__(self, db_service: DatabaseService = database_service) -> None:
        """Initialize the store with shared database access."""
        self.db_service = db_service
        self._legacy_paths = (
            Path(__file__).resolve().parents[2] / "data" / "weixin_user_map.json",
            Path("/app/data/weixin_user_map.json"),
        )

    async def upsert_binding(self, *, openid: str, user_id: int, thread_id: str) -> WeixinBinding:
        """Create or update the binding for a WeChat openid."""
        normalized_openid = openid.strip()
        normalized_thread_id = thread_id.strip()
        with self.db_service.get_session_maker() as session:
            binding = session.get(WeixinBinding, normalized_openid)
            if binding is None:
                binding = WeixinBinding(
                    openid=normalized_openid,
                    user_id=user_id,
                    thread_id=normalized_thread_id,
                )
            else:
                binding.user_id = user_id
                binding.thread_id = normalized_thread_id
                binding.updated_at = datetime.now(UTC)
            session.add(binding)
            session.commit()
            session.refresh(binding)
            logger.info(
                "weixin_binding_upserted",
                openid=normalized_openid,
                user_id=user_id,
                thread_id=normalized_thread_id,
            )
            return binding

    async def get_binding(self, openid: str) -> WeixinBinding | None:
        """Fetch the binding for an exact WeChat openid."""
        normalized_openid = openid.strip()
        if not normalized_openid:
            return None
        with self.db_service.get_session_maker() as session:
            binding = session.get(WeixinBinding, normalized_openid)
        if binding is not None:
            return binding
        return await self._migrate_legacy_binding(normalized_openid)

    def _read_legacy_mapping(self) -> dict[str, Any]:
        for path in self._legacy_paths:
            try:
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
            except (json.JSONDecodeError, OSError):
                logger.exception("weixin_legacy_mapping_read_failed", path=str(path))
        return {}

    async def _migrate_legacy_binding(self, openid: str) -> WeixinBinding | None:
        mapping = self._read_legacy_mapping()
        if not mapping:
            return None

        raw_binding = mapping.get(openid)
        if not isinstance(raw_binding, dict):
            for legacy_openid, value in mapping.items():
                if not isinstance(legacy_openid, str) or not isinstance(value, dict):
                    continue
                if openid.startswith(legacy_openid) or legacy_openid.startswith(openid):
                    raw_binding = value
                    break

        if not isinstance(raw_binding, dict):
            return None

        user_id = raw_binding.get("user_id")
        thread_id = raw_binding.get("thread_id") or raw_binding.get("session_id")
        if not isinstance(user_id, int) or not isinstance(thread_id, str) or not thread_id:
            return None

        logger.info("weixin_legacy_binding_migrated", openid=openid, user_id=user_id, thread_id=thread_id)
        return await self.upsert_binding(openid=openid, user_id=user_id, thread_id=thread_id)


weixin_binding_store = WeixinBindingStore()
