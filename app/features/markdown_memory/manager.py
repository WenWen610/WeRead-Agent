"""Service layer for Markdown-backed long-term memory."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.features.markdown_memory.indexer import MarkdownMemoryIndexer
from app.features.markdown_memory.jobs import MarkdownMemoryJob, MarkdownMemoryJobStore
from app.features.markdown_memory.prompts import AUTO_MEMORY_PROMPT, DREAM_PROMPT
from app.features.markdown_memory.schemas import (
    AutoMemoryResponse,
    DreamResponse,
    MemoryFileContent,
    MemoryFileInfo,
    MemorySearchHit,
)
from app.features.markdown_memory.workspace import MarkdownMemoryWorkspace
from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

_SILENT_TOKEN = "[SILENT]"


@dataclass
class _MemoryTask:
    task_id: str
    task_type: str
    user_id: int | None
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class _MemoryTaskStatus:
    task_id: str
    status: str  # pending | running | completed | failed
    error: str | None = None


def _today_path() -> str:
    return f"memory/{datetime.now(timezone.utc).date().isoformat()}.md"


def _message_text(message: dict[str, str]) -> str:
    role = str(message.get("role") or "").strip() or "unknown"
    content = str(message.get("content") or "").strip()
    return f"{role}: {content}" if content else ""


def _extract_response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return str(content).strip()


def _messages_from_agent_result(result: Any) -> list[Any]:
    if isinstance(result, dict):
        messages = result.get("messages")
        return messages if isinstance(messages, list) else []
    messages = getattr(result, "messages", None)
    return messages if isinstance(messages, list) else []


def _auto_memory_job_had_durable_outcome(result: Any) -> bool:
    messages = _messages_from_agent_result(result)
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        content = _extract_response_text(message)
        if "Appended to " in content or "Skipped: content already exists" in content:
            return True

    final_message = messages[-1] if messages else result
    return _extract_response_text(final_message) == _SILENT_TOKEN


def _get_llm():
    """Import the shared LLM service lazily so memory search does not initialize LLM clients."""
    from app.infrastructure.llm import llm_service

    return llm_service.get_llm()


class MarkdownMemoryManager:
    """Coordinate Markdown files, indexing, search, and background memory passes."""

    def __init__(
        self,
        workspace: MarkdownMemoryWorkspace | None = None,
        indexer: MarkdownMemoryIndexer | None = None,
        job_store: MarkdownMemoryJobStore | None = None,
    ) -> None:
        """Initialize memory workspace, indexer, and background task state."""
        self.workspace = workspace or MarkdownMemoryWorkspace()
        self.indexer = indexer or MarkdownMemoryIndexer(self.workspace)
        self.job_store = job_store or MarkdownMemoryJobStore()
        self._locks: dict[str, asyncio.Lock] = {}
        self._task_queue: asyncio.Queue[_MemoryTask] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._task_statuses: dict[str, _MemoryTaskStatus] = {}
        self._task_counter: int = 0

    def _lock_for_user(self, user_id: int | None) -> asyncio.Lock:
        key = self.workspace.user_dir(user_id).name
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def start(self) -> None:
        """Initialize the index schema."""
        await self.indexer.ensure_schema()
        await asyncio.to_thread(self.job_store.ensure_schema)
        await self.recover_auto_memory_jobs()

    async def close(self) -> None:
        """Release manager resources."""
        await self.stop_task_worker(drain=True)

    # ------------------------------------------------------------------
    # Background task queue
    # ------------------------------------------------------------------

    async def start_task_worker(self) -> None:
        """Start the background task worker loop."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._task_worker_loop())
        logger.info("markdown_memory_task_worker_started")

    async def stop_task_worker(self, *, drain: bool = False) -> None:
        """Stop the background task worker.

        Args:
            drain: If True, wait for the current task to finish before canceling.
        """
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = None
            return
        if drain:
            await self._task_queue.join()
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None
        logger.info("markdown_memory_task_worker_stopped")

    async def enqueue_task(
        self,
        task_type: str,
        user_id: int | None,
        **kwargs: Any,
    ) -> str:
        """Enqueue a background memory task and return its task_id.

        Supported task types: auto_memory, dream.
        """
        self._task_counter += 1
        task_id = f"mem_task_{self._task_counter}"
        self._task_statuses[task_id] = _MemoryTaskStatus(task_id=task_id, status="pending")
        await self._task_queue.put(_MemoryTask(task_id=task_id, task_type=task_type, user_id=user_id, kwargs=kwargs))
        logger.info("markdown_memory_task_enqueued", task_id=task_id, task_type=task_type, user_id=user_id)
        return task_id

    async def enqueue_auto_memory_job(
        self,
        *,
        user_id: int,
        thread_id: str,
        day_path: str,
        start_message_id: str | None,
        end_message_id: str | None,
        start_index: int,
        end_index: int,
        message_hash: str,
        reason: str,
        messages: list[dict[str, str]],
    ) -> str:
        """Persist an auto-memory job and enqueue it for background execution."""
        job_id, should_enqueue = await asyncio.to_thread(
            self.job_store.enqueue_auto_memory_job,
            user_id=user_id,
            thread_id=thread_id,
            day_path=day_path,
            start_message_id=start_message_id,
            end_message_id=end_message_id,
            start_index=start_index,
            end_index=end_index,
            message_hash=message_hash,
            reason=reason,
            messages=messages,
        )
        if should_enqueue:
            await self._task_queue.put(
                _MemoryTask(
                    task_id=job_id,
                    task_type="auto_memory",
                    user_id=user_id,
                    kwargs={"job_id": job_id},
                )
            )
            logger.info(
                "markdown_memory_auto_memory_job_enqueued",
                task_id=job_id,
                user_id=user_id,
                thread_id=thread_id,
                reason=reason,
                message_count=len(messages),
            )
        else:
            logger.info(
                "markdown_memory_auto_memory_job_reused",
                task_id=job_id,
                user_id=user_id,
                thread_id=thread_id,
                reason=reason,
            )
        return job_id

    async def recover_auto_memory_jobs(self) -> None:
        """Requeue pending auto-memory jobs and stale interrupted running jobs."""
        job_ids = await asyncio.to_thread(self.job_store.reset_interrupted_jobs)
        for job_id in job_ids:
            job = await asyncio.to_thread(self.job_store.get_job, job_id)
            if job is None:
                continue
            await self._task_queue.put(
                _MemoryTask(
                    task_id=job_id,
                    task_type="auto_memory",
                    user_id=job.user_id,
                    kwargs={"job_id": job_id},
                )
            )
        if job_ids:
            logger.info("markdown_memory_auto_memory_jobs_recovered", job_count=len(job_ids))

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        """Return the status of a previously enqueued task."""
        status = self._task_statuses.get(task_id)
        if status is None:
            return None
        return {"task_id": status.task_id, "status": status.status, "error": status.error}

    async def _task_worker_loop(self) -> None:
        """Consume tasks from the queue serially."""
        while True:
            task = await self._task_queue.get()
            status = self._task_statuses.get(task.task_id)
            if status is not None:
                status.status = "running"
            try:
                await self._execute_task(task)
                if status is not None:
                    status.status = "completed"
            except asyncio.CancelledError:
                if status is not None:
                    status.status = "cancelled"
                raise
            except Exception as exc:
                logger.exception("markdown_memory_task_failed", task_id=task.task_id, task_type=task.task_type, error=str(exc))
                if status is not None:
                    status.status = "failed"
                    status.error = str(exc)
            finally:
                self._task_queue.task_done()

    async def _execute_task(self, task: _MemoryTask) -> None:
        """Route a task to the appropriate handler."""
        if task.task_type == "auto_memory":
            await self._run_auto_memory_subagent(task)
        elif task.task_type == "dream":
            await self._run_dream_subagent(task)
        else:
            logger.warning("markdown_memory_unknown_task_type", task_type=task.task_type)

    async def _run_auto_memory_subagent(self, task: _MemoryTask) -> None:
        """Run auto-memory using a dedicated subagent."""
        from app.runtimes.deep_agent.memory_subagents import MemoryAgentContext, make_auto_memory_agent

        job_id = task.kwargs.get("job_id")
        job: MarkdownMemoryJob | None = None
        if isinstance(job_id, str) and job_id:
            job = await asyncio.to_thread(self.job_store.claim_job, job_id)
            if job is None:
                logger.info("markdown_memory_auto_memory_job_claim_skipped", task_id=job_id)
                return
            messages = job.messages
            thread_id = job.thread_id
        else:
            messages = task.kwargs.get("messages", [])
            thread_id = task.kwargs.get("thread_id") or task.kwargs.get("session_id")
        transcript = "\n".join(item for item in (_message_text(message) for message in messages) if item)
        if not transcript:
            if job is not None:
                await asyncio.to_thread(self.job_store.complete_job, job)
            return
        if task.user_id is None:
            logger.warning("markdown_memory_auto_memory_skipped_missing_user", thread_id=thread_id)
            if job is not None:
                await asyncio.to_thread(self.job_store.fail_job, job.job_id, "missing_user_id")
            return

        agent = make_auto_memory_agent(task.user_id)
        try:
            source_lines = []
            if job is not None:
                source_lines = [
                    "## Sources",
                    f"- memory_job_id: {job.job_id}",
                    f"- thread_id: {job.thread_id}",
                    f"- message_range: {job.start_message_id or job.start_index}..{job.end_message_id or job.end_index}",
                    f"- message_hash: {job.message_hash}",
                    f"- reason: {job.reason}",
                ]
            source_block = "\n".join(source_lines)
            prompt_parts = [
                f"Conversation:\n{transcript}",
                "Return only Markdown to append to today's memory file.",
            ]
            if source_block:
                prompt_parts.append(
                    "If you append memory, include this exact source block at the end:\n"
                    f"{source_block}"
                )
            configurable: dict[str, Any] = {"user_id": task.user_id}
            if job is not None:
                configurable.update(
                    {
                        "memory_job_id": job.job_id,
                        "thread_id": job.thread_id,
                        "message_range": f"{job.start_message_id or job.start_index}..{job.end_message_id or job.end_index}",
                        "message_hash": job.message_hash,
                        "memory_reason": job.reason,
                    }
                )
            result = await agent.ainvoke(
                {
                    "messages": [
                        SystemMessage(content=AUTO_MEMORY_PROMPT),
                        HumanMessage(content="\n\n".join(prompt_parts)),
                    ],
                },
                config={"configurable": configurable},
                context=MemoryAgentContext(user_id=task.user_id),
            )
            if job is not None:
                if not _auto_memory_job_had_durable_outcome(result):
                    raise RuntimeError("auto_memory_agent_did_not_append_or_silent")
                await asyncio.to_thread(self.job_store.complete_job, job)
            logger.info(
                "markdown_memory_auto_memory_subagent_completed",
                user_id=task.user_id,
                thread_id=thread_id,
                task_id=job.job_id if job is not None else task.task_id,
            )
        except Exception as exc:
            if job is not None:
                await asyncio.to_thread(self.job_store.fail_job, job.job_id, str(exc))
            raise

    async def _run_dream_subagent(self, task: _MemoryTask) -> None:
        """Run dream consolidation using a dedicated subagent."""
        from app.runtimes.deep_agent.memory_subagents import MemoryAgentContext, make_dream_agent

        lookback_days = task.kwargs.get("lookback_days") or settings.MARKDOWN_MEMORY_DREAM_LOOKBACK_DAYS
        if task.user_id is None:
            logger.warning("markdown_memory_dream_skipped_missing_user")
            return
        memory = await self.read_file(task.user_id, "MEMORY.md")
        daily_notes = await self._read_recent_daily_notes(task.user_id, lookback_days)
        if not daily_notes:
            logger.info("markdown_memory_dream_skipped_no_daily_notes", user_id=task.user_id)
            return

        backup_path = await self._backup_memory(task.user_id)
        context = f"Current MEMORY.md:\n{memory.content}\n\nRecent daily memory notes:\n{daily_notes}"

        agent = make_dream_agent(task.user_id)
        await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=DREAM_PROMPT),
                    HumanMessage(content=context),
                ],
            },
            config={"configurable": {"user_id": task.user_id}},
            context=MemoryAgentContext(user_id=task.user_id),
        )
        logger.info(
            "markdown_memory_dream_subagent_completed",
            user_id=task.user_id,
            backup_path=backup_path,
        )

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def list_files(self, user_id: int | None) -> list[MemoryFileInfo]:
        """List user memory files."""
        return await asyncio.to_thread(self.workspace.list_files, user_id)

    async def read_file(self, user_id: int | None, path: str) -> MemoryFileContent:
        """Read a user memory file."""
        return await asyncio.to_thread(self.workspace.read_file, user_id, path)

    async def write_file(
        self,
        user_id: int | None,
        path: str,
        content: str,
        *,
        base_version: str | None = None,
        force: bool = False,
    ) -> MemoryFileContent:
        """Write a user memory file and refresh its index."""
        async with self._lock_for_user(user_id):
            result = await asyncio.to_thread(
                self.workspace.write_file,
                user_id,
                path,
                content,
                base_version=base_version,
                force=force,
            )
            await self.indexer.index_file(user_id, result.path)
        return result

    async def append_file(self, user_id: int | None, path: str, content: str) -> MemoryFileContent:
        """Append Markdown content to a memory file and update the index."""
        async with self._lock_for_user(user_id):
            result = await asyncio.to_thread(self.workspace.append_file, user_id, path, content)
            await self.indexer.index_file(user_id, result.path)
        return result

    async def rebuild_index(self, user_id: int | None) -> int:
        """Rebuild the user's memory index."""
        async with self._lock_for_user(user_id):
            return await self.indexer.rebuild_user(user_id)

    async def search(
        self,
        user_id: int | None,
        query: str,
        *,
        max_results: int | None = None,
        min_score: float | None = None,
    ) -> list[MemorySearchHit]:
        """Search Markdown memory with hybrid retrieval."""
        cleaned = query.strip()
        if not cleaned:
            return []
        return await self.indexer.search(
            user_id,
            cleaned,
            max_results=max_results or settings.MARKDOWN_MEMORY_SEARCH_MAX_RESULTS,
            min_score=min_score if min_score is not None else settings.MARKDOWN_MEMORY_SEARCH_MIN_SCORE,
        )

    # ------------------------------------------------------------------
    # Legacy sync methods (used for direct API calls)
    # ------------------------------------------------------------------

    async def auto_memory(
        self,
        user_id: int | None,
        *,
        messages: list[dict[str, str]],
        thread_id: str | None = None,
        session_id: str | None = None,
    ) -> AutoMemoryResponse:
        """Extract durable reading memory and append it to today's daily note.

        This method is used by the synchronous API endpoint.
        For background use, call enqueue_task("auto_memory", ...) instead.
        """
        transcript = "\n".join(item for item in (_message_text(message) for message in messages) if item)
        if not transcript:
            return AutoMemoryResponse(path=_today_path(), appended=False)

        source_lines = [
            "## Sources",
            f"- thread_id: {thread_id or session_id or 'manual'}",
            f"- created_at: {datetime.now(timezone.utc).isoformat()}",
        ]
        prompt = "\n\n".join(
            [
                "Conversation:",
                transcript,
                "Return only Markdown to append to today's memory file.",
            ]
        )
        response = await _get_llm().ainvoke(
            [
                SystemMessage(content=AUTO_MEMORY_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        content = _extract_response_text(response)
        if not content or content.strip() == _SILENT_TOKEN:
            return AutoMemoryResponse(path=_today_path(), appended=False)

        append_content = f"{content.strip()}\n\n" + "\n".join(source_lines)
        path = _today_path()
        async with self._lock_for_user(user_id):
            result = await asyncio.to_thread(self.workspace.append_file, user_id, path, append_content)
            await self.indexer.index_file(user_id, result.path)
        logger.info("markdown_memory_auto_memory_appended", user_id=user_id, path=path, thread_id=thread_id or session_id)
        return AutoMemoryResponse(path=path, appended=True, content=append_content)

    async def dream(self, user_id: int | None, *, lookback_days: int | None = None) -> DreamResponse:
        """Consolidate daily notes into MEMORY.md with a backup.

        This method is used by the synchronous API endpoint.
        For background use, call enqueue_task("dream", ...) instead.
        """
        lookback = lookback_days or settings.MARKDOWN_MEMORY_DREAM_LOOKBACK_DAYS
        memory = await self.read_file(user_id, "MEMORY.md")
        daily_notes = await self._read_recent_daily_notes(user_id, lookback)
        if not daily_notes:
            return DreamResponse(updated=False, report="no_recent_daily_notes")

        backup_path = await self._backup_memory(user_id)

        from app.runtimes.deep_agent.memory_subagents import MemoryAgentContext, make_dream_agent

        context = f"Current MEMORY.md:\n{memory.content}\n\nRecent daily memory notes:\n{daily_notes}"
        agent = make_dream_agent(user_id)
        invoke_kwargs: dict[str, Any] = {}
        if user_id is not None:
            invoke_kwargs = {
                "config": {"configurable": {"user_id": user_id}},
                "context": MemoryAgentContext(user_id=user_id),
            }
        await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=DREAM_PROMPT),
                    HumanMessage(content=context),
                ],
            },
            **invoke_kwargs,
        )

        logger.info("markdown_memory_dream_completed", user_id=user_id, backup_path=backup_path)
        return DreamResponse(updated=True, backup_path=backup_path, report="memory_consolidated")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _backup_memory(self, user_id: int | None) -> str | None:
        workspace = self.workspace.ensure_user_workspace(user_id)
        source = workspace / "MEMORY.md"
        if not source.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        relative = f"backup/MEMORY_{stamp}.md"
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, target)
        return relative

    async def _read_recent_daily_notes(self, user_id: int | None, lookback_days: int) -> str:
        workspace = self.workspace.ensure_user_workspace(user_id)
        memory_dir = workspace / "memory"
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
        parts: list[str] = []
        for path in sorted(memory_dir.glob("*.md")):
            match = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", path.name)
            if not match:
                continue
            try:
                day = datetime.fromisoformat(match.group(1)).date()
            except ValueError:
                continue
            if day < cutoff:
                continue
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            parts.append(f"## {path.name}\n\n{content}")
        return "\n\n".join(parts)

    @staticmethod
    def format_search_results(results: list[MemorySearchHit]) -> str:
        """Format search hits for a tool-result message."""
        lines: list[str] = []
        for index, result in enumerate(results, start=1):
            heading = f" ({result.heading})" if result.heading else ""
            lines.append(
                f"{index}. Source: {result.path}{heading}\n"
                f"Score: {result.score:.3f}\n"
                f"{result.snippet}"
            )
        return "\n\n".join(lines)


markdown_memory_manager = MarkdownMemoryManager()
