"""Saved Content API — browse and edit locally saved markdown analyses."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from app.api.v1.auth import get_current_session
from app.infrastructure.config import settings
from app.infrastructure.limiter import limiter
from app.infrastructure.logging import get_logger
from app.models.session import Session

logger = get_logger(__name__)

router = APIRouter()

_CONTENT_TYPE = "text/markdown; charset=utf-8"


def _user_dir(user_id: int) -> Path:
    return settings.SAVED_CONTENT_DIR / str(user_id)


def _resolve_path(user_id: int, path: str) -> Path:
    base = _user_dir(user_id).resolve()
    target = (base / path).resolve()
    if str(base) != str(target) and not str(target).startswith(str(base) + os.sep):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="path_traversal_disallowed")
    return target


def _safe_filename(path_obj: Path) -> str:
    return str(path_obj.relative_to(_user_dir(0).parent.parent / "0")).replace("\\", "/")


@router.get("")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def list_saved_files(
    request: Request,
    session: Session = Depends(get_current_session),
) -> list[dict]:
    """List all saved markdown files grouped by directory."""
    _ = request
    entries: list[dict] = []

    def _scan_dir(base_dir: Path, prefix: str = "") -> None:
        if not base_dir.exists():
            return
        for file_path in sorted(base_dir.rglob("*.md")):
            if not file_path.is_file():
                continue
            rel = prefix + str(file_path.relative_to(base_dir))
            if rel.startswith("/"):
                rel = rel[1:]
            size = file_path.stat().st_size
            book_id = ""
            parts = rel.split("/", 1)
            if len(parts) >= 2 and parts[0]:
                book_id = parts[0]
            entries.append({
                "path": rel.replace("\\", "/"),
                "size": size,
                "modified_at": file_path.stat().st_mtime,
                "book_id": book_id,
            })

    user_dir = _user_dir(session.user_id)
    _scan_dir(user_dir)

    root_dir = settings.SAVED_CONTENT_DIR.resolve()
    for entry in root_dir.iterdir() if root_dir.exists() else []:
        if entry.is_file() and entry.suffix == ".md":
            rel = entry.name
            size = entry.stat().st_size
            entries.append({
                "path": rel,
                "size": size,
                "modified_at": entry.stat().st_mtime,
                "book_id": "",
            })

    return entries


@router.get("/{path:path}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def read_saved_file(
    path: str,
    request: Request,
    session: Session = Depends(get_current_session),
) -> PlainTextResponse:
    """Read a saved markdown file."""
    _ = request
    try:
        resolved = _resolve_path(session.user_id, path)
        if not resolved.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file_not_found")
        return PlainTextResponse(resolved.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("saved_content_read_failed", path=path, user_id=session.user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="read_failed") from exc


@router.put("/{path:path}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def write_saved_file(
    path: str,
    request: Request,
    session: Session = Depends(get_current_session),
) -> dict:
    """Write or overwrite a saved markdown file."""
    _ = request
    try:
        content_bytes = await request.body()
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_utf8_content") from None

    try:
        resolved = _resolve_path(session.user_id, path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info("saved_content_written", path=path, user_id=session.user_id)
        return {
            "status": "saved",
            "path": path,
            "size": len(content.encode("utf-8")),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("saved_content_write_failed", path=path, user_id=session.user_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="write_failed") from exc
