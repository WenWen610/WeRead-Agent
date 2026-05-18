"""Workspace-scoped LangChain tools for memory subagents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools.base import InjectedToolCallId
from pydantic import Field

from app.features.markdown_memory.manager import markdown_memory_manager
from app.features.markdown_memory.workspace import MemoryPathError
from app.infrastructure.logging import get_logger

logger = get_logger(__name__)

_PATH_DESCRIPTION = "Relative path within the memory workspace (e.g. MEMORY.md or memory/2025-01-01.md)."


def _resolve_user_id(runtime: ToolRuntime | None) -> int | None:
    if runtime is None:
        return None
    configurable = runtime.config.get("configurable", {}) if runtime.config else {}
    user_id = configurable.get("user_id")
    if isinstance(user_id, int):
        return user_id
    if runtime.context is not None:
        context = runtime.context
        user_id = context.get("user_id") if isinstance(context, dict) else getattr(context, "user_id", None)
        if isinstance(user_id, int):
            return user_id
    return None


def _resolve_configurable(runtime: ToolRuntime | None) -> dict[str, Any]:
    if runtime is None or not runtime.config:
        return {}
    configurable = runtime.config.get("configurable", {})
    return configurable if isinstance(configurable, dict) else {}


def _source_block_from_runtime(runtime: ToolRuntime | None) -> str:
    configurable = _resolve_configurable(runtime)
    memory_job_id = configurable.get("memory_job_id")
    thread_id = configurable.get("thread_id")
    message_range = configurable.get("message_range")
    message_hash = configurable.get("message_hash")
    reason = configurable.get("memory_reason")
    lines: list[str] = []
    if isinstance(memory_job_id, str) and memory_job_id:
        lines.append(f"- memory_job_id: {memory_job_id}")
    if isinstance(thread_id, str) and thread_id:
        lines.append(f"- thread_id: {thread_id}")
    if isinstance(message_range, str) and message_range:
        lines.append(f"- message_range: {message_range}")
    if isinstance(message_hash, str) and message_hash:
        lines.append(f"- message_hash: {message_hash}")
    if isinstance(reason, str) and reason:
        lines.append(f"- reason: {reason}")
    if not lines:
        return ""
    return "## Sources\n" + "\n".join(lines)


@tool("read_memory_note")
async def read_memory_note(
    path: Annotated[str, Field(description=_PATH_DESCRIPTION)],
    *,
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Read a file from the user's memory workspace.

    Use this to read MEMORY.md (long-term curated memory) or a daily note
    under memory/ (e.g. memory/2025-01-01.md).
    """
    _ = tool_call_id
    user_id = _resolve_user_id(runtime)
    if user_id is None:
        return "Error: user context not available."
    try:
        content = await markdown_memory_manager.read_file(user_id, path)
        return content.content
    except FileNotFoundError:
        return f"Error: file '{path}' not found in memory workspace."
    except MemoryPathError:
        return f"Error: '{path}' is a directory. Pass a specific .md file path such as 'memory/2025-01-01.md'."
    except Exception as exc:
        logger.exception("read_memory_note_failed", path=path, error=str(exc))
        return f"Error reading file: {exc}"


@tool("write_memory_note")
async def write_memory_note(
    path: Annotated[str, Field(description=_PATH_DESCRIPTION)],
    content: Annotated[str, Field(description="Full Markdown content to write.")],
    *,
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Write or overwrite a file in the user's memory workspace.

    Use this to update MEMORY.md during dream consolidation.
    The full file content must be provided; partial updates are not supported.
    """
    _ = tool_call_id
    user_id = _resolve_user_id(runtime)
    if user_id is None:
        return "Error: user context not available."
    try:
        await markdown_memory_manager.write_file(user_id, path, content, force=True)
        return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}."
    except MemoryPathError:
        return f"Error: '{path}' is a directory. Pass a specific .md file path such as 'MEMORY.md'."
    except Exception as exc:
        logger.exception("write_memory_note_failed", path=path, error=str(exc))
        return f"Error writing file: {exc}"


@tool("append_memory_daily_note")
async def append_memory_daily_note(
    content: Annotated[str, Field(description="Markdown content to append to today's daily note.")],
    *,
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Append content to today's daily note (memory/YYYY-MM-DD.md).

    Use this during auto-memory to record durable facts from the current
    conversation. Duplicate content will not be appended a second time.
    """
    _ = tool_call_id
    user_id = _resolve_user_id(runtime)
    if user_id is None:
        return "Error: user context not available."
    source_block = _source_block_from_runtime(runtime)
    normalized_content = content.strip()
    if source_block and source_block not in normalized_content:
        normalized_content = f"{normalized_content}\n\n{source_block}"
    try:
        path = f"memory/{datetime.now(timezone.utc).date().isoformat()}.md"
        existing = await markdown_memory_manager.read_file(user_id, path)
        if normalized_content in existing.content:
            return "Skipped: content already exists in today's note."
    except FileNotFoundError:
        pass
    except MemoryPathError:
        pass
    except Exception:
        pass

    try:
        result = await markdown_memory_manager.append_file(user_id, path, normalized_content)
        return f"Appended to {result.path} ({len(normalized_content.encode('utf-8'))} bytes)."
    except Exception as exc:
        logger.exception("append_memory_daily_note_failed", error=str(exc))
        return f"Error appending to daily note: {exc}"


@tool("search_user_memory")
async def search_user_memory(
    query: Annotated[str, Field(description="Search query to find relevant memory snippets.", min_length=1)],
    max_results: Annotated[int, Field(description="Maximum number of results.", ge=1, le=20)] = 6,
    *,
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Search the user's memory workspace (MEMORY.md + daily notes).

    Returns relevant snippets with file paths and scores.
    Use this to recall prior decisions, preferences, or reading context.
    """
    _ = tool_call_id
    user_id = _resolve_user_id(runtime)
    if user_id is None:
        return "Error: user context not available."
    try:
        results = await markdown_memory_manager.search(user_id, query, max_results=max_results)
        if not results:
            return "No relevant memory found."
        return markdown_memory_manager.format_search_results(results)
    except Exception as exc:
        logger.exception("search_user_memory_failed", query=query, error=str(exc))
        return f"Error searching memory: {exc}"
