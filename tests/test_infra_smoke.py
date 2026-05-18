"""Smoke tests for Phase 1 infrastructure — verify fixtures and mocks load correctly.

These tests validate that the shared test infrastructure (conftest fixtures,
WeRead transport mock, agent factory) is wired up properly before we write
real integration tests in Phase 2.
"""

from __future__ import annotations

import pytest

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from tests.factories.agent import build_test_config, make_test_agent_client
from tests.mocks.weread_transport import WEREAD_TRANSPORT, _resolve_fixture


class TestSharedFixtures:
    """Verify that conftest.py fixtures are discoverable and return correct types."""

    def test_tmp_sqlite_checkpointer_fixture(self, tmp_sqlite_checkpointer):
        """Fixture must be an initialized AsyncSqliteSaver."""
        assert isinstance(tmp_sqlite_checkpointer, AsyncSqliteSaver)

    def test_tmp_sqlite_store_fixture(self, tmp_sqlite_store):
        """Fixture must be an initialized AsyncSqliteStore."""
        assert isinstance(tmp_sqlite_store, AsyncSqliteStore)

    def test_tmp_state_backend_fixture(self, tmp_state_backend):
        """Fixture must be a writable directory."""
        assert tmp_state_backend.is_dir()
        test_file = tmp_state_backend / "test.txt"
        test_file.write_text("hello")
        assert test_file.read_text() == "hello"

    def test_mock_weread_fixture_registers_patch(self, mock_weread):
        """After mock_weread is active, WeReadApi should use mock cookie."""
        from app.features.weread.api_client import WeReadApi

        api = WeReadApi()
        import asyncio

        async def check():
            await api._ensure_initialized()
            assert api.cookie == "mock-cookie"

        asyncio.run(check())

    def test_mock_weread_shelf_request(self, mock_weread):
        """Mock WeReadApi.get_entire_shelf() should return fixture data."""
        from app.features.weread.api_client import WeReadApi

        api = WeReadApi()
        import asyncio

        async def check():
            result = await api.get_entire_shelf()
            assert isinstance(result, dict)
            assert result.get("bookCount") >= 2

        asyncio.run(check())


class TestWeReadTransport:
    """Verify the HTTP transport mock matches URLs to correct fixtures."""

    def test_resolve_shelf_sync(self):
        fixture = _resolve_fixture("/web/shelf/sync", "GET", {})
        assert fixture is not None
        assert fixture["bookCount"] >= 2

    def test_resolve_notebook(self):
        fixture = _resolve_fixture("/api/user/notebook", "GET", {})
        assert fixture is not None
        assert len(fixture["books"]) >= 1

    def test_resolve_book_info(self):
        fixture = _resolve_fixture("/api/book/info", "GET", {"bookId": "WR19384756"})
        assert fixture is not None
        assert fixture["title"] == "三体"

    def test_resolve_book_info_unknown(self):
        fixture = _resolve_fixture("/api/book/info", "GET", {"bookId": "NONEXIST"})
        assert fixture is None

    def test_resolve_bookmark_list(self):
        fixture = _resolve_fixture("/web/book/bookmarklist", "GET", {"bookId": "WR19384756"})
        assert fixture is not None
        assert len(fixture["updated"]) == 3

    def test_resolve_read_info(self):
        fixture = _resolve_fixture("/web/book/getProgress", "GET", {"bookId": "WR19384756"})
        assert fixture is not None
        assert fixture["progress"] == 45

    def test_resolve_review_list(self):
        fixture = _resolve_fixture("/web/review/list", "GET", {"bookId": "WR19384756"})
        assert fixture is not None
        assert len(fixture["reviews"]) == 2

    def test_resolve_chapter_info(self):
        fixture = _resolve_fixture("/web/book/chapterInfos", "POST", {"bookId": "WR19384756"})
        assert fixture is not None
        assert "data" in fixture

    def test_resolve_homepage(self):
        fixture = _resolve_fixture("/", "GET", {})
        assert fixture is not None
        assert fixture == {}

    def test_resolve_unknown_path(self):
        fixture = _resolve_fixture("/nonexistent/path", "GET", {})
        assert fixture is None


class TestAgentFactory:
    """Verify the agent factory produces correctly configured clients."""

    def test_make_test_agent_client_no_deps(self):
        client = make_test_agent_client()
        assert client is not None

    def test_make_test_agent_client_with_deps(self, tmp_sqlite_checkpointer, tmp_sqlite_store):
        client = make_test_agent_client(
            checkpointer=tmp_sqlite_checkpointer,
            store=tmp_sqlite_store,
        )
        assert client._checkpointer is tmp_sqlite_checkpointer
        assert client._store is tmp_sqlite_store

    def test_build_test_config_defaults(self):
        config = build_test_config()
        assert config["configurable"]["thread_id"] == "test-thread"
        assert config["metadata"]["user_id"] == 42
        assert "recursion_limit" in config

    def test_build_test_config_custom(self):
        config = build_test_config(thread_id="my-thread", user_id=7)
        assert config["configurable"]["thread_id"] == "my-thread"
        assert config["metadata"]["user_id"] == 7


class TestFixtureData:
    """Verify fixture JSON files exist and are valid."""

    def test_all_fixtures_loadable(self):
        """Every .json fixture must be parseable."""
        from tests.mocks.weread_transport import FIXTURE_DIR

        json_files = list(FIXTURE_DIR.glob("*.json"))
        assert len(json_files) > 0, "No fixture JSON files found"

        for path in json_files:
            raw = path.read_text(encoding="utf-8")
            import json

            data = json.loads(raw)
            assert isinstance(data, (dict, list)), f"Fixture {path.name} is not a dict or list"
