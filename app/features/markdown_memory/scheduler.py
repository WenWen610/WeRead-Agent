"""Cron scheduler for background memory tasks (dream consolidation)."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.features.markdown_memory.manager import markdown_memory_manager
from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)


class MemoryScheduler:
    """Schedule periodic background memory operations.

    Currently supports:
    - Dream consolidation (cron-based, consolidates daily notes → MEMORY.md)
    """

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None

    async def start(self) -> None:
        """Register cron jobs and start the scheduler."""
        cron_expr = settings.MARKDOWN_MEMORY_DREAM_CRON
        if not cron_expr:
            logger.info("memory_dream_cron_disabled")
            return

        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._trigger_dream_for_all,
            CronTrigger.from_crontab(cron_expr),
            id="memory_dream",
            name="Memory dream consolidation",
        )
        self._scheduler.start()
        logger.info("memory_scheduler_started", dream_cron=cron_expr)

    async def stop(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("memory_scheduler_stopped")

    async def _trigger_dream_for_all(self) -> None:
        """Enqueue dream tasks for all active memory workspaces."""
        logger.info("memory_dream_cron_triggered")
        try:
            users_dir = markdown_memory_manager.workspace.root
            if not users_dir.exists():
                return
            count = 0
            for entry in sorted(users_dir.iterdir()):
                if not entry.is_dir() or not entry.name.startswith("user_"):
                    continue
                try:
                    user_id = int(entry.name.removeprefix("user_"))
                except (ValueError, TypeError):
                    logger.warning("memory_dream_skip_invalid_workspace", name=entry.name)
                    continue
                await markdown_memory_manager.enqueue_task("dream", user_id=user_id)
                count += 1
            if count:
                logger.info("memory_dream_tasks_enqueued", user_count=count)
        except Exception:
            logger.exception("memory_dream_cron_failed")


# Global scheduler instance
memory_scheduler = MemoryScheduler()
