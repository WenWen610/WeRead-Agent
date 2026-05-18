"""WeRead binding endpoints."""

import asyncio

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.v1.auth import get_current_session
from app.infrastructure.config import settings
from app.infrastructure.limiter import limiter
from app.infrastructure.logging import get_logger
from app.models.session import Session
from app.models.weread_binding import WeReadBinding
from app.models.weread_binding import WeReadBindingStatus
from app.models.weread_login_session import WeReadLoginSession
from app.schemas.weread import (
    WeReadBindCookieRequest,
    WeReadBindingMutationResponse,
    WeReadBindingResponse,
    WeReadRecentBooksResponse,
    WeReadQrLoginSessionResponse,
)
from app.features.weread.services.browser_login import (
    WeReadQrLoginUnavailableError,
    weread_browser_service,
)
from app.features.weread.services.auth import weread_auth_service
from app.features.weread.services.weread import WeReadDomainError, weread_service
from app.features.weread.stores.document_store import weread_local_document_store

logger = get_logger(__name__)
router = APIRouter()


def _to_binding_response(binding: WeReadBinding | None) -> WeReadBindingResponse:
    """Convert a binding model into a safe API payload."""
    if binding is None:
        return WeReadBindingResponse(connected=False)

    return WeReadBindingResponse(
        connected=True,
        status=binding.status,
        source=binding.source,
        last_error=binding.last_error,
        last_validated_at=binding.last_validated_at,
        updated_at=binding.updated_at,
    )


def _to_qr_login_session_response(login_session: WeReadLoginSession) -> WeReadQrLoginSessionResponse:
    """Convert an internal QR login session into a safe API payload."""
    return WeReadQrLoginSessionResponse(
        session_id=login_session.id,
        status=login_session.status,
        qr_image_base64=login_session.qr_image_base64,
        last_error=login_session.last_error,
        expires_at=login_session.expires_at,
        created_at=login_session.created_at,
        updated_at=login_session.updated_at,
        completed_at=login_session.completed_at,
    )


def _to_recent_books_response(
    *,
    connected: bool,
    refreshed: bool,
    snapshot,
) -> WeReadRecentBooksResponse:
    if snapshot is None:
        return WeReadRecentBooksResponse(connected=connected, refreshed=refreshed, books=[])

    return WeReadRecentBooksResponse(
        connected=connected,
        refreshed=refreshed,
        sync_key=snapshot.sync_key,
        generated_at=snapshot.generated_at,
        books=snapshot.books,
    )


