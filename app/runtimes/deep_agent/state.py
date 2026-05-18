"""Minimal state types for the Deep Agent runtime.

Only defines the state fields actually used by deep_agent's middleware.
"""

from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class BookReferenceState(TypedDict):
    book_id: str
    title: NotRequired[str | None]
    author: NotRequired[str | None]
    translator: NotRequired[str | None]


def _take_non_none(existing, new):
    return new if new is not None else existing


def _merge_recent_books(
    existing: list[BookReferenceState] | None,
    new: list[BookReferenceState] | None,
) -> list[BookReferenceState]:
    merged = list(existing) if existing else []
    seen = {b.get("book_id") for b in merged}
    for book in (new or []):
        bid = book.get("book_id")
        if bid and bid not in seen:
            merged.append(book)
            seen.add(bid)
    return merged


class ThreadState(AgentState):
    current_book: NotRequired[Annotated[BookReferenceState | None, _take_non_none]]
    pending_clarification: NotRequired[Annotated[dict | None, _take_non_none]]
    pending_capability_requirement: NotRequired[Annotated[dict | None, _take_non_none]]
    recent_books: NotRequired[Annotated[list[BookReferenceState], _merge_recent_books]]
    artifacts: NotRequired[list[dict]]
