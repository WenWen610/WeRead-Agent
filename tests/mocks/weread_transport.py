"""WeRead HTTP transport mock — intercepts httpx requests and returns fixture data.

This module replaces the real WeRead HTTP layer with deterministic fixture responses
so that integration tests never require real WeRead credentials or network access.

Architecture:
    WeReadApi._ensure_initialized()
        → httpx.AsyncClient(transport=MockTransport(WEREAD_ROUTER))
                                                  ↑
                                     WEREAD_ROUTER matches URL patterns
                                     and returns fixture JSON from disk.

Fixture files live under tests/fixtures/weread/ and use naming conventions:
    - Parameterless endpoints: {endpoint_name}.json
    - Book-specific endpoints:  {endpoint_name}_{bookId}.json
    - Edge cases:               edge_{scenario}.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

WEREAD_HOST = "weread.qq.com"
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "weread"
EVAL_FIXTURE_DIR = FIXTURE_DIR / "eval"


def _params(request: httpx.Request) -> dict[str, str]:
    """Extract flattened query params from a request URL."""
    parsed = urlparse(str(request.url))
    return dict(parse_qsl(parsed.query))


def _resolve_fixture(url_path: str, method: str, req_params: dict[str, str]) -> dict[str, Any] | None:
    """Match an incoming request to a fixture file and return its parsed JSON.

    Matching rules (evaluated in order):
    1.  /web/shelf/sync                                   → shelf_sync.json
    2.  /api/user/notebook                                → notebook.json
    3.  /api/book/info          ?bookId=xxx               → book_info_{bookId}.json
    4.  /web/book/bookmarklist  ?bookId=xxx               → bookmark_list_{bookId}.json
    5.  /web/review/list        ?bookId=xxx               → review_list_{bookId}.json
    6.  /web/book/getProgress   ?bookId=xxx               → read_info_{bookId}.json
    7.  /web/book/chapterInfos  (POST with json body)     → chapter_info_{bookId}.json
    8.  /web/review/list/best   ?bookId=xxx               → best_reviews_{bookId}.json
    9.  / (homepage)                                      → homepage.json or 200 OK

    Fixture priority: eval/ subdirectory first, then main directory.
    """
    _ = method

    # (1) Shelf sync
    if url_path.rstrip("/") == "/web/shelf/sync":
        return _load_fixture("shelf_sync")

    # (2) Notebook
    if url_path.rstrip("/") == "/api/user/notebook":
        return _load_fixture("notebook")

    book_id = req_params.get("bookId", req_params.get("bookid", ""))

    # (3) Book info
    if url_path.rstrip("/") == "/api/book/info":
        return _load_fixture(f"book_info_{book_id}") if book_id else None

    # (4) Bookmark list
    if url_path.rstrip("/") == "/web/book/bookmarklist":
        return _load_fixture(f"bookmark_list_{book_id}") if book_id else None

    # (5) Review list
    if url_path.rstrip("/") == "/web/review/list":
        return _load_fixture(f"review_list_{book_id}") if book_id else None

    # (6) Reading progress
    if url_path.rstrip("/") == "/web/book/getProgress":
        return _load_fixture(f"read_info_{book_id}") if book_id else None

    # (7) Chapter info (POST)
    if url_path.rstrip("/") == "/web/book/chapterInfos":
        return _load_fixture(f"chapter_info_{book_id}") if book_id else None

    # (8) Best reviews
    if url_path.rstrip("/") == "/web/review/list/best":
        return _load_fixture(f"best_reviews_{book_id}") if book_id else None

    # (9) Homepage — return empty 200
    if url_path.rstrip("/") in ("", "/"):
        return {}

    return None


def _load_fixture(name: str) -> dict[str, Any] | None:
    """Load and parse a fixture JSON file.

    Checks eval/ subdirectory first (for evaluation-specific synthetic data),
    then falls back to the main fixture directory (real recorded data).
    """
    eval_path = (EVAL_FIXTURE_DIR / f"{name}.json").resolve()
    if eval_path.exists():
        return _parse_json(eval_path.read_text(encoding="utf-8"))

    path = (FIXTURE_DIR / f"{name}.json").resolve()
    if not path.exists():
        return None
    return _parse_json(path.read_text(encoding="utf-8"))


def _parse_json(raw: str) -> dict[str, Any]:
    import json

    result = json.loads(raw)
    return result if isinstance(result, dict) else {}


def _weread_handler(request: httpx.Request) -> httpx.Response:
    """Main handler for httpx.MockTransport — match request to fixture."""
    parsed = urlparse(str(request.url))
    fixture = _resolve_fixture(parsed.path, request.method, _params(request))

    if fixture is not None:
        return httpx.Response(200, json=fixture)

    return httpx.Response(404, json={"error": f"no fixture for {parsed.path}"})


WEREAD_TRANSPORT = httpx.MockTransport(_weread_handler)


def make_weread_mocked_client() -> httpx.AsyncClient:
    """Create an httpx.AsyncClient that returns fixture data for weread.qq.com.

    Use this to replace the client created by WeReadApi._ensure_initialized().
    """
    return httpx.AsyncClient(
        transport=WEREAD_TRANSPORT,
        timeout=httpx.Timeout(30.0),
    )
