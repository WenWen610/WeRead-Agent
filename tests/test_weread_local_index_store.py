import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app.infrastructure.config import settings
from app.features.weread.models import BookMarkResponse, BookReviewsResponse, HighlightItem, ReviewItem


@pytest.fixture(autouse=True)
def patch_note_embedding_dimensions_for_weread_index_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests use 2-D toy embeddings; align with NOTE_EMBEDDING_DIMENSIONS for vec + BLOB paths."""
    monkeypatch.setattr(settings, "NOTE_EMBEDDING_DIMENSIONS", 2)


def _load_local_store_modules():
    sys.modules.pop("app.infrastructure.chinese_fts_text", None)
    sys.modules.pop("app.features.weread.stores.document_store", None)
    sys.modules.pop("app.features.weread.stores.index_store", None)

    chinese_fts_module_name = "app.infrastructure.chinese_fts_text"
    chinese_fts_module_path = Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "chinese_fts_text.py"
    chinese_fts_spec = importlib.util.spec_from_file_location(chinese_fts_module_name, chinese_fts_module_path)
    chinese_fts_module = importlib.util.module_from_spec(chinese_fts_spec)
    assert chinese_fts_spec is not None and chinese_fts_spec.loader is not None
    sys.modules[chinese_fts_module_name] = chinese_fts_module
    chinese_fts_spec.loader.exec_module(chinese_fts_module)

    document_module_name = "app.features.weread.stores.document_store"
    document_module_path = (
        Path(__file__).resolve().parents[1] / "app" / "features" / "weread" / "stores" / "document_store.py"
    )
    document_spec = importlib.util.spec_from_file_location(document_module_name, document_module_path)
    document_module = importlib.util.module_from_spec(document_spec)
    assert document_spec is not None and document_spec.loader is not None
    sys.modules[document_module_name] = document_module
    document_spec.loader.exec_module(document_module)

    index_module_name = "app.features.weread.stores.index_store"
    index_module_path = Path(__file__).resolve().parents[1] / "app" / "features" / "weread" / "stores" / "index_store.py"
    index_spec = importlib.util.spec_from_file_location(index_module_name, index_module_path)
    index_module = importlib.util.module_from_spec(index_spec)
    assert index_spec is not None and index_spec.loader is not None
    sys.modules[index_module_name] = index_module
    index_spec.loader.exec_module(index_module)
    return document_module, index_module


def test_sync_document_index_and_search_marks(tmp_path) -> None:
    document_module, index_module = _load_local_store_modules()
    document_store = document_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    index_store = index_module.WeReadLocalIndexStore(db_path=tmp_path / "local_knowledge.db")
    document = BookMarkResponse(
        book_id="book-1",
        book_title="Sample Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=2,
        marks=[
            HighlightItem(
                text="这是一段测试划线内容。",
                style=2,
                create_time="2026-03-17T08:00:00Z",
                chapter_uid=11,
                chapter_title="第一章",
            ),
            HighlightItem(
                text="另一条关于教育的划线。",
                style=1,
                create_time="2026-03-17T09:00:00Z",
                chapter_uid=11,
                chapter_title="第一章",
            ),
        ],
        last_updated="2026-03-17T09:00:00Z",
    )

    materialized = asyncio.run(document_store.save_marks_document(user_id=123, document=document))
    extracted = document_store.extract_note_chunks(user_id=123, doc_id=materialized.metadata.doc_id)

    assert extracted is not None
    metadata, chunks = extracted

    indexed = asyncio.run(index_store.sync_document_index(metadata=metadata, chunks=chunks))
    search_result = asyncio.run(
        index_store.search_note_chunks(
            user_id=123,
            book_id="book-1",
            source_type="marks",
            query="测试",
            top_k=5,
        )
    )

    assert indexed.status == "created"
    assert indexed.chunk_count == 2
    assert search_result.total_hits == 1
    assert search_result.hits[0].heading in ("Highlight 001", "Highlight 002")
    assert "测试" in search_result.hits[0].text


def test_extract_note_chunks_splits_long_review_into_multiple_chunks(tmp_path) -> None:
    document_module, _ = _load_local_store_modules()
    document_store = document_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    long_review = "\n\n".join(
        [
            "第一段想法，长度足够长。" * 30,
            "第二段想法，继续补充。" * 30,
        ]
    )
    document = BookReviewsResponse(
        book_id="book-2",
        book_title="Review Book",
        include_chapter=True,
        total_reviews_count=1,
        reviews=[
            ReviewItem(
                content=long_review,
                mark_text="一段很重要的引用。",
                create_time="2026-03-17T10:00:00Z",
                chapter_uid=22,
                chapter_title="第二章",
            )
        ],
        last_updated="2026-03-17T10:00:00Z",
    )

    materialized = asyncio.run(document_store.save_reviews_document(user_id=456, document=document))
    extracted = document_store.extract_note_chunks(
        user_id=456,
        doc_id=materialized.metadata.doc_id,
        max_review_chars=240,
    )

    assert extracted is not None
    _, chunks = extracted
    assert len(chunks) >= 2
    assert chunks[0].heading.startswith("Review 001")
    assert chunks[1].heading.startswith("Review 001 (Part 2)")


def test_search_note_chunks_supports_vector_only_hybrid_retrieval(tmp_path) -> None:
    document_module, index_module = _load_local_store_modules()
    document_store = document_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    index_store = index_module.WeReadLocalIndexStore(db_path=tmp_path / "local_knowledge.db")
    document = BookMarkResponse(
        book_id="book-3",
        book_title="Vector Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=2,
        marks=[
            HighlightItem(
                text="关于控制感的一段划线。",
                style=2,
                create_time="2026-03-17T08:00:00Z",
                chapter_uid=31,
                chapter_title="第一章",
            ),
            HighlightItem(
                text="另一段关于关系的划线。",
                style=1,
                create_time="2026-03-17T09:00:00Z",
                chapter_uid=31,
                chapter_title="第一章",
            ),
        ],
        last_updated="2026-03-17T09:00:00Z",
    )

    materialized = asyncio.run(document_store.save_marks_document(user_id=123, document=document))
    extracted = document_store.extract_note_chunks(user_id=123, doc_id=materialized.metadata.doc_id)

    assert extracted is not None
    metadata, chunks = extracted
    asyncio.run(index_store.sync_document_index(metadata=metadata, chunks=chunks))
    asyncio.run(
        index_store.sync_chunk_embeddings(
            doc_id=metadata.doc_id,
            embed_model="test-embedding-model",
            embeddings={
                chunks[0].chunk_id: [1.0, 0.0],
                chunks[1].chunk_id: [0.0, 1.0],
            },
        )
    )

    search_result = asyncio.run(
        index_store.search_note_chunks(
            user_id=123,
            book_id="book-3",
            source_type="marks",
            query="完全不存在的关键词",
            top_k=3,
            query_embedding=[1.0, 0.0],
        )
    )

    assert search_result.total_hits == 1
    assert search_result.retrieval_mode == "hybrid"
    assert len(search_result.hits) == 1
    assert search_result.hits[0].heading == "Highlight 001"
    assert search_result.hits[0].text_score is None
    assert search_result.hits[0].vector_score is not None
    assert search_result.hits[0].vector_score > 0.99


def test_sync_document_index_preserves_existing_embeddings_for_unchanged_chunks(tmp_path) -> None:
    document_module, index_module = _load_local_store_modules()
    document_store = document_module.WeReadLocalDocumentStore(root_dir=tmp_path)
    index_store = index_module.WeReadLocalIndexStore(db_path=tmp_path / "local_knowledge.db")
    initial_document = BookMarkResponse(
        book_id="book-4",
        book_title="Incremental Index Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=2,
        marks=[
            HighlightItem(
                text="第一条原有划线。",
                style=2,
                create_time="2026-03-17T08:00:00Z",
                chapter_uid=41,
                chapter_title="第一章",
            ),
            HighlightItem(
                text="第二条原有划线。",
                style=1,
                create_time="2026-03-17T09:00:00Z",
                chapter_uid=41,
                chapter_title="第一章",
            ),
        ],
        last_updated="2026-03-17T09:00:00Z",
    )

    initial_materialized = asyncio.run(document_store.save_marks_document(user_id=123, document=initial_document))
    initial_extracted = document_store.extract_note_chunks(user_id=123, doc_id=initial_materialized.metadata.doc_id)

    assert initial_extracted is not None
    metadata, initial_chunks = initial_extracted
    asyncio.run(index_store.sync_document_index(metadata=metadata, chunks=initial_chunks))
    asyncio.run(
        index_store.sync_chunk_embeddings(
            doc_id=metadata.doc_id,
            embed_model="test-embedding-model",
            embeddings={
                initial_chunks[0].chunk_id: [1.0, 0.0],
                initial_chunks[1].chunk_id: [0.0, 1.0],
            },
        )
    )

    updated_document = BookMarkResponse(
        book_id="book-4",
        book_title="Incremental Index Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=3,
        marks=[
            *initial_document.marks,
            HighlightItem(
                text="第三条新增划线。",
                style=2,
                create_time="2026-03-17T10:00:00Z",
                chapter_uid=41,
                chapter_title="第一章",
            ),
        ],
        last_updated="2026-03-17T10:00:00Z",
    )

    updated_materialized = asyncio.run(document_store.save_marks_document(user_id=123, document=updated_document))
    updated_extracted = document_store.extract_note_chunks(user_id=123, doc_id=updated_materialized.metadata.doc_id)

    assert updated_extracted is not None
    updated_metadata, updated_chunks = updated_extracted
    indexed = asyncio.run(index_store.sync_document_index(metadata=updated_metadata, chunks=updated_chunks))
    missing_chunk_ids = asyncio.run(index_store.get_chunks_missing_embeddings(doc_id=updated_metadata.doc_id))

    assert indexed.status == "updated"
    assert indexed.chunk_count == 3
    assert indexed.embedding_status == "pending"
    assert [chunk.chunk_id for chunk in initial_chunks] == [chunk.chunk_id for chunk in updated_chunks[:2]]
    assert missing_chunk_ids == [updated_chunks[2].chunk_id]

    with index_store._connect() as connection:
        embedding_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM note_chunk_embeddings WHERE doc_id = ?",
                (updated_metadata.doc_id,),
            ).fetchone()["count"]
        )

    assert embedding_count == 2
