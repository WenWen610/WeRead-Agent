"""SQLite hybrid index for Markdown memory files."""

from __future__ import annotations

import asyncio
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.features.markdown_memory.schemas import MemorySearchHit
from app.features.markdown_memory.workspace import MarkdownMemoryWorkspace
from app.features.weread.indexing.embeddings import note_embedding_service
from app.infrastructure.chinese_fts_text import build_fts5_match_query, segment_text_for_fts
from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class MarkdownChunk:
    """One chunk extracted from a Markdown file."""

    chunk_id: str
    path: str
    chunk_index: int
    heading: str | None
    text: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_weights(text_weight: float, vector_weight: float) -> tuple[float, float]:
    text = max(0.0, text_weight)
    vector = max(0.0, vector_weight)
    total = text + vector
    if total <= 0:
        return 0.5, 0.5
    return text / total, vector / total


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, min(1.0, (dot / (left_norm * right_norm) + 1.0) / 2.0))


def _rank_to_text_score(rank: float) -> float:
    """Map SQLite bm25 rank, where lower is better, into a positive score."""
    return 1.0 / (1.0 + max(0.0, abs(rank)))


def _snippet(text: str, max_chars: int = 700) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 1].rstrip()}…"


def chunk_markdown(relative_path: str, content: str) -> list[MarkdownChunk]:
    """Split Markdown into heading-aware chunks."""
    max_chars = max(200, settings.MARKDOWN_MEMORY_CHUNK_CHARS)
    overlap = max(0, min(settings.MARKDOWN_MEMORY_CHUNK_OVERLAP, max_chars // 2))
    chunks: list[MarkdownChunk] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n".join(buffer).strip()
        buffer = []
        if not text:
            return
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            chunk_text = text[start:end].strip()
            if chunk_text:
                index = len(chunks)
                chunks.append(
                    MarkdownChunk(
                        chunk_id=f"{relative_path}:{index}",
                        path=relative_path,
                        chunk_index=index,
                        heading=current_heading,
                        text=chunk_text,
                    )
                )
            if end >= len(text):
                break
            start = max(0, end - overlap)

    for line in content.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush()
            current_heading = heading_match.group(2).strip()
            buffer.append(line)
            continue
        buffer.append(line)
        if sum(len(item) + 1 for item in buffer) >= max_chars:
            flush()
    flush()
    return chunks


class MarkdownMemoryIndexer:
    """Maintain a local SQLite hybrid index for Markdown memory files."""

    def __init__(
        self,
        workspace: MarkdownMemoryWorkspace,
        db_path: Path | None = None,
    ) -> None:
        self.workspace = workspace
        self.db_path = (db_path or settings.MARKDOWN_MEMORY_INDEX_DB_PATH).expanduser()
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    async def ensure_schema(self) -> None:
        """Create index tables once."""
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await asyncio.to_thread(self._ensure_schema_sync)
            self._schema_ready = True

    def _ensure_schema_sync(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS markdown_memory_chunks (
                    id TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading TEXT,
                    text TEXT NOT NULL,
                    text_fts TEXT NOT NULL,
                    file_mtime REAL NOT NULL,
                    file_size INTEGER NOT NULL,
                    embedding_json TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS markdown_memory_chunks_fts
                USING fts5(chunk_id UNINDEXED, text_fts)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_markdown_memory_user_path
                ON markdown_memory_chunks(user_key, path)
                """
            )
            connection.commit()

    async def rebuild_user(self, user_id: int | None) -> int:
        """Rebuild all Markdown files for one user."""
        await self.ensure_schema()
        user_key = self.workspace.user_dir(user_id).name
        files = self.workspace.list_files(user_id)
        indexed = 0
        with self._connect() as connection:
            old_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM markdown_memory_chunks WHERE user_key = ?",
                    (user_key,),
                ).fetchall()
            ]
            self._delete_chunks(connection, old_ids)
            connection.execute("DELETE FROM markdown_memory_chunks WHERE user_key = ?", (user_key,))
            connection.commit()

        for file_info in files:
            if file_info.path.startswith("backup/"):
                continue
            indexed += await self.index_file(user_id, file_info.path)
        return indexed

    async def sync_user(self, user_id: int | None) -> int:
        """Index changed files and remove deleted files for one user."""
        await self.ensure_schema()
        user_key = self.workspace.user_dir(user_id).name
        files = [info for info in self.workspace.list_files(user_id) if not info.path.startswith("backup/")]
        current_paths = {info.path for info in files}
        indexed = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, MAX(file_mtime) AS file_mtime, MAX(file_size) AS file_size FROM markdown_memory_chunks WHERE user_key = ? GROUP BY path",
                (user_key,),
            ).fetchall()
            indexed_paths = {str(row["path"]) for row in rows}
            deleted = indexed_paths - current_paths
            if deleted:
                placeholders = ",".join("?" for _ in deleted)
                old_ids = [
                    str(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM markdown_memory_chunks WHERE user_key = ? AND path IN ({placeholders})",
                        (user_key, *sorted(deleted)),
                    ).fetchall()
                ]
                self._delete_chunks(connection, old_ids)
                connection.execute(
                    f"DELETE FROM markdown_memory_chunks WHERE user_key = ? AND path IN ({placeholders})",
                    (user_key, *sorted(deleted)),
                )
                connection.commit()

        indexed_by_path = {str(row["path"]): row for row in rows}
        for file_info in files:
            row = indexed_by_path.get(file_info.path)
            path = self.workspace.resolve_path(user_id, file_info.path)
            stat = path.stat()
            if row is None or float(row["file_mtime"]) != stat.st_mtime or int(row["file_size"]) != stat.st_size:
                indexed += await self.index_file(user_id, file_info.path)
        return indexed

    async def index_file(self, user_id: int | None, relative_path: str) -> int:
        """Index one Markdown file."""
        await self.ensure_schema()
        path = self.workspace.resolve_path(user_id, relative_path)
        if not path.exists():
            return 0
        content = path.read_text(encoding="utf-8")
        stat = path.stat()
        chunks = chunk_markdown(relative_path, content)
        embeddings = await self._embed_chunks(chunks)
        user_key = self.workspace.user_dir(user_id).name
        await asyncio.to_thread(
            self._replace_file_chunks_sync,
            user_key,
            relative_path,
            chunks,
            embeddings,
            stat.st_mtime,
            stat.st_size,
        )
        logger.info("markdown_memory_file_indexed", user_id=user_id, path=relative_path, chunk_count=len(chunks))
        return len(chunks)

    async def _embed_chunks(self, chunks: list[MarkdownChunk]) -> list[list[float] | None]:
        if not chunks or not settings.MARKDOWN_MEMORY_VECTOR_ENABLED or not note_embedding_service.is_enabled():
            return [None for _ in chunks]
        texts = [chunk.text[:2000] for chunk in chunks]
        embeddings = await note_embedding_service.embed_texts(texts)
        if len(embeddings) != len(chunks):
            logger.warning("markdown_memory_embedding_count_mismatch", expected=len(chunks), actual=len(embeddings))
            return [None for _ in chunks]
        return embeddings

    def _replace_file_chunks_sync(
        self,
        user_key: str,
        relative_path: str,
        chunks: list[MarkdownChunk],
        embeddings: list[list[float] | None],
        file_mtime: float,
        file_size: int,
    ) -> None:
        with self._connect() as connection:
            old_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM markdown_memory_chunks WHERE user_key = ? AND path = ?",
                    (user_key, relative_path),
                ).fetchall()
            ]
            self._delete_chunks(connection, old_ids)
            connection.execute(
                "DELETE FROM markdown_memory_chunks WHERE user_key = ? AND path = ?",
                (user_key, relative_path),
            )
            now = _utc_now()
            for chunk, embedding in zip(chunks, embeddings, strict=False):
                text_fts = segment_text_for_fts(chunk.text)
                connection.execute(
                    """
                    INSERT INTO markdown_memory_chunks(
                        id, user_key, path, chunk_index, heading, text, text_fts,
                        file_mtime, file_size, embedding_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        user_key,
                        chunk.path,
                        chunk.chunk_index,
                        chunk.heading,
                        chunk.text,
                        text_fts,
                        file_mtime,
                        file_size,
                        json.dumps(embedding) if embedding is not None else None,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO markdown_memory_chunks_fts(chunk_id, text_fts) VALUES (?, ?)",
                    (chunk.chunk_id, text_fts),
                )
            connection.commit()

    @staticmethod
    def _delete_chunks(connection: sqlite3.Connection, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        placeholders = ",".join("?" for _ in chunk_ids)
        connection.execute(
            f"DELETE FROM markdown_memory_chunks_fts WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )

    async def search(
        self,
        user_id: int | None,
        query: str,
        *,
        max_results: int,
        min_score: float,
    ) -> list[MemorySearchHit]:
        """Hybrid FTS + vector search over indexed Markdown chunks."""
        await self.ensure_schema()
        await self.sync_user(user_id)
        query_embedding = await self._embed_query(query)
        return await asyncio.to_thread(
            self._search_sync,
            self.workspace.user_dir(user_id).name,
            query,
            query_embedding,
            max_results,
            min_score,
        )

    async def _embed_query(self, query: str) -> list[float] | None:
        if not settings.MARKDOWN_MEMORY_VECTOR_ENABLED or not note_embedding_service.is_enabled():
            return None
        embeddings = await note_embedding_service.embed_texts([query[:2000]])
        return embeddings[0] if embeddings else None

    def _search_sync(
        self,
        user_key: str,
        query: str,
        query_embedding: list[float] | None,
        max_results: int,
        min_score: float,
    ) -> list[MemorySearchHit]:
        text_weight, vector_weight = _normalize_weights(
            settings.MARKDOWN_MEMORY_TEXT_WEIGHT,
            settings.MARKDOWN_MEMORY_VECTOR_WEIGHT,
        )
        candidate_limit = max(max_results * 4, max_results)
        text_scores: dict[str, float] = {}
        rows_by_id: dict[str, sqlite3.Row] = {}
        with self._connect() as connection:
            if settings.MARKDOWN_MEMORY_FTS_ENABLED:
                fts_query = build_fts5_match_query(query)
                if fts_query:
                    try:
                        for row in connection.execute(
                            """
                            SELECT c.*, bm25(markdown_memory_chunks_fts) AS rank
                            FROM markdown_memory_chunks_fts
                            JOIN markdown_memory_chunks c ON c.id = markdown_memory_chunks_fts.chunk_id
                            WHERE markdown_memory_chunks_fts MATCH ? AND c.user_key = ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (fts_query, user_key, candidate_limit),
                        ).fetchall():
                            chunk_id = str(row["id"])
                            rows_by_id[chunk_id] = row
                            text_scores[chunk_id] = _rank_to_text_score(float(row["rank"]))
                    except sqlite3.OperationalError as exc:
                        logger.warning("markdown_memory_fts_search_failed", error=str(exc))

            vector_scores: dict[str, float] = {}
            if query_embedding is not None:
                for row in connection.execute(
                    """
                    SELECT * FROM markdown_memory_chunks
                    WHERE user_key = ? AND embedding_json IS NOT NULL
                    """,
                    (user_key,),
                ).fetchall():
                    try:
                        embedding = json.loads(str(row["embedding_json"]))
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(embedding, list):
                        continue
                    score = _cosine_similarity(query_embedding, [float(item) for item in embedding])
                    if score <= 0:
                        continue
                    chunk_id = str(row["id"])
                    rows_by_id.setdefault(chunk_id, row)
                    vector_scores[chunk_id] = score

            if not rows_by_id:
                for row in connection.execute(
                    """
                    SELECT * FROM markdown_memory_chunks
                    WHERE user_key = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (user_key, max_results),
                ).fetchall():
                    rows_by_id[str(row["id"])] = row

        hits: list[tuple[float, MemorySearchHit]] = []
        for chunk_id, row in rows_by_id.items():
            text_score = text_scores.get(chunk_id)
            vector_score = vector_scores.get(chunk_id)
            score = (text_weight * (text_score or 0.0)) + (vector_weight * (vector_score or 0.0))
            if score <= 0:
                score = 0.2
            if score < min_score:
                continue
            hits.append(
                (
                    score,
                    MemorySearchHit(
                        path=str(row["path"]),
                        chunk_id=chunk_id,
                        chunk_index=int(row["chunk_index"]),
                        heading=str(row["heading"]) if row["heading"] else None,
                        snippet=_snippet(str(row["text"])),
                        score=score,
                        text_score=text_score,
                        vector_score=vector_score,
                    ),
                )
            )

        hits.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in hits[:max_results]]
