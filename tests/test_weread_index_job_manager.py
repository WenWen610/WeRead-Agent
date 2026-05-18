import asyncio
import importlib.util
import sys
from pathlib import Path


def _load_job_manager_class():
    module_name = "app.features.weread.indexing.index_job_manager"
    module_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "features"
        / "weread"
        / "indexing"
        / "index_job_manager.py"
    )
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.WeReadIndexJobManager


async def _run_schedule_dedup_scenario() -> None:
    WeReadIndexJobManager = _load_job_manager_class()
    manager = WeReadIndexJobManager()
    captured = {"calls": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_operation() -> str:
        captured["calls"] += 1
        started.set()
        await release.wait()
        return "ok"

    first_future = await manager.schedule_index(
        doc_id="weread:123:book-1:marks",
        operation_factory=fake_operation,
        debounce_ms=0,
    )
    second_future = await manager.schedule_index(
        doc_id="weread:123:book-1:marks",
        operation_factory=fake_operation,
        debounce_ms=0,
    )

    assert first_future is second_future

    await started.wait()
    assert await manager.get_running_task("weread:123:book-1:marks") is not None

    release.set()
    assert await first_future == "ok"
    assert captured["calls"] == 1
    assert await manager.get_running_task("weread:123:book-1:marks") is None
    assert await manager.get_inflight_task("weread:123:book-1:marks") is None


async def _run_debounce_scenario() -> None:
    WeReadIndexJobManager = _load_job_manager_class()
    manager = WeReadIndexJobManager()
    captured = {"calls": 0}

    async def fake_operation() -> str:
        captured["calls"] += 1
        return "ok"

    first_future = await manager.schedule_index(
        doc_id="weread:123:book-1:marks",
        operation_factory=fake_operation,
        debounce_ms=30,
    )
    second_future = await manager.schedule_index(
        doc_id="weread:123:book-1:marks",
        operation_factory=fake_operation,
        debounce_ms=30,
    )

    assert first_future is second_future
    assert await manager.is_pending("weread:123:book-1:marks") is True
    assert await manager.get_running_task("weread:123:book-1:marks") is None

    completed = await manager.wait_for_doc(doc_id="weread:123:book-1:marks", timeout_ms=5)
    assert completed is False

    assert await first_future == "ok"
    assert captured["calls"] == 1
    assert await manager.is_pending("weread:123:book-1:marks") is False


async def _run_wait_for_doc_scenario() -> None:
    WeReadIndexJobManager = _load_job_manager_class()
    manager = WeReadIndexJobManager()
    release = asyncio.Event()

    async def fake_operation() -> str:
        await release.wait()
        return "ok"

    future = await manager.schedule_index(
        doc_id="weread:123:book-1:marks",
        operation_factory=fake_operation,
        debounce_ms=0,
    )

    completed = await manager.wait_for_doc(doc_id="weread:123:book-1:marks", timeout_ms=10)
    assert completed is False

    release.set()
    assert await manager.await_inflight_task("weread:123:book-1:marks") == "ok"
    assert await future == "ok"


def test_schedule_index_deduplicates_running_jobs() -> None:
    asyncio.run(_run_schedule_dedup_scenario())


def test_schedule_index_debounces_pending_jobs() -> None:
    asyncio.run(_run_debounce_scenario())


def test_wait_for_doc_times_out_without_spawning_new_job() -> None:
    asyncio.run(_run_wait_for_doc_scenario())
