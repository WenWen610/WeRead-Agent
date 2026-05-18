from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import (
    BookItem,
    BooklistItem,
    BooklistStatItem,
    BooklistStats,
    BookSource,
    BookshelfResponse,
    CategoriesStats,
    MainCategory,
    BookshelfSnapshotResponse,
    RawEntireShelfResponse,
    ReadingStatus,
    SnapshotBookItem,
    Stats,
)


def format_reading_time(seconds: int) -> str:
    if not seconds:
        return "0分钟"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        if minutes > 0:
            return f"{hours}小时{minutes}分钟"
        return f"{hours}小时"
    return f"{minutes}分钟"


def to_iso(seconds: Any) -> str:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# def build_bookshelf_response(
#     entire_shelf_payload: dict[str, Any],
#     notebook_payload: dict[str, Any],
# ) -> BookshelfResponse:
#     # 1) 先把上游原始响应解析为强类型模型。
#     # Raw 模型使用 extra="ignore"，上游多余字段会被自动忽略。
#     entire_shelf = RawEntireShelfResponse.model_validate(entire_shelf_payload)
#     notebook = RawNotebookResponse.model_validate(notebook_payload)

#     # 2) 先构建查询映射，避免后续重复 O(n) 扫描。
#     progress_map = {item.bookId: item for item in entire_shelf.bookProgress if item.bookId}
#     notebook_map = {item.bookId: item for item in notebook.books if item.bookId}

#     # 3) 统计整书架阅读状态：未读/在读/读完。
#     total_books = len(entire_shelf.books)
#     unread_books = 0
#     reading_books = 0
#     finished_books = 0

#     for book in entire_shelf.books:
#         progress_info = progress_map.get(book.bookId)
#         progress = progress_info.progress if progress_info else 0
#         finish_reading = int(book.finishReading) == 1
#         if finish_reading:
#             finished_books += 1
#         elif progress == 0:
#             unread_books += 1
#         else:
#             reading_books += 1

#     # 4) 统计来源与付费信息，以及“有笔记的书籍数”。
#     imported_books = sum(1 for book in entire_shelf.books if book.bookId.startswith("CB_"))
#     weread_books = total_books - imported_books
#     paid_books = sum(1 for book in entire_shelf.books if int(book.paid) == 1)
#     books_with_notes = len(notebook.books)

#     # 5) 统计分类词频（按每本书的 categories 聚合）。
#     category_stats: dict[str, int] = {}
#     for book in entire_shelf.books:
#         for category in book.categories:
#             title = category.title or "unknown"
#             category_stats[title] = category_stats.get(title, 0) + 1

#     # 提取出现频率最高的前 5 个分类，用于摘要展示。
#     main_categories = [
#         MainCategory(category=category, count=count)
#         for category, count in sorted(category_stats.items(), key=lambda item: item[1], reverse=True)[:5]
#     ]

#     # 6) 构建书单统计（最大/最小书单、书单列表）。
#     category_counts = [
#         BooklistStatItem(name=archive.name, count=len(archive.bookIds)) for archive in entire_shelf.archive
#     ]
#     sorted_categories = sorted(category_counts, key=lambda item: item.count, reverse=True)
#     largest_category = sorted_categories[0] if sorted_categories else None
#     smallest_category = sorted_categories[-1] if sorted_categories else None

#     booklists = [
#         BooklistItem(name=archive.name, id=archive.archiveId, bookCount=len(archive.bookIds))
#         for archive in entire_shelf.archive
#     ]

#     # 7) 映射每本书的输出结构（统一字段与格式）。
#     books: list[BookItem] = []
#     for book in entire_shelf.books:
#         progress_info = progress_map.get(book.bookId)
#         notebook_info = notebook_map.get(book.bookId)

#         # 找出该书所属的书单名称。
#         belong_categories = [
#             archive.name for archive in entire_shelf.archive if book.bookId in archive.bookIds and archive.name
#         ]

