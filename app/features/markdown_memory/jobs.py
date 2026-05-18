"""Durable job and cursor storage for Markdown memory extraction."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from app.features.markdown_memory.workspace import user_key
from app.infrastructure.config import settings

AutoMemoryJobStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class MarkdownMemoryCursor:
    """Confirmed auto-memory boundary for one thread."""

    user_key: str
    user_id: int | None
    thread_id: str
    completed_message_id: str | None
    completed_index: int
    completed_at: str | None


@dataclass(frozen=True)
class MarkdownMemoryJob:
    """One durable auto-memory extraction job."""

    job_id: str
    user_key: str
    user_id: int | None
    thread_id: str
    day_path: str
    start_message_id: str | None
    end_message_id: str | None
    start_index: int
    end_index: int
    message_hash: str
    reason: str
    status: AutoMemoryJobStatus
    attempts: int
    error: str | None
    messages: list[dict[str, str]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: sqlite3.Row) -> MarkdownMemoryJob:
    raw_messages = row["messages_json"]
    try:
        messages = json.loads(raw_messages) if isinstance(raw_messages, str) else []
    except json.JSONDecodeError:
        messages = []
    return MarkdownMemoryJob(
        job_id=str(row["id"]),
        user_key=str(row["user_key"]),
        user_id=row["user_id"],
        thread_id=str(row["thread_id"]),
        day_path=str(row["day_path"]),
        start_message_id=row["start_message_id"],
        end_message_id=row["end_message_id"],
        start_index=int(row["start_index"]),
        end_index=int(row["end_index"]),
        message_hash=str(row["message_hash"]),
        reason=str(row["reason"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        error=row["error"],
        messages=[item for item in messages if isinstance(item, dict)],
    )


class MarkdownMemoryJobStore:
    """SQLite-backed state for auto-memory jobs and per-thread cursors."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the store with a SQLite database path."""
        self.db_path = (db_path or settings.MARKDOWN_MEMORY_TASK_DB_PATH).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def ensure_schema(self) -> None:
        """Create durable memory job tables."""
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS markdown_memory_cursors (
                    user_key TEXT NOT NULL,
                    user_id INTEGER,
                    thread_id TEXT NOT NULL,
                    completed_message_id TEXT,
                    completed_index INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_key, thread_id)
                );

                CREATE TABLE IF NOT EXISTS markdown_memory_jobs (
                    id TEXT PRIMARY KEY,
                    user_key TEXT NOT NULL,
                    user_id INTEGER,
                    thread_id TEXT NOT NULL,
                    day_path TEXT NOT NULL,
                    start_message_id TEXT,
                    end_message_id TEXT,
                    start_index INTEGER NOT NULL,
                    end_index INTEGER NOT NULL,
                    message_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    messages_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_markdown_memory_jobs_status
                ON markdown_memory_jobs(status, updated_at);

                CREATE INDEX IF NOT EXISTS idx_markdown_memory_jobs_thread
                ON markdown_memory_jobs(user_key, thread_id, status);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_markdown_memory_jobs_window
                ON markdown_memory_jobs(user_key, thread_id, start_index, end_index, message_hash);
                """
            )

    def get_cursor(self, user_id: int | None, thread_id: str) -> MarkdownMemoryCursor | None:
        """Return the confirmed cursor for one thread."""
        key = user_key(user_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT user_key, user_id, thread_id, completed_message_id, completed_index, completed_at
                FROM markdown_memory_cursors
                WHERE user_key = ? AND thread_id = ?
                """,
                (key, thread_id),
            ).fetchone()
        if row is None:
            return None
        return MarkdownMemoryCursor(
            user_key=str(row["user_key"]),
            user_id=row["user_id"],
            thread_id=str(row["thread_id"]),
            completed_message_id=row["completed_message_id"],
            completed_index=int(row["completed_index"]),
            completed_at=row["completed_at"],
        )

    def enqueue_auto_memory_job(
        self,
        *,
        user_id: int | None,
        thread_id: str,
        day_path: str,
        start_message_id: str | None,
        end_message_id: str | None,
        start_index: int,
        end_index: int,
        message_hash: str,
        reason: str,
        messages: list[dict[str, str]],
    ) -> tuple[str, bool]:
        """Insert or refresh an auto-memory job for one thread window."""
        key = user_key(user_id)
        now = _utc_now()
        encoded_messages = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT *
                FROM markdown_memory_jobs
                WHERE user_key = ?
                  AND thread_id = ?
                  AND status IN ('pending', 'running')
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (key, thread_id),
            ).fetchone()
            if active is not None:
                job = _row_to_job(active)
                if job.status == "pending" and end_index > job.end_index:
                    connection.execute(
                        """
                        UPDATE markdown_memory_jobs
                        SET day_path = ?,
                            end_message_id = ?,
                            end_index = ?,
                            message_hash = ?,
                            reason = ?,
                            messages_json = ?,
                            updated_at = ?,
                            error = NULL
                        WHERE id = ?
                        """,
                        (
                            day_path,
                            end_message_id,
                            end_index,
                            message_hash,
                            reason,
                            encoded_messages,
                            now,
                            job.job_id,
                        ),
                    )
                return job.job_id, False

            existing = connection.execute(
                """
                SELECT *
                FROM markdown_memory_jobs
                WHERE user_key = ?
                  AND thread_id = ?
                  AND start_index = ?
                  AND end_index = ?
                  AND message_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (key, thread_id, start_index, end_index, message_hash),
            ).fetchone()
            if existing is not None:
                job = _row_to_job(existing)
                if job.status == "failed" and job.attempts < 3:
                    connection.execute(
                        """
                        UPDATE markdown_memory_jobs
                        SET status = 'pending',
                            error = NULL,
                            reason = ?,
                            messages_json = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (reason, encoded_messages, now, job.job_id),
                    )
                    return job.job_id, True
                return job.job_id, False

            job_id = f"mem_job_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO markdown_memory_jobs(
                    id,
                    user_key,
                    user_id,
                    thread_id,
                    day_path,
                    start_message_id,
                    end_message_id,
                    start_index,
                    end_index,
                    message_hash,
                    reason,
                    status,
                    attempts,
                    error,
                    messages_json,
                    created_at,
                    updated_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, NULL, ?, ?, ?, NULL)
                """,
                (
                    job_id,
                    key,
                    user_id,
                    thread_id,
                    day_path,
                    start_message_id,
                    end_message_id,
                    start_index,
                    end_index,
                    message_hash,
                    reason,
                    encoded_messages,
                    now,
                    now,
                ),
            )
            return job_id, True

    def reset_interrupted_jobs(self, *, older_than_seconds: int = 0) -> list[str]:
        """Move stale running jobs back to pending and return recoverable job ids."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE markdown_memory_jobs
                SET status = 'pending',
                    updated_at = ?,
                    error = NULL
                WHERE status = 'running'
                  AND updated_at <= ?
                """,
                (now, cutoff),
            )
            rows = connection.execute(
                """
                SELECT id
                FROM markdown_memory_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def claim_job(self, job_id: str) -> MarkdownMemoryJob | None:
        """Claim one pending job for execution."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM markdown_memory_jobs WHERE id = ? AND status = 'pending'",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE markdown_memory_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    updated_at = ?,
                    error = NULL
                WHERE id = ?
                """,
                (now, job_id),
            )
            claimed = connection.execute(
                "SELECT * FROM markdown_memory_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _row_to_job(claimed) if claimed is not None else None

    def complete_job(self, job: MarkdownMemoryJob) -> None:
        """Mark a job complete and advance the thread cursor."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE markdown_memory_jobs
                SET status = 'completed',
                    error = NULL,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (now, now, job.job_id),
            )
            connection.execute(
                """
                INSERT INTO markdown_memory_cursors(
                    user_key,
                    user_id,
                    thread_id,
                    completed_message_id,
                    completed_index,
                    completed_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_key, thread_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    completed_message_id = excluded.completed_message_id,
                    completed_index = excluded.completed_index,
                    completed_at = excluded.completed_at,
                    updated_at = excluded.updated_at
                WHERE excluded.completed_index >= markdown_memory_cursors.completed_index
                """,
                (
                    job.user_key,
                    job.user_id,
                    job.thread_id,
                    job.end_message_id,
                    job.end_index,
                    now,
                    now,
                ),
            )

    def fail_job(self, job_id: str, error: str) -> None:
        """Mark a job failed without advancing the cursor."""
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE markdown_memory_jobs
                SET status = 'failed',
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error[:2000], now, job_id),
            )

    def get_job(self, job_id: str) -> MarkdownMemoryJob | None:
        """Return a job by id."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM markdown_memory_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row is not None else None
