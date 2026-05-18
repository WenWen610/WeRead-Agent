"""Background WeRead index job manager with doc_id-level debounce and in-flight deduplication."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar, cast

from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class _WeReadIndexJob:
    future: asyncio.Future[object]
    operation_factory: Callable[[], Awaitable[object]]
    pending_task: asyncio.Task[None] | None = None
    running_task: asyncio.Task[object] | None = None


class WeReadIndexJobManager:
    """Manage background WeRead index jobs keyed by doc_id."""

    def __init__(self) -> None:
        self._jobs: dict[str, _WeReadIndexJob] = {}
        self._lock = asyncio.Lock()

    async def schedule_index(
        self,
        *,
        doc_id: str,
        operation_factory: Callable[[], Awaitable[T]],
        debounce_ms: int = 0,
    ) -> asyncio.Future[T]:
        async with self._lock:
            existing_job = self._get_active_job_without_lock(doc_id)
            if existing_job is not None:
                existing_job.operation_factory = cast(Callable[[], Awaitable[object]], operation_factory)
                if existing_job.pending_task is not None and not existing_job.pending_task.done():
                    if debounce_ms <= 0:
                        existing_job.pending_task.cancel()
                        existing_job.pending_task = None
                        existing_job.running_task = asyncio.create_task(
                            self._run_index_job(doc_id=doc_id),
                            name=f"weread_index:{doc_id}",
                        )
                        logger.info("weread_index_job_pending_promoted", doc_id=doc_id)
                    else:
                        existing_job.pending_task.cancel()
                        existing_job.pending_task = asyncio.create_task(
                            self._wait_and_start(doc_id=doc_id, debounce_ms=debounce_ms),
                            name=f"weread_index_pending:{doc_id}",
                        )
                        logger.info(
                            "weread_index_job_debounce_refreshed",
                            doc_id=doc_id,
                            debounce_ms=debounce_ms,
                        )
                else:
                    logger.info("weread_index_job_reused", doc_id=doc_id)
                return cast(asyncio.Future[T], existing_job.future)

            loop = asyncio.get_running_loop()
            job = _WeReadIndexJob(
                future=loop.create_future(),
                operation_factory=cast(Callable[[], Awaitable[object]], operation_factory),
            )
            self._jobs[doc_id] = job
            if debounce_ms > 0:
                job.pending_task = asyncio.create_task(
                    self._wait_and_start(doc_id=doc_id, debounce_ms=debounce_ms),
                    name=f"weread_index_pending:{doc_id}",
                )
                logger.info(
                    "weread_index_job_pending",
                    doc_id=doc_id,
                    debounce_ms=debounce_ms,
                )
            else:
                job.running_task = asyncio.create_task(
                    self._run_index_job(doc_id=doc_id),
                    name=f"weread_index:{doc_id}",
                )
                logger.info("weread_index_job_scheduled", doc_id=doc_id)
            return cast(asyncio.Future[T], job.future)

    async def get_inflight_task(self, doc_id: str) -> asyncio.Future[object] | None:
        async with self._lock:
            job = self._get_active_job_without_lock(doc_id)
            if job is None:
                return None
            return job.future

    async def get_running_task(self, doc_id: str) -> asyncio.Task[object] | None:
        async with self._lock:
            job = self._get_active_job_without_lock(doc_id)
            if job is None:
                return None
            running_task = job.running_task
            if running_task is None or running_task.done():
                return None
            return running_task

    async def is_pending(self, doc_id: str) -> bool:
        async with self._lock:
            job = self._get_active_job_without_lock(doc_id)
            if job is None:
                return False
            pending_task = job.pending_task
            return pending_task is not None and not pending_task.done()

    async def wait_for_doc(self, *, doc_id: str, timeout_ms: int) -> bool:
        future = await self.get_inflight_task(doc_id)
        if future is None:
            return True
        if timeout_ms <= 0:
            return future.done()

        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout_ms / 1000)
            return True
        except asyncio.TimeoutError:
            return False

    async def await_inflight_task(self, doc_id: str) -> object | None:
        future = await self.get_inflight_task(doc_id)
        if future is None:
            return None
        return await asyncio.shield(future)

    async def await_running_task(self, doc_id: str) -> object | None:
        return await self.await_inflight_task(doc_id)

    async def reset(self) -> None:
        async with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()

        for job in jobs:
            if job.pending_task is not None and not job.pending_task.done():
                job.pending_task.cancel()
            if job.running_task is not None and not job.running_task.done():
                job.running_task.cancel()
            if job.pending_task is not None:
                try:
                    await job.pending_task
                except asyncio.CancelledError:
                    pass
            if job.running_task is not None:
                try:
                    await job.running_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("weread_index_job_reset_failed")

    def _get_active_job_without_lock(self, doc_id: str) -> _WeReadIndexJob | None:
        job = self._jobs.get(doc_id)
        if job is None:
            return None

        pending_active = job.pending_task is not None and not job.pending_task.done()
        running_active = job.running_task is not None and not job.running_task.done()
        if pending_active or running_active:
            return job
        if not job.future.done():
            return job

        self._jobs.pop(doc_id, None)
        return None

    async def _wait_and_start(self, *, doc_id: str, debounce_ms: int) -> None:
        try:
            await asyncio.sleep(debounce_ms / 1000)
        except asyncio.CancelledError:
            logger.info("weread_index_job_pending_cancelled", doc_id=doc_id)
            return

        async with self._lock:
            job = self._jobs.get(doc_id)
            current_task = asyncio.current_task()
            if job is None or job.pending_task is not current_task or job.future.done():
                return
            job.pending_task = None
            job.running_task = asyncio.create_task(
                self._run_index_job(doc_id=doc_id),
                name=f"weread_index:{doc_id}",
            )
            logger.info("weread_index_job_started_from_pending", doc_id=doc_id)

    async def _run_index_job(self, *, doc_id: str) -> object:
        async with self._lock:
            job = self._jobs.get(doc_id)
            if job is None:
                raise RuntimeError("weread_index_job_missing")
            operation_factory = job.operation_factory

        logger.info("weread_index_job_started", doc_id=doc_id)
        try:
            result = await operation_factory()
            logger.info("weread_index_job_completed", doc_id=doc_id)
        except asyncio.CancelledError:
            logger.info("weread_index_job_cancelled", doc_id=doc_id)
            async with self._lock:
                job = self._jobs.get(doc_id)
                if job is not None and not job.future.done():
                    job.future.cancel()
            raise
        except Exception as exc:
            logger.exception(
                "weread_index_job_failed",
                doc_id=doc_id,
                error=str(exc),
            )
            async with self._lock:
                job = self._jobs.get(doc_id)
                if job is not None and not job.future.done():
                    job.future.set_exception(exc)
            raise
        else:
            async with self._lock:
                job = self._jobs.get(doc_id)
                if job is not None and not job.future.done():
                    job.future.set_result(result)
            return result
        finally:
            async with self._lock:
                job = self._jobs.get(doc_id)
                current_task = asyncio.current_task()
                if job is not None:
                    if job.running_task is current_task:
                        job.running_task = None
                    if job.future.done() and (job.pending_task is None or job.pending_task.done()):
                        self._jobs.pop(doc_id, None)


weread_index_job_manager = WeReadIndexJobManager()
