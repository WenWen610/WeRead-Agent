"""Tests for durable Markdown memory job storage."""

from app.features.markdown_memory.jobs import MarkdownMemoryJobStore


def test_auto_memory_job_complete_advances_thread_cursor(tmp_path):
    store = MarkdownMemoryJobStore(tmp_path / "memory_tasks.db")
    store.ensure_schema()

    job_id, created = store.enqueue_auto_memory_job(
        user_id=7,
        thread_id="thread-a",
        day_path="memory/2026-05-17.md",
        start_message_id=None,
        end_message_id="msg-2",
        start_index=0,
        end_index=2,
        message_hash="hash-a",
        reason="interval",
        messages=[
            {"role": "user", "content": "读了第一章"},
            {"role": "assistant", "content": "好的"},
        ],
    )

    assert created is True
    claimed = store.claim_job(job_id)
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 1

    store.complete_job(claimed)
    cursor = store.get_cursor(7, "thread-a")

    assert cursor is not None
    assert cursor.completed_message_id == "msg-2"
    assert cursor.completed_index == 2


def test_running_jobs_are_recovered_as_pending(tmp_path):
    store = MarkdownMemoryJobStore(tmp_path / "memory_tasks.db")
    store.ensure_schema()
    job_id, _ = store.enqueue_auto_memory_job(
        user_id=7,
        thread_id="thread-a",
        day_path="memory/2026-05-17.md",
        start_message_id=None,
        end_message_id="msg-2",
        start_index=0,
        end_index=2,
        message_hash="hash-a",
        reason="interval",
        messages=[{"role": "user", "content": "读了第一章"}],
    )
    assert store.claim_job(job_id) is not None

    recovered = store.reset_interrupted_jobs(older_than_seconds=0)
    claimed_again = store.claim_job(job_id)

    assert job_id in recovered
    assert claimed_again is not None
    assert claimed_again.attempts == 2
