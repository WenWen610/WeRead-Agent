from __future__ import annotations

from typing import Any

from ..models import (
    BookMarkResponse,
    BookNotesResponse,
    BookMarksReivewsResponse,
    BookReviewsResponse,
    BookReadingStatusResponse,
    ChapterNode,
    HighlightItem,
    ReviewItem,
    NotesBookInfo,
    NotesReadingStatus,
)
from .bookshelf import format_reading_time, to_iso
from .common import (
    as_dict,
    as_list,
    extract_note_content,
    extract_note_mark_text,
    is_valid_note_review,
    now_iso,
    to_int,
)


def _build_notes_book_info(book_info: dict[str, Any]) -> NotesBookInfo:
    return NotesBookInfo(
        author=str(book_info.get("author") or ""),
        translator=str(book_info.get("translator") or ""),
        publisher=str(book_info.get("publisher") or ""),
        publish_time=str(book_info.get("publishTime") or ""),
        word_count=to_int(book_info.get("totalWords"), 0),
        rating=float((to_int(book_info.get("newRating"), 0) or 0) / 100),
        category=str(book_info.get("category") or ""),
    )


def _build_notes_reading_status(book_info: dict[str, Any], read_info: dict[str, Any]) -> NotesReadingStatus:
    read_book = as_dict(read_info.get("book"))
    start_reading_time = to_int(read_book.get("startReadingTime"), 0)
    reading_time = to_int(read_book.get("readingTime"), 0)
    return NotesReadingStatus(
        progress=to_int(read_book.get("progress"), 0),
        reading_time=reading_time,
        reading_time_formatted=format_reading_time(reading_time),
        start_reading_time=to_iso(start_reading_time),
        has_started_reading=start_reading_time > 0,
        last_read_time=to_iso(read_book.get("updateTime")),
        finish_reading=to_int(book_info.get("finishReading"), 0) == 1,
    )


def build_book_reading_status_response(
    *,
    book_id: str,
    book_info_payload: dict[str, Any],
    read_info_payload: dict[str, Any],
) -> BookReadingStatusResponse:
    book_info = as_dict(book_info_payload)
    read_info = as_dict(read_info_payload)
    return BookReadingStatusResponse(
        book_id=book_id,
        book_title=str(book_info.get("title") or ""),
        book_info=_build_notes_book_info(book_info),
        reading_status=_build_notes_reading_status(book_info, read_info),
        last_updated=now_iso(),
    )


def build_book_bookmark_response(
    *,
    book_id: str,
    include_chapter: bool,
    highlight_style: int | None,
    book_info_payload: dict[str, Any],
    chapter_info_payload: dict[str, Any],
    bookmark_payload: list[dict[str, Any]],
) -> BookMarkResponse:
    book_info = as_dict(book_info_payload)
    chapter_info = as_dict(chapter_info_payload)
    highlights = [item for item in as_list(bookmark_payload) if isinstance(item, dict)]

    result = BookMarkResponse(
        book_id=book_id,
        book_title=str(book_info.get("title") or ""),
        include_chapter=include_chapter,
        highlight_style=highlight_style,
        last_updated=now_iso(),
    )

    for highlight in highlights:
        if not highlight.get("markText"):
            continue
        if highlight_style is not None and highlight.get("colorStyle") != highlight_style:
            continue

        chapter_uid: int | None = None
        chapter_title = ""
        if include_chapter:
            chapter_uid_raw = highlight.get("chapterUid")
            chapter_uid = to_int(chapter_uid_raw, 0) or None
            if chapter_uid is not None:
                chapter_title = str(as_dict(chapter_info.get(str(chapter_uid))).get("title") or "未知章节")

        result.marks.append(
            HighlightItem(
                text=str(highlight.get("markText") or ""),
                style=to_int(highlight.get("colorStyle") or highlight.get("style"), 0),
                create_time=to_iso(highlight.get("createTime")),
                chapter_uid=chapter_uid,
                chapter_title=chapter_title,
            )
        )

    result.total_marks_count = len(result.marks)
    result.returned_marks_count = len(result.marks)
    return result


