"""Runtime context passed to Deep Agent tools."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeepAgentContext:
    """Business context available to Deep Agent tools."""

    user_id: int | None
    thread_id: str

    @property
    def session_id(self) -> str:
        """Backward-compatible alias for code that still expects session_id."""
        return self.thread_id
