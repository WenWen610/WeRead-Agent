"""Persistent web chat thread and timeline storage."""

from datetime import UTC, datetime
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import select

from app.infrastructure.logging import get_logger
from app.models.chat_item import ChatItem
from app.models.chat_thread import ChatThread
from app.infrastructure.database import DatabaseService, database_service

logger = get_logger(__name__)


def _build_chat_preview(content: str, max_length: int = 120) -> str:
    """Build a compact preview string for thread lists."""
    normalized = " ".join(content.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "…"


class ChatStore:
    """Persist chat threads and timeline items for the web UI."""

    def __init__(self, db_service: DatabaseService = database_service) -> None:
        self.db_service = db_service

    async def create_chat_thread(self, thread_id: str, user_id: int, title: str = "") -> ChatThread:
        """Create a persistent chat thread."""
        with self.db_service.get_session_maker() as session:
            chat_thread = ChatThread(id=thread_id, user_id=user_id, title=title)
            session.add(chat_thread)
            session.commit()
            session.refresh(chat_thread)
            logger.info("chat_thread_created", thread_id=thread_id, user_id=user_id, title=title)
            return chat_thread

    async def get_chat_thread(self, thread_id: str) -> Optional[ChatThread]:
        """Fetch a chat thread by id."""
        with self.db_service.get_session_maker() as session:
            return session.get(ChatThread, thread_id)

    async def get_user_chat_threads(self, user_id: int, include_archived: bool = False) -> List[ChatThread]:
        """List chat threads for a user ordered by most recent activity."""
        with self.db_service.get_session_maker() as session:
            statement = select(ChatThread).where(ChatThread.user_id == user_id)
            if not include_archived:
                statement = statement.where(ChatThread.archived_at.is_(None))
            statement = statement.order_by(ChatThread.updated_at.desc())
            return list(session.exec(statement).all())

    async def upsert_chat_thread(self, thread_id: str, user_id: int, title: str = "") -> ChatThread:
        """Get or create a chat thread."""
        with self.db_service.get_session_maker() as session:
            chat_thread = session.get(ChatThread, thread_id)
            if chat_thread is None:
                chat_thread = ChatThread(id=thread_id, user_id=user_id, title=title)
            elif title:
                chat_thread.title = title
            session.add(chat_thread)
            session.commit()
            session.refresh(chat_thread)
            logger.info("chat_thread_upserted", thread_id=thread_id, user_id=user_id, title=chat_thread.title)
            return chat_thread

    async def update_chat_thread_title(self, thread_id: str, title: str) -> ChatThread:
        """Update a chat thread title."""
        with self.db_service.get_session_maker() as session:
            chat_thread = session.get(ChatThread, thread_id)
            if chat_thread is None:
                raise HTTPException(status_code=404, detail="Chat thread not found")

            chat_thread.title = title
            chat_thread.updated_at = datetime.now(UTC)
            session.add(chat_thread)
            session.commit()
            session.refresh(chat_thread)
            logger.info("chat_thread_title_updated", thread_id=thread_id, title=title)
            return chat_thread

    async def create_chat_item(
        self,
        *,
        thread_id: str,
        user_id: int,
        item_type: str,
        content: str,
        item_metadata: dict | None = None,
    ) -> ChatItem:
        """Append a timeline item to a chat thread."""
        with self.db_service.get_session_maker() as session:
            chat_thread = session.get(ChatThread, thread_id)
            if chat_thread is None:
                chat_thread = ChatThread(id=thread_id, user_id=user_id)
                session.add(chat_thread)
                session.flush()

            max_seq_statement = select(func.max(ChatItem.seq)).where(ChatItem.thread_id == thread_id)
            max_seq = session.exec(max_seq_statement).one()
            next_seq = 1 if max_seq is None else int(max_seq) + 1

            chat_item = ChatItem(
                thread_id=thread_id,
                user_id=user_id,
                seq=next_seq,
                item_type=item_type,
                content=content,
                item_metadata=item_metadata or {},
            )
            session.add(chat_item)

            if not chat_thread.title and item_type == "user":
                chat_thread.title = _build_chat_preview(content, max_length=60)
            chat_thread.last_item_preview = _build_chat_preview(content)
            chat_thread.last_item_type = item_type
            chat_thread.last_message_at = chat_item.created_at
            chat_thread.item_count = next_seq
            chat_thread.updated_at = chat_item.created_at
            session.add(chat_thread)

            session.commit()
            session.refresh(chat_item)
            logger.info(
                "chat_item_created",
                thread_id=thread_id,
                user_id=user_id,
                item_type=item_type,
                seq=next_seq,
            )
            return chat_item

    async def get_chat_items(self, thread_id: str) -> List[ChatItem]:
        """List timeline items for a chat thread in stable order."""
        with self.db_service.get_session_maker() as session:
            statement = select(ChatItem).where(ChatItem.thread_id == thread_id).order_by(ChatItem.seq.asc())
            return list(session.exec(statement).all())

    async def delete_chat_thread(self, thread_id: str) -> bool:
        """Delete a chat thread and all of its timeline items."""
        with self.db_service.get_session_maker() as session:
            chat_thread = session.get(ChatThread, thread_id)
            if chat_thread is None:
                return False

            items = session.exec(select(ChatItem).where(ChatItem.thread_id == thread_id)).all()
            for item in items:
                session.delete(item)
            session.delete(chat_thread)
            session.commit()
            logger.info("chat_thread_deleted", thread_id=thread_id)
            return True


chat_store = ChatStore()
