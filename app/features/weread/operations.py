"""Shared WeRead operations used by multiple agent runtimes."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any, Literal

from app.features.weread.services.weread import WeReadDomainError, weread_service
from app.features.weread.stores.document_store import weread_local_document_store
from app.features.weread.stores.bookshelf_store import weread_local_store

DocumentSourceFilter = Literal["marks", "reviews", "both"]


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json", exclude_none=True)
        return payload if isinstance(payload, dict) else {}
    return value if isinstance(value, dict) else {}


def _looks_like_book_id(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if re.fullmatch(r"\d{5,}", raw):
        return True
    if re.fullmatch(r"book-[A-Za-z0-9_-]+", raw):
        return True
    return raw.startswith(("CB_", "MP_", "EPUB_"))


def _document_summary(metadata: Any) -> dict[str, Any]:
    chapters: dict[str, int] = defaultdict(int)
    for item in metadata.items:
        chapter_title = item.chapter_title or "Uncategorized"
        chapters[chapter_title] += 1
    return {
        "doc_id": metadata.doc_id,
        "book_id": metadata.book_id,
        "book_title": metadata.book_title,
        "source_type": metadata.source_type,
        "item_count": metadata.item_count,
        "generated_at": metadata.generated_at,
        "file_path": metadata.file_path,
        "chapters_count": len(chapters),
        "chapters_preview": [
            {"title": title, "item_count": count}
            for title, count in list(chapters.items())[:12]
        ],
    }


async def _find_cached_book_payload(user_id: int, book_id: str) -> dict[str, Any] | None:
    snapshot = await weread_local_store.get_bookshelf_snapshot(user_id)
    if snapshot is None:
        return None
    for book in snapshot.books:
        if book.book_id == book_id:
            return _model_payload(book)
    return None


async def resolve_weread_book_identifier(
    *,
    user_id: int,
    title_or_book_id: str,
    exact_match: bool = False,
    max_candidates: int = 5,
) -> tuple[str | None, dict[str, Any]]:
    """Resolve a title/keyword to book_id, with direct book_id fast path."""
    raw = title_or_book_id.strip()
    if not raw:
        return None, {"status": "invalid_request", "message": "title_or_book_id must not be empty."}

    cached_book = await _find_cached_book_payload(user_id, raw)
    if cached_book is not None:
        return raw, {
            "status": "resolved",
            "recommended_action": "proceed_with_detail_tool",
            "message": "A target book_id was matched from the local bookshelf cache.",
            "book": cached_book,
            "source": "local_book_id_cache",
        }

    if _looks_like_book_id(raw):
        return raw, {
            "status": "resolved",
            "recommended_action": "proceed_with_detail_tool",
            "message": "A target book_id was provided directly.",
            "book": {"book_id": raw},
            "source": "direct_book_id",
        }

    resolved = await weread_service.resolve_book(
        user_id=user_id,
        keyword=raw,
        exact_match=exact_match,
        max_candidates=max_candidates,
    )
    payload = _model_payload(resolved)
    if resolved.status != "resolved" or resolved.book is None:
        return None, payload

    book_payload = _model_payload(resolved.book)
    book_id = str(book_payload.get("book_id") or book_payload.get("bookId") or "")
    if not book_id:
        return None, payload
    return book_id, payload


async def ensure_weread_book_notes_document(
    *,
    user_id: int,
    title_or_book_id: str,
    source_type: DocumentSourceFilter = "both",
    force_refresh: bool = False,
    freshness_check: bool = False,
    reverse_order: bool = True,
) -> dict[str, Any]:
    """Ensure local markdown documents exist and return metadata/artifacts only."""
    book_id, resolution = await resolve_weread_book_identifier(
        user_id=user_id,
        title_or_book_id=title_or_book_id,
    )
    if book_id is None:
        return {"status": "book_not_resolved", "resolution": resolution}

    result = await weread_service.ensure_weread_book_notes_document(
        user_id=user_id,
        book_id=book_id,
        source_type=source_type,
        force_refresh=force_refresh,
        freshness_check=freshness_check,
        reverse_order=reverse_order,
    )
    return {
        "status": "ready",
        "book_id": book_id,
        "book": resolution,
        "source_type": source_type,
        "documents": [_document_summary(document.metadata) for document in result.exported_documents],
        "artifacts": [
            artifact.model_dump(mode="json", exclude_none=True)
            for artifact in result.artifacts
        ],
    }


async def present_weread_book_document(
    *,
    user_id: int,
    title_or_book_id: str,
    source_type: DocumentSourceFilter = "both",
    force_refresh: bool = False,
    reverse_order: bool = True,
) -> dict[str, Any]:
    """Ensure a WeRead markdown document and return frontend artifact metadata."""
    payload = await ensure_weread_book_notes_document(
        user_id=user_id,
        title_or_book_id=title_or_book_id,
        source_type=source_type,
        force_refresh=force_refresh,
        reverse_order=reverse_order,
    )
    if payload.get("status") != "ready":
        return payload

    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    return {
        "status": "presented",
        "book_id": payload.get("book_id"),
        "book": payload.get("book"),
        "source_type": source_type,
        "artifacts_count": len(artifacts),
        "artifacts": artifacts,
        "display": {
            "mode": "inline_preview",
            "placement": "chat",
        },
    }


async def search_weread_notes(
    *,
    user_id: int,
    title_or_book_id: str,
    query: str,
    source_type: DocumentSourceFilter = "both",
    top_k: int = 5,
) -> dict[str, Any]:
    """Search one book's local WeRead note index."""
    book_id, resolution = await resolve_weread_book_identifier(
        user_id=user_id,
        title_or_book_id=title_or_book_id,
    )
    if book_id is None:
        return {"status": "book_not_resolved", "resolution": resolution}

    result = await weread_service.query_weread_book_notes(
        user_id=user_id,
        book_id=book_id,
        source_type=source_type,
        query=query,
        top_k=top_k,
    )
    payload = result.model_dump(mode="json", exclude_none=True)
    payload["status"] = "ok"
    payload["book"] = resolution
    return payload


