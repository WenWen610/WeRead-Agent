from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_note_content(review: dict[str, Any]) -> str:
    return str(review.get("content") or "").strip()


def extract_note_mark_text(review: dict[str, Any]) -> str:
    # 新老字段兼容：上游可能返回 highlight_text /  abstract。
    return str(review.get("abstract") or review.get("highlight_text") or "").strip()


def is_valid_note_review(review: dict[str, Any]) -> bool:
    # 当前业务定义：note 必须绑定一段被划线的原文。
    return bool(extract_note_content(review) and extract_note_mark_text(review))


def extract_best_review_content(review: dict[str, Any]) -> str:
    return str(review.get("content") or review.get("htmlContent") or "").strip()


def extract_best_review_rating(review: dict[str, Any]) -> float:
    if review.get("star"):
        return float(to_int(review.get("star"), 0)) / 20
    if review.get("newRatingLevel") is not None:
        level = to_int(review.get("newRatingLevel"), 0)
        if level == 1:
            return 5.0
        if level == 2:
            return 3.0
        if level == 3:
            return 1.0
    return 0.0
