"""Business-state middleware for the Deep Agent runtime."""

from typing import Any

from app.runtimes.deep_agent.middleware.capability_middleware import CapabilityMiddleware
from app.runtimes.deep_agent.middleware.clarification_middleware import ClarificationMiddleware
from app.runtimes.deep_agent.middleware.weread_state_middleware import WeReadStateMiddleware
from app.runtimes.deep_agent.middleware.markdown_memory_middleware import MarkdownMemoryMiddleware
from app.runtimes.deep_agent.middleware.artifact_presentation_middleware import ArtifactPresentationMiddleware
from app.runtimes.deep_agent.middleware.tool_exclusion_middleware import ToolExclusionMiddleware


def build_deep_agent_middlewares() -> list[Any]:
    """Return only business-state middleware that does not duplicate Deep Agent context management."""
    return [
        ToolExclusionMiddleware(),
        MarkdownMemoryMiddleware(),
        WeReadStateMiddleware(),
        CapabilityMiddleware(),
        ClarificationMiddleware(),
        ArtifactPresentationMiddleware(),
    ]
