"""Database infrastructure and health checks."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, SQLModel, create_engine, select

import app.models.database  # noqa: F401
from app.infrastructure.config import Environment, settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)


class DatabaseService:
    """Provide shared database infrastructure for domain stores."""

    def __init__(self) -> None:
        """Initialize the shared SQLModel engine."""
        try:
            connection_url = settings.DATABASE_URL
            backend = connection_url.split("://", 1)[0] if "://" in connection_url else "unknown"

            if connection_url.startswith("sqlite"):
                self.engine = create_engine(
                    connection_url,
                    connect_args={"check_same_thread": False},
                    pool_pre_ping=True,
                )
                logger.info(
                    "database_initialized",
                    environment=settings.ENVIRONMENT.value,
                    backend=backend,
                )
            else:
                pool_size = settings.POSTGRES_POOL_SIZE
                max_overflow = settings.POSTGRES_MAX_OVERFLOW
                self.engine = create_engine(
                    connection_url,
                    pool_pre_ping=True,
                    poolclass=QueuePool,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_timeout=30,
                    pool_recycle=1800,
                )
                logger.info(
                    "database_initialized",
                    environment=settings.ENVIRONMENT.value,
                    backend=backend,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                )

            SQLModel.metadata.create_all(self.engine)
        except SQLAlchemyError:
            logger.exception("database_initialization_error", environment=settings.ENVIRONMENT.value)
            if settings.ENVIRONMENT != Environment.PRODUCTION:
                raise

    def get_session_maker(self) -> Session:
        """Create a new SQLModel session bound to the shared engine."""
        return Session(self.engine)

    async def health_check(self) -> bool:
        """Check database connectivity."""
        try:
            with Session(self.engine) as session:
                session.exec(select(1)).first()
                return True
        except Exception:
            logger.exception("database_health_check_failed")
            return False


database_service = DatabaseService()
