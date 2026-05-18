"""Filesystem workspace helpers for Markdown-backed memory."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.features.markdown_memory.schemas import MemoryFileContent, MemoryFileInfo
from app.infrastructure.config import settings

_DEFAULT_MEMORY_TEMPLATE = "# MEMORY\n"


def user_key(user_id: int | None) -> str:
    """Return stable filesystem key for a user."""
    return f"user_{user_id}" if user_id is not None else "anonymous"


class MemoryPathError(ValueError):
    """Raised when a requested memory path is unsafe or unsupported."""


class MarkdownMemoryWorkspace:
    """Resolve and mutate real Markdown files for one user's memory workspace."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.MARKDOWN_MEMORY_ROOT).expanduser()
        self.template_dir = Path(__file__).resolve().parent / "templates"

    def user_dir(self, user_id: int | None) -> Path:
        """Return the workspace directory for a user."""
        return self.root / user_key(user_id)

    def ensure_user_workspace(self, user_id: int | None) -> Path:
        """Create the canonical workspace layout and seed canonical Markdown files."""
        workspace = self.user_dir(user_id)
        (workspace / "memory").mkdir(parents=True, exist_ok=True)
        (workspace / "backup").mkdir(parents=True, exist_ok=True)
        (workspace / ".index").mkdir(parents=True, exist_ok=True)
        memory_file = workspace / "MEMORY.md"
        if not memory_file.exists():
            memory_file.write_text(self._load_template("memory.md"), encoding="utf-8")
        return workspace

    def _load_template(self, template_name: str) -> str:
        """Load a Markdown template from disk with a minimal fallback."""
        template_path = self.template_dir / template_name
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")
        return _DEFAULT_MEMORY_TEMPLATE

    def resolve_path(self, user_id: int | None, relative_path: str) -> Path:
        """Resolve a user-facing relative Markdown path safely."""
        normalized = relative_path.strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized.startswith("../") or "/../" in normalized or normalized == "..":
            raise MemoryPathError("invalid_memory_path")
        if normalized.startswith(".index/") or normalized == ".index":
            raise MemoryPathError("invalid_memory_path")
        if not normalized.endswith(".md"):
            raise MemoryPathError("memory_path_must_be_markdown")
        workspace = self.ensure_user_workspace(user_id)
        target = (workspace / normalized).resolve()
        if workspace.resolve() not in target.parents and target != workspace.resolve():
            raise MemoryPathError("memory_path_outside_workspace")
        return target

    @staticmethod
    def version_for_content(content: bytes) -> str:
        """Return a stable content hash."""
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _modified_at(path: Path) -> str:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()

    def list_files(self, user_id: int | None) -> list[MemoryFileInfo]:
        """List Markdown files in the user's memory workspace."""
        workspace = self.ensure_user_workspace(user_id)
        files: list[MemoryFileInfo] = []
        for path in sorted(workspace.rglob("*.md")):
            if ".index" in path.parts:
                continue
            content = path.read_bytes()
            files.append(
                MemoryFileInfo(
                    path=path.relative_to(workspace).as_posix(),
                    size=len(content),
                    version=self.version_for_content(content),
                    modified_at=self._modified_at(path),
                )
            )
        return files

    def read_file(self, user_id: int | None, relative_path: str) -> MemoryFileContent:
        """Read one Markdown memory file."""
        path = self.resolve_path(user_id, relative_path)
        if not path.exists():
            raise FileNotFoundError(relative_path)
        content_bytes = path.read_bytes()
        return MemoryFileContent(
            path=relative_path.strip().replace("\\", "/").lstrip("/"),
            content=content_bytes.decode("utf-8"),
            version=self.version_for_content(content_bytes),
            modified_at=self._modified_at(path),
        )

    def write_file(
        self,
        user_id: int | None,
        relative_path: str,
        content: str,
        *,
        base_version: str | None = None,
        force: bool = False,
    ) -> MemoryFileContent:
        """Save one Markdown memory file with optional version checking."""
        path = self.resolve_path(user_id, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and base_version and not force:
            current = self.version_for_content(path.read_bytes())
            if current != base_version:
                raise FileExistsError("memory_file_version_conflict")
        path.write_text(content, encoding="utf-8")
        return self.read_file(user_id, relative_path)

    def append_file(self, user_id: int | None, relative_path: str, content: str) -> MemoryFileContent:
        """Append Markdown content to a file, creating parent directories."""
        path = self.resolve_path(user_id, relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        path.write_text(f"{existing}{prefix}{content.strip()}\n", encoding="utf-8")
        return self.read_file(user_id, relative_path)
