from __future__ import annotations

from typing import Any

from ..models import BestReviewItem, BookBestReviewsResponse
from .bookshelf import to_iso
from .common import (
    as_dict,
    as_list,
    extract_best_review_content,
    extract_best_review_rating,
    now_iso,
    to_int,
)


def build_book_best_reviews_response(
    *,
    book_id: str,
    book_info_payload: dict[str, Any],
    best_reviews_payload: dict[str, Any],
) -> BookBestReviewsResponse:
    book_info = as_dict(book_info_payload)
    best_reviews_data = as_dict(best_reviews_payload)

    processed_reviews: list[BestReviewItem] = []
    for item in as_list(best_reviews_data.get("reviews")):
        if not isinstance(item, dict):
            continue
        review_container = as_dict(item.get("review"))
        review = as_dict(review_container.get("review"))
        if not review:
            continue

        content = extract_best_review_content(review)
        if not content:
            continue

        author = as_dict(review.get("author"))
        processed_reviews.append(
            BestReviewItem(
                review_id=str(review_container.get("reviewId") or ""),
                content=content,
                rating=extract_best_review_rating(review),
                likes=to_int(review.get("liked") or review_container.get("likesCount"), 0),
                comments=to_int(review.get("comments"), 0),
                created_time=to_iso(review.get("createTime")),
                author_nickname=str(author.get("name") or ""),
                is_spoiler=bool(review.get("notVisibleToFriends")),
                is_top=to_int(item.get("idx"), 0) == 1 or bool(item.get("isTop")),
            )
        )

    return BookBestReviewsResponse(
        book_id=book_id,
        book_title=str(book_info.get("title") or ""),
        book_author=str(book_info.get("author") or ""),
        total_reviews=to_int(best_reviews_data.get("reviewsCnt"), 0),
        has_more=bool(best_reviews_data.get("reviewsHasMore", False)),
        sync_key=to_int(best_reviews_data.get("synckey"), 0),
        reviews=processed_reviews,
        last_updated=now_iso(),
    )
