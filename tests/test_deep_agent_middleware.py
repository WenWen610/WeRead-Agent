"""Tests for Deep Agent middleware assembly."""

from app.runtimes.deep_agent.middleware.markdown_memory_middleware import MarkdownMemoryMiddleware
from app.runtimes.deep_agent.middleware import build_deep_agent_middlewares
from app.runtimes.deep_agent.middleware.capability_middleware import CapabilityMiddleware
from app.runtimes.deep_agent.middleware.clarification_middleware import ClarificationMiddleware
from app.runtimes.deep_agent.middleware.weread_state_middleware import WeReadStateMiddleware


class TestBuildDeepAgentMiddlewares:
    def test_returns_correct_middleware_count(self):
        middlewares = build_deep_agent_middlewares()
        assert len(middlewares) == 6

    def test_order_is_preserved(self):
        middlewares = build_deep_agent_middlewares()
        types = [type(m).__name__ for m in middlewares]
        assert types == [
            "ToolExclusionMiddleware",
            "MarkdownMemoryMiddleware",
            "WeReadStateMiddleware",
            "CapabilityMiddleware",
            "ClarificationMiddleware",
            "ArtifactPresentationMiddleware",
        ]
