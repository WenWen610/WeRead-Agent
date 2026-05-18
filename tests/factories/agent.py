"""Deep Agent test factory — builds a DeepAgentClient with test dependencies.

All DeepAgentClient creation in tests should go through this module
to ensure consistent, isolated infrastructure.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from app.infrastructure.config import settings
from app.runtimes.deep_agent.client import DeepAgentClient


def make_test_agent_client(
    *,
    checkpointer: AsyncSqliteSaver | None = None,
    store: AsyncSqliteStore | None = None,
) -> DeepAgentClient:
    """Create a DeepAgentClient pre-configured for testing.

    Args:
        checkpointer: An optional pre-built AsyncSqliteSaver. If not provided,
                      the client will lazily create its own using the default
                      config path — which is fine for unit tests that don't
                      touch LangGraph checkpoints.
        store: An optional pre-built AsyncSqliteStore. Same semantics as
               checkpointer.

    Returns:
        A DeepAgentClient ready for .chat() or .stream() calls.
        Callers must use `mock_weread` fixture to intercept WeRead HTTP.

    Example:
        def test_my_agent(tmp_sqlite_checkpointer, tmp_sqlite_store, mock_weread):
            client = make_test_agent_client(
                checkpointer=tmp_sqlite_checkpointer,
                store=tmp_sqlite_store,
            )
            result = await client.chat("你好", thread_id="test-1")
            assert result.type == "answer"
    """
    client = DeepAgentClient()
    if checkpointer is not None:
        client._checkpointer = checkpointer
    if store is not None:
        client._store = store
    return client


def build_test_config(
    thread_id: str = "test-thread",
    user_id: int | None = 42,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a RunnableConfig-compatible dict for testing.

    This mirrors DeepAgentClient._build_config() but without coupling
    to settings or session lookups.

    Args:
        thread_id: The LangGraph thread identifier.
        user_id: Mock user id for tool context.
        **overrides: Additional config keys to merge.

    Returns:
        A dict suitable for passing as `config` to agent.ainvoke() / agent.astream().
    """
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "metadata": {
            "session_id": thread_id,
            "user_id": user_id,
            "runtime": "deep_agent",
            "environment": settings.ENVIRONMENT.value,
        },
        "recursion_limit": settings.DEEP_AGENT_RECURSION_LIMIT,
    }
    config.update(overrides)
    return config
