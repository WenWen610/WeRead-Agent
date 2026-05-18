"""Shared pytest fixtures for the Deep Agent test suite.

Provides common infrastructure dependencies (checkpointer, store, backend)
and WeRead HTTP mocking so that integration tests can run without real
credentials or network access.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

# Make `app` importable from the project root.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ── NO_PROXY IPv6 workaround ─────────────────────────────────────────────────
# httpx (used by the OpenAI SDK inside langchain-openai) parses every entry
# in NO_PROXY as a URL pattern.  IPv6 addresses like [::1] have brackets that
# confuse the URL parser.  Strip them before tests run.


@pytest.fixture(scope="session", autouse=True)
def _strip_ipv6_from_no_proxy():
    """Remove IPv6 addresses from NO_PROXY so httpx URL parsing doesn't crash.

    Autouse=True means this runs automatically before every test session.
    """
    for key in ("NO_PROXY", "no_proxy"):
        value = os.environ.get(key, "")
        if value:
            # Keep only entries that do NOT look like an IPv6 address.
            fixed = ",".join(v for v in value.split(",") if not v.strip().startswith("["))
            os.environ[key] = fixed


# ── pytest-asyncio configuration ────────────────────────────────────────────

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


# ── LangGraph infrastructure fixtures ────────────────────────────────────────


@pytest_asyncio.fixture
async def tmp_sqlite_checkpointer():
    """Provide a fresh in-memory AsyncSqliteSaver for LangGraph checkpointing.

    Each test gets its own connection so checkpoints never leak between tests.
    """
    conn = await aiosqlite.connect(":memory:")
    checkpointer = AsyncSqliteSaver(conn)
    await checkpointer.setup()
    try:
        yield checkpointer
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def tmp_sqlite_store():
    """Provide a fresh in-memory AsyncSqliteStore for LangGraph long-term memory.

    Each test gets its own connection so store data never leaks between tests.
    """
    conn = await aiosqlite.connect(":memory:")
    store = AsyncSqliteStore(conn)
    await store.setup()
    try:
        yield store
    finally:
        await conn.close()


@pytest.fixture
def tmp_state_backend(tmp_path: Path):
    """Provide a temporary filesystem directory for the agent's StateBackend.

    The backend reads/writes skills and artifacts to this directory.
    """
    backend_dir = tmp_path / "state_backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    return backend_dir


# ── WeRead HTTP mock fixture ─────────────────────────────────────────────────


@pytest.fixture
def mock_weread(monkeypatch):
    """Replace WeReadApi HTTP client with a MockTransport that returns fixture data.

    All requests to weread.qq.com are intercepted and served from
    tests/fixtures/weread/*.json files based on URL path matching.

    Usage in a test:
        def test_bookshelf(mock_weread):
            # WeReadApi calls now return fixture data automatically.
            api = WeReadApi(cookie="fake-cookie")
            result = await api.get_entire_shelf()
            # result comes from shelf_sync.json
    """
    from tests.mocks.weread_transport import make_weread_mocked_client

    import app.features.weread.api_client as weread_api_module

    _original_ensure_initialized = weread_api_module.WeReadApi._ensure_initialized

    async def _patched_ensure_initialized(self):
        if self.initialized:
            return
        self.cookie = "mock-cookie"
        self.client = make_weread_mocked_client()
        self.initialized = True

    monkeypatch.setattr(weread_api_module.WeReadApi, "_ensure_initialized", _patched_ensure_initialized)
    yield
    monkeypatch.setattr(weread_api_module.WeReadApi, "_ensure_initialized", _original_ensure_initialized)


@pytest.fixture
def weread_bound(monkeypatch):
    """Mock WeReadAuthService to return a fake valid cookie AND a valid binding.

    Without this fixture, WeRead tools return ``binding_required`` errors
    because the test database has no WeRead binding record.  Use this
    together with ``mock_weread`` to make WeRead tools return fixture data.

    Tests that deliberately verify the *unbound* path should NOT request
    this fixture.
    """
    from app.features.weread.services.auth import WeReadAuthService
    from app.models.weread_binding import WeReadBinding, WeReadBindingSource, WeReadBindingStatus

    _original_cookie = WeReadAuthService.require_active_cookie
    _original_get_binding = WeReadAuthService.get_binding
    _original_mark_expired = WeReadAuthService.mark_expired
    _original_mark_valid = WeReadAuthService.mark_cookie_valid

    async def _fake_require_active_cookie(self, user_id: int) -> str:
        _ = (self, user_id)
        return "fake-cookie"

    async def _fake_get_binding(self, user_id: int) -> WeReadBinding | None:
        _ = (self, user_id)
        return WeReadBinding(
            user_id=user_id,
            encrypted_cookie="fake-encrypted",
            source=WeReadBindingSource.QR,
            status=WeReadBindingStatus.ACTIVE,
        )

    async def _noop_mark_expired(self, user_id: int, reason: str = "") -> None:
        pass

    async def _noop_mark_valid(self, user_id: int) -> None:
        pass

    monkeypatch.setattr(WeReadAuthService, "require_active_cookie", _fake_require_active_cookie)
    monkeypatch.setattr(WeReadAuthService, "get_binding", _fake_get_binding)
    monkeypatch.setattr(WeReadAuthService, "mark_expired", _noop_mark_expired)
    monkeypatch.setattr(WeReadAuthService, "mark_cookie_valid", _noop_mark_valid)

    # Mock get_chapter_info — creates its own httpx.AsyncClient, bypasses mock
    import app.features.weread.api_client as weread_api

    _original_chapter = weread_api.WeReadApi.get_chapter_info

    async def _fake_chapter_info(self, book_id: str) -> dict:
        _ = (self, book_id)
        return {}

    monkeypatch.setattr(weread_api.WeReadApi, "get_chapter_info", _fake_chapter_info)

    yield
    monkeypatch.setattr(WeReadAuthService, "require_active_cookie", _original_cookie)
    monkeypatch.setattr(WeReadAuthService, "get_binding", _original_get_binding)
    monkeypatch.setattr(WeReadAuthService, "mark_expired", _original_mark_expired)
    monkeypatch.setattr(WeReadAuthService, "mark_cookie_valid", _original_mark_valid)
    monkeypatch.setattr(weread_api.WeReadApi, "get_chapter_info", _original_chapter)
