import json
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

from app.features.weread.models import BookMarkResponse, BookReviewsResponse, HighlightItem, ReviewItem


def _load_weread_local_document_store_module():
    fake_services = types.ModuleType("app.services")
    fake_services.__path__ = []

    sys.modules["app.services"] = fake_services
    sys.modules.pop("app.features.weread.stores.document_store", None)

    module_name = "app.features.weread.stores.document_store"
    module_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "features"
        / "weread"
        / "stores"
        / "document_store.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_save_marks_document_writes_markdown_and_meta(tmp_path) -> None:
    store_module = _load_weread_local_document_store_module()
    store = store_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    document = BookMarkResponse(
        book_id="book-1",
        book_title="Sample Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=1,
        marks=[
            HighlightItem(
                text="One useful highlight.",
                style=2,
                create_time="2026-03-15T09:00:00Z",
                chapter_uid=11,
                chapter_title="Chapter One",
            )
        ],
        last_updated="2026-03-15T09:00:00Z",
    )

    result = asyncio.run(store.save_marks_document(user_id=123, document=document))
    book_dir = tmp_path / "weread" / "users" / "123" / "books" / "book-1"
    markdown_path = book_dir / "marks.md"
    meta_path = book_dir / "marks.meta.json"

    assert result.status == "created"
    assert markdown_path.exists()
    assert meta_path.exists()
    assert result.metadata.file_path == "weread/users/123/books/book-1/marks.md"
    markdown_content = markdown_path.read_text(encoding="utf-8")
    meta_content = meta_path.read_text(encoding="utf-8")
    meta_payload = json.loads(meta_content)

    assert 'source_type: "marks"' in markdown_content
    assert "# Sample Book" in markdown_content
    assert "## Chapter: Chapter One" in markdown_content
    assert "### Highlight 001" in markdown_content
    assert "One useful highlight." in markdown_content
    assert "- style:" not in markdown_content
    assert "- create_time:" not in markdown_content
    assert "- chapter_uid:" not in markdown_content
    assert meta_payload["source_type"] == "marks"
    assert meta_payload["item_count"] == 1
    assert meta_payload["items"][0]["item_fingerprint"]
    assert meta_payload["items"][0]["style"] == 2
    assert meta_payload["items"][0]["create_time"] == "2026-03-15T09:00:00Z"
    assert meta_payload["items"][0]["chapter_uid"] == 11

    unchanged = asyncio.run(store.save_marks_document(user_id=123, document=document))

    assert unchanged.status == "unchanged"
    assert unchanged.metadata.content_hash == result.metadata.content_hash


def test_save_reviews_document_writes_markdown_and_meta(tmp_path) -> None:
    store_module = _load_weread_local_document_store_module()
    store = store_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    document = BookReviewsResponse(
        book_id="book-2",
        book_title="Review Book",
        include_chapter=True,
        total_reviews_count=1,
        reviews=[
            ReviewItem(
                content="This note connects the chapter to a broader idea.",
                mark_text="A key quoted sentence.",
                create_time="2026-03-15T10:00:00Z",
                chapter_uid=22,
                chapter_title="Chapter Two",
            )
        ],
        last_updated="2026-03-15T10:00:00Z",
    )

    result = asyncio.run(store.save_reviews_document(user_id=456, document=document))
    book_dir = tmp_path / "weread" / "users" / "456" / "books" / "book-2"
    markdown_path = book_dir / "reviews.md"
    meta_path = book_dir / "reviews.meta.json"

    assert result.status == "created"
    assert markdown_path.exists()
    assert meta_path.exists()
    markdown_content = markdown_path.read_text(encoding="utf-8")
    meta_content = meta_path.read_text(encoding="utf-8")
    meta_payload = json.loads(meta_content)

    assert 'source_type: "reviews"' in markdown_content
    assert "# Review Book" in markdown_content
    assert "## Chapter: Chapter Two" in markdown_content
    assert "### Review 001" in markdown_content
    assert "> A key quoted sentence." in markdown_content
    assert "A key quoted sentence." in markdown_content
    assert "This note connects the chapter to a broader idea." in markdown_content
    assert "- create_time:" not in markdown_content
    assert "- chapter_uid:" not in markdown_content
    assert meta_payload["source_type"] == "reviews"
    assert meta_payload["items"][0]["item_fingerprint"]
    assert meta_payload["items"][0]["create_time"] == "2026-03-15T10:00:00Z"
    assert meta_payload["items"][0]["chapter_uid"] == 22


def test_extract_note_chunks_keeps_stable_chunk_ids_for_unchanged_marks_after_append(tmp_path) -> None:
    store_module = _load_weread_local_document_store_module()
    store = store_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    initial_document = BookMarkResponse(
        book_id="book-3",
        book_title="Incremental Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=2,
        marks=[
            HighlightItem(
                text="第一条划线。",
                style=2,
                create_time="2026-03-15T09:00:00Z",
                chapter_uid=31,
                chapter_title="Chapter One",
            ),
            HighlightItem(
                text="第二条划线。",
                style=1,
                create_time="2026-03-15T09:10:00Z",
                chapter_uid=31,
                chapter_title="Chapter One",
            ),
        ],
        last_updated="2026-03-15T09:10:00Z",
    )

    initial_result = asyncio.run(store.save_marks_document(user_id=123, document=initial_document))
    initial_extracted = store.extract_note_chunks(user_id=123, doc_id=initial_result.metadata.doc_id)

    assert initial_extracted is not None
    _, initial_chunks = initial_extracted

    updated_document = BookMarkResponse(
        book_id="book-3",
        book_title="Incremental Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=3,
        marks=[
            *initial_document.marks,
            HighlightItem(
                text="第三条新增划线。",
                style=2,
                create_time="2026-03-15T09:20:00Z",
                chapter_uid=31,
                chapter_title="Chapter One",
            ),
        ],
        last_updated="2026-03-15T09:20:00Z",
    )
    updated_result = asyncio.run(store.save_marks_document(user_id=123, document=updated_document))
    updated_extracted = store.extract_note_chunks(user_id=123, doc_id=updated_result.metadata.doc_id)

    assert updated_extracted is not None
    _, updated_chunks = updated_extracted
    assert [chunk.chunk_id for chunk in initial_chunks] == [chunk.chunk_id for chunk in updated_chunks[:2]]
    assert len({chunk.chunk_id for chunk in updated_chunks}) == 3
