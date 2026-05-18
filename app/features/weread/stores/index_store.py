"""Local SQLite note index for WeRead markdown documents."""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
import struct
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger
from app.infrastructure.chinese_fts_text import (
    build_fts5_match_query,
    fts_virtual_table_uses_trigram,
    segment_text_for_fts,
)
from app.infrastructure.retrieval_utils import (
    bm25_rank_to_score,
    hybrid_candidate_limit,
    normalize_hybrid_weights,
    vector_distance_to_score,
)
from app.features.weread.models import (
    WeReadLocalDocumentIndexState,
    WeReadLocalDocumentIndexResponse,
    WeReadLocalDocumentMetadata,
    WeReadLocalNoteChunk,
    WeReadLocalNoteChunkHit,
    WeReadLocalNoteChunkSearchResponse,
)

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _serialize_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _deserialize_vector(vector_blob: bytes, dim: int) -> list[float]:
    if dim <= 0:
        return []
    return list(struct.unpack(f"<{dim}f", vector_blob))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    numerator = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


class WeReadLocalIndexStore:
    """Persist item-level chunks and FTS indexes for local WeRead documents."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or settings.LOCAL_KNOWLEDGE_DB_PATH)
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._sqlite_vec_available = False

    @property
    def sqlite_vec_available(self) -> bool:
        return self._sqlite_vec_available

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
        connection.execute("PRAGMA foreign_keys = ON")
        if self._load_sqlite_vec_into_connection(connection):
            self._sqlite_vec_available = True
        return connection

    @staticmethod
    def _load_sqlite_vec_into_connection(connection: sqlite3.Connection) -> bool:
        enable = getattr(connection, "enable_load_extension", None)
        load_ext = getattr(connection, "load_extension", None)
        if not callable(enable) or not callable(load_ext):
            return False
        try:
            enable(True)
            sqlite_vec.load(connection)
        except (AttributeError, OSError, sqlite3.OperationalError, sqlite3.DatabaseError):
            logger.warning("weread_local_index_sqlite_vec_load_failed", exc_info=True)
            return False
        return True

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _trigger_exists(connection: sqlite3.Connection, trigger_name: str) -> bool:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
        if not WeReadLocalIndexStore._table_exists(connection, table_name):
            return set()
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _rebuild_note_chunk_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute("DROP TRIGGER IF EXISTS note_chunks_ad_note_chunk_vec")
        connection.execute("DROP TABLE IF EXISTS note_chunk_vec")
        for trigger_name in ("note_chunks_ai", "note_chunks_ad", "note_chunks_au"):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        connection.execute("DROP TABLE IF EXISTS note_chunk_embeddings")
        connection.execute("DROP TABLE IF EXISTS note_chunks_fts")
        connection.execute("DROP TABLE IF EXISTS note_chunks")
        connection.execute(
            """
            UPDATE note_documents
            SET indexed_at = '',
                index_status = 'pending',
                embedding_model = '',
                embedding_dim = 0,
                embedding_indexed_at = ''
            """
        )

    def _create_note_chunk_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS note_chunks (
                id INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL UNIQUE,
                doc_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                book_id TEXT NOT NULL,
                book_title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                heading TEXT NOT NULL DEFAULT '',
                chapter_uid INTEGER,
                chapter_title TEXT NOT NULL DEFAULT '',
                start_item_index INTEGER NOT NULL DEFAULT 0,
                end_item_index INTEGER NOT NULL DEFAULT 0,
                create_time TEXT NOT NULL DEFAULT '',
                style INTEGER,
                text TEXT NOT NULL DEFAULT '',
                char_count INTEGER NOT NULL DEFAULT 0,
                token_estimate INTEGER NOT NULL DEFAULT 0,
                book_title_fts TEXT NOT NULL DEFAULT '',
                chapter_title_fts TEXT NOT NULL DEFAULT '',
                heading_fts TEXT NOT NULL DEFAULT '',
                text_fts TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(doc_id) REFERENCES note_documents(doc_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_note_chunks_doc_ordinal
            ON note_chunks (doc_id, ordinal)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_note_chunks_user_book_source
            ON note_chunks (user_id, book_id, source_type)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS note_chunk_embeddings (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                embed_model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(chunk_id) REFERENCES note_chunks(chunk_id),
                FOREIGN KEY(doc_id) REFERENCES note_documents(doc_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_note_chunk_embeddings_doc_id
            ON note_chunk_embeddings (doc_id)
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks_fts USING fts5(
                chunk_id UNINDEXED,
                book_title_fts,
                chapter_title_fts,
                heading_fts,
                text_fts,
                content = 'note_chunks',
                content_rowid = 'id',
                tokenize = 'unicode61'
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_ai AFTER INSERT ON note_chunks BEGIN
                INSERT INTO note_chunks_fts(
                    rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    new.id, new.chunk_id, new.book_title_fts, new.chapter_title_fts, new.heading_fts, new.text_fts
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_ad AFTER DELETE ON note_chunks BEGIN
                INSERT INTO note_chunks_fts(
                    note_chunks_fts, rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    'delete', old.id, old.chunk_id, old.book_title_fts, old.chapter_title_fts, old.heading_fts, old.text_fts
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_au AFTER UPDATE ON note_chunks BEGIN
                INSERT INTO note_chunks_fts(
                    note_chunks_fts, rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    'delete', old.id, old.chunk_id, old.book_title_fts, old.chapter_title_fts, old.heading_fts, old.text_fts
                );
                INSERT INTO note_chunks_fts(
                    rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    new.id, new.chunk_id, new.book_title_fts, new.chapter_title_fts, new.heading_fts, new.text_fts
                );
            END
            """
        )

    def _recreate_note_chunks_fts_only(self, connection: sqlite3.Connection) -> None:
        for trigger_name in ("note_chunks_ai", "note_chunks_ad", "note_chunks_au"):
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
        connection.execute("DROP TABLE IF EXISTS note_chunks_fts")
        connection.execute(
            """
            CREATE VIRTUAL TABLE note_chunks_fts USING fts5(
                chunk_id UNINDEXED,
                book_title_fts,
                chapter_title_fts,
                heading_fts,
                text_fts,
                content = 'note_chunks',
                content_rowid = 'id',
                tokenize = 'unicode61'
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_ai AFTER INSERT ON note_chunks BEGIN
                INSERT INTO note_chunks_fts(
                    rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    new.id, new.chunk_id, new.book_title_fts, new.chapter_title_fts, new.heading_fts, new.text_fts
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_ad AFTER DELETE ON note_chunks BEGIN
                INSERT INTO note_chunks_fts(
                    note_chunks_fts, rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    'delete', old.id, old.chunk_id, old.book_title_fts, old.chapter_title_fts, old.heading_fts, old.text_fts
                );
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_au AFTER UPDATE ON note_chunks BEGIN
                INSERT INTO note_chunks_fts(
                    note_chunks_fts, rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    'delete', old.id, old.chunk_id, old.book_title_fts, old.chapter_title_fts, old.heading_fts, old.text_fts
                );
                INSERT INTO note_chunks_fts(
                    rowid, chunk_id, book_title_fts, chapter_title_fts, heading_fts, text_fts
                ) VALUES (
                    new.id, new.chunk_id, new.book_title_fts, new.chapter_title_fts, new.heading_fts, new.text_fts
                );
            END
            """
        )

    def _backfill_note_chunks_fts_columns(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT id, book_title, chapter_title, heading, text
            FROM note_chunks
            """,
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE note_chunks
                SET
                    book_title_fts = ?,
                    chapter_title_fts = ?,
                    heading_fts = ?,
                    text_fts = ?
                WHERE id = ?
                """,
                (
                    segment_text_for_fts(str(row["book_title"] or "")),
                    segment_text_for_fts(str(row["chapter_title"] or "")),
                    segment_text_for_fts(str(row["heading"] or "")),
                    segment_text_for_fts(str(row["text"] or "")),
                    int(row["id"]),
                ),
            )

    def _migrate_note_chunks_to_jieba_fts(self, connection: sqlite3.Connection) -> None:
        for column_name in ("book_title_fts", "chapter_title_fts", "heading_fts", "text_fts"):
            try:
                connection.execute(
                    f"""
                    ALTER TABLE note_chunks
                    ADD COLUMN {column_name} TEXT NOT NULL DEFAULT ''
                    """,
                )
            except sqlite3.OperationalError:
                pass
        self._recreate_note_chunks_fts_only(connection)
        self._backfill_note_chunks_fts_columns(connection)
        connection.execute("INSERT INTO note_chunks_fts(note_chunks_fts) VALUES('rebuild')")

    @staticmethod
    def _existing_note_chunk_vec_dimensions(connection: sqlite3.Connection) -> int | None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'note_chunk_vec'",
        ).fetchone()
        if row is None or not row[0]:
            return None
        match = re.search(r"float\[(\d+)\]", str(row[0]), flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _ensure_note_chunk_vec_schema(self, connection: sqlite3.Connection) -> None:
        if not self._sqlite_vec_available:
            return
        dims = int(settings.NOTE_EMBEDDING_DIMENSIONS)
        if dims <= 0:
            logger.warning("weread_note_chunk_vec_skipped_invalid_dimensions", dimensions=dims)
            return

        existing_dims = self._existing_note_chunk_vec_dimensions(connection)
        if existing_dims is not None and existing_dims != dims:
            logger.info(
                "weread_note_chunk_vec_dimension_mismatch_rebuild",
                existing_dimensions=existing_dims,
                expected_dimensions=dims,
            )
            connection.execute("DROP TRIGGER IF EXISTS note_chunks_ad_note_chunk_vec")
            connection.execute("DROP TABLE IF EXISTS note_chunk_vec")

        if self._existing_note_chunk_vec_dimensions(connection) is None:
            create_sql_cosine = (
                f"CREATE VIRTUAL TABLE note_chunk_vec USING vec0("
                f"embedding float[{dims}] distance_metric=cosine"
                f")"
            )
            try:
                connection.execute(create_sql_cosine)
            except sqlite3.OperationalError:
                logger.warning(
                    "weread_note_chunk_vec_cosine_create_failed_retry_l2",
                    dimensions=dims,
                    exc_info=True,
                )
                try:
                    connection.execute(
                        f"CREATE VIRTUAL TABLE note_chunk_vec USING vec0(embedding float[{dims}])",
                    )
                except sqlite3.OperationalError:
                    logger.exception("weread_note_chunk_vec_table_create_failed", dimensions=dims)
                    return

        if self._existing_note_chunk_vec_dimensions(connection) is None:
            logger.warning("weread_note_chunk_vec_table_missing_after_create", dimensions=dims)
            return

        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS note_chunks_ad_note_chunk_vec
            AFTER DELETE ON note_chunks
            BEGIN
                DELETE FROM note_chunk_vec WHERE rowid = OLD.id;
            END
            """
        )

        vec_count_row = connection.execute("SELECT COUNT(*) AS n FROM note_chunk_vec").fetchone()
        emb_count_row = connection.execute("SELECT COUNT(*) AS n FROM note_chunk_embeddings").fetchone()
        vec_count = int(vec_count_row["n"] if vec_count_row else 0)
        emb_count = int(emb_count_row["n"] if emb_count_row else 0)
        if vec_count == 0 and emb_count > 0:
            self._backfill_note_chunk_vec_from_embeddings(connection)

    def _backfill_note_chunk_vec_from_embeddings(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT c.id, e.dim, e.vector
            FROM note_chunk_embeddings e
            JOIN note_chunks c ON c.chunk_id = e.chunk_id
            """,
        ).fetchall()
        expected_dims = int(settings.NOTE_EMBEDDING_DIMENSIONS)
        for row in rows:
            dim = int(row["dim"] or 0)
            blob = row["vector"]
            rid = int(row["id"])
            if dim != expected_dims or not isinstance(blob, bytes) or len(blob) != dim * 4:
                continue
            connection.execute("DELETE FROM note_chunk_vec WHERE rowid = ?", (rid,))
            connection.execute(
                "INSERT INTO note_chunk_vec(rowid, embedding) VALUES (?, ?)",
                (rid, blob),
            )
        logger.info(
            "weread_note_chunk_vec_backfilled_from_embeddings",
            row_count=len(rows),
        )

    def _delete_note_chunk_vec_rows_for_chunk_ids(
        self,
        connection: sqlite3.Connection,
        chunk_ids: list[str],
    ) -> None:
        if not chunk_ids or not self._sqlite_vec_available:
            return
        if not self._table_exists(connection, "note_chunk_vec"):
            return
        for chunk_id in chunk_ids:
            row = connection.execute(
                "SELECT id FROM note_chunks WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
            if row is None:
                continue
            connection.execute(
                "DELETE FROM note_chunk_vec WHERE rowid = ?",
                (int(row["id"]),),
            )

    def _upsert_note_chunk_vec_rows(
        self,
        connection: sqlite3.Connection,
        *,
        doc_id: str,
        embeddings: dict[str, list[float]],
    ) -> None:
        if not self._sqlite_vec_available or not embeddings:
            return
        if not self._table_exists(connection, "note_chunk_vec"):
            return
        expected_dims = int(settings.NOTE_EMBEDDING_DIMENSIONS)
        for chunk_id, vector in embeddings.items():
            if len(vector) != expected_dims:
                logger.warning(
                    "weread_note_chunk_vec_upsert_skipped_dimension_mismatch",
                    chunk_id=chunk_id,
                    expected_dimensions=expected_dims,
                    actual_dimensions=len(vector),
                )
                continue
            row = connection.execute(
                "SELECT id FROM note_chunks WHERE chunk_id = ? AND doc_id = ?",
                (chunk_id, doc_id),
            ).fetchone()
            if row is None:
                continue
            rid = int(row["id"])
            blob = sqlite_vec.serialize_float32(vector)
            connection.execute("DELETE FROM note_chunk_vec WHERE rowid = ?", (rid,))
            connection.execute(
                "INSERT INTO note_chunk_vec(rowid, embedding) VALUES (?, ?)",
                (rid, blob),
            )

    def _ensure_note_chunk_schema(self, connection: sqlite3.Connection) -> None:
        note_chunk_columns = self._table_columns(connection, "note_chunks")
        has_expected_note_chunk_schema = {"id", "chunk_id", "book_title"}.issubset(note_chunk_columns)
        has_expected_triggers = all(
            self._trigger_exists(connection, trigger_name)
            for trigger_name in ("note_chunks_ai", "note_chunks_ad", "note_chunks_au")
        )
        has_embedding_table = self._table_exists(connection, "note_chunk_embeddings")

        if note_chunk_columns and (not has_expected_note_chunk_schema or not has_expected_triggers or not has_embedding_table):
            self._rebuild_note_chunk_tables(connection)
            note_chunk_columns = set()

        self._create_note_chunk_tables(connection)
        note_chunk_columns = self._table_columns(connection, "note_chunks")
        if note_chunk_columns and "text_fts" not in note_chunk_columns:
            self._migrate_note_chunks_to_jieba_fts(connection)
        else:
            fts_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='note_chunks_fts'",
            ).fetchone()
            fts_sql = str(fts_sql_row[0]) if fts_sql_row and fts_sql_row[0] else None
            if fts_sql and fts_virtual_table_uses_trigram(fts_sql):
                self._recreate_note_chunks_fts_only(connection)
                self._backfill_note_chunks_fts_columns(connection)
                connection.execute("INSERT INTO note_chunks_fts(note_chunks_fts) VALUES('rebuild')")

        has_expected_triggers = all(
            self._trigger_exists(connection, trigger_name)
            for trigger_name in ("note_chunks_ai", "note_chunks_ad", "note_chunks_au")
        )
        note_chunk_columns = self._table_columns(connection, "note_chunks")
        if (not note_chunk_columns or not has_expected_triggers) and self._table_exists(
            connection,
            "note_chunks_fts",
        ):
            connection.execute("INSERT INTO note_chunks_fts(note_chunks_fts) VALUES('rebuild')")

    def _ensure_note_documents_schema(self, connection: sqlite3.Connection) -> None:
        existing_columns = self._table_columns(connection, "note_documents")
        if "embedding_model" not in existing_columns:
            connection.execute(
                """
                ALTER TABLE note_documents
                ADD COLUMN embedding_model TEXT NOT NULL DEFAULT ''
                """
            )
        if "embedding_dim" not in existing_columns:
            connection.execute(
                """
                ALTER TABLE note_documents
                ADD COLUMN embedding_dim INTEGER NOT NULL DEFAULT 0
                """
            )
        if "embedding_indexed_at" not in existing_columns:
            connection.execute(
                """
                ALTER TABLE note_documents
                ADD COLUMN embedding_indexed_at TEXT NOT NULL DEFAULT ''
                """
            )

    def _initialize_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS note_documents (
                    doc_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL DEFAULT 'weread',
                    book_id TEXT NOT NULL,
                    book_title TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL,
                    file_path TEXT NOT NULL DEFAULT '',
                    include_chapter INTEGER NOT NULL DEFAULT 1,
                    highlight_style INTEGER,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL DEFAULT '',
                    metadata_hash TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL DEFAULT '',
                    index_status TEXT NOT NULL DEFAULT 'pending',
                    embedding_model TEXT NOT NULL DEFAULT '',
                    embedding_dim INTEGER NOT NULL DEFAULT 0,
                    embedding_indexed_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_note_documents_user_book_source
                ON note_documents (user_id, book_id, source_type)
                """
            )
            self._ensure_note_documents_schema(connection)
            self._ensure_note_chunk_schema(connection)
            self._ensure_note_chunk_vec_schema(connection)
            connection.commit()

    async def sync_document_index(
        self,
        *,
        metadata: WeReadLocalDocumentMetadata,
        chunks: list[WeReadLocalNoteChunk],
    ) -> WeReadLocalDocumentIndexResponse:
        await self._ensure_initialized()
        return await asyncio.to_thread(self._sync_document_index, metadata, chunks)

    def _sync_document_index(
        self,
        metadata: WeReadLocalDocumentMetadata,
        chunks: list[WeReadLocalNoteChunk],
    ) -> WeReadLocalDocumentIndexResponse:
        with self._connect() as connection:
            existing_document = connection.execute(
                """
                SELECT
                    content_hash,
                    metadata_hash,
                    indexed_at,
                    embedding_model,
                    embedding_dim,
                    embedding_indexed_at
                FROM note_documents
                WHERE doc_id = ?
                """,
                (metadata.doc_id,),
            ).fetchone()
            existing_chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM note_chunks WHERE doc_id = ?",
                    (metadata.doc_id,),
                ).fetchone()["count"]
            )

            unchanged = (
                existing_document is not None
                and str(existing_document["content_hash"] or "") == metadata.content_hash
                and str(existing_document["metadata_hash"] or "") == metadata.metadata_hash
                and bool(str(existing_document["indexed_at"] or ""))
                and (existing_chunk_count > 0 or not chunks)
            )
            if unchanged:
                return WeReadLocalDocumentIndexResponse(
                    doc_id=metadata.doc_id,
                    book_id=metadata.book_id,
                    book_title=metadata.book_title,
                    source_type=metadata.source_type,
                    status="unchanged",
                    chunk_count=existing_chunk_count,
                    indexed_at=str(existing_document["indexed_at"] or ""),
                    embedding_status="ready" if str(existing_document["embedding_indexed_at"] or "") else "pending",
                )

            indexed_at = _now_iso()
            status = "created" if existing_document is None else "updated"
            existing_chunk_rows = connection.execute(
                """
                SELECT chunk_id, text
                FROM note_chunks
                WHERE doc_id = ?
                """,
                (metadata.doc_id,),
            ).fetchall()
            existing_chunks_by_id = {str(row["chunk_id"]): row for row in existing_chunk_rows}
            incoming_chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
            existing_chunk_ids = set(existing_chunks_by_id)
            incoming_chunk_ids = set(incoming_chunks_by_id)
            removed_chunk_ids = existing_chunk_ids - incoming_chunk_ids
            text_changed_chunk_ids = {
                chunk_id
                for chunk_id in (existing_chunk_ids & incoming_chunk_ids)
                if str(existing_chunks_by_id[chunk_id]["text"] or "") != incoming_chunks_by_id[chunk_id].text
            }

            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO note_documents (
                    doc_id, user_id, provider, book_id, book_title, source_type, file_path,
                    include_chapter, highlight_style, item_count, content_hash, metadata_hash,
                    generated_at, indexed_at, index_status, embedding_model, embedding_dim, embedding_indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    provider = excluded.provider,
                    book_id = excluded.book_id,
                    book_title = excluded.book_title,
                    source_type = excluded.source_type,
                    file_path = excluded.file_path,
                    include_chapter = excluded.include_chapter,
                    highlight_style = excluded.highlight_style,
                    item_count = excluded.item_count,
                    content_hash = excluded.content_hash,
                    metadata_hash = excluded.metadata_hash,
                    generated_at = excluded.generated_at,
                    indexed_at = excluded.indexed_at,
                    index_status = excluded.index_status,
                    embedding_model = excluded.embedding_model,
                    embedding_dim = excluded.embedding_dim,
                    embedding_indexed_at = excluded.embedding_indexed_at
                """,
                (
                    metadata.doc_id,
                    metadata.user_id,
                    metadata.provider,
                    metadata.book_id,
                    metadata.book_title,
                    metadata.source_type,
                    metadata.file_path,
                    int(metadata.include_chapter),
                    metadata.highlight_style,
                    metadata.item_count,
                    metadata.content_hash,
                    metadata.metadata_hash,
                    metadata.generated_at,
                    indexed_at,
                    "fts_ready",
                    str(existing_document["embedding_model"] or "") if existing_document is not None else "",
                    int(existing_document["embedding_dim"] or 0) if existing_document is not None else 0,
                    str(existing_document["embedding_indexed_at"] or "") if existing_document is not None else "",
                ),
            )
            if removed_chunk_ids:
                connection.executemany(
                    "DELETE FROM note_chunk_embeddings WHERE chunk_id = ?",
                    [(chunk_id,) for chunk_id in sorted(removed_chunk_ids)],
                )
                connection.executemany(
                    "DELETE FROM note_chunks WHERE chunk_id = ?",
                    [(chunk_id,) for chunk_id in sorted(removed_chunk_ids)],
                )
            if text_changed_chunk_ids:
                connection.executemany(
                    "DELETE FROM note_chunk_embeddings WHERE chunk_id = ?",
                    [(chunk_id,) for chunk_id in sorted(text_changed_chunk_ids)],
                )
                self._delete_note_chunk_vec_rows_for_chunk_ids(
                    connection,
                    sorted(text_changed_chunk_ids),
                )

            if chunks:
                connection.executemany(
                    """
                    INSERT INTO note_chunks (
                        chunk_id, doc_id, user_id, book_id, book_title, source_type, ordinal, heading,
                        chapter_uid, chapter_title, start_item_index, end_item_index,
                        create_time, style, text, char_count, token_estimate,
                        book_title_fts, chapter_title_fts, heading_fts, text_fts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        doc_id = excluded.doc_id,
                        user_id = excluded.user_id,
                        book_id = excluded.book_id,
                        book_title = excluded.book_title,
                        source_type = excluded.source_type,
                        ordinal = excluded.ordinal,
                        heading = excluded.heading,
                        chapter_uid = excluded.chapter_uid,
                        chapter_title = excluded.chapter_title,
                        start_item_index = excluded.start_item_index,
                        end_item_index = excluded.end_item_index,
                        create_time = excluded.create_time,
                        style = excluded.style,
                        text = excluded.text,
                        char_count = excluded.char_count,
                        token_estimate = excluded.token_estimate,
                        book_title_fts = excluded.book_title_fts,
                        chapter_title_fts = excluded.chapter_title_fts,
                        heading_fts = excluded.heading_fts,
                        text_fts = excluded.text_fts
                    """,
                    [
                        (
                            chunk.chunk_id,
                            chunk.doc_id,
                            chunk.user_id,
                            chunk.book_id,
                            chunk.book_title,
                            chunk.source_type,
                            chunk.ordinal,
                            chunk.heading,
                            chunk.chapter_uid,
                            chunk.chapter_title,
                            chunk.start_item_index,
                            chunk.end_item_index,
                            chunk.create_time,
                            chunk.style,
                            chunk.text,
                            chunk.char_count,
                            chunk.token_estimate,
                            segment_text_for_fts(chunk.book_title),
                            segment_text_for_fts(chunk.chapter_title),
                            segment_text_for_fts(chunk.heading),
                            segment_text_for_fts(chunk.text),
                        )
                        for chunk in chunks
                    ],
                )

            embedded_chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM note_chunk_embeddings WHERE doc_id = ?",
                    (metadata.doc_id,),
                ).fetchone()["count"]
            )
            existing_embedding_indexed_at = (
                str(existing_document["embedding_indexed_at"] or "") if existing_document is not None else ""
            )
            embedding_ready = (not chunks) or (
                bool(existing_embedding_indexed_at) and embedded_chunk_count == len(chunks)
            )
            if embedding_ready:
                connection.execute(
                    """
                    UPDATE note_documents
                    SET embedding_model = ?,
                        embedding_dim = ?,
                        embedding_indexed_at = ?,
                        index_status = 'ready'
                    WHERE doc_id = ?
                    """,
                    (
                        str(existing_document["embedding_model"] or "") if existing_document is not None else "",
                        int(existing_document["embedding_dim"] or 0) if existing_document is not None else 0,
                        existing_embedding_indexed_at,
                        metadata.doc_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE note_documents
                    SET embedding_model = '',
                        embedding_dim = 0,
                        embedding_indexed_at = '',
                        index_status = 'fts_ready'
                    WHERE doc_id = ?
                    """,
                    (metadata.doc_id,),
                )
            connection.commit()

        logger.info(
            "weread_local_document_index_synced",
            doc_id=metadata.doc_id,
            user_id=metadata.user_id,
            book_id=metadata.book_id,
            source_type=metadata.source_type,
            status=status,
            chunk_count=len(chunks),
        )
        return WeReadLocalDocumentIndexResponse(
            doc_id=metadata.doc_id,
            book_id=metadata.book_id,
            book_title=metadata.book_title,
            source_type=metadata.source_type,
            status=status,
            chunk_count=len(chunks),
            indexed_at=indexed_at,
            embedding_status="ready" if embedding_ready else "pending",
        )

    async def search_note_chunks(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
        text_weight: float = 0.3,
        vector_weight: float = 0.7,
        candidate_multiplier: int = 4,
    ) -> WeReadLocalNoteChunkSearchResponse:
        await self._ensure_initialized()
        return await asyncio.to_thread(
            self._search_note_chunks,
            user_id,
            book_id,
            source_type,
            query,
            top_k,
            query_embedding,
            text_weight,
            vector_weight,
            candidate_multiplier,
        )

    async def sync_chunk_embeddings(
        self,
        *,
        doc_id: str,
        embed_model: str,
        embeddings: dict[str, list[float]],
    ) -> None:
        await self._ensure_initialized()
        await asyncio.to_thread(self._sync_chunk_embeddings, doc_id, embed_model, embeddings)

    async def get_chunks_missing_embeddings(
        self,
        *,
        doc_id: str,
    ) -> list[str]:
        await self._ensure_initialized()
        return await asyncio.to_thread(self._get_chunks_missing_embeddings, doc_id)

    async def get_document_index_state(
        self,
        *,
        metadata: WeReadLocalDocumentMetadata,
    ) -> WeReadLocalDocumentIndexState:
        await self._ensure_initialized()
        return await asyncio.to_thread(self._get_document_index_state, metadata)

    @staticmethod
    def _build_source_filter(source_type: str) -> tuple[str, list[object]]:
        if source_type == "both":
            return "", []
        return "AND c.source_type = ?", [source_type]

    def _search_text_candidates(
        self,
        *,
        connection: sqlite3.Connection,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        limit: int,
    ) -> tuple[int, list[sqlite3.Row]]:
        source_filter_sql, source_filter_params = self._build_source_filter(source_type)
        base_parameters: list[object] = [user_id, book_id, *source_filter_params]

        fts_match = build_fts5_match_query(query)
        if not fts_match:
            logger.debug(
                "weread_note_chunks_fts_query_skipped_no_tokens",
                user_id=user_id,
                book_id=book_id,
                source_type=source_type,
            )
            return 0, []

        count_sql = f"""
            SELECT COUNT(*) AS total_hits
            FROM note_chunks_fts
            JOIN note_chunks c ON c.id = note_chunks_fts.rowid
            WHERE note_chunks_fts MATCH ?
              AND c.user_id = ?
              AND c.book_id = ?
              {source_filter_sql}
        """
        search_sql = f"""
            SELECT
                c.chunk_id,
                c.doc_id,
                c.source_type,
                c.ordinal,
                c.heading,
                c.chapter_uid,
                c.chapter_title,
                c.create_time,
                c.style,
                c.text,
                d.book_id,
                d.book_title,
                bm25(note_chunks_fts) AS rank
            FROM note_chunks_fts
            JOIN note_chunks c ON c.id = note_chunks_fts.rowid
            JOIN note_documents d ON d.doc_id = c.doc_id
            WHERE note_chunks_fts MATCH ?
              AND c.user_id = ?
              AND c.book_id = ?
              {source_filter_sql}
            ORDER BY rank
            LIMIT ?
        """
        parameters = [fts_match, *base_parameters]
        try:
            total_hits = int(connection.execute(count_sql, parameters).fetchone()["total_hits"])
            rows = connection.execute(search_sql, [*parameters, limit]).fetchall()
        except sqlite3.OperationalError:
            logger.warning("weread_note_chunks_fts_match_failed", exc_info=True)
            return 0, []
        return total_hits, rows

    def _search_vector_candidates(
        self,
        *,
        connection: sqlite3.Connection,
        user_id: int,
        book_id: str,
        source_type: str,
        query_embedding: list[float] | None,
        limit: int,
    ) -> list[tuple[sqlite3.Row, float]]:
        if not query_embedding:
            return []

        expected_dims = int(settings.NOTE_EMBEDDING_DIMENSIONS)
        if len(query_embedding) != expected_dims:
            logger.warning(
                "weread_note_chunks_vector_query_dimension_mismatch",
                expected_dimensions=expected_dims,
                actual_dimensions=len(query_embedding),
            )
            return []

        source_filter_sql, source_filter_params = self._build_source_filter(source_type)
        use_vec = (
            self._sqlite_vec_available
            and self._table_exists(connection, "note_chunk_vec")
        )
        if use_vec:
            vec_scan_limit = max(200, limit * 40)
            blob = sqlite_vec.serialize_float32(query_embedding)
            try:
                vec_rows = connection.execute(
                    """
                    SELECT rowid, distance
                    FROM note_chunk_vec
                    WHERE embedding MATCH ?
                    ORDER BY distance
                    LIMIT ?
                    """,
                    (blob, vec_scan_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                logger.warning("weread_note_chunk_vec_match_failed", exc_info=True)
                vec_rows = []

            if vec_rows:
                ordered_ids: list[int] = []
                dist_map: dict[int, float] = {}
                for vec_row in vec_rows:
                    rid = int(vec_row["rowid"])
                    ordered_ids.append(rid)
                    dist_map[rid] = float(vec_row["distance"])

                id_placeholders = ", ".join("?" for _ in ordered_ids)
                filter_params: list[object] = [
                    *ordered_ids,
                    user_id,
                    book_id,
                    *source_filter_params,
                ]
                filter_sql = f"""
                    SELECT
                        c.id AS _chunk_internal_id,
                        c.chunk_id,
                        c.doc_id,
                        c.source_type,
                        c.ordinal,
                        c.heading,
                        c.chapter_uid,
                        c.chapter_title,
                        c.create_time,
                        c.style,
                        c.text,
                        d.book_id,
                        d.book_title
                    FROM note_chunks c
                    JOIN note_documents d ON d.doc_id = c.doc_id
                    WHERE c.id IN ({id_placeholders})
                      AND c.user_id = ?
                      AND c.book_id = ?
                      {source_filter_sql}
                """
                rows = connection.execute(filter_sql, filter_params).fetchall()
                row_by_id = {int(r["_chunk_internal_id"]): r for r in rows}
                scored_rows: list[tuple[sqlite3.Row, float]] = []
                for chunk_row_id in ordered_ids:
                    row = row_by_id.get(chunk_row_id)
                    if row is None:
                        continue
                    v_score = vector_distance_to_score(dist_map[chunk_row_id])
                    if v_score <= 0:
                        continue
                    scored_rows.append((row, v_score))
                    if len(scored_rows) >= limit:
                        break
                return scored_rows

        sql = f"""
            SELECT
                c.chunk_id,
                c.doc_id,
                c.source_type,
                c.ordinal,
                c.heading,
                c.chapter_uid,
                c.chapter_title,
                c.create_time,
                c.style,
                c.text,
                d.book_id,
                d.book_title,
                e.dim,
                e.vector
            FROM note_chunk_embeddings e
            JOIN note_chunks c ON c.chunk_id = e.chunk_id
            JOIN note_documents d ON d.doc_id = c.doc_id
            WHERE c.user_id = ?
              AND c.book_id = ?
              {source_filter_sql}
        """
        rows = connection.execute(sql, [user_id, book_id, *source_filter_params]).fetchall()
        blob_scored: list[tuple[sqlite3.Row, float]] = []
        for row in rows:
            dim = int(row["dim"] or 0)
            vector_blob = row["vector"]
            if not isinstance(vector_blob, bytes):
                continue
            candidate_vector = _deserialize_vector(vector_blob, dim)
            vector_score = max(0.0, _cosine_similarity(query_embedding, candidate_vector))
            if vector_score <= 0:
                continue
            blob_scored.append((row, vector_score))

        blob_scored.sort(key=lambda item: item[1], reverse=True)
        return blob_scored[:limit]

    @staticmethod
    def _row_to_hit(
        row: sqlite3.Row,
        *,
        final_score: float,
        text_score: float | None,
        vector_score: float | None,
    ) -> WeReadLocalNoteChunkHit:
        return WeReadLocalNoteChunkHit(
            chunk_id=str(row["chunk_id"]),
            doc_id=str(row["doc_id"]),
            book_id=str(row["book_id"]),
            book_title=str(row["book_title"]),
            source_type=str(row["source_type"]),
            ordinal=int(row["ordinal"]),
            heading=str(row["heading"] or ""),
            chapter_uid=int(row["chapter_uid"]) if row["chapter_uid"] is not None else None,
            chapter_title=str(row["chapter_title"] or ""),
            create_time=str(row["create_time"] or ""),
            style=int(row["style"]) if row["style"] is not None else None,
            text=str(row["text"] or ""),
            score=final_score,
            text_score=text_score,
            vector_score=vector_score,
        )

    def _merge_candidates(
        self,
        *,
        text_candidates: list[sqlite3.Row],
        vector_candidates: list[tuple[sqlite3.Row, float]],
        top_k: int,
        text_weight: float,
        vector_weight: float,
    ) -> tuple[list[WeReadLocalNoteChunkHit], str]:
        if not vector_candidates:
            hits = []
            for row in text_candidates[:top_k]:
                raw_rank = row["rank"] if "rank" in row.keys() and row["rank"] is not None else 0.0
                ts = bm25_rank_to_score(float(raw_rank))
                final = text_weight * ts + vector_weight * 0.0
                hits.append(
                    self._row_to_hit(
                        row,
                        final_score=final,
                        text_score=ts,
                        vector_score=None,
                    ),
                )
            return hits, "fts"

        candidate_rows: dict[str, sqlite3.Row] = {}
        text_scores: dict[str, float] = {}
        vector_scores: dict[str, float] = {}

        for row in text_candidates:
            chunk_id = str(row["chunk_id"])
            candidate_rows.setdefault(chunk_id, row)
            raw_rank = row["rank"] if "rank" in row.keys() and row["rank"] is not None else 0.0
            text_scores[chunk_id] = bm25_rank_to_score(float(raw_rank))

        for row, raw_vector_score in vector_candidates:
            chunk_id = str(row["chunk_id"])
            candidate_rows.setdefault(chunk_id, row)
            vector_scores[chunk_id] = raw_vector_score

        ranked_candidates = []
        for chunk_id, row in candidate_rows.items():
            text_score = text_scores.get(chunk_id)
            vector_score = vector_scores.get(chunk_id)
            final_score = (text_weight * (text_score or 0.0)) + (vector_weight * (vector_score or 0.0))
            ranked_candidates.append((row, final_score, text_score, vector_score))

        ranked_candidates.sort(key=lambda item: item[1], reverse=True)
        hits = [
            self._row_to_hit(
                row,
                final_score=final_score,
                text_score=text_score,
                vector_score=vector_score,
            )
            for row, final_score, text_score, vector_score in ranked_candidates[:top_k]
        ]
        return hits, "hybrid"

    def _sync_chunk_embeddings(
        self,
        doc_id: str,
        embed_model: str,
        embeddings: dict[str, list[float]],
    ) -> None:
        if not embeddings:
            return

        updated_at = _now_iso()
        first_vector = next(iter(embeddings.values()))
        dim = len(first_vector)
        with self._connect() as connection:
            connection.execute("BEGIN")
            connection.executemany(
                """
                INSERT INTO note_chunk_embeddings (
                    chunk_id, doc_id, embed_model, dim, vector, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    doc_id = excluded.doc_id,
                    embed_model = excluded.embed_model,
                    dim = excluded.dim,
                    vector = excluded.vector,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        chunk_id,
                        doc_id,
                        embed_model,
                        dim,
                        _serialize_vector(vector),
                        updated_at,
                    )
                    for chunk_id, vector in embeddings.items()
                ],
            )
            self._upsert_note_chunk_vec_rows(connection, doc_id=doc_id, embeddings=embeddings)
            total_chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM note_chunks WHERE doc_id = ?",
                    (doc_id,),
                ).fetchone()["count"]
            )
            embedded_chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM note_chunk_embeddings WHERE doc_id = ?",
                    (doc_id,),
                ).fetchone()["count"]
            )
            if total_chunk_count == 0 or embedded_chunk_count == total_chunk_count:
                connection.execute(
                    """
                    UPDATE note_documents
                    SET embedding_model = ?,
                        embedding_dim = ?,
                        embedding_indexed_at = ?,
                        index_status = 'ready'
                    WHERE doc_id = ?
                    """,
                    (embed_model, dim, updated_at, doc_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE note_documents
                    SET embedding_model = '',
                        embedding_dim = 0,
                        embedding_indexed_at = '',
                        index_status = 'fts_ready'
                    WHERE doc_id = ?
                    """,
                    (doc_id,),
                )
            connection.commit()

    def _get_chunks_missing_embeddings(self, doc_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT c.chunk_id
                FROM note_chunks c
                LEFT JOIN note_chunk_embeddings e ON e.chunk_id = c.chunk_id
                WHERE c.doc_id = ?
                  AND e.chunk_id IS NULL
                ORDER BY c.ordinal
                """,
                (doc_id,),
            ).fetchall()
        return [str(row["chunk_id"]) for row in rows]

    def _get_document_index_state(
        self,
        metadata: WeReadLocalDocumentMetadata,
    ) -> WeReadLocalDocumentIndexState:
        with self._connect() as connection:
            document_row = connection.execute(
                """
                SELECT
                    content_hash,
                    metadata_hash,
                    indexed_at,
                    index_status
                FROM note_documents
                WHERE doc_id = ?
                """,
                (metadata.doc_id,),
            ).fetchone()
            if document_row is None:
                return WeReadLocalDocumentIndexState(doc_id=metadata.doc_id)

            chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM note_chunks WHERE doc_id = ?",
                    (metadata.doc_id,),
                ).fetchone()["count"]
            )
            has_searchable_index = bool(str(document_row["indexed_at"] or "")) and (
                chunk_count > 0 or metadata.item_count == 0
            )
            is_fresh = (
                has_searchable_index
                and str(document_row["content_hash"] or "") == metadata.content_hash
                and str(document_row["metadata_hash"] or "") == metadata.metadata_hash
            )
            raw_index_status = str(document_row["index_status"] or "pending")
            index_status = (
                raw_index_status
                if raw_index_status in {"pending", "fts_ready", "ready"}
                else "pending"
            )
            return WeReadLocalDocumentIndexState(
                doc_id=metadata.doc_id,
                chunk_count=chunk_count,
                index_status=index_status,
                has_searchable_index=has_searchable_index,
                is_fresh=is_fresh,
            )

    def _search_note_chunks(
        self,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int,
        query_embedding: list[float] | None,
        text_weight: float,
        vector_weight: float,
        candidate_multiplier: int,
    ) -> WeReadLocalNoteChunkSearchResponse:
        capped_top_k = max(1, min(top_k, 20))
        candidate_limit = hybrid_candidate_limit(capped_top_k, max(1, candidate_multiplier))
        normalized_query = " ".join(query.strip().split())
        normalized_text_weight, normalized_vector_weight = normalize_hybrid_weights(
            text_weight,
            vector_weight,
        )

        with self._connect() as connection:
            text_total_hits, text_candidates = self._search_text_candidates(
                connection=connection,
                user_id=user_id,
                book_id=book_id,
                source_type=source_type,
                query=normalized_query,
                limit=candidate_limit,
            )
            vector_candidates = self._search_vector_candidates(
                connection=connection,
                user_id=user_id,
                book_id=book_id,
                source_type=source_type,
                query_embedding=query_embedding,
                limit=candidate_limit,
            )

        hits, retrieval_mode = self._merge_candidates(
            text_candidates=text_candidates,
            vector_candidates=vector_candidates,
            top_k=capped_top_k,
            text_weight=normalized_text_weight,
            vector_weight=normalized_vector_weight,
        )
        total_hits = max(text_total_hits, len(vector_candidates), len(text_candidates))

        logger.info(
            "weread_local_note_chunks_searched",
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            query=query,
            total_hits=total_hits,
            returned_hits=len(hits),
            retrieval_mode=retrieval_mode,
        )
        return WeReadLocalNoteChunkSearchResponse(
            book_id=book_id,
            source_type=source_type,
            query=query,
            total_hits=total_hits,
            retrieval_mode=retrieval_mode,
            hits=hits,
        )


weread_local_index_store = WeReadLocalIndexStore()