#         reading_time = progress_info.readingTime if progress_info else 0
#         update_time = progress_info.updateTime if progress_info else 0
#         # 输出里仅保留分类标题列表。
#         categories = [cat.title for cat in book.categories if cat.title]

#         books.append(
#             BookItem(
#                 bookId=book.bookId,
#                 title=book.title,
#                 author=book.author,
#                 translator=book.translator,
#                 categories=categories,
#                 bookLists=belong_categories,
#                 publishTime=book.publishTime,
#                 finishReading=int(book.finishReading) == 1,
#                 price=book.price,
#                 paid=int(book.paid) == 1,
#                 isImported=book.bookId.startswith("CB_"),
#                 progress=progress_info.progress if progress_info else 0,
#                 readingTime=reading_time,
#                 readingTimeFormatted=format_reading_time(reading_time),
#                 updateTime=to_iso(update_time),
#                 noteCount=notebook_info.noteCount if notebook_info else 0,
#                 reviewCount=notebook_info.reviewCount if notebook_info else 0,
#                 bookmarkCount=notebook_info.bookmarkCount if notebook_info else 0,
#             )
#         )

#     # 8) 组装最终 MCP 返回模型（严格输出契约）。
#     return BookshelfResponse(
#         # 书架总体统计（总数、阅读状态、来源、付费、分类概览）。
#         stats=Stats(
#             totalBooks=total_books,
#             readingStatus=ReadingStatus(
#                 unreadBooks=unread_books,
#                 readingBooks=reading_books,
#                 finishedBooks=finished_books,
#             ),
#             bookSource=BookSource(importedBooks=imported_books, wereadBooks=weread_books),
#             paidBooks=paid_books,
#             booksWithNotes=books_with_notes,
#             categories=CategoriesStats(
#                 categoryStats=category_stats,
#                 mainCategories=main_categories,
#             ),
#         ),
#         # 书单层面的统计（书单总数、最大/最小书单）。
#         booklistStats=BooklistStats(
#             totalCategories=len(entire_shelf.archive),
#             largestCategory=largest_category,
#             smallestCategory=smallest_category,
#         ),
#         # 书单明细列表。
#         booklists=booklists,
#         # 每本书的标准化明细列表。
#         books=books,
#     )


def build_bookshelf_snapshot(
    entire_shelf_payload: dict[str, Any],
) -> BookshelfSnapshotResponse:
    entire_shelf = RawEntireShelfResponse.model_validate(entire_shelf_payload)

    progress_map = {item.bookId: item for item in entire_shelf.bookProgress if item.bookId}

    books: list[SnapshotBookItem] = []
    for book in entire_shelf.books:
        progress_info = progress_map.get(book.bookId)

        belong_categories = [
            archive.name for archive in entire_shelf.archive if book.bookId in archive.bookIds and archive.name
        ]
        categories = [cat.title for cat in book.categories if cat.title]
        reading_time = progress_info.readingTime if progress_info else 0
        last_read_time = progress_info.updateTime if progress_info else 0

        books.append(
            SnapshotBookItem(
                book_id=book.bookId,
                title=book.title,
                author=book.author,
                translator=book.translator,
                cover=book.cover,
                format=book.format,
                categories=categories,
                book_lists=belong_categories,
                publish_time=book.publishTime,
                finish_reading=int(book.finishReading) == 1,
                paid=int(book.paid) == 1,
                is_imported=book.bookId.startswith("CB_"),
                price=book.price,
                progress=progress_info.progress if progress_info else 0,
                reading_time=reading_time,
                reading_time_formatted=format_reading_time(reading_time),
                last_read_time=to_iso(last_read_time),
            )
        )

    return BookshelfSnapshotResponse(
        sync_key=entire_shelf.synckey,
        lecture_sync_key=entire_shelf.lectureSynckey,
        pure_book_count=entire_shelf.pureBookCount,
        book_count=entire_shelf.bookCount,
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        books=books,
    )