def build_book_reviews_response(
    *,
    book_id: str,
    include_chapter: bool,
    book_info_payload: dict[str, Any],
    chapter_info_payload: dict[str, Any],
    reviews_payload: list[dict[str, Any]],
) -> BookReviewsResponse:
    book_info = as_dict(book_info_payload)
    chapter_info = as_dict(chapter_info_payload)
    reviews = [item for item in as_list(reviews_payload) if isinstance(item, dict)]

    result = BookReviewsResponse(
        book_id=book_id,
        book_title=str(book_info.get("title") or ""),
        include_chapter=include_chapter,
        last_updated=now_iso(),
    )

    for review in reviews:
        if not is_valid_note_review(review):
            continue

        chapter_uid: int | None = None
        chapter_title = ""
        if include_chapter:
            chapter_uid_raw = review.get("chapterUid")
            chapter_uid = to_int(chapter_uid_raw, 0) or None
            if chapter_uid is not None:
                chapter_title = str(as_dict(chapter_info.get(str(chapter_uid))).get("title") or "未知章节")

        result.reviews.append(
            ReviewItem(
                content=extract_note_content(review),
                mark_text=extract_note_mark_text(review),
                create_time=to_iso(review.get("createTime")),
                chapter_uid=chapter_uid,
                chapter_title=chapter_title,
            )
        )

    result.total_reviews_count = len(result.reviews)
    result.returned_reviews_count = len(result.reviews)
    return result


def build_book_notes_response(
    *,
    book_id: str,
    notebook_entry_payload: dict[str, Any] | None,
) -> BookNotesResponse:
    notebook_entry = as_dict(notebook_entry_payload or {})
    mark_count = to_int(notebook_entry.get("noteCount"), 0)
    review_count = to_int(notebook_entry.get("reviewCount"), 0)
    return BookNotesResponse(
        book_id=book_id,
        total_notes=mark_count + review_count,
        mark_count=mark_count,
        review_count=review_count,
        last_updated=now_iso(),
    )


