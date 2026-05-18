"""Markdown memory management API."""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.v1.auth import get_current_session
from app.features.markdown_memory.manager import markdown_memory_manager
from app.features.markdown_memory.schemas import (
    AutoMemoryRequest,
    AutoMemoryResponse,
    DreamRequest,
    DreamResponse,
    MemoryFileContent,
    MemoryFileInfo,
    MemoryFileUpdate,
    MemorySearchRequest,
    MemorySearchResponse,
    RebuildIndexResponse,
)
from app.features.markdown_memory.workspace import MemoryPathError
from app.infrastructure.config import settings
from app.infrastructure.limiter import limiter
from app.infrastructure.logging import get_logger
from app.models.session import Session

logger = get_logger(__name__)

router = APIRouter()


def _memory_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory_file_not_found")
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="memory_file_version_conflict")
    if isinstance(exc, MemoryPathError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="memory_operation_failed")


@router.get("/files", response_model=list[MemoryFileInfo])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def list_memory_files(
    request: Request,
    session: Session = Depends(get_current_session),
) -> list[MemoryFileInfo]:
    """List Markdown memory files."""
    _ = request
    return await markdown_memory_manager.list_files(session.user_id)


@router.get("/files/{path:path}", response_model=MemoryFileContent)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def read_memory_file(
    path: str,
    request: Request,
    session: Session = Depends(get_current_session),
) -> MemoryFileContent:
    """Read one Markdown memory file."""
    _ = request
    try:
        return await markdown_memory_manager.read_file(session.user_id, path)
    except Exception as exc:
        logger.exception("memory_file_read_failed", path=path, user_id=session.user_id)
        raise _memory_error(exc) from exc


@router.put("/files/{path:path}", response_model=MemoryFileContent)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def write_memory_file(
    path: str,
    payload: MemoryFileUpdate,
    request: Request,
    session: Session = Depends(get_current_session),
) -> MemoryFileContent:
    """Save one Markdown memory file with optimistic concurrency."""
    _ = request
    try:
        return await markdown_memory_manager.write_file(
            session.user_id,
            path,
            payload.content,
            base_version=payload.base_version,
            force=payload.force,
        )
    except Exception as exc:
        logger.exception("memory_file_write_failed", path=path, user_id=session.user_id)
        raise _memory_error(exc) from exc


@router.post("/search", response_model=MemorySearchResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def search_memory(
    payload: MemorySearchRequest,
    request: Request,
    session: Session = Depends(get_current_session),
) -> MemorySearchResponse:
    """Hybrid search over Markdown memory."""
    _ = request
    results = await markdown_memory_manager.search(
        session.user_id,
        payload.query,
        max_results=payload.max_results,
        min_score=payload.min_score,
    )
    return MemorySearchResponse(query=payload.query, results=results)


@router.post("/auto-memory", response_model=AutoMemoryResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def auto_memory(
    payload: AutoMemoryRequest,
    request: Request,
    session: Session = Depends(get_current_session),
) -> AutoMemoryResponse:
    """Manually extract current conversation messages into today's daily memory."""
    _ = request
    return await markdown_memory_manager.auto_memory(
        session.user_id,
        messages=payload.messages,
        thread_id=payload.thread_id,
        session_id=payload.session_id,
    )


@router.post("/dream", response_model=DreamResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def dream_memory(
    payload: DreamRequest,
    request: Request,
    session: Session = Depends(get_current_session),
) -> DreamResponse:
    """Manually consolidate daily memory into MEMORY.md."""
    _ = request
    return await markdown_memory_manager.dream(session.user_id, lookback_days=payload.lookback_days)


@router.post("/rebuild-index", response_model=RebuildIndexResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def rebuild_memory_index(
    request: Request,
    session: Session = Depends(get_current_session),
) -> RebuildIndexResponse:
    """Rebuild the Markdown memory index after manual edits."""
    _ = request
    indexed_chunks = await markdown_memory_manager.rebuild_index(session.user_id)
    return RebuildIndexResponse(indexed_chunks=indexed_chunks)
