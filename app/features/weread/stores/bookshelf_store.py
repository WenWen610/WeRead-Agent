"""Local bookshelf metadata cache backed by SQLite."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.infrastructure.config import settings
from app.features.weread.models import BookshelfSnapshotResponse, SnapshotBookItem


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _serialize_string_list(values: list[str]) -> str:
    return json.dumps([str(value) for value in values if isinstance(value, str)], ensure_ascii=False)


def _deserialize_string_list(raw_value: str) -> list[str]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return []

    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


class WeReadLocalStore:
    """Persist user bookshelf metadata into a local SQLite database."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.LOCAL_KNOWLEDGE_DB_PATH)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            await asyncio.to_thread(self._initialize_db)
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS weread_bookshelf_sync_state (
                    user_id INTEGER PRIMARY KEY,
                    sync_key INTEGER NOT NULL DEFAULT 0,
                    lecture_sync_key INTEGER NOT NULL DEFAULT 0,
                    pure_book_count INTEGER NOT NULL DEFAULT 0,
                    book_count INTEGER NOT NULL DEFAULT 0,
                    generated_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS weread_user_books (
                    user_id INTEGER NOT NULL,
                    book_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    author TEXT NOT NULL DEFAULT '',
                    translator TEXT NOT NULL DEFAULT '',
                    cover TEXT NOT NULL DEFAULT '',
                    format TEXT NOT NULL DEFAULT '',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    book_lists_json TEXT NOT NULL DEFAULT '[]',
                    publish_time TEXT NOT NULL DEFAULT '',
                    finish_reading INTEGER NOT NULL DEFAULT 0,
                    paid INTEGER NOT NULL DEFAULT 0,
                    is_imported INTEGER NOT NULL DEFAULT 0,
                    price REAL NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0,
                    reading_time INTEGER NOT NULL DEFAULT 0,
                    reading_time_formatted TEXT NOT NULL DEFAULT '',
                    last_read_time TEXT NOT NULL DEFAULT '',
                    snapshot_generated_at TEXT NOT NULL DEFAULT '',
                    sync_key INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, book_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weread_user_books_recent
                ON weread_user_books (user_id, last_read_time DESC, reading_time DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weread_user_books_lookup
                ON weread_user_books (user_id, title, author, translator)
                """
            )
            connection.commit()

    async def upsert_bookshelf_snapshot(self, *, user_id: int, snapshot: BookshelfSnapshotResponse) -> None:
        await self._ensure_initialized()
        await asyncio.to_thread(self._upsert_bookshelf_snapshot, user_id, snapshot)

    def _upsert_bookshelf_snapshot(self, user_id: int, snapshot: BookshelfSnapshotResponse) -> None:
        updated_at = _now_iso()
        book_rows = [
            (
                user_id,
                book.book_id,
                book.title,
                book.author,
                book.translator,
                book.cover,
                book.format,
                _serialize_string_list(book.categories),
                _serialize_string_list(book.book_lists),
                book.publish_time,
                int(book.finish_reading),
                int(book.paid),
                int(book.is_imported),
                float(book.price),
                int(book.progress),
                int(book.reading_time),
                book.reading_time_formatted,
                book.last_read_time,
                snapshot.generated_at,
                int(snapshot.sync_key),
                updated_at,
            )
            for book in snapshot.books
        ]

        with self._connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO weread_bookshelf_sync_state (
                    user_id, sync_key, lecture_sync_key, pure_book_count, book_count, generated_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    sync_key = excluded.sync_key,
                    lecture_sync_key = excluded.lecture_sync_key,
                    pure_book_count = excluded.pure_book_count,
                    book_count = excluded.book_count,
                    generated_at = excluded.generated_at,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    snapshot.sync_key,
                    snapshot.lecture_sync_key,
                    snapshot.pure_book_count,
                    snapshot.book_count,
                    snapshot.generated_at,
                    updated_at,
                ),
            )
            connection.execute("DELETE FROM weread_user_books WHERE user_id = ?", (user_id,))
            connection.executemany(
                """
                INSERT INTO weread_user_books (
                    user_id, book_id, title, author, translator, cover, format, categories_json, book_lists_json,
                    publish_time, finish_reading, paid, is_imported, price, progress, reading_time,
                    reading_time_formatted, last_read_time, snapshot_generated_at, sync_key, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                book_rows,
            )
            connection.commit()

    async def get_bookshelf_snapshot(self, user_id: int) -> BookshelfSnapshotResponse | None:
        await self._ensure_initialized()
        return await asyncio.to_thread(self._get_bookshelf_snapshot, user_id)

    def _get_bookshelf_snapshot(self, user_id: int) -> BookshelfSnapshotResponse | None:
        with self._connect() as connection:
            state_row = connection.execute(
                """
                SELECT sync_key, lecture_sync_key, pure_book_count, book_count, generated_at
                FROM weread_bookshelf_sync_state
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if state_row is None:
                return None

            rows = connection.execute(
                """
                SELECT *
                FROM weread_user_books
                WHERE user_id = ?
                ORDER BY
                    CASE WHEN last_read_time = '' THEN 1 ELSE 0 END ASC,
                    last_read_time DESC,
                    reading_time DESC,
                    title COLLATE NOCASE ASC
                """,
                (user_id,),
            ).fetchall()

        books = [self._row_to_snapshot_book(row) for row in rows]
        return BookshelfSnapshotResponse(
            sync_key=int(state_row["sync_key"]),
            lecture_sync_key=int(state_row["lecture_sync_key"]),
            pure_book_count=int(state_row["pure_book_count"]),
            book_count=int(state_row["book_count"]),
            generated_at=str(state_row["generated_at"] or ""),
            books=books,
        )

    async def list_recent_books(self, *, user_id: int, limit: int = 6) -> BookshelfSnapshotResponse | None:
        await self._ensure_initialized()
        capped_limit = max(1, min(limit, 20))
        return await asyncio.to_thread(self._list_recent_books, user_id, capped_limit)

    def _list_recent_books(self, user_id: int, limit: int) -> BookshelfSnapshotResponse | None:
        with self._connect() as connection:
            state_row = connection.execute(
                """
                SELECT sync_key, lecture_sync_key, pure_book_count, book_count, generated_at
                FROM weread_bookshelf_sync_state
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if state_row is None:
                return None

            rows = connection.execute(
                """
                SELECT *
                FROM weread_user_books
                WHERE user_id = ?
                ORDER BY
                    CASE WHEN last_read_time = '' THEN 1 ELSE 0 END ASC,
                    last_read_time DESC,
                    reading_time DESC,
                    title COLLATE NOCASE ASC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        books = [self._row_to_snapshot_book(row) for row in rows]
        return BookshelfSnapshotResponse(
            sync_key=int(state_row["sync_key"]),
            lecture_sync_key=int(state_row["lecture_sync_key"]),
            pure_book_count=int(state_row["pure_book_count"]),
            book_count=int(state_row["book_count"]),
            generated_at=str(state_row["generated_at"] or ""),
            books=books,
        )

    async def clear_user_bookshelf(self, user_id: int) -> None:
        await self._ensure_initialized()
        await asyncio.to_thread(self._clear_user_bookshelf, user_id)

    def _clear_user_bookshelf(self, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM weread_user_books WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM weread_bookshelf_sync_state WHERE user_id = ?", (user_id,))
            connection.commit()

    @staticmethod
    def _row_to_snapshot_book(row: sqlite3.Row) -> SnapshotBookItem:
        return SnapshotBookItem(
            book_id=str(row["book_id"] or ""),
            title=str(row["title"] or ""),
            author=str(row["author"] or ""),
            translator=str(row["translator"] or ""),
            cover=str(row["cover"] or ""),
            format=str(row["format"] or ""),
            categories=_deserialize_string_list(str(row["categories_json"] or "[]")),
            book_lists=_deserialize_string_list(str(row["book_lists_json"] or "[]")),
            publish_time=str(row["publish_time"] or ""),
            finish_reading=bool(row["finish_reading"]),
            paid=bool(row["paid"]),
            is_imported=bool(row["is_imported"]),
            price=float(row["price"] or 0),
            progress=int(row["progress"] or 0),
            reading_time=int(row["reading_time"] or 0),
            reading_time_formatted=str(row["reading_time_formatted"] or ""),
            last_read_time=str(row["last_read_time"] or ""),
        )


weread_local_store = WeReadLocalStore()