def _extract_chunks_payload(*, user_id: int, doc_id: str) -> tuple[Any, list[Any]] | None:
    return weread_local_document_store.extract_note_chunks(user_id=user_id, doc_id=doc_id)


async def read_weread_document_outline(*, user_id: int, doc_id: str) -> dict[str, Any]:
    """Return the section outline for a local WeRead markdown document."""
    extracted = await asyncio.to_thread(_extract_chunks_payload, user_id=user_id, doc_id=doc_id)
    if extracted is None:
        raise WeReadDomainError("Local WeRead document not found.")
    metadata, chunks = extracted
    chapter_map: dict[str, dict[str, Any]] = {}
    ordered_titles: list[str] = []
    for chunk in chunks:
        title = chunk.chapter_title or "Uncategorized"
        if title not in chapter_map:
            ordered_titles.append(title)
            chapter_map[title] = {
                "section_id": f"chapter:{len(ordered_titles)}",
                "chapter_title": title,
                "chunk_count": 0,
                "char_count": 0,
                "token_estimate": 0,
            }
        entry = chapter_map[title]
        entry["chunk_count"] += 1
        entry["char_count"] += chunk.char_count
        entry["token_estimate"] += chunk.token_estimate
    return {
        "doc_id": metadata.doc_id,
        "book_id": metadata.book_id,
        "book_title": metadata.book_title,
        "source_type": metadata.source_type,
        "item_count": metadata.item_count,
        "sections": [chapter_map[title] for title in ordered_titles],
    }


async def read_weread_document_section(
    *,
    user_id: int,
    doc_id: str,
    section_id: str,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """Read one bounded section from a local WeRead markdown document."""
    extracted = await asyncio.to_thread(_extract_chunks_payload, user_id=user_id, doc_id=doc_id)
    if extracted is None:
        raise WeReadDomainError("Local WeRead document not found.")
    metadata, chunks = extracted
    chapter_titles: list[str] = []
    for chunk in chunks:
        title = chunk.chapter_title or "Uncategorized"
        if title not in chapter_titles:
            chapter_titles.append(title)

    normalized_section = section_id.strip().lower()
    selected_title: str | None = None
    if normalized_section not in {"", "all"}:
        if normalized_section.startswith("chapter:"):
            raw_index = normalized_section.removeprefix("chapter:")
            if raw_index.isdigit():
                index = int(raw_index) - 1
                if 0 <= index < len(chapter_titles):
                    selected_title = chapter_titles[index]
        else:
            for title in chapter_titles:
                if title.casefold() == section_id.strip().casefold():
                    selected_title = title
                    break
        if selected_title is None:
            raise WeReadDomainError("Requested section was not found.")

    selected_chunks = [
        chunk
        for chunk in chunks
        if selected_title is None or (chunk.chapter_title or "Uncategorized") == selected_title
    ]
    parts: list[str] = []
    current_chars = 0
    truncated = False
    for chunk in selected_chunks:
        block = f"### {chunk.heading}\n\n{chunk.text}".strip()
        if current_chars + len(block) > max_chars:
            remaining = max_chars - current_chars
            if remaining > 200:
                parts.append(block[:remaining].rstrip())
            truncated = True
            break
        parts.append(block)
        current_chars += len(block) + 2
    return {
        "doc_id": metadata.doc_id,
        "book_title": metadata.book_title,
        "source_type": metadata.source_type,
        "section_id": section_id,
        "chapter_title": selected_title,
        "returned_chunks": len(parts),
        "total_selected_chunks": len(selected_chunks),
        "truncated": truncated,
        "content": "\n\n".join(parts),
    }
