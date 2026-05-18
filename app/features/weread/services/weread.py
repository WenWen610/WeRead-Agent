"""User-scoped WeRead domain service."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger
from app.models.weread_binding import WeReadBinding, WeReadBindingStatus
from app.features.weread.models import (
    BookBestReviewsResponse,
    BookMarkResponse,
    BookNotesResponse,
    BookMarksReivewsResponse,
    BookshelfOverviewResponse,
    WeReadBookNotesExportResponse,
    BookReviewsResponse,
    BookReadingStatusResponse,
    BookshelfSearchItem,
    BookshelfSearchResponse,
    BookshelfSnapshotResponse,
    ChapterNode,
    HighlightItem,
    MainCategory,
    ResolveWereadBookResponse,
    ReviewItem,
    UncategorizedNotes,
    WeReadLocalDocumentIndexState,
    WeReadLocalDocumentIndexResponse,
    WeReadLocalDocumentMaterializationResponse,
    WeReadLocalDocumentMetadata,
    WeReadLocalNoteChunkSearchResponse,
)
from app.features.weread.builders import (
    build_book_best_reviews_response,
    build_book_bookmark_response,
    build_book_notes_response,
    build_book_marks_and_reviews_response,
    build_book_reviews_response,
    build_book_reading_status_response,
    build_bookshelf_snapshot,
)
from app.features.weread.api_client import WeReadApi
from app.features.weread.services.auth import (
    WeReadAuthService,
    WeReadBindingError,
    WeReadBindingRequiredError,
    WeReadReauthRequiredError,
    weread_auth_service,
)
from app.features.weread.indexing.embeddings import note_embedding_service
from app.features.weread.indexing.index_job_manager import weread_index_job_manager
from app.features.weread.stores.bookshelf_store import weread_local_store
from app.features.weread.stores.document_store import weread_local_document_store
from app.features.weread.stores.index_store import weread_local_index_store

logger = get_logger(__name__)

T = TypeVar("T")

MARK_PREVIEW_DEFAULT_ITEMS = 4
MARK_PREVIEW_TEXT_CHARS = 120
REVIEW_PREVIEW_DEFAULT_ITEMS = 3
REVIEW_PREVIEW_CONTENT_CHARS = 120
REVIEW_PREVIEW_MARK_TEXT_CHARS = 80
MARKS_AND_REVIEWS_PREVIEW_DEFAULT_REVIEW_ITEMS = 3
MARKS_AND_REVIEWS_PREVIEW_DEFAULT_MARK_ITEMS = 2
PREVIEW_NEXT_ACTION = (
    "Use query_weread_book_notes for precise retrieval or "
    "export_weread_book_notes_to_markdown for complete notes."
)


def _truncate_preview_text(text: str, max_chars: int) -> str:
    content = text.strip()
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    if max_chars <= 3:
        return content[:max_chars]
    return content[: max_chars - 3].rstrip() + "..."


def _preview_highlight_item(item: HighlightItem, *, text_chars: int) -> HighlightItem:
    return item.model_copy(update={"text": _truncate_preview_text(item.text, text_chars)})


def _preview_review_item(
    item: ReviewItem,
    *,
    content_chars: int,
    mark_text_chars: int,
) -> ReviewItem:
    return item.model_copy(
        update={
            "content": _truncate_preview_text(item.content, content_chars),
            "mark_text": _truncate_preview_text(item.mark_text, mark_text_chars),
        }
    )


def _apply_marks_preview(
    response: BookMarkResponse,
    *,
    max_items: int | None,
    text_chars: int = MARK_PREVIEW_TEXT_CHARS,
) -> BookMarkResponse:
    if max_items is None:
        return response.model_copy(
            update={
                "returned_marks_count": len(response.marks),
                "omitted_marks_count": max(0, response.total_marks_count - len(response.marks)),
            }
        )

    trimmed_marks = [_preview_highlight_item(item, text_chars=text_chars) for item in response.marks[:max_items]]
    omitted_count = max(0, response.total_marks_count - len(trimmed_marks))
    return response.model_copy(
        update={
            "marks": trimmed_marks,
            "returned_marks_count": len(trimmed_marks),
            "omitted_marks_count": omitted_count,
            "preview_mode": omitted_count > 0,
            "next_action": PREVIEW_NEXT_ACTION if omitted_count > 0 else None,
        }
    )


def _apply_reviews_preview(
    response: BookReviewsResponse,
    *,
    max_items: int | None,
    content_chars: int = REVIEW_PREVIEW_CONTENT_CHARS,
    mark_text_chars: int = REVIEW_PREVIEW_MARK_TEXT_CHARS,
) -> BookReviewsResponse:
    if max_items is None:
        return response.model_copy(
            update={
                "returned_reviews_count": len(response.reviews),
                "omitted_reviews_count": max(0, response.total_reviews_count - len(response.reviews)),
            }
        )

    trimmed_reviews = [
        _preview_review_item(
            item,
            content_chars=content_chars,
            mark_text_chars=mark_text_chars,
        )
        for item in response.reviews[:max_items]
    ]
    omitted_count = max(0, response.total_reviews_count - len(trimmed_reviews))
    return response.model_copy(
        update={
            "reviews": trimmed_reviews,
            "returned_reviews_count": len(trimmed_reviews),
            "omitted_reviews_count": omitted_count,
            "preview_mode": omitted_count > 0,
            "next_action": PREVIEW_NEXT_ACTION if omitted_count > 0 else None,
        }
    )


def _flatten_preview_marks_from_chapters(chapters: list[ChapterNode]) -> list[HighlightItem]:
    flattened: list[HighlightItem] = []
    for chapter in chapters:
        flattened.extend(
            item.model_copy(update={"chapter_uid": chapter.uid, "chapter_title": chapter.title})
            for item in chapter.marks
        )
        flattened.extend(_flatten_preview_marks_from_chapters(chapter.children))
    return flattened


def _flatten_preview_reviews_from_chapters(chapters: list[ChapterNode]) -> list[ReviewItem]:
    flattened: list[ReviewItem] = []
    for chapter in chapters:
        flattened.extend(
            item.model_copy(update={"chapter_uid": chapter.uid, "chapter_title": chapter.title})
            for item in chapter.reviews
        )
        flattened.extend(_flatten_preview_reviews_from_chapters(chapter.children))
    return flattened


def _apply_marks_and_reviews_preview(
    response: BookMarksReivewsResponse,
    *,
    max_review_items: int | None,
    max_mark_items: int | None,
    review_content_chars: int = REVIEW_PREVIEW_CONTENT_CHARS,
    review_mark_text_chars: int = REVIEW_PREVIEW_MARK_TEXT_CHARS,
    mark_text_chars: int = MARK_PREVIEW_TEXT_CHARS,
) -> BookMarksReivewsResponse:
    if max_review_items is None and max_mark_items is None:
        return response.model_copy(
            update={
                "returned_reviews_count": response.total_reviews_count,
                "returned_marks_count": response.total_marks_count,
                "omitted_reviews_count": 0,
                "omitted_marks_count": 0,
            }
        )

    all_reviews = (
        list(response.reviews)
        if response.mode == "flat"
        else [*_flatten_preview_reviews_from_chapters(response.chapters), *response.uncategorized.reviews]
    )
    all_marks = (
        list(response.marks)
        if response.mode == "flat"
        else [*_flatten_preview_marks_from_chapters(response.chapters), *response.uncategorized.marks]
    )

    selected_reviews = all_reviews if max_review_items is None else all_reviews[:max_review_items]
    selected_marks = all_marks if max_mark_items is None else all_marks[:max_mark_items]
    preview_reviews = [
        _preview_review_item(
            item,
            content_chars=review_content_chars,
            mark_text_chars=review_mark_text_chars,
        )
        for item in selected_reviews
    ]
    preview_marks = [_preview_highlight_item(item, text_chars=mark_text_chars) for item in selected_marks]
    omitted_reviews_count = max(0, response.total_reviews_count - len(preview_reviews))
    omitted_marks_count = max(0, response.total_marks_count - len(preview_marks))

    return response.model_copy(
        update={
            "mode": "flat",
            "reviews": preview_reviews,
            "marks": preview_marks,
            "chapters": [],
            "uncategorized": UncategorizedNotes(),
            "returned_reviews_count": len(preview_reviews),
            "returned_marks_count": len(preview_marks),
            "omitted_reviews_count": omitted_reviews_count,
            "omitted_marks_count": omitted_marks_count,
            "preview_mode": omitted_reviews_count > 0 or omitted_marks_count > 0,
            "next_action": (
                PREVIEW_NEXT_ACTION
                if omitted_reviews_count > 0 or omitted_marks_count > 0
                else None
            ),
        }
    )

_auto_validate_backoff_until: dict[int, datetime] = {}


def _weread_auto_validate_backoff_active(user_id: int) -> bool:
    until = _auto_validate_backoff_until.get(user_id)
    if until is None:
        return False
    now = datetime.now(UTC)
    if now >= until:
        _auto_validate_backoff_until.pop(user_id, None)
        return False
    return True


def _weread_set_auto_validate_backoff(user_id: int) -> None:
    _auto_validate_backoff_until[user_id] = datetime.now(UTC) + timedelta(
        seconds=settings.WEREAD_BINDING_VALIDATE_NETWORK_BACKOFF_SECONDS,
    )


def _weread_clear_auto_validate_backoff(user_id: int) -> None:
    _auto_validate_backoff_until.pop(user_id, None)


def _binding_validation_stale(binding: WeReadBinding) -> bool:
    if binding.last_validated_at is None:
        return True
    validated = binding.last_validated_at
    if validated.tzinfo is None:
        validated = validated.replace(tzinfo=UTC)
    age = datetime.now(UTC) - validated
    return age > timedelta(hours=settings.WEREAD_BINDING_AUTO_VALIDATE_AFTER_HOURS)


class WeReadDomainError(RuntimeError):
    """Base error for user-scoped WeRead domain calls."""


class WeReadService:
    """Use a user's saved WeRead credential to fetch domain data."""

    def __init__(self, auth_service: WeReadAuthService = weread_auth_service) -> None:
        self.auth_service = auth_service

    async def _with_api(
        self,
        user_id: int,
        operation_name: str,
        operation: Callable[[WeReadApi], Awaitable[T]],
    ) -> T:
        """Execute a WeRead API operation using the current user's binding."""
        cookie = await self.auth_service.require_active_cookie(user_id)
        api = WeReadApi(cookie=cookie)

        try:
            result = await operation(api)
            await self.auth_service.mark_cookie_valid(user_id)
            return result
        except (WeReadBindingRequiredError, WeReadReauthRequiredError):
            raise
        except Exception as exc:
            if self._is_cookie_expired_error(exc):
                await self.auth_service.mark_expired(user_id, str(exc))
                raise WeReadReauthRequiredError("WeRead authorization expired. Please rebind and retry.") from exc

            logger.exception(
                "weread_operation_failed",
                user_id=user_id,
                operation_name=operation_name,
                error=str(exc),
            )
            raise WeReadDomainError(str(exc)) from exc
        finally:
            await api.aclose()

    @staticmethod
    def _is_cookie_expired_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "cookie expired" in message or "refresh the cookie" in message

    @staticmethod
    def _ensure_dict(payload: Any, payload_name: str) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        raise WeReadDomainError(f"Unexpected {payload_name} response type: {type(payload)}")

    @staticmethod
    def _ensure_list(payload: Any, payload_name: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        raise WeReadDomainError(f"Unexpected {payload_name} response type: {type(payload)}")

    @staticmethod
    def _normalize_search_text(value: str) -> str:
        return value.strip().casefold()

    async def _has_active_binding(self, user_id: int) -> bool:
        binding = await self.auth_service.get_binding(user_id)
        return binding is not None and binding.status == WeReadBindingStatus.ACTIVE.value

    @staticmethod
    def _is_likely_upstream_auth_error(exc: Exception) -> bool:
        msg = str(exc)
        if "401" in msg or "403" in msg:
            return True
        if "API returned error" in msg:
            return True
        return False

    async def ensure_binding_with_auto_validation(self, user_id: int) -> WeReadBinding | None:
        """Return binding; if active and last validation older than configured age, ping WeRead once."""
        binding = await self.auth_service.get_binding(user_id)
        if binding is None:
            return None
        if binding.status != WeReadBindingStatus.ACTIVE.value:
            return binding
        if not _binding_validation_stale(binding):
            return binding
        if _weread_auto_validate_backoff_active(user_id):
            return binding
        return await self.validate_stored_binding_cookie(user_id, force=False)

    async def validate_stored_binding_cookie(self, user_id: int, *, force: bool) -> WeReadBinding | None:
        """Ping WeRead with stored cookie; update binding status. ``force`` skips stale/backoff gates."""
        binding = await self.auth_service.get_binding(user_id)
        if binding is None:
            return None

        if not force:
            if binding.status != WeReadBindingStatus.ACTIVE.value:
                return binding
            if not _binding_validation_stale(binding):
                return binding
            if _weread_auto_validate_backoff_active(user_id):
                return binding

        try:
            cookie = self.auth_service.cipher.decrypt_cookie(binding.encrypted_cookie)
        except WeReadBindingError:
            await self.auth_service.mark_reauth_required(
                user_id,
                "Stored WeRead credential could not be decrypted",
            )
            _weread_clear_auto_validate_backoff(user_id)
            return await self.auth_service.get_binding(user_id)

        api = WeReadApi(cookie=cookie)
        try:
            try:
                await api.ping_session()
            except Exception as exc:
                await self._handle_binding_validate_exception(user_id, exc, force=force)
                return await self.auth_service.get_binding(user_id)
            await self.auth_service.mark_cookie_valid(user_id)
            _weread_clear_auto_validate_backoff(user_id)
            logger.info("weread_binding_cookie_validated", user_id=user_id, force=force)
            return await self.auth_service.get_binding(user_id)
        finally:
            await api.aclose()

    async def _handle_binding_validate_exception(self, user_id: int, exc: Exception, *, force: bool) -> None:
        if self._is_cookie_expired_error(exc):
            await self.auth_service.mark_expired(user_id, str(exc)[:500])
            _weread_clear_auto_validate_backoff(user_id)
            return
        if self._is_likely_upstream_auth_error(exc):
            await self.auth_service.mark_reauth_required(user_id, str(exc)[:500])
            _weread_clear_auto_validate_backoff(user_id)
            return
        logger.warning(
            "weread_binding_validate_transient_failure",
            user_id=user_id,
            force=force,
            error=str(exc),
            exc_info=True,
        )
        if not force:
            _weread_set_auto_validate_backoff(user_id)
            return
        raise WeReadDomainError(f"WeRead validation failed: {exc}") from exc

    @staticmethod
    def _sort_snapshot_books(books: list[Any]) -> list[Any]:
        return sorted(
            books,
            key=lambda book: (
                bool(getattr(book, "last_read_time", "")),
                getattr(book, "last_read_time", ""),
                getattr(book, "reading_time", 0),
                getattr(book, "title", ""),
            ),
            reverse=True,
        )

    @classmethod
    def _collect_match_fields(
        cls,
        book: Any,
        keyword: str,
        exact_match: bool,
    ) -> list[str]:
        normalized_keyword = cls._normalize_search_text(keyword)
        if not normalized_keyword:
            return []

        scalar_fields = {
            "title": book.title,
            "author": book.author,
            "translator": book.translator,
        }
        list_fields = {
            "category": book.categories,
            "book_list": book.book_lists,
        }

        def matches(value: str) -> bool:
            normalized_value = cls._normalize_search_text(value)
            if not normalized_value:
                return False
            if exact_match:
                return normalized_keyword == normalized_value
            return normalized_keyword in normalized_value

        matched_on: list[str] = []
        for field_name, value in scalar_fields.items():
            if value and matches(value):
                matched_on.append(field_name)

        for field_name, values in list_fields.items():
            if any(matches(item) for item in values):
                matched_on.append(field_name)

        # Token-level fallback: tokens may match different fields
        if not matched_on and not exact_match:
            keyword_tokens = normalized_keyword.split()
            if len(keyword_tokens) > 1:
                remaining = set(keyword_tokens)
                for field_name, value in scalar_fields.items():
                    if not value or not remaining:
                        continue
                    nv = cls._normalize_search_text(value)
                    found = {t for t in remaining if t in nv}
                    if found:
                        remaining -= found
                        matched_on.append(field_name)
                for field_name, values in list_fields.items():
                    if not remaining:
                        break
                    for item in values:
                        nv = cls._normalize_search_text(item)
                        found = {t for t in remaining if t in nv}
                        if found:
                            remaining -= found
                            matched_on.append(field_name)
                            break
                if remaining:
                    matched_on = []

        return matched_on

    # async def get_bookshelf(self, user_id: int) -> BookshelfResponse:
    #     """Return normalized bookshelf data for a user."""

    #     async def operation(api: WeReadApi) -> BookshelfResponse:
    #         entire_shelf_data = self._ensure_dict(await api.get_entire_shelf(), "entire_shelf")
    #         notebook_data = self._ensure_dict(await api.get_bookshelf(), "notebook")
    #         return build_bookshelf_response(entire_shelf_data, notebook_data)

    #     return await self._with_api(user_id, "get_bookshelf", operation)

    async def sync_bookshelf_snapshot(self, user_id: int) -> BookshelfSnapshotResponse:
        """Fetch the latest bookshelf snapshot from WeRead and persist it locally."""

        async def operation(api: WeReadApi) -> BookshelfSnapshotResponse:
            entire_shelf_data = self._ensure_dict(await api.get_entire_shelf(), "entire_shelf")
            return build_bookshelf_snapshot(entire_shelf_data)

        snapshot = await self._with_api(user_id, "sync_bookshelf_snapshot", operation)
        await weread_local_store.upsert_bookshelf_snapshot(user_id=user_id, snapshot=snapshot)
        logger.info(
            "weread_bookshelf_snapshot_synced",
            user_id=user_id,
            sync_key=snapshot.sync_key,
            book_count=snapshot.book_count,
        )
        return snapshot

    async def get_bookshelf_snapshot(
        self,
        user_id: int,
        force_refresh: bool = False,
    ) -> BookshelfSnapshotResponse:
        """Return a bookshelf snapshot, preferring the local cache when available."""
        if not force_refresh:
            cached_snapshot = await weread_local_store.get_bookshelf_snapshot(user_id)
            if cached_snapshot is not None:
                logger.info(
                    "weread_bookshelf_snapshot_loaded_from_cache",
                    user_id=user_id,
                    sync_key=cached_snapshot.sync_key,
                    book_count=cached_snapshot.book_count,
                )
                return cached_snapshot

        return await self.sync_bookshelf_snapshot(user_id)

    async def get_recent_bookshelf_snapshot(
        self,
        user_id: int,
        limit: int = 6,
        force_refresh: bool = False,
    ) -> tuple[BookshelfSnapshotResponse | None, bool]:
        """Return a recent-books view backed by the local bookshelf cache."""
        if force_refresh:
            if not await self._has_active_binding(user_id):
                return None, False
            snapshot = await self.sync_bookshelf_snapshot(user_id)
            return snapshot.model_copy(update={"books": snapshot.books[: max(1, min(limit, 20))]}), True

        cached_snapshot = await weread_local_store.list_recent_books(user_id=user_id, limit=limit)
        if cached_snapshot is not None:
            return cached_snapshot, False

        if not await self._has_active_binding(user_id):
            return None, False

        snapshot = await self.sync_bookshelf_snapshot(user_id)
        return snapshot.model_copy(update={"books": snapshot.books[: max(1, min(limit, 20))]}), True

    async def clear_cached_bookshelf(self, user_id: int) -> None:
        """Delete the local bookshelf cache for a user."""
        await weread_local_store.clear_user_bookshelf(user_id)
        logger.info("weread_bookshelf_cache_cleared", user_id=user_id)

    def _build_bookshelf_overview(
        self,
        *,
        snapshot: BookshelfSnapshotResponse,
        preview_limit: int = 8,
        reading_limit: int = 5,
    ) -> BookshelfOverviewResponse:
        sorted_books = self._sort_snapshot_books(snapshot.books)
        capped_preview_limit = max(1, min(preview_limit, 20))
        capped_reading_limit = max(1, min(reading_limit, 10))

        unread_books_count = 0
        reading_books_count = 0
        finished_books_count = 0
        category_counts: dict[str, int] = {}

        for book in snapshot.books:
            if book.finish_reading:
                finished_books_count += 1
            elif book.progress > 0:
                reading_books_count += 1
            else:
                unread_books_count += 1

            for category in book.categories:
                normalized_category = category.strip()
                if normalized_category:
                    category_counts[normalized_category] = category_counts.get(normalized_category, 0) + 1

        currently_reading_books = [
            book for book in sorted_books if not book.finish_reading and (book.progress > 0 or book.reading_time > 0)
        ]

        main_categories = [
            MainCategory(category=category, count=count)
            for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        ]

        return BookshelfOverviewResponse(
            sync_key=snapshot.sync_key,
            lecture_sync_key=snapshot.lecture_sync_key,
            pure_book_count=snapshot.pure_book_count,
            book_count=snapshot.book_count,
            generated_at=snapshot.generated_at,
            unread_books_count=unread_books_count,
            reading_books_count=reading_books_count,
            finished_books_count=finished_books_count,
            recent_books=sorted_books[:capped_preview_limit],
            currently_reading_books=currently_reading_books[:capped_reading_limit],
            main_categories=main_categories,
        )

    async def get_bookshelf_overview(
        self,
        user_id: int,
        *,
        force_refresh: bool = False,
        preview_limit: int = 8,
        reading_limit: int = 5,
    ) -> BookshelfOverviewResponse:
        """Return a compact bookshelf overview optimized for agent consumption."""
        snapshot = await self.get_bookshelf_snapshot(user_id, force_refresh=force_refresh)
        overview = self._build_bookshelf_overview(
            snapshot=snapshot,
            preview_limit=preview_limit,
            reading_limit=reading_limit,
        )
        logger.info(
            "weread_bookshelf_overview_built",
            user_id=user_id,
            book_count=overview.book_count,
            recent_books_count=len(overview.recent_books),
            currently_reading_books_count=len(overview.currently_reading_books),
        )
        return overview

    def _build_search_response(
        self,
        *,
        snapshot: BookshelfSnapshotResponse,
        keyword: str,
        exact_match: bool,
        max_results: int,
    ) -> BookshelfSearchResponse:
        """Build a bookshelf search response from an existing snapshot."""
        capped_results = max(1, min(max_results, 20))

        matches: list[BookshelfSearchItem] = []
        for book in snapshot.books:
            matched_on = self._collect_match_fields(book, keyword, exact_match)
            if not matched_on:
                continue

            matches.append(
                BookshelfSearchItem(
                    book_id=book.book_id,
                    title=book.title,
                    author=book.author,
                    translator=book.translator,
                    categories=book.categories,
                    book_lists=book.book_lists,
                    progress=book.progress,
                    finish_reading=book.finish_reading,
                    reading_time_formatted=book.reading_time_formatted,
                    last_read_time=book.last_read_time,
                    matched_on=matched_on,
                )
            )

        return BookshelfSearchResponse(
            keyword=keyword,
            exact_match=exact_match,
            total_matches=len(matches),
            max_results=capped_results,
            sync_key=snapshot.sync_key,
            generated_at=snapshot.generated_at,
            books=matches[:capped_results],
        )

    async def search_bookshelf(
        self,
        user_id: int,
        keyword: str,
        exact_match: bool = False,
        max_results: int = 10,
    ) -> BookshelfSearchResponse:
        """Search the user's bookshelf by title, author, translator, category, or list."""
        normalized_keyword = self._normalize_search_text(keyword)
        if not normalized_keyword:
            raise WeReadDomainError("Search keyword must not be empty.")

        snapshot = await self.get_bookshelf_snapshot(user_id)
        return self._build_search_response(
            snapshot=snapshot,
            keyword=keyword,
            exact_match=exact_match,
            max_results=max_results,
        )

    async def resolve_book(
        self,
        user_id: int,
        keyword: str,
        exact_match: bool = False,
        max_candidates: int = 5,
    ) -> ResolveWereadBookResponse:
        """Resolve one target book for detail queries and return deterministic status."""
        cached_snapshot = await weread_local_store.get_bookshelf_snapshot(user_id)
        did_refresh_snapshot = False

        if cached_snapshot is None:
            cached_snapshot = await self.get_bookshelf_snapshot(user_id)
            did_refresh_snapshot = True

        search_result = self._build_search_response(
            snapshot=cached_snapshot,
            keyword=keyword,
            exact_match=exact_match,
            max_results=max_candidates,
        )

        if search_result.total_matches == 0 and not did_refresh_snapshot and await self._has_active_binding(user_id):
            refreshed_snapshot = await self.sync_bookshelf_snapshot(user_id)
            search_result = self._build_search_response(
                snapshot=refreshed_snapshot,
                keyword=keyword,
                exact_match=exact_match,
                max_results=max_candidates,
            )

        if search_result.total_matches == 0:
            return ResolveWereadBookResponse(
                keyword=search_result.keyword,
                exact_match=search_result.exact_match,
                status="not_found",
                recommended_action="reply_not_found",
                message="No matching book was found in the user's WeRead bookshelf.",
                total_matches=0,
                sync_key=search_result.sync_key,
                generated_at=search_result.generated_at,
            )

        if search_result.total_matches == 1 and search_result.books:
            return ResolveWereadBookResponse(
                keyword=search_result.keyword,
                exact_match=search_result.exact_match,
                status="resolved",
                recommended_action="proceed_with_detail_tool",
                message="A single target book was resolved. Use its book_id for the next detail tool call.",
                total_matches=1,
                sync_key=search_result.sync_key,
                generated_at=search_result.generated_at,
                book=search_result.books[0],
            )

        return ResolveWereadBookResponse(
            keyword=search_result.keyword,
            exact_match=search_result.exact_match,
            status="multiple_candidates",
            recommended_action="ask_clarification",
            message="Multiple candidate books were found. Ask the user to choose one before continuing.",
            total_matches=search_result.total_matches,
            sync_key=search_result.sync_key,
            generated_at=search_result.generated_at,
            candidates=search_result.books,
        )

    async def get_book_reading_status(self, user_id: int, book_id: str) -> BookReadingStatusResponse:
        """Return reading progress and metadata for a book."""

        async def operation(api: WeReadApi) -> BookReadingStatusResponse:
            book_info = self._ensure_dict(await api.get_book_info(book_id), "book_info")
            read_info = self._ensure_dict(await api.get_read_info(book_id), "read_info")
            return build_book_reading_status_response(
                book_id=book_id,
                book_info_payload=book_info,
                read_info_payload=read_info,
            )

        return await self._with_api(user_id, "get_book_reading_status", operation)

    async def get_weread_book_note_counts(self, user_id: int, book_id: str) -> BookNotesResponse:
        """Return note-related counts for one book using the notebook summary endpoint."""

        async def operation(api: WeReadApi) -> BookNotesResponse:
            notebook_entries = self._ensure_list(await api.get_notebook_list(), "notebook_list")
            notebook_entry_payload = next(
                (item for item in notebook_entries if isinstance(item, dict) and item.get("bookId") == book_id),
                None,
            )
            return build_book_notes_response(
                book_id=book_id,
                notebook_entry_payload=notebook_entry_payload,
            )

        return await self._with_api(user_id, "get_weread_book_note_counts", operation)

    async def get_weread_book_marks(
        self,
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        max_items: int | None = None,
    ) -> BookMarkResponse:
        """Return bookmark for a book."""

        async def operation(api: WeReadApi) -> BookMarkResponse:
            book_info = self._ensure_dict(await api.get_book_info(book_id), "book_info")
            chapter_info: dict[str, Any] = {}
            if include_chapter:
                chapter_info = self._ensure_dict(await api.get_chapter_info(book_id), "chapter_info")
            bookmarks = self._ensure_list(await api.get_bookmark_list(book_id), "bookmark_list")
            response = build_book_bookmark_response(
                book_id=book_id,
                include_chapter=include_chapter,
                highlight_style=highlight_style,
                book_info_payload=book_info,
                chapter_info_payload=chapter_info,
                bookmark_payload=bookmarks,
            )
            return _apply_marks_preview(response, max_items=max_items)

        return await self._with_api(user_id, "get_weread_book_marks", operation)

    async def get_weread_book_reviews(
        self,
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        max_items: int | None = None,
    ) -> BookReviewsResponse:
        """Return reviews for a book."""

        async def operation(api: WeReadApi) -> BookReviewsResponse:
            book_info = self._ensure_dict(await api.get_book_info(book_id), "book_info")
            chapter_info: dict[str, Any] = {}
            if include_chapter:
                chapter_info = self._ensure_dict(await api.get_chapter_info(book_id), "chapter_info")
            reviews = self._ensure_list(await api.get_review_list(book_id), "review_list")
            response = build_book_reviews_response(
                book_id=book_id,
                include_chapter=include_chapter,
                book_info_payload=book_info,
                chapter_info_payload=chapter_info,
                reviews_payload=reviews,
            )
            return _apply_reviews_preview(response, max_items=max_items)

        return await self._with_api(user_id, "get_weread_book_reviews", operation)

    async def materialize_weread_book_marks(
        self,
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ) -> WeReadLocalDocumentMaterializationResponse:
        """Fetch marks for a book and persist them as a local markdown document."""
        marks = await self.get_weread_book_marks(
            user_id=user_id,
            book_id=book_id,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
            max_items=None,
        )
        marks.reverse_order = reverse_order
        materialized = await weread_local_document_store.save_marks_document(
            user_id=user_id,
            document=marks,
        )
        logger.info(
            "weread_book_marks_materialized",
            user_id=user_id,
            book_id=book_id,
            status=materialized.status,
            item_count=materialized.metadata.item_count,
            file_path=materialized.metadata.file_path,
        )
        return materialized

    async def materialize_weread_book_reviews(
        self,
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        reverse_order: bool = True,
    ) -> WeReadLocalDocumentMaterializationResponse:
        """Fetch reviews for a book and persist them as a local markdown document."""
        reviews = await self.get_weread_book_reviews(
            user_id=user_id,
            book_id=book_id,
            include_chapter=include_chapter,
            max_items=None,
        )
        reviews.reverse_order = reverse_order
        materialized = await weread_local_document_store.save_reviews_document(
            user_id=user_id,
            document=reviews,
        )
        logger.info(
            "weread_book_reviews_materialized",
            user_id=user_id,
            book_id=book_id,
            status=materialized.status,
            item_count=materialized.metadata.item_count,
            file_path=materialized.metadata.file_path,
        )
        return materialized

    @staticmethod
    def _expected_local_item_count(
        *,
        note_counts: BookNotesResponse,
        source_type: str,
    ) -> int:
        if source_type == "marks":
            return note_counts.mark_count
        return note_counts.review_count

    async def _get_remote_note_counts_for_freshness_check(
        self,
        *,
        user_id: int,
        book_id: str,
    ) -> BookNotesResponse | None:
        if not await self._has_active_binding(user_id):
            return None

        try:
            return await self.get_weread_book_note_counts(user_id=user_id, book_id=book_id)
        except (WeReadDomainError, WeReadBindingRequiredError, WeReadReauthRequiredError):
            logger.warning(
                "weread_note_counts_freshness_check_failed",
                user_id=user_id,
                book_id=book_id,
            )
            return None

    async def _materialize_local_note_document(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        reverse_order: bool = True,
    ) -> WeReadLocalDocumentMaterializationResponse:
        if source_type == "marks":
            return await self.materialize_weread_book_marks(
                user_id=user_id,
                book_id=book_id,
                include_chapter=True,
                highlight_style=None,
                reverse_order=reverse_order,
            )
        return await self.materialize_weread_book_reviews(
            user_id=user_id,
            book_id=book_id,
            include_chapter=True,
            reverse_order=reverse_order,
        )

    async def _ensure_local_note_document(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        note_counts: BookNotesResponse | None,
    ) -> WeReadLocalDocumentMetadata:
        doc_id = weread_local_document_store.build_doc_id(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            include_chapter=True,
            highlight_style=None,
        )
        metadata = await asyncio.to_thread(
            weread_local_document_store.get_document_metadata,
            user_id=user_id,
            doc_id=doc_id,
        )
        if metadata is None:
            materialized = await self._materialize_local_note_document(
                user_id=user_id,
                book_id=book_id,
                source_type=source_type,
            )
            return materialized.metadata

        if note_counts is None:
            return metadata

        expected_item_count = self._expected_local_item_count(
            note_counts=note_counts,
            source_type=source_type,
        )
        if metadata.item_count == expected_item_count:
            return metadata

        logger.info(
            "weread_local_document_refresh_required",
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            local_item_count=metadata.item_count,
            remote_item_count=expected_item_count,
        )
        materialized = await self._materialize_local_note_document(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
        )
        return materialized.metadata

    async def _schedule_local_note_document_index(
        self,
        *,
        user_id: int,
        doc_id: str,
    ) -> None:
        await weread_index_job_manager.schedule_index(
            doc_id=doc_id,
            operation_factory=lambda: self.index_weread_local_document(
                user_id=user_id,
                doc_id=doc_id,
            ),
            debounce_ms=settings.WEREAD_INDEX_DEBOUNCE_MS,
        )

    async def _get_local_note_index_state(
        self,
        *,
        metadata: WeReadLocalDocumentMetadata,
    ) -> WeReadLocalDocumentIndexState:
        return await weread_local_index_store.get_document_index_state(metadata=metadata)

    async def _ensure_local_note_index_for_query(
        self,
        *,
        user_id: int,
        metadata: WeReadLocalDocumentMetadata,
    ) -> None:
        index_state = await self._get_local_note_index_state(metadata=metadata)
        inflight_task = await weread_index_job_manager.get_inflight_task(metadata.doc_id)

        if index_state.is_fresh:
            if inflight_task is not None and index_state.has_searchable_index:
                await weread_index_job_manager.wait_for_doc(
                    doc_id=metadata.doc_id,
                    timeout_ms=settings.WEREAD_INDEX_QUERY_WAIT_MS,
                )
            return

        if inflight_task is not None:
            try:
                await weread_index_job_manager.await_inflight_task(metadata.doc_id)
            except Exception as exc:
                logger.exception(
                    "weread_running_index_reuse_failed",
                    user_id=user_id,
                    doc_id=metadata.doc_id,
                    error=str(exc),
                )
            else:
                refreshed_index_state = await self._get_local_note_index_state(metadata=metadata)
                if refreshed_index_state.is_fresh:
                    return

        await self.index_weread_local_document(
            user_id=user_id,
            doc_id=metadata.doc_id,
        )

    async def ensure_weread_book_notes_document(
        self,
        user_id: int,
        book_id: str,
        source_type: str = "both",
        *,
        force_refresh: bool = False,
        freshness_check: bool = False,
        reverse_order: bool = True,
    ) -> WeReadBookNotesExportResponse:
        """Ensure local markdown note documents exist without forcing remote sync by default."""
        normalized_source_type = source_type.strip().lower()
        if normalized_source_type not in {"marks", "reviews", "both"}:
            raise WeReadDomainError("source_type must be one of: marks, reviews, both.")

        if force_refresh:
            return await self.export_weread_book_notes_to_markdown(
                user_id=user_id,
                book_id=book_id,
                source_type=normalized_source_type,
                include_chapter=True,
                highlight_style=None,
                reverse_order=reverse_order,
            )

        source_types = ["marks", "reviews"] if normalized_source_type == "both" else [normalized_source_type]
        note_counts = (
            await self._get_remote_note_counts_for_freshness_check(user_id=user_id, book_id=book_id)
            if freshness_check
            else None
        )

        documents: list[WeReadLocalDocumentMaterializationResponse] = []
        for current_source_type in source_types:
            doc_id = weread_local_document_store.build_doc_id(
                user_id=user_id,
                book_id=book_id,
                source_type=current_source_type,
                include_chapter=True,
                highlight_style=None,
            )
            metadata = await asyncio.to_thread(
                weread_local_document_store.get_document_metadata,
                user_id=user_id,
                doc_id=doc_id,
            )
            if metadata is None:
                documents.append(
                    await self._materialize_local_note_document(
                        user_id=user_id,
                        book_id=book_id,
                        source_type=current_source_type,
                        reverse_order=reverse_order,
                    )
                )
                continue

            if note_counts is not None:
                expected_item_count = self._expected_local_item_count(
                    note_counts=note_counts,
                    source_type=current_source_type,
                )
                if metadata.item_count != expected_item_count:
                    logger.info(
                        "weread_local_document_refresh_required",
                        user_id=user_id,
                        book_id=book_id,
                        source_type=current_source_type,
                        local_item_count=metadata.item_count,
                        remote_item_count=expected_item_count,
                    )
                    documents.append(
                        await self._materialize_local_note_document(
                            user_id=user_id,
                            book_id=book_id,
                            source_type=current_source_type,
                        )
                    )
                    continue

            documents.append(WeReadLocalDocumentMaterializationResponse(status="unchanged", metadata=metadata))

        artifacts = [weread_local_document_store.build_artifact(document.metadata) for document in documents]
        logger.info(
            "weread_book_notes_document_ensured",
            user_id=user_id,
            book_id=book_id,
            source_type=normalized_source_type,
            force_refresh=force_refresh,
            freshness_check=freshness_check,
            documents_count=len(documents),
        )
        return WeReadBookNotesExportResponse(
            book_id=book_id,
            source_type=normalized_source_type,
            exported_documents=documents,
            artifacts=artifacts,
        )

    async def export_weread_book_notes_to_markdown(
        self,
        user_id: int,
        book_id: str,
        source_type: str = "both",
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ) -> WeReadBookNotesExportResponse:
        """Export one book's marks and/or reviews into local markdown files."""
        normalized_source_type = source_type.strip().lower()
        if normalized_source_type not in {"marks", "reviews", "both"}:
            raise WeReadDomainError("source_type must be one of: marks, reviews, both.")

        exported_documents: list[WeReadLocalDocumentMaterializationResponse] = []
        if normalized_source_type in {"marks", "both"}:
            exported_documents.append(
                await self.materialize_weread_book_marks(
                    user_id=user_id,
                    book_id=book_id,
                    include_chapter=include_chapter,
                    highlight_style=highlight_style,
                    reverse_order=reverse_order,
                )
            )

        if normalized_source_type in {"reviews", "both"}:
            exported_documents.append(
                await self.materialize_weread_book_reviews(
                    user_id=user_id,
                    book_id=book_id,
                    include_chapter=include_chapter,
                    reverse_order=reverse_order,
                )
            )

        logger.info(
            "weread_book_notes_exported_to_markdown",
            user_id=user_id,
            book_id=book_id,
            source_type=normalized_source_type,
            exported_documents_count=len(exported_documents),
        )
        await asyncio.gather(
            *(
                self._schedule_local_note_document_index(
                    user_id=user_id,
                    doc_id=document.metadata.doc_id,
                )
                for document in exported_documents
            )
        )
        artifacts = [
            weread_local_document_store.build_artifact(document.metadata)
            for document in exported_documents
        ]
        return WeReadBookNotesExportResponse(
            book_id=book_id,
            source_type=normalized_source_type,
            exported_documents=exported_documents,
            artifacts=artifacts,
        )

    async def get_local_document(
        self,
        user_id: int,
        doc_id: str,
    ) -> tuple[WeReadLocalDocumentMetadata, Path]:
        resolved = await asyncio.to_thread(
            weread_local_document_store.resolve_document_path,
            user_id=user_id,
            doc_id=doc_id,
        )
        if resolved is None:
            raise WeReadDomainError("Local WeRead document not found.")
        return resolved

    async def index_weread_local_document(
        self,
        user_id: int,
        doc_id: str,
    ) -> WeReadLocalDocumentIndexResponse:
        extracted = await asyncio.to_thread(
            weread_local_document_store.extract_note_chunks,
            user_id=user_id,
            doc_id=doc_id,
        )
        if extracted is None:
            raise WeReadDomainError("Local WeRead document not found.")

        metadata, chunks = extracted
        indexed_document = await weread_local_index_store.sync_document_index(
            metadata=metadata,
            chunks=chunks,
        )
        if not note_embedding_service.is_enabled():
            indexed_document.embedding_status = "disabled"
            logger.info(
                "weread_local_document_indexed",
                user_id=user_id,
                doc_id=doc_id,
                status=indexed_document.status,
                chunk_count=indexed_document.chunk_count,
                embedding_status=indexed_document.embedding_status,
            )
            return indexed_document

        if indexed_document.embedding_status == "ready":
            logger.info(
                "weread_local_document_indexed",
                user_id=user_id,
                doc_id=doc_id,
                status=indexed_document.status,
                chunk_count=indexed_document.chunk_count,
                embedding_status=indexed_document.embedding_status,
            )
            return indexed_document

        missing_chunk_ids = await weread_local_index_store.get_chunks_missing_embeddings(doc_id=metadata.doc_id)
        if not missing_chunk_ids:
            indexed_document.embedding_status = "ready"
            logger.info(
                "weread_local_document_indexed",
                user_id=user_id,
                doc_id=doc_id,
                status=indexed_document.status,
                chunk_count=indexed_document.chunk_count,
                embedding_status=indexed_document.embedding_status,
            )
            return indexed_document
        missing_chunk_id_set = set(missing_chunk_ids)
        missing_chunk_map = {chunk.chunk_id: chunk for chunk in chunks if chunk.chunk_id in missing_chunk_id_set}
        embeddings = await note_embedding_service.embed_texts(
            [chunk.text for chunk in missing_chunk_map.values()]
        )
        if embeddings:
            await weread_local_index_store.sync_chunk_embeddings(
                doc_id=metadata.doc_id,
                embed_model=note_embedding_service.get_model_name(),
                embeddings={
                    chunk.chunk_id: vector
                    for chunk, vector in zip(missing_chunk_map.values(), embeddings, strict=False)
                },
            )
            remaining_missing_chunk_ids = await weread_local_index_store.get_chunks_missing_embeddings(doc_id=metadata.doc_id)
            indexed_document.embedding_status = "ready" if not remaining_missing_chunk_ids else "pending"
        logger.info(
            "weread_local_document_indexed",
            user_id=user_id,
            doc_id=doc_id,
            status=indexed_document.status,
            chunk_count=indexed_document.chunk_count,
            embedding_status=indexed_document.embedding_status,
        )
        return indexed_document

    async def search_weread_note_chunks(
        self,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int = 5,
    ) -> WeReadLocalNoteChunkSearchResponse:
        normalized_source_type = source_type.strip().lower()
        if normalized_source_type not in {"marks", "reviews", "both"}:
            raise WeReadDomainError("source_type must be one of: marks, reviews, both.")
        if not query.strip():
            raise WeReadDomainError("query must not be empty.")

        source_types = ["marks", "reviews"] if normalized_source_type == "both" else [normalized_source_type]
        note_counts = await self._get_remote_note_counts_for_freshness_check(
            user_id=user_id,
            book_id=book_id,
        )
        for current_source_type in source_types:
            metadata = await self._ensure_local_note_document(
                user_id=user_id,
                book_id=book_id,
                source_type=current_source_type,
                note_counts=note_counts,
            )
            await self._ensure_local_note_index_for_query(
                user_id=user_id,
                metadata=metadata,
            )

        query_embedding = None
        query_embeddings = await note_embedding_service.embed_texts([query])
        if query_embeddings:
            query_embedding = query_embeddings[0]

        search_result = await weread_local_index_store.search_note_chunks(
            user_id=user_id,
            book_id=book_id,
            source_type=normalized_source_type,
            query=query,
            top_k=top_k,
            query_embedding=query_embedding,
            text_weight=settings.NOTE_TEXT_WEIGHT,
            vector_weight=settings.NOTE_VECTOR_WEIGHT,
            candidate_multiplier=settings.NOTE_CANDIDATE_MULTIPLIER,
        )
        logger.info(
            "weread_note_chunks_search_completed",
            user_id=user_id,
            book_id=book_id,
            source_type=normalized_source_type,
            query=query,
            total_hits=search_result.total_hits,
            retrieval_mode=search_result.retrieval_mode,
        )
        return search_result

    async def query_weread_book_notes(
        self,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int = 5,
    ) -> WeReadLocalNoteChunkSearchResponse:
        """Query one book's locally cached marks/reviews and return relevant note chunks."""
        search_result = await self.search_weread_note_chunks(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            query=query,
            top_k=top_k,
        )
        logger.info(
            "weread_book_notes_queried",
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            query=query,
            total_hits=search_result.total_hits,
            returned_hits=len(search_result.hits),
        )
        return search_result

    async def get_book_marks_and_reviews(
        self,
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        max_review_items: int | None = None,
        max_mark_items: int | None = None,
    ) -> BookMarksReivewsResponse:
        """Return notes and highlights for a book."""

        async def operation(api: WeReadApi) -> BookMarksReivewsResponse:
            book_info = self._ensure_dict(await api.get_book_info(book_id), "book_info")
            read_info = self._ensure_dict(await api.get_read_info(book_id), "read_info")
            chapter_info: dict[str, Any] = {}
            if include_chapter:
                chapter_info = self._ensure_dict(await api.get_chapter_info(book_id), "chapter_info")
            bookmarks = self._ensure_list(await api.get_bookmark_list(book_id), "bookmark_list")
            reviews = self._ensure_list(await api.get_review_list(book_id), "review_list")
            response = build_book_marks_and_reviews_response(
                book_id=book_id,
                include_chapter=include_chapter,
                highlight_style=highlight_style,
                book_info_payload=book_info,
                read_info_payload=read_info,
                chapter_info_payload=chapter_info,
                bookmark_payload=bookmarks,
                reviews_payload=reviews,
            )
            return _apply_marks_and_reviews_preview(
                response,
                max_review_items=max_review_items,
                max_mark_items=max_mark_items,
            )

        return await self._with_api(user_id, "get_book_marks_and_reviews", operation)

    async def get_book_best_reviews(
        self,
        user_id: int,
        book_id: str,
        count: int = 10,
        max_idx: int = 0,
        synckey: int = 0,
    ) -> BookBestReviewsResponse:
        """Return popular reviews for a book."""

        async def operation(api: WeReadApi) -> BookBestReviewsResponse:
            book_info = self._ensure_dict(await api.get_book_info(book_id), "book_info")
            best_reviews = self._ensure_dict(
                await api.get_best_reviews(book_id, count, max_idx, synckey),
                "best_reviews",
            )
            return build_book_best_reviews_response(
                book_id=book_id,
                book_info_payload=book_info,
                best_reviews_payload=best_reviews,
            )

        return await self._with_api(user_id, "get_book_best_reviews", operation)


weread_service = WeReadService()
