"""Deep Agent assembly helpers."""

import importlib
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.infrastructure.config import settings
from app.runtimes.deep_agent.backends import build_deep_agent_backend
from app.runtimes.deep_agent.middleware import build_deep_agent_middlewares
from app.runtimes.deep_agent.prompts import MAIN_DEEP_AGENT_SYSTEM_PROMPT
from app.runtimes.deep_agent.skills import main_agent_skills
from app.runtimes.deep_agent.subagents import build_deep_agent_subagents
from app.runtimes.deep_agent.tools import build_deep_agent_tools

try:
    from deepagents import create_deep_agent
except ImportError:  # pragma: no cover - dependency is optional until installed.
    create_deep_agent = None


def _get_llm_service():
    """Return the shared LLM service without importing it during module import."""
    module = importlib.import_module("app.infrastructure.llm")
    return module.llm_service


def get_deep_agent_key(
    config: RunnableConfig,
    *,
    web_search_enabled: bool = False,
) -> tuple[str, str, bool]:
    """Return cache key for the Deep Agent runtime shape."""
    _ = config
    return (settings.DEFAULT_LLM_MODEL, "deep_agent_v1", web_search_enabled)


def make_deep_agent(
    config: RunnableConfig,
    *,
    checkpointer: Any = None,
    store: Any = None,
    context_schema: type | None = None,
    web_search_enabled: bool = False,
):
    """Create a Deep Agent reading-assistant runtime."""
    _ = config
    if create_deep_agent is None:
        raise RuntimeError(
            "deepagents is not installed. Run `uv sync` after updating dependencies before using Deep Agent routes."
        )

    agent_kwargs: dict[str, Any] = {
        "model": _get_llm_service().get_llm(),
        "tools": build_deep_agent_tools(web_search_enabled=web_search_enabled),
        "system_prompt": MAIN_DEEP_AGENT_SYSTEM_PROMPT,
        "backend": build_deep_agent_backend(),
        "middleware": build_deep_agent_middlewares(),
        "skills": main_agent_skills(web_search_enabled=web_search_enabled),
        "subagents": build_deep_agent_subagents(),
    }
    if checkpointer is not None:
        agent_kwargs["checkpointer"] = checkpointer
    if store is not None:
        agent_kwargs["store"] = store
    if context_schema is not None:
        agent_kwargs["context_schema"] = context_schema

    try:
        from deepagents import FilesystemPermission
    except ImportError:
        FilesystemPermission = None

    if FilesystemPermission is not None:
        agent_kwargs["permissions"] = [
            FilesystemPermission(
                operations=["write"],
                paths=["/saved/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["write"],
                paths=["/**"],
                mode="deny",
            ),
        ]

    return create_deep_agent(**agent_kwargs)
