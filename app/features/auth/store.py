"""Authentication domain data access."""

from typing import List, Optional

from fastapi import HTTPException
from sqlmodel import select

from app.infrastructure.logging import get_logger
from app.models.session import Session as AuthSession
from app.models.user import User
from app.infrastructure.database import DatabaseService, database_service

logger = get_logger(__name__)


class AuthStore:
    """Persist and retrieve users and authenticated app sessions."""

    def __init__(self, db_service: DatabaseService = database_service) -> None:
        self.db_service = db_service

    async def create_user(self, email: str, password: str) -> User:
        """Create a new user record."""
        with self.db_service.get_session_maker() as session:
            user = User(email=email, hashed_password=password)
            session.add(user)
            session.commit()
            session.refresh(user)
            logger.info("user_created", email=email)
            return user

    async def get_user(self, user_id: int) -> Optional[User]:
        """Fetch a user by id."""
        with self.db_service.get_session_maker() as session:
            return session.get(User, user_id)

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Fetch a user by email."""
        with self.db_service.get_session_maker() as session:
            statement = select(User).where(User.email == email)
            return session.exec(statement).first()

    async def delete_user_by_email(self, email: str) -> bool:
        """Delete a user by email."""
        with self.db_service.get_session_maker() as session:
            user = session.exec(select(User).where(User.email == email)).first()
            if user is None:
                return False

            session.delete(user)
            session.commit()
            logger.info("user_deleted", email=email)
            return True

    async def create_session(self, session_id: str, user_id: int, name: str = "") -> AuthSession:
        """Create an authenticated app session."""
        with self.db_service.get_session_maker() as session:
            auth_session = AuthSession(id=session_id, user_id=user_id, name=name)
            session.add(auth_session)
            session.commit()
            session.refresh(auth_session)
            logger.info("session_created", session_id=session_id, user_id=user_id, name=name)
            return auth_session

    async def delete_session(self, session_id: str) -> bool:
        """Delete an authenticated app session."""
        with self.db_service.get_session_maker() as session:
            auth_session = session.get(AuthSession, session_id)
            if auth_session is None:
                return False

            session.delete(auth_session)
            session.commit()
            logger.info("session_deleted", session_id=session_id)
            return True

    async def get_session(self, session_id: str) -> Optional[AuthSession]:
        """Fetch an authenticated app session by id."""
        with self.db_service.get_session_maker() as session:
            return session.get(AuthSession, session_id)

    async def get_user_sessions(self, user_id: int) -> List[AuthSession]:
        """List authenticated app sessions for a user."""
        with self.db_service.get_session_maker() as session:
            statement = select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.created_at)
            return list(session.exec(statement).all())

    async def update_session_name(self, session_id: str, name: str) -> AuthSession:
        """Update an authenticated app session display name."""
        with self.db_service.get_session_maker() as session:
            auth_session = session.get(AuthSession, session_id)
            if auth_session is None:
                raise HTTPException(status_code=404, detail="Session not found")

            auth_session.name = name
            session.add(auth_session)
            session.commit()
            session.refresh(auth_session)
            logger.info("session_name_updated", session_id=session_id, name=name)
            return auth_session


auth_store = AuthStore()
