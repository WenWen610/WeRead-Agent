"""Filesystem backend configuration for Deep Agents."""

from app.infrastructure.config import settings

try:
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
except ImportError:  # pragma: no cover - dependency is optional until installed.
    CompositeBackend = None
    FilesystemBackend = None
    StateBackend = None


def build_deep_agent_backend():
    """Build the Deep Agent CompositeBackend with saved-content routing.

    Returns CompositeBackend that routes / to StateBackend (ephemeral) and
    /saved/ to FilesystemBackend on the local disk (persistent).
    """
    if StateBackend is None:
        raise RuntimeError(
            "deepagents is not installed. Install project dependencies with `uv sync` before using Deep Agent routes."
        )

    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/saved/": FilesystemBackend(
                root_dir=str(settings.SAVED_CONTENT_DIR.resolve()),
                virtual_mode=True,
            ),
        },
    )
