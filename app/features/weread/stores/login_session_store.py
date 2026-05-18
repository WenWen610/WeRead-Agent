"""Persistence for temporary WeRead QR login sessions."""

from datetime import UTC, datetime

from sqlmodel import select

from app.infrastructure.logging import get_logger
from app.models.weread_login_session import (
    WeReadLoginSession,
    WeReadQrLoginSessionStatus,
)
from app.infrastructure.database import DatabaseService, database_service

logger = get_logger(__name__)


class WeReadLoginSessionStore:
    """Persist WeRead QR login session state."""

    _MAX_LAST_ERROR_LENGTH = 500
    _ACTIVE_STATUSES = {
        WeReadQrLoginSessionStatus.PENDING.value,
        WeReadQrLoginSessionStatus.QR_READY.value,
    }

    def __init__(self, db_service: DatabaseService = database_service) -> None:
        self.db_service = db_service

    async def create_login_session(
        self,
        *,
        session_id: str,
        user_id: int,
        expires_at: datetime,
    ) -> WeReadLoginSession:
        """Create a new QR login session."""
        with self.db_service.get_session_maker() as session:
            login_session = WeReadLoginSession(id=session_id, user_id=user_id, expires_at=expires_at)
            session.add(login_session)
            session.commit()
            session.refresh(login_session)
            logger.info("weread_qr_login_session_created", login_session_id=session_id, user_id=user_id)
            return login_session

    async def get_login_session(self, session_id: str) -> WeReadLoginSession | None:
        """Fetch a QR login session by id."""
        with self.db_service.get_session_maker() as session:
            return session.get(WeReadLoginSession, session_id)

    async def get_user_login_session(self, *, user_id: int, session_id: str) -> WeReadLoginSession | None:
        """Fetch a QR login session scoped to the user."""
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadLoginSession).where(
                WeReadLoginSession.id == session_id,
                WeReadLoginSession.user_id == user_id,
            )
            return session.exec(statement).first()

    async def list_active_sessions(self, user_id: int) -> list[WeReadLoginSession]:
        """List non-terminal QR login sessions for a user."""
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadLoginSession).where(
                WeReadLoginSession.user_id == user_id,
                WeReadLoginSession.status.in_(self._ACTIVE_STATUSES),
            )
            return list(session.exec(statement).all())

    async def update_login_session(
        self,
        *,
        session_id: str,
        status: str | None = None,
        qr_image_base64: str | None = None,
        last_error: str | None = None,
        completed_at: datetime | None = None,
    ) -> WeReadLoginSession | None:
        """Update a QR login session."""
        with self.db_service.get_session_maker() as session:
            login_session = session.get(WeReadLoginSession, session_id)
            if login_session is None:
                return None

            if status is not None:
                login_session.status = status
            if qr_image_base64 is not None:
                login_session.qr_image_base64 = qr_image_base64
            login_session.last_error = self._truncate_last_error(last_error)
            login_session.completed_at = completed_at
            login_session.updated_at = datetime.now(UTC)

            session.add(login_session)
            session.commit()
            session.refresh(login_session)
            logger.info(
                "weread_qr_login_session_updated",
                login_session_id=session_id,
                status=login_session.status,
            )
            return login_session

    async def cancel_active_sessions(self, user_id: int) -> list[WeReadLoginSession]:
        """Cancel every active QR login session for the user."""
        cancelled: list[WeReadLoginSession] = []
        with self.db_service.get_session_maker() as session:
            statement = select(WeReadLoginSession).where(
                WeReadLoginSession.user_id == user_id,
                WeReadLoginSession.status.in_(self._ACTIVE_STATUSES),
            )
            active_sessions = list(session.exec(statement).all())
            for login_session in active_sessions:
                login_session.status = WeReadQrLoginSessionStatus.CANCELLED.value
                login_session.completed_at = datetime.now(UTC)
                login_session.updated_at = login_session.completed_at
                session.add(login_session)
                cancelled.append(login_session)

            session.commit()
            for login_session in cancelled:
                session.refresh(login_session)

        if cancelled:
            logger.info("weread_qr_active_sessions_cancelled", user_id=user_id, count=len(cancelled))
        return cancelled

    def _truncate_last_error(self, last_error: str | None) -> str | None:
        """Keep persisted QR login errors within the database column size."""
        if last_error is None:
            return None
        if len(last_error) <= self._MAX_LAST_ERROR_LENGTH:
            return last_error
        return f"{last_error[: self._MAX_LAST_ERROR_LENGTH - 3]}..."


weread_login_session_store = WeReadLoginSessionStore()
