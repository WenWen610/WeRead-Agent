"""Restricted local text-file reader for skills and local knowledge."""

from __future__ import annotations

from pathlib import Path

from langchain.tools import tool

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)


def _resolve_allowed_roots() -> tuple[Path, ...]:
    return (
        Path(settings.SKILLS_DIR).resolve(),
        Path(settings.LOCAL_KNOWLEDGE_DIR).resolve(),
    )


def _is_allowed_path(path: Path) -> bool:
    for root in _resolve_allowed_roots():
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@tool("read_local_text_file", parse_docstring=True)
def read_local_text_file_tool(
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read a local UTF-8 text file from the skills or local knowledge directories.

    Use this when you need to inspect a skill file, a local markdown file, or another approved text artifact.
    Do not use this for normal WeRead note retrieval when a dedicated query/export tool is available.

    Args:
        description: Explain briefly why you are reading this file.
        path: The absolute path to the local text file.
        start_line: Optional starting line number (1-indexed, inclusive).
        end_line: Optional ending line number (1-indexed, inclusive).
    """
    _ = description
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            return "Error: path must be absolute."

        resolved_path = candidate.resolve(strict=True)
        if not _is_allowed_path(resolved_path):
            return "Error: path is outside the allowed local roots."
        if resolved_path.is_dir():
            return f"Error: path is a directory, not a file: {resolved_path}"

        content = resolved_path.read_text(encoding="utf-8")
        if start_line is not None and end_line is not None:
            if start_line < 1 or end_line < start_line:
                return "Error: invalid line range."
            content = "\n".join(content.splitlines()[start_line - 1 : end_line])

        logger.info(
            "local_text_file_read",
            path=str(resolved_path),
            start_line=start_line,
            end_line=end_line,
        )
        return content or "(empty)"
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except UnicodeDecodeError:
        return f"Error: file is not valid UTF-8 text: {path}"
    except OSError as exc:
        return f"Error: unable to read file: {type(exc).__name__}: {exc}"
