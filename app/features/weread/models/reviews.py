from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BestReviewItem(BaseModel):
    review_id: str = ""
    content: str = ""
    rating: float = 0.0
    likes: int = 0
    comments: int = 0
    created_time: str = ""
    author_nickname: str = ""
    is_spoiler: bool = False
    is_top: bool = False


class BookBestReviewsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    book_title: str = ""
    book_author: str = ""
    total_reviews: int = 0
    has_more: bool = False
    sync_key: int = 0
    reviews: list[BestReviewItem] = Field(default_factory=list)
    last_updated: str = ""