def build_book_marks_and_reviews_response(
    *,
    book_id: str,
    include_chapter: bool,
    highlight_style: int | None,
    book_info_payload: dict[str, Any],
    read_info_payload: dict[str, Any],
    chapter_info_payload: dict[str, Any],
    bookmark_payload: list[dict[str, Any]],
    reviews_payload: list[dict[str, Any]],
) -> BookMarksReivewsResponse:
    book_info = as_dict(book_info_payload)
    read_info = as_dict(read_info_payload)
    chapter_info = as_dict(chapter_info_payload)
    highlights = [item for item in as_list(bookmark_payload) if isinstance(item, dict)]
    reviews = [item for item in as_list(reviews_payload) if isinstance(item, dict)]
    valid_note_reviews = [review for review in reviews if is_valid_note_review(review)]

    by_chapter_mode = include_chapter
    result = BookMarksReivewsResponse(
        mode="by_chapter" if by_chapter_mode else "flat",
        include_chapter=include_chapter,
        highlight_style=highlight_style,
        book_id=book_id,
        book_title=str(book_info.get("title") or ""),
        book_info=_build_notes_book_info(book_info),
        reading_status=_build_notes_reading_status(book_info, read_info),
        total_marks_count=len(highlights),
        total_reviews_count=len(valid_note_reviews),
        returned_marks_count=len(highlights),
        returned_reviews_count=len(valid_note_reviews),
        last_updated=now_iso(),
    )

    if by_chapter_mode:
        chapter_map: dict[str, dict[str, Any]] = {}
        for chapter in chapter_info.values():
            if not isinstance(chapter, dict):
                continue
            chapter_uid = chapter.get("chapterUid")
            if chapter_uid is None:
                continue
            chapter_uid_str = str(chapter_uid)
            chapter_map[chapter_uid_str] = {
                "uid": to_int(chapter_uid, 0),
                "title": str(chapter.get("title") or ""),
                "_level": to_int(chapter.get("level"), 1),
                "_index": to_int(chapter.get("chapterIdx"), 0),
                "children": [],
                "marks": [],
                "reviews": [],
            }

        chapter_levels: dict[int, list[dict[str, Any]]] = {}
        for chapter in chapter_map.values():
            level = to_int(chapter.get("_level"), 1)
            if level not in chapter_levels:
                chapter_levels[level] = []
            chapter_levels[level].append(chapter)

        levels = sorted(chapter_levels.keys())
        root_chapters: list[dict[str, Any]] = []
        if levels:
            top_level = levels[0]
            root_chapters.extend(sorted(chapter_levels[top_level], key=lambda item: item["_index"]))
            for i in range(1, len(levels)):
                current_level = levels[i]
                current_chapters = sorted(chapter_levels[current_level], key=lambda item: item["_index"])
                prev_level = levels[i - 1]
                prev_level_chapters = sorted(chapter_levels[prev_level], key=lambda item: item["_index"])
                for chapter in current_chapters:
                    parent = None
                    for parent_candidate in reversed(prev_level_chapters):
                        if parent_candidate["_index"] < chapter["_index"]:
                            parent = parent_candidate
                            break
                    if parent is not None:
                        parent["children"].append(chapter)
                    else:
                        root_chapters.append(chapter)

        for highlight in highlights:
            if not highlight.get("markText"):
                continue
            chapter_uid = highlight.get("chapterUid")
            if not chapter_uid:
                continue
            if highlight_style is not None and highlight.get("colorStyle") != highlight_style:
                continue

            highlight_item = HighlightItem(
                text=str(highlight.get("markText") or ""),
                style=to_int(highlight.get("colorStyle") or highlight.get("style"), 0),
                create_time=to_iso(highlight.get("createTime")),
            )
            chapter = chapter_map.get(str(chapter_uid))
            if chapter:
                chapter["marks"].append(highlight_item)
            else:
                result.uncategorized.marks.append(highlight_item)

        for review in valid_note_reviews:
            chapter_uid = review.get("chapterUid")
            note_item = ReviewItem(
                content=extract_note_content(review),
                mark_text=extract_note_mark_text(review),
                create_time=to_iso(review.get("createTime")),
            )
            chapter = chapter_map.get(str(chapter_uid)) if chapter_uid else None
            if chapter is not None:
                chapter["reviews"].append(note_item)
            else:
                result.uncategorized.reviews.append(note_item)

        def clean_and_remove_empty(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
            cleaned: list[dict[str, Any]] = []
            for chapter in chapters:
                chapter.pop("_level", None)
                chapter.pop("_index", None)
                children = chapter.get("children", [])
                if isinstance(children, list):
                    chapter["children"] = clean_and_remove_empty(children)

                has_marks = bool(chapter.get("marks"))
                has_reviews = bool(chapter.get("reviews"))
                has_children = bool(chapter.get("children"))
                if has_marks or has_reviews or has_children:
                    cleaned.append(chapter)
            return cleaned

        cleaned_chapters = clean_and_remove_empty(root_chapters)
        result.chapters = [ChapterNode.model_validate(chapter) for chapter in cleaned_chapters]
        return result

    for highlight in highlights:
        if not highlight.get("markText") or not highlight.get("chapterUid"):
            continue
        if highlight_style is not None and highlight.get("colorStyle") != highlight_style:
            continue

        chapter_uid = to_int(highlight.get("chapterUid"), 0)
        chapter_title = str(as_dict(chapter_info.get(str(chapter_uid))).get("title") or "未知章节")
        result.marks.append(
            HighlightItem(
                text=str(highlight.get("markText") or ""),
                style=to_int(highlight.get("colorStyle") or highlight.get("style"), 0),
                create_time=to_iso(highlight.get("createTime")),
                chapter_uid=chapter_uid,
                chapter_title=chapter_title,
            )
        )

    for review in valid_note_reviews:
        chapter_uid_raw = review.get("chapterUid")
        chapter_uid = to_int(chapter_uid_raw, 0) if chapter_uid_raw else 0
        chapter_uid_value = chapter_uid if chapter_uid > 0 else None
        chapter_title = ""
        if chapter_uid_value is not None:
            chapter_title = str(as_dict(chapter_info.get(str(chapter_uid_value))).get("title") or "未知章节")
        result.reviews.append(
            ReviewItem(
                content=extract_note_content(review),
                mark_text=extract_note_mark_text(review),
                create_time=to_iso(review.get("createTime")),
                chapter_uid=chapter_uid_value,
                chapter_title=chapter_title,
            )
        )

    return result
