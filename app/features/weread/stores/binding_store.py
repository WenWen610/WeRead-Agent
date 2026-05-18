"""WeRead binding persistence."""

from datetime import UTC, datetime
from typing import Optional

from sqlmodel import select

from app.infrastructure.logging import get_logger
from app.models.weread_binding import WeReadBinding
from app.infrastructure.database import DatabaseService, database_service

logger = get_logger(__name__)


class WeReadBindingStore:
    """Persist WeRead credential bindings for users."""

    def __init__(self, db_service: DatabaseService = database_service) -> None:
        self.db_service = db_service

    async def get_weread_binding(self, user_id: int) -> Optional[WeReadBinding]:
        """Fetch a user's WeRead binding."""
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadBinding).where(WeReadBinding.user_id == user_id)
            return session.exec(statement).first()

    async def upsert_weread_binding(
        self,
        *,
        user_id: int,
        encrypted_cookie: str,
        status: str,
        source: str,
        last_error: str | None = None,
        last_validated_at: datetime | None = None,
    ) -> WeReadBinding:
        """Create or update a user's encrypted WeRead binding."""
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadBinding).where(WeReadBinding.user_id == user_id)
            binding = session.exec(statement).first()

            if binding is None:
                binding = WeReadBinding(
                    user_id=user_id,
                    encrypted_cookie=encrypted_cookie,
                    status=status,
                    source=source,
                    last_error=last_error,
                    last_validated_at=last_validated_at,
                )
            else:
                binding.encrypted_cookie = encrypted_cookie
                binding.status = status
                binding.source = source
                binding.last_error = last_error
                binding.last_validated_at = last_validated_at
                binding.updated_at = datetime.now(UTC)

            session.add(binding)
            session.commit()
            session.refresh(binding)
            logger.info("weread_binding_saved", user_id=user_id, status=status, source=source)
            return binding

    async def update_weread_binding_status(
        self,
        *,
        user_id: int,
        status: str,
        last_error: str | None = None,
        last_validated_at: datetime | None = None,
    ) -> Optional[WeReadBinding]:
        """Update binding status fields for a user."""
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadBinding).where(WeReadBinding.user_id == user_id)
            binding = session.exec(statement).first()
            if binding is None:
                return None

            binding.status = status
            binding.last_error = last_error
            binding.last_validated_at = last_validated_at
            binding.updated_at = datetime.now(UTC)

            session.add(binding)
            session.commit()
            session.refresh(binding)
            logger.info("weread_binding_status_updated", user_id=user_id, status=status)
            return binding

    async def delete_weread_binding(self, user_id: int) -> bool:
        """Delete the WeRead binding for a user."""
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadBinding).where(WeReadBinding.user_id == user_id)
            binding = session.exec(statement).first()
            if binding is None:
                return False

            session.delete(binding)
            session.commit()
            logger.info("weread_binding_deleted", user_id=user_id)
            return True


weread_binding_store = WeReadBindingStore()
