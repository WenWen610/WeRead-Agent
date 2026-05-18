"""Pydantic schemas for Markdown-backed memory."""

from pydantic import BaseModel, Field


class MemoryFileInfo(BaseModel):
    """Metadata for one Markdown memory file."""

    path: str
    size: int
    version: str
    modified_at: str


class MemoryFileContent(BaseModel):
    """Markdown file content with optimistic-concurrency version."""

    path: str
    content: str
    version: str
    modified_at: str


class MemoryFileUpdate(BaseModel):
    """Request body for saving a Markdown memory file."""

    content: str
    base_version: str | None = None
    force: bool = False


class MemorySearchRequest(BaseModel):
    """Search request for Markdown memory."""

    query: str
    max_results: int = Field(default=6, ge=1, le=20)
    min_score: float = Field(default=0.25, ge=0.0, le=1.0)


class MemorySearchHit(BaseModel):
    """One hybrid Markdown memory search result."""

    path: str
    chunk_id: str
    chunk_index: int
    heading: str | None = None
    snippet: str
    score: float
    text_score: float | None = None
    vector_score: float | None = None


class MemorySearchResponse(BaseModel):
    """Search response for Markdown memory."""

    query: str
    results: list[MemorySearchHit]


class AutoMemoryRequest(BaseModel):
    """Manual auto-memory request."""

    thread_id: str | None = None
    session_id: str | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)


class AutoMemoryResponse(BaseModel):
    """Auto-memory result."""

    path: str
    appended: bool
    content: str = ""


class DreamRequest(BaseModel):
    """Manual dream/consolidation request."""

    lookback_days: int | None = Field(default=None, ge=1, le=90)


class DreamResponse(BaseModel):
    """Dream/consolidation result."""

    updated: bool
    backup_path: str | None = None
    report: str = ""


class RebuildIndexResponse(BaseModel):
    """Index rebuild result."""

    indexed_chunks: int
