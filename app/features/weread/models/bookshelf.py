from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadingStatus(BaseModel):
    unreadBooks: int = 0
    readingBooks: int = 0
    finishedBooks: int = 0


class BookSource(BaseModel):
    importedBooks: int = 0
    wereadBooks: int = 0


class MainCategory(BaseModel):
    category: str
    count: int


class CategoriesStats(BaseModel):
    categoryStats: dict[str, int] = Field(default_factory=dict)
    mainCategories: list[MainCategory] = Field(default_factory=list)


class Stats(BaseModel):
    totalBooks: int = 0
    readingStatus: ReadingStatus
    bookSource: BookSource
    paidBooks: int = 0
    booksWithNotes: int = 0
    categories: CategoriesStats


class BooklistStatItem(BaseModel):
    name: str | None = None
    count: int = 0


class BooklistStats(BaseModel):
    totalCategories: int = 0
    largestCategory: BooklistStatItem | None = None
    smallestCategory: BooklistStatItem | None = None


class BooklistItem(BaseModel):
    name: str | None = None
    id: Any = None
    bookCount: int = 0


class BookItem(BaseModel):
    bookId: str = ""
    title: str = ""
    author: str = ""
    translator: str = ""
    categories: list[str] = Field(default_factory=list)
    bookLists: list[str] = Field(default_factory=list)
    publishTime: str = ""
    finishReading: bool = False
    price: float | int = 0
    paid: bool = False
    isImported: bool = False
    progress: int = 0
    readingTime: int = 0
    readingTimeFormatted: str = ""
    updateTime: str = ""
    noteCount: int = 0
    reviewCount: int = 0
    bookmarkCount: int = 0


class BookshelfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stats: Stats
    booklistStats: BooklistStats
    booklists: list[BooklistItem] = Field(default_factory=list)
    books: list[BookItem] = Field(default_factory=list)


class SnapshotBookItem(BaseModel):
    book_id: str = ""
    title: str = ""
    author: str = ""
    translator: str = ""
    cover: str = ""
    format: str = ""
    categories: list[str] = Field(default_factory=list)
    book_lists: list[str] = Field(default_factory=list)
    publish_time: str = ""
    finish_reading: bool = False
    paid: bool = False
    is_imported: bool = False
    price: float | int = 0
    progress: int = 0
    reading_time: int = 0
    reading_time_formatted: str = ""
    last_read_time: str = ""


class BookshelfSearchItem(BaseModel):
    book_id: str = ""
    title: str = ""
    author: str = ""
    translator: str = ""
    categories: list[str] = Field(default_factory=list)
    book_lists: list[str] = Field(default_factory=list)
    progress: int = 0
    finish_reading: bool = False
    reading_time_formatted: str = ""
    last_read_time: str = ""
    matched_on: list[str] = Field(default_factory=list)


class BookshelfSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_key: int = 0
    lecture_sync_key: int = 0
    pure_book_count: int = 0
    book_count: int = 0
    generated_at: str = ""
    books: list[SnapshotBookItem] = Field(default_factory=list)


class BookshelfOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_key: int = 0
    lecture_sync_key: int = 0
    pure_book_count: int = 0
    book_count: int = 0
    generated_at: str = ""
    unread_books_count: int = 0
    reading_books_count: int = 0
    finished_books_count: int = 0
    recent_books: list[SnapshotBookItem] = Field(default_factory=list)
    currently_reading_books: list[SnapshotBookItem] = Field(default_factory=list)
    main_categories: list[MainCategory] = Field(default_factory=list)


class BookshelfSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = ""
    exact_match: bool = False
    total_matches: int = 0
    max_results: int = 0
    sync_key: int = 0
    generated_at: str = ""
    books: list[BookshelfSearchItem] = Field(default_factory=list)


class ResolveWereadBookResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = ""
    exact_match: bool = False
    status: Literal["resolved", "multiple_candidates", "not_found"] = "not_found"
    recommended_action: Literal["proceed_with_detail_tool", "ask_clarification", "reply_not_found"] = (
        "reply_not_found"
    )
    message: str = ""
    total_matches: int = 0
    sync_key: int = 0
    generated_at: str = ""
    book: BookshelfSearchItem | None = None
    candidates: list[BookshelfSearchItem] = Field(default_factory=list)