@router.get("/binding", response_model=WeReadBindingResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_weread_binding(
    request: Request,
    session: Session = Depends(get_current_session),
) -> WeReadBindingResponse:
    """Return the authenticated user's current WeRead binding status."""
    try:
        binding = await weread_service.ensure_binding_with_auto_validation(session.user_id)
        logger.info("weread_binding_status_retrieved", user_id=session.user_id, connected=binding is not None)
        return _to_binding_response(binding)
    except Exception as exc:
        logger.exception("weread_binding_status_failed", user_id=session.user_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to load WeRead binding status") from exc


@router.post("/binding/validate", response_model=WeReadBindingResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["weread_validate"][0])
async def validate_weread_binding(
    request: Request,
    session: Session = Depends(get_current_session),
) -> WeReadBindingResponse:
    """Ping WeRead with the stored cookie and refresh binding status (manual check)."""
    try:
        binding = await weread_auth_service.get_binding(session.user_id)
        if binding is None:
            raise HTTPException(status_code=404, detail="No WeRead binding to validate")
        try:
            updated = await weread_service.validate_stored_binding_cookie(session.user_id, force=True)
        except WeReadDomainError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        logger.info("weread_binding_validated_via_api", user_id=session.user_id)
        return _to_binding_response(updated)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("weread_binding_validate_failed", user_id=session.user_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to validate WeRead binding") from exc


@router.post("/binding/cookie", response_model=WeReadBindingResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def bind_weread_cookie(
    request: Request,
    payload: WeReadBindCookieRequest,
    session: Session = Depends(get_current_session),
) -> WeReadBindingResponse:
    """Store a user's WeRead cookie for later tool execution."""
    cookie = payload.cookie.get_secret_value().strip()
    if not cookie:
        raise HTTPException(status_code=422, detail="Cookie cannot be empty")

    try:
        binding = await weread_auth_service.bind_cookie(
            user_id=session.user_id,
            cookie=cookie,
            source=payload.source,
        )
        try:
            await weread_service.sync_bookshelf_snapshot(session.user_id)
        except Exception as sync_exc:
            logger.exception(
                "weread_bookshelf_initial_sync_failed",
                user_id=session.user_id,
                error=str(sync_exc),
            )
        logger.info("weread_cookie_bound_via_api", user_id=session.user_id, source=payload.source.value)
        return _to_binding_response(binding)
    except Exception as exc:
        logger.exception("weread_cookie_bind_failed", user_id=session.user_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to bind WeRead cookie") from exc


@router.delete("/binding", response_model=WeReadBindingMutationResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_weread_binding(
    request: Request,
    session: Session = Depends(get_current_session),
) -> WeReadBindingMutationResponse:
    """Delete the authenticated user's saved WeRead binding."""
    try:
        deleted = await weread_auth_service.clear_binding(session.user_id)
        await weread_service.clear_cached_bookshelf(session.user_id)
        if not deleted:
            return WeReadBindingMutationResponse(success=True, message="No WeRead binding was stored")

        logger.info("weread_binding_cleared_via_api", user_id=session.user_id)
        return WeReadBindingMutationResponse(success=True, message="WeRead binding cleared")
    except Exception as exc:
        logger.exception("weread_binding_clear_failed", user_id=session.user_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to clear WeRead binding") from exc


@router.get("/bookshelf/recent", response_model=WeReadRecentBooksResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_recent_weread_books(
    request: Request,
    limit: int = Query(default=6, ge=1, le=20),
    refresh: bool = Query(default=False),
    session: Session = Depends(get_current_session),
) -> WeReadRecentBooksResponse:
    """Return recent bookshelf items from the local cache, with optional refresh."""
    try:
        binding = await weread_auth_service.get_binding(session.user_id)
        connected = binding is not None and binding.status == WeReadBindingStatus.ACTIVE.value
        snapshot, refreshed = await weread_service.get_recent_bookshelf_snapshot(
            session.user_id,
            limit=limit,
            force_refresh=refresh,
        )
        logger.info(
            "weread_recent_books_retrieved",
            user_id=session.user_id,
            connected=connected,
            refreshed=refreshed,
            book_count=len(snapshot.books) if snapshot is not None else 0,
        )
        return _to_recent_books_response(connected=connected, refreshed=refreshed, snapshot=snapshot)
    except Exception as exc:
        logger.exception("weread_recent_books_failed", user_id=session.user_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to load recent WeRead books") from exc


@router.get("/documents/{doc_id}")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_weread_local_document(
    request: Request,
    doc_id: str,
    download: bool = Query(default=False),
    session: Session = Depends(get_current_session),
):
    """Preview or download one locally materialized WeRead markdown document."""
    try:
        metadata, file_path = await weread_service.get_local_document(session.user_id, doc_id)
        logger.info(
            "weread_local_document_retrieved",
            user_id=session.user_id,
            doc_id=doc_id,
            source_type=metadata.source_type,
            download=download,
        )

        if download:
            return FileResponse(
                path=file_path,
                filename=file_path.name,
                media_type="text/markdown",
            )

        content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
        return PlainTextResponse(
            content=content,
            media_type="text/markdown",
        )
    except WeReadDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "weread_local_document_retrieve_failed",
            user_id=session.user_id,
            doc_id=doc_id,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to load WeRead local document") from exc


@router.get("/books/{book_id}/documents")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def list_weread_book_documents(
    book_id: str,
    request: Request,
    session: Session = Depends(get_current_session),
) -> list[dict]:
    """List local WeRead documents available for a book."""
    _ = request
    book_dir = weread_local_document_store._book_dir(session.user_id, book_id)
    if not book_dir.exists():
        return []
    docs: list[dict] = []
    for meta_path in sorted(book_dir.glob("*.meta.json")):
        try:
            import json
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        docs.append({
            "doc_id": meta.get("doc_id", ""),
            "book_title": meta.get("book_title", ""),
            "source_type": meta.get("source_type", ""),
            "item_count": meta.get("item_count", 0),
            "generated_at": meta.get("generated_at", ""),
        })
    return docs


@router.get("/user/books")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def list_user_weread_books(
    request: Request,
    session: Session = Depends(get_current_session),
) -> list[dict]:
    """List all local WeRead books and their documents for the authenticated user."""
    _ = request
    return weread_local_document_store.list_user_books(session.user_id)


@router.post("/binding/qr/session", response_model=WeReadQrLoginSessionResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def start_weread_qr_login(
    request: Request,
    session: Session = Depends(get_current_session),
) -> WeReadQrLoginSessionResponse:
    """Start a temporary server-side WeRead QR login session."""
    try:
        login_session = await weread_browser_service.start_qr_login_session(session.user_id)
        logger.info(
            "weread_qr_login_session_started_via_api",
            user_id=session.user_id,
            login_session_id=login_session.id,
        )
        return _to_qr_login_session_response(login_session)
    except WeReadQrLoginUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("weread_qr_login_session_start_failed", user_id=session.user_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to start WeRead QR login session") from exc


@router.get("/binding/qr/session/{login_session_id}", response_model=WeReadQrLoginSessionResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_weread_qr_login_session(
    request: Request,
    login_session_id: str,
    session: Session = Depends(get_current_session),
) -> WeReadQrLoginSessionResponse:
    """Return the current QR login session state for the authenticated user."""
    try:
        login_session = await weread_browser_service.get_qr_login_session(
            user_id=session.user_id,
            session_id=login_session_id,
        )
        if login_session is None:
            raise HTTPException(status_code=404, detail="WeRead QR login session not found")
        return _to_qr_login_session_response(login_session)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "weread_qr_login_session_retrieve_failed",
            user_id=session.user_id,
            login_session_id=login_session_id,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to load WeRead QR login session") from exc


@router.delete("/binding/qr/session/{login_session_id}", response_model=WeReadBindingMutationResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def cancel_weread_qr_login_session(
    request: Request,
    login_session_id: str,
    session: Session = Depends(get_current_session),
) -> WeReadBindingMutationResponse:
    """Cancel an active WeRead QR login session."""
    try:
        login_session = await weread_browser_service.cancel_qr_login_session(
            user_id=session.user_id,
            session_id=login_session_id,
        )
        if login_session is None:
            raise HTTPException(status_code=404, detail="WeRead QR login session not found")
        logger.info(
            "weread_qr_login_session_cancelled_via_api",
            user_id=session.user_id,
            login_session_id=login_session_id,
            status=login_session.status,
        )
        return WeReadBindingMutationResponse(success=True, message="WeRead QR login session cancelled")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "weread_qr_login_session_cancel_failed",
            user_id=session.user_id,
            login_session_id=login_session_id,
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail="Failed to cancel WeRead QR login session") from exc
