from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RawBookCategory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    categoryId: int = 0
    subCategoryId: int = 0
    categoryType: int = 0
    title: str = ""


class RawShelfBook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bookId: str = ""
    title: str = ""
    author: str = ""
    translator: str = ""
    cover: str = ""
    format: str = ""
    category: str = ""
    categories: list[RawBookCategory] = Field(default_factory=list)
    publishTime: str = ""
    finishReading: int = 0
    paid: int = 0
    price: float | int = 0


class RawBookProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bookId: str = ""
    progress: int = 0
    readingTime: int = 0
    updateTime: int = 0


class RawArchive(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    archiveId: Any = None
    bookIds: list[str] = Field(default_factory=list)


class RawNotebookBook(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bookId: str = ""
    noteCount: int = 0
    reviewCount: int = 0
    bookmarkCount: int = 0


class RawEntireShelfResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pureBookCount: int = 0
    bookCount: int = 0
    synckey: int = 0
    lectureSynckey: int = 0
    books: list[RawShelfBook] = Field(default_factory=list)
    bookProgress: list[RawBookProgress] = Field(default_factory=list)
    archive: list[RawArchive] = Field(default_factory=list)


class RawNotebookResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    books: list[RawNotebookBook] = Field(default_factory=list)
