import importlib.util
import sys
import types
import asyncio
from pathlib import Path

from app.features.weread.models import (
    BookNotesResponse,
    BookshelfSnapshotResponse,
    SnapshotBookItem,
    WeReadLocalDocumentIndexState,
    WeReadLocalDocumentIndexResponse,
    WeReadLocalDocumentMetadata,
    WeReadLocalNoteChunk,
    WeReadLocalNoteChunkHit,
    WeReadLocalNoteChunkSearchResponse,
)


def _build_snapshot(book_id: str, title: str, author: str, *, sync_key: int) -> BookshelfSnapshotResponse:
    return BookshelfSnapshotResponse(
        sync_key=sync_key,
        lecture_sync_key=0,
        pure_book_count=1,
        book_count=1,
        generated_at="2026-03-14T10:00:00Z",
        books=[
            SnapshotBookItem(
                book_id=book_id,
                title=title,
                author=author,
                translator="",
                cover="",
                format="epub",
                categories=[],
                book_lists=[],
                publish_time="",
                finish_reading=False,
                paid=True,
                is_imported=False,
                price=0,
                progress=0,
                reading_time=0,
                reading_time_formatted="0分钟",
                last_read_time="2026-03-14T09:00:00Z",
            )
        ],
    )


def _load_weread_service_module(monkeypatch):
    fake_services = types.ModuleType("app.services")
    fake_services.__path__ = []

    fake_weread_auth = types.ModuleType("app.features.weread.services.auth")

    class _FakeBindingRequiredError(RuntimeError):
        pass

    class _FakeBindingError(RuntimeError):
        pass

    class _FakeReauthRequiredError(RuntimeError):
        pass

    class _FakeWeReadAuthService:
        async def get_binding(self, user_id: int):
            _ = user_id
            return None

        async def require_active_cookie(self, user_id: int):
            _ = user_id
            raise _FakeBindingRequiredError("binding_required")

        async def mark_cookie_valid(self, user_id: int):
            _ = user_id
            return None

        async def mark_expired(self, user_id: int, last_error: str | None = None):
            _ = (user_id, last_error)
            return None

    fake_weread_auth.WeReadAuthService = _FakeWeReadAuthService
    fake_weread_auth.WeReadBindingError = _FakeBindingError
    fake_weread_auth.WeReadBindingRequiredError = _FakeBindingRequiredError
    fake_weread_auth.WeReadReauthRequiredError = _FakeReauthRequiredError
    fake_weread_auth.weread_auth_service = _FakeWeReadAuthService()

    fake_local_store = types.ModuleType("app.features.weread.stores.bookshelf_store")
    fake_local_store.weread_local_store = types.SimpleNamespace(
        get_bookshelf_snapshot=None,
        upsert_bookshelf_snapshot=None,
        list_recent_books=None,
        clear_user_bookshelf=None,
    )

    fake_local_document_store = types.ModuleType("app.features.weread.stores.document_store")
    fake_local_document_store.weread_local_document_store = types.SimpleNamespace(
        save_marks_document=None,
        save_reviews_document=None,
        build_doc_id=lambda *, user_id, book_id, source_type, include_chapter=True, highlight_style=None: (
            f"weread:{user_id}:{book_id}:{source_type}"
            + ("" if include_chapter else ":flat")
            + (f":style_{highlight_style}" if highlight_style is not None else "")
        ),
        build_artifact=lambda metadata: {
            "artifact_id": metadata.doc_id,
            "kind": "weread_markdown",
            "doc_id": metadata.doc_id,
            "book_id": metadata.book_id,
            "book_title": metadata.book_title,
            "source_type": metadata.source_type,
            "name": Path(metadata.file_path).name,
            "generated_at": metadata.generated_at,
        },
        resolve_document_path=None,
        get_document_metadata=None,
        extract_note_chunks=None,
    )
    fake_local_index_store = types.ModuleType("app.features.weread.stores.index_store")
    fake_local_index_store.weread_local_index_store = types.SimpleNamespace(
        sync_document_index=None,
        sync_chunk_embeddings=None,
        get_chunks_missing_embeddings=None,
        get_document_index_state=None,
        search_note_chunks=None,
    )
    fake_note_embedding_service = types.ModuleType("app.features.weread.indexing.embeddings")
    fake_note_embedding_service.note_embedding_service = types.SimpleNamespace(
        embed_texts=None,
        is_enabled=lambda: False,
        get_model_name=lambda: "test-note-embedding-model",
    )
    fake_index_job_manager = types.ModuleType("app.features.weread.indexing.index_job_manager")
    fake_index_job_manager.weread_index_job_manager = types.SimpleNamespace(
        schedule_index=None,
        get_inflight_task=None,
        get_running_task=None,
        wait_for_doc=None,
        await_inflight_task=None,
        await_running_task=None,
        reset=None,
    )

    monkeypatch.setitem(sys.modules, "app.services", fake_services)
    monkeypatch.setitem(sys.modules, "app.features.weread.services.auth", fake_weread_auth)
    monkeypatch.setitem(sys.modules, "app.features.weread.stores.bookshelf_store", fake_local_store)
    monkeypatch.setitem(sys.modules, "app.features.weread.stores.document_store", fake_local_document_store)
    monkeypatch.setitem(sys.modules, "app.features.weread.stores.index_store", fake_local_index_store)
    monkeypatch.setitem(sys.modules, "app.features.weread.indexing.embeddings", fake_note_embedding_service)
    monkeypatch.setitem(sys.modules, "app.features.weread.indexing.index_job_manager", fake_index_job_manager)
    sys.modules.pop("app.features.weread.services.weread", None)

    module_name = "app.features.weread.services.weread"
    module_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "features"
        / "weread"
        / "services"
        / "weread.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_resolve_book_refreshes_snapshot_on_local_miss(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    cached_snapshot = _build_snapshot("book-1", "测试书籍", "测试作者", sync_key=1)
    refreshed_snapshot = _build_snapshot("book-2", "小王子", "圣埃克苏佩里", sync_key=2)

    async def fake_get_cached_snapshot(user_id: int):
        assert user_id == 123
        return cached_snapshot

    sync_calls: dict[str, int] = {"count": 0}

    async def fake_sync_snapshot(user_id: int):
        assert user_id == 123
        sync_calls["count"] += 1
        return refreshed_snapshot

    async def fake_has_active_binding(user_id: int) -> bool:
        assert user_id == 123
        return True

    monkeypatch.setattr(weread_module.weread_local_store, "get_bookshelf_snapshot", fake_get_cached_snapshot)
    monkeypatch.setattr(service, "sync_bookshelf_snapshot", fake_sync_snapshot)
    monkeypatch.setattr(service, "_has_active_binding", fake_has_active_binding)

    result = asyncio.run(service.resolve_book(user_id=123, keyword="小王子"))

    assert result.status == "resolved"
    assert result.book is not None
    assert result.book.book_id == "book-2"
    assert sync_calls["count"] == 1


def test_get_bookshelf_snapshot_raises_when_uncached_and_unbound(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()

    async def fake_get_cached_snapshot(user_id: int):
        assert user_id == 123
        return None

    async def fake_sync_snapshot(user_id: int):
        raise weread_module.WeReadBindingRequiredError("binding_required")

    monkeypatch.setattr(weread_module.weread_local_store, "get_bookshelf_snapshot", fake_get_cached_snapshot)
    monkeypatch.setattr(service, "sync_bookshelf_snapshot", fake_sync_snapshot)

    try:
        asyncio.run(service.get_bookshelf_snapshot(user_id=123))
    except weread_module.WeReadBindingRequiredError:
        return

    raise AssertionError("expected binding_required error")


def test_build_bookshelf_overview_returns_compact_summary(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    snapshot = BookshelfSnapshotResponse(
        sync_key=9,
        lecture_sync_key=0,
        pure_book_count=3,
        book_count=3,
        generated_at="2026-03-14T10:00:00Z",
        books=[
            SnapshotBookItem(
                book_id="book-1",
                title="最近在读",
                author="作者A",
                translator="",
                cover="",
                format="epub",
                categories=["社会", "随笔"],
                book_lists=[],
                publish_time="",
                finish_reading=False,
                paid=True,
                is_imported=False,
                price=0,
                progress=35,
                reading_time=7200,
                reading_time_formatted="2小时",
                last_read_time="2026-03-14T09:00:00Z",
            ),
            SnapshotBookItem(
                book_id="book-2",
                title="已经读完",
                author="作者B",
                translator="",
                cover="",
                format="epub",
                categories=["社会"],
                book_lists=[],
                publish_time="",
                finish_reading=True,
                paid=True,
                is_imported=False,
                price=0,
                progress=100,
                reading_time=5400,
                reading_time_formatted="1小时30分钟",
                last_read_time="2026-03-13T09:00:00Z",
            ),
            SnapshotBookItem(
                book_id="book-3",
                title="还没开始",
                author="作者C",
                translator="",
                cover="",
                format="epub",
                categories=["小说"],
                book_lists=[],
                publish_time="",
                finish_reading=False,
                paid=True,
                is_imported=False,
                price=0,
                progress=0,
                reading_time=0,
                reading_time_formatted="0分钟",
                last_read_time="",
            ),
        ],
    )

    overview = service._build_bookshelf_overview(snapshot=snapshot, preview_limit=2, reading_limit=2)

    assert overview.book_count == 3
    assert overview.unread_books_count == 1
    assert overview.reading_books_count == 1
    assert overview.finished_books_count == 1
    assert len(overview.recent_books) == 2
    assert overview.recent_books[0].book_id == "book-1"
    assert len(overview.currently_reading_books) == 1
    assert overview.currently_reading_books[0].book_id == "book-1"
    assert overview.main_categories[0].category == "社会"


def test_materialize_weread_book_marks_delegates_to_local_store(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    marks_response = weread_module.BookMarkResponse(
        book_id="book-1",
        book_title="Sample Book",
        include_chapter=True,
        highlight_style=None,
        total_marks_count=1,
        marks=[],
        last_updated="2026-03-15T08:00:00Z",
    )
    captured: dict[str, object] = {}

    async def fake_get_marks(
        user_id: int,
        book_id: str,
        include_chapter: bool,
        highlight_style: int | None,
        max_items: int | None = None,
    ):
        captured["get_marks"] = (user_id, book_id, include_chapter, highlight_style, max_items)
        return marks_response

    expected_result = weread_module.WeReadLocalDocumentMaterializationResponse(
        status="created",
        metadata=WeReadLocalDocumentMetadata(
            doc_id="weread:123:book-1:marks",
            user_id=123,
            provider="weread",
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            file_path="weread/users/123/books/book-1/marks.md",
            generated_at="2026-03-15T08:00:00Z",
            include_chapter=True,
            highlight_style=None,
            item_count=1,
            content_hash="sha256:test",
            schema_version=1,
        ),
    )

    async def fake_save_marks_document(*, user_id: int, document):
        captured["save_marks"] = (user_id, document.book_id)
        return expected_result

    monkeypatch.setattr(service, "get_weread_book_marks", fake_get_marks)
    monkeypatch.setattr(weread_module.weread_local_document_store, "save_marks_document", fake_save_marks_document)

    result = asyncio.run(service.materialize_weread_book_marks(user_id=123, book_id="book-1"))

    assert result == expected_result
    assert captured["get_marks"] == (123, "book-1", True, None, None)
    assert captured["save_marks"] == (123, "book-1")


def test_export_weread_book_notes_to_markdown_dispatches_by_source_type(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    marks_result = weread_module.WeReadLocalDocumentMaterializationResponse(
        status="created",
        metadata=WeReadLocalDocumentMetadata(
            doc_id="weread:123:book-1:marks",
            user_id=123,
            provider="weread",
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            file_path="weread/users/123/books/book-1/marks.md",
            generated_at="2026-03-15T08:00:00Z",
            include_chapter=True,
            highlight_style=None,
            item_count=2,
            content_hash="sha256:marks",
            metadata_hash="sha256:marks-meta",
            schema_version=1,
            items=[],
        ),
    )
    reviews_result = weread_module.WeReadLocalDocumentMaterializationResponse(
        status="updated",
        metadata=WeReadLocalDocumentMetadata(
            doc_id="weread:123:book-1:reviews",
            user_id=123,
            provider="weread",
            book_id="book-1",
            book_title="Sample Book",
            source_type="reviews",
            file_path="weread/users/123/books/book-1/reviews.md",
            generated_at="2026-03-15T08:00:00Z",
            include_chapter=True,
            highlight_style=None,
            item_count=1,
            content_hash="sha256:reviews",
            metadata_hash="sha256:reviews-meta",
            schema_version=1,
            items=[],
        ),
    )
    calls: list[tuple[str, int, str]] = []

    async def fake_materialize_marks(
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ):
        _ = (include_chapter, highlight_style)
        calls.append(("marks", user_id, book_id))
        return marks_result

    async def fake_materialize_reviews(
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        reverse_order: bool = True,
    ):
        _ = include_chapter
        calls.append(("reviews", user_id, book_id))
        return reviews_result

    scheduled_doc_ids: list[str] = []

    scheduled_requests: list[tuple[str, int]] = []

    async def fake_schedule_index(*, doc_id: str, operation_factory, debounce_ms: int):
        _ = operation_factory
        scheduled_doc_ids.append(doc_id)
        scheduled_requests.append((doc_id, debounce_ms))
        return object()

    monkeypatch.setattr(service, "materialize_weread_book_marks", fake_materialize_marks)
    monkeypatch.setattr(service, "materialize_weread_book_reviews", fake_materialize_reviews)
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "schedule_index",
        fake_schedule_index,
    )

    result = asyncio.run(service.export_weread_book_notes_to_markdown(user_id=123, book_id="book-1", source_type="both"))

    assert result.book_id == "book-1"
    assert result.source_type == "both"
    assert len(result.exported_documents) == 2
    assert len(result.artifacts) == 2
    assert result.artifacts[0].artifact_id == "weread:123:book-1:marks"
    assert result.artifacts[0].name == "marks.md"
    assert result.artifacts[1].artifact_id == "weread:123:book-1:reviews"
    assert result.artifacts[1].name == "reviews.md"
    assert result.exported_documents[0] == marks_result
    assert result.exported_documents[1] == reviews_result
    assert calls == [("marks", 123, "book-1"), ("reviews", 123, "book-1")]
    assert scheduled_doc_ids == ["weread:123:book-1:marks", "weread:123:book-1:reviews"]
    assert scheduled_requests == [
        ("weread:123:book-1:marks", 3000),
        ("weread:123:book-1:reviews", 3000),
    ]


def test_search_weread_note_chunks_materializes_missing_document_and_returns_hits(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    marks_metadata = WeReadLocalDocumentMetadata(
        doc_id="weread:123:book-1:marks",
        user_id=123,
        provider="weread",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        file_path="weread/users/123/books/book-1/marks.md",
        generated_at="2026-03-15T08:00:00Z",
        include_chapter=True,
        highlight_style=None,
        item_count=1,
        content_hash="sha256:marks",
        metadata_hash="sha256:marks-meta",
        schema_version=1,
        items=[],
    )
    materialized_marks = weread_module.WeReadLocalDocumentMaterializationResponse(
        status="created",
        metadata=marks_metadata,
    )
    captured: dict[str, object] = {"indexed_doc_ids": []}

    def fake_get_document_metadata(*, user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == "weread:123:book-1:marks"
        return None

    async def fake_materialize_marks(
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ):
        captured["materialize_marks"] = (user_id, book_id, include_chapter, highlight_style)
        return materialized_marks

    async def fake_index_document(user_id: int, doc_id: str):
        assert user_id == 123
        captured["indexed_doc_ids"].append(doc_id)
        return WeReadLocalDocumentIndexResponse(
            doc_id=doc_id,
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            status="created",
            chunk_count=1,
            indexed_at="2026-03-15T08:00:00Z",
        )

    async def fake_get_document_index_state(*, metadata: WeReadLocalDocumentMetadata):
        assert metadata.doc_id == marks_metadata.doc_id
        return WeReadLocalDocumentIndexState(
            doc_id=metadata.doc_id,
            chunk_count=0,
            index_status="missing",
            has_searchable_index=False,
            is_fresh=False,
        )

    async def fake_get_inflight_task(doc_id: str):
        assert doc_id == marks_metadata.doc_id
        return None

    async def fake_search_note_chunks(
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int,
        query_embedding: list[float] | None,
        text_weight: float,
        vector_weight: float,
        candidate_multiplier: int,
    ):
        assert user_id == 123
        assert book_id == "book-1"
        assert source_type == "marks"
        assert query == "历史"
        assert top_k == 3
        assert query_embedding is None
        assert text_weight == 0.3
        assert vector_weight == 0.7
        assert candidate_multiplier == 4
        return WeReadLocalNoteChunkSearchResponse(
            book_id=book_id,
            source_type=source_type,
            query=query,
            total_hits=1,
            retrieval_mode="fts",
            hits=[
                WeReadLocalNoteChunkHit(
                    chunk_id="weread:123:book-1:marks:chunk:0001",
                    doc_id="weread:123:book-1:marks",
                    book_id=book_id,
                    book_title="Sample Book",
                    source_type="marks",
                    ordinal=1,
                    heading="Highlight 001",
                    chapter_uid=11,
                    chapter_title="Chapter One",
                    create_time="2026-03-15T08:00:00Z",
                    style=2,
                    text="这是一段测试划线内容。",
                    score=-1.0,
                )
            ],
        )

    async def fake_embed_texts(texts: list[str]):
        assert texts == ["历史"]
        return []

    monkeypatch.setattr(weread_module.note_embedding_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(weread_module.note_embedding_service, "is_enabled", lambda: False)
    monkeypatch.setattr(weread_module.note_embedding_service, "get_model_name", lambda: "test-note-embedding-model")
    monkeypatch.setattr(
        weread_module,
        "settings",
        types.SimpleNamespace(
            NOTE_TEXT_WEIGHT=0.3,
            NOTE_VECTOR_WEIGHT=0.7,
            NOTE_CANDIDATE_MULTIPLIER=4,
            WEREAD_INDEX_QUERY_WAIT_MS=500,
        ),
    )

    monkeypatch.setattr(
        weread_module.weread_local_document_store,
        "get_document_metadata",
        fake_get_document_metadata,
    )
    monkeypatch.setattr(service, "materialize_weread_book_marks", fake_materialize_marks)
    monkeypatch.setattr(service, "index_weread_local_document", fake_index_document)
    monkeypatch.setattr(
        weread_module.weread_local_index_store,
        "get_document_index_state",
        fake_get_document_index_state,
    )
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "get_inflight_task",
        fake_get_inflight_task,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "search_note_chunks", fake_search_note_chunks)

    result = asyncio.run(
        service.search_weread_note_chunks(
            user_id=123,
            book_id="book-1",
            source_type="marks",
            query="历史",
            top_k=3,
        )
    )

    assert captured["materialize_marks"] == (123, "book-1", True, None)
    assert captured["indexed_doc_ids"] == ["weread:123:book-1:marks"]
    assert result.total_hits == 1
    assert result.retrieval_mode == "fts"
    assert result.hits[0].heading == "Highlight 001"


def test_search_weread_note_chunks_refreshes_stale_local_document_when_counts_mismatch(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    stale_metadata = WeReadLocalDocumentMetadata(
        doc_id="weread:123:book-1:marks",
        user_id=123,
        provider="weread",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        file_path="weread/users/123/books/book-1/marks.md",
        generated_at="2026-03-15T08:00:00Z",
        include_chapter=True,
        highlight_style=None,
        item_count=1,
        content_hash="sha256:old",
        metadata_hash="sha256:old-meta",
        schema_version=1,
        items=[],
    )
    refreshed_metadata = stale_metadata.model_copy(
        update={
            "item_count": 2,
            "content_hash": "sha256:new",
            "metadata_hash": "sha256:new-meta",
        }
    )
    refreshed_document = weread_module.WeReadLocalDocumentMaterializationResponse(
        status="updated",
        metadata=refreshed_metadata,
    )
    captured: dict[str, object] = {"indexed_doc_ids": []}

    def fake_get_document_metadata(*, user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == stale_metadata.doc_id
        return stale_metadata

    async def fake_has_active_binding(user_id: int) -> bool:
        assert user_id == 123
        return True

    async def fake_get_note_counts(user_id: int, book_id: str):
        assert user_id == 123
        assert book_id == "book-1"
        return BookNotesResponse(
            book_id="book-1",
            total_notes=2,
            mark_count=2,
            review_count=0,
            last_updated="2026-03-15T08:10:00Z",
        )

    async def fake_materialize_marks(
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ):
        captured["materialize_marks"] = (user_id, book_id, include_chapter, highlight_style)
        return refreshed_document

    async def fake_index_document(user_id: int, doc_id: str):
        captured["indexed_doc_ids"].append((user_id, doc_id))
        return WeReadLocalDocumentIndexResponse(
            doc_id=doc_id,
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            status="updated",
            chunk_count=2,
            indexed_at="2026-03-15T08:10:00Z",
            embedding_status="disabled",
        )

    async def fake_get_document_index_state(*, metadata: WeReadLocalDocumentMetadata):
        assert metadata.doc_id == refreshed_metadata.doc_id
        return WeReadLocalDocumentIndexState(
            doc_id=metadata.doc_id,
            chunk_count=0,
            index_status="missing",
            has_searchable_index=False,
            is_fresh=False,
        )

    async def fake_get_inflight_task(doc_id: str):
        assert doc_id == refreshed_metadata.doc_id
        return None

    async def fake_search_note_chunks(
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int,
        query_embedding: list[float] | None,
        text_weight: float,
        vector_weight: float,
        candidate_multiplier: int,
    ):
        _ = (user_id, book_id, source_type, query, top_k, query_embedding, text_weight, vector_weight, candidate_multiplier)
        return WeReadLocalNoteChunkSearchResponse(
            book_id="book-1",
            source_type="marks",
            query="历史",
            total_hits=0,
            retrieval_mode="fts",
            hits=[],
        )

    async def fake_embed_texts(texts: list[str]):
        assert texts == ["历史"]
        return []

    monkeypatch.setattr(service, "_has_active_binding", fake_has_active_binding)
    monkeypatch.setattr(service, "get_weread_book_note_counts", fake_get_note_counts)
    monkeypatch.setattr(service, "materialize_weread_book_marks", fake_materialize_marks)
    monkeypatch.setattr(service, "index_weread_local_document", fake_index_document)
    monkeypatch.setattr(weread_module.weread_local_document_store, "get_document_metadata", fake_get_document_metadata)
    monkeypatch.setattr(
        weread_module.weread_local_index_store,
        "get_document_index_state",
        fake_get_document_index_state,
    )
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "get_inflight_task",
        fake_get_inflight_task,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "search_note_chunks", fake_search_note_chunks)
    monkeypatch.setattr(weread_module.note_embedding_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(
        weread_module,
        "settings",
        types.SimpleNamespace(
            NOTE_TEXT_WEIGHT=0.3,
            NOTE_VECTOR_WEIGHT=0.7,
            NOTE_CANDIDATE_MULTIPLIER=4,
            WEREAD_INDEX_QUERY_WAIT_MS=500,
        ),
    )

    result = asyncio.run(
        service.search_weread_note_chunks(
            user_id=123,
            book_id="book-1",
            source_type="marks",
            query="历史",
            top_k=3,
        )
    )

    assert captured["materialize_marks"] == (123, "book-1", True, None)
    assert captured["indexed_doc_ids"] == [(123, refreshed_metadata.doc_id)]
    assert result.total_hits == 0


def test_search_weread_note_chunks_keeps_fresh_local_document_when_counts_match(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    local_metadata = WeReadLocalDocumentMetadata(
        doc_id="weread:123:book-1:marks",
        user_id=123,
        provider="weread",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        file_path="weread/users/123/books/book-1/marks.md",
        generated_at="2026-03-15T08:00:00Z",
        include_chapter=True,
        highlight_style=None,
        item_count=2,
        content_hash="sha256:current",
        metadata_hash="sha256:current-meta",
        schema_version=1,
        items=[],
    )
    captured: dict[str, object] = {"indexed_doc_ids": [], "wait_calls": []}

    def fake_get_document_metadata(*, user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == local_metadata.doc_id
        return local_metadata

    async def fake_has_active_binding(user_id: int) -> bool:
        assert user_id == 123
        return True

    async def fake_get_note_counts(user_id: int, book_id: str):
        assert user_id == 123
        assert book_id == "book-1"
        return BookNotesResponse(
            book_id="book-1",
            total_notes=2,
            mark_count=2,
            review_count=0,
            last_updated="2026-03-15T08:10:00Z",
        )

    async def fake_materialize_marks(
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ):
        raise AssertionError(f"materialize_weread_book_marks should not be called for fresh local documents: {(user_id, book_id, include_chapter, highlight_style)}")

    async def fake_index_document(user_id: int, doc_id: str):
        captured["indexed_doc_ids"].append((user_id, doc_id))
        return WeReadLocalDocumentIndexResponse(
            doc_id=doc_id,
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            status="unchanged",
            chunk_count=2,
            indexed_at="2026-03-15T08:10:00Z",
            embedding_status="disabled",
        )

    async def fake_get_document_index_state(*, metadata: WeReadLocalDocumentMetadata):
        assert metadata.doc_id == local_metadata.doc_id
        return WeReadLocalDocumentIndexState(
            doc_id=metadata.doc_id,
            chunk_count=2,
            index_status="fts_ready",
            has_searchable_index=True,
            is_fresh=True,
        )

    async def fake_get_inflight_task(doc_id: str):
        assert doc_id == local_metadata.doc_id
        return object()

    async def fake_wait_for_doc(*, doc_id: str, timeout_ms: int):
        captured["wait_calls"].append((doc_id, timeout_ms))
        return False

    async def fake_search_note_chunks(
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int,
        query_embedding: list[float] | None,
        text_weight: float,
        vector_weight: float,
        candidate_multiplier: int,
    ):
        _ = (user_id, book_id, source_type, query, top_k, query_embedding, text_weight, vector_weight, candidate_multiplier)
        return WeReadLocalNoteChunkSearchResponse(
            book_id="book-1",
            source_type="marks",
            query="历史",
            total_hits=0,
            retrieval_mode="fts",
            hits=[],
        )

    async def fake_embed_texts(texts: list[str]):
        assert texts == ["历史"]
        return []

    monkeypatch.setattr(service, "_has_active_binding", fake_has_active_binding)
    monkeypatch.setattr(service, "get_weread_book_note_counts", fake_get_note_counts)
    monkeypatch.setattr(service, "materialize_weread_book_marks", fake_materialize_marks)
    monkeypatch.setattr(service, "index_weread_local_document", fake_index_document)
    monkeypatch.setattr(weread_module.weread_local_document_store, "get_document_metadata", fake_get_document_metadata)
    monkeypatch.setattr(
        weread_module.weread_local_index_store,
        "get_document_index_state",
        fake_get_document_index_state,
    )
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "get_inflight_task",
        fake_get_inflight_task,
    )
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "wait_for_doc",
        fake_wait_for_doc,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "search_note_chunks", fake_search_note_chunks)
    monkeypatch.setattr(weread_module.note_embedding_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(
        weread_module,
        "settings",
        types.SimpleNamespace(
            NOTE_TEXT_WEIGHT=0.3,
            NOTE_VECTOR_WEIGHT=0.7,
            NOTE_CANDIDATE_MULTIPLIER=4,
            WEREAD_INDEX_QUERY_WAIT_MS=500,
        ),
    )

    result = asyncio.run(
        service.search_weread_note_chunks(
            user_id=123,
            book_id="book-1",
            source_type="marks",
            query="历史",
            top_k=3,
        )
    )

    assert captured["indexed_doc_ids"] == []
    assert captured["wait_calls"] == [(local_metadata.doc_id, 500)]
    assert result.total_hits == 0


def test_search_weread_note_chunks_reuses_running_index_when_local_index_is_stale(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    local_metadata = WeReadLocalDocumentMetadata(
        doc_id="weread:123:book-1:marks",
        user_id=123,
        provider="weread",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        file_path="weread/users/123/books/book-1/marks.md",
        generated_at="2026-03-15T08:00:00Z",
        include_chapter=True,
        highlight_style=None,
        item_count=2,
        content_hash="sha256:current",
        metadata_hash="sha256:current-meta",
        schema_version=1,
        items=[],
    )
    captured: dict[str, object] = {"await_calls": []}

    def fake_get_document_metadata(*, user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == local_metadata.doc_id
        return local_metadata

    async def fake_has_active_binding(user_id: int) -> bool:
        assert user_id == 123
        return True

    async def fake_get_note_counts(user_id: int, book_id: str):
        assert user_id == 123
        assert book_id == "book-1"
        return BookNotesResponse(
            book_id="book-1",
            total_notes=2,
            mark_count=2,
            review_count=0,
            last_updated="2026-03-15T08:10:00Z",
        )

    async def fake_materialize_marks(
        user_id: int,
        book_id: str,
        include_chapter: bool = True,
        highlight_style: int | None = None,
        reverse_order: bool = True,
    ):
        raise AssertionError(f"materialize_weread_book_marks should not be called: {(user_id, book_id, include_chapter, highlight_style)}")

    async def fake_index_document(user_id: int, doc_id: str):
        raise AssertionError(f"index_weread_local_document should not be called when reusing running task: {(user_id, doc_id)}")

    index_state_calls = {"count": 0}

    async def fake_get_document_index_state(*, metadata: WeReadLocalDocumentMetadata):
        assert metadata.doc_id == local_metadata.doc_id
        index_state_calls["count"] += 1
        if index_state_calls["count"] == 1:
            return WeReadLocalDocumentIndexState(
                doc_id=metadata.doc_id,
                chunk_count=0,
                index_status="pending",
                has_searchable_index=False,
                is_fresh=False,
            )
        return WeReadLocalDocumentIndexState(
            doc_id=metadata.doc_id,
            chunk_count=2,
            index_status="fts_ready",
            has_searchable_index=True,
            is_fresh=True,
        )

    async def fake_get_inflight_task(doc_id: str):
        assert doc_id == local_metadata.doc_id
        return object()

    async def fake_await_inflight_task(doc_id: str):
        captured["await_calls"].append(doc_id)
        return None

    async def fake_search_note_chunks(
        *,
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int,
        query_embedding: list[float] | None,
        text_weight: float,
        vector_weight: float,
        candidate_multiplier: int,
    ):
        _ = (user_id, book_id, source_type, query, top_k, query_embedding, text_weight, vector_weight, candidate_multiplier)
        return WeReadLocalNoteChunkSearchResponse(
            book_id="book-1",
            source_type="marks",
            query="历史",
            total_hits=0,
            retrieval_mode="fts",
            hits=[],
        )

    async def fake_embed_texts(texts: list[str]):
        assert texts == ["历史"]
        return []

    monkeypatch.setattr(service, "_has_active_binding", fake_has_active_binding)
    monkeypatch.setattr(service, "get_weread_book_note_counts", fake_get_note_counts)
    monkeypatch.setattr(service, "materialize_weread_book_marks", fake_materialize_marks)
    monkeypatch.setattr(service, "index_weread_local_document", fake_index_document)
    monkeypatch.setattr(weread_module.weread_local_document_store, "get_document_metadata", fake_get_document_metadata)
    monkeypatch.setattr(
        weread_module.weread_local_index_store,
        "get_document_index_state",
        fake_get_document_index_state,
    )
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "get_inflight_task",
        fake_get_inflight_task,
    )
    monkeypatch.setattr(
        weread_module.weread_index_job_manager,
        "await_inflight_task",
        fake_await_inflight_task,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "search_note_chunks", fake_search_note_chunks)
    monkeypatch.setattr(weread_module.note_embedding_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(
        weread_module,
        "settings",
        types.SimpleNamespace(
            NOTE_TEXT_WEIGHT=0.3,
            NOTE_VECTOR_WEIGHT=0.7,
            NOTE_CANDIDATE_MULTIPLIER=4,
            WEREAD_INDEX_QUERY_WAIT_MS=500,
        ),
    )

    result = asyncio.run(
        service.search_weread_note_chunks(
            user_id=123,
            book_id="book-1",
            source_type="marks",
            query="历史",
            top_k=3,
        )
    )

    assert captured["await_calls"] == [local_metadata.doc_id]
    assert result.total_hits == 0


def test_index_weread_local_document_skips_embedding_when_ready(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    metadata = WeReadLocalDocumentMetadata(
        doc_id="weread:123:book-1:marks",
        user_id=123,
        provider="weread",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        file_path="weread/users/123/books/book-1/marks.md",
        generated_at="2026-03-15T08:00:00Z",
        include_chapter=True,
        highlight_style=None,
        item_count=1,
        content_hash="sha256:marks",
        metadata_hash="sha256:marks-meta",
        schema_version=1,
        items=[],
    )
    chunk = WeReadLocalNoteChunk(
        chunk_id="weread:123:book-1:marks:chunk:0001",
        doc_id=metadata.doc_id,
        user_id=123,
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        ordinal=1,
        heading="Highlight 001",
        chapter_uid=11,
        chapter_title="Chapter One",
        start_item_index=1,
        end_item_index=1,
        create_time="2026-03-15T08:00:00Z",
        style=2,
        text="这是一段测试划线内容。",
        char_count=10,
        token_estimate=10,
    )

    def fake_extract_note_chunks(*, user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == metadata.doc_id
        return metadata, [chunk]

    async def fake_sync_document_index(*, metadata: WeReadLocalDocumentMetadata, chunks: list):
        assert metadata.doc_id == "weread:123:book-1:marks"
        assert len(chunks) == 1
        return WeReadLocalDocumentIndexResponse(
            doc_id=metadata.doc_id,
            book_id=metadata.book_id,
            book_title=metadata.book_title,
            source_type=metadata.source_type,
            status="unchanged",
            chunk_count=1,
            indexed_at="2026-03-15T08:00:00Z",
            embedding_status="ready",
        )

    async def fake_embed_texts(texts: list[str]):
        raise AssertionError(f"embed_texts should not be called when embeddings are already ready: {texts}")

    monkeypatch.setattr(
        weread_module.weread_local_document_store,
        "extract_note_chunks",
        fake_extract_note_chunks,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "sync_document_index", fake_sync_document_index)
    monkeypatch.setattr(weread_module.note_embedding_service, "is_enabled", lambda: True)
    monkeypatch.setattr(weread_module.note_embedding_service, "embed_texts", fake_embed_texts)

    result = asyncio.run(service.index_weread_local_document(user_id=123, doc_id=metadata.doc_id))

    assert result.embedding_status == "ready"


def test_index_weread_local_document_only_embeds_missing_chunks(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    metadata = WeReadLocalDocumentMetadata(
        doc_id="weread:123:book-1:marks",
        user_id=123,
        provider="weread",
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        file_path="weread/users/123/books/book-1/marks.md",
        generated_at="2026-03-15T08:00:00Z",
        include_chapter=True,
        highlight_style=None,
        item_count=2,
        content_hash="sha256:marks-updated",
        metadata_hash="sha256:marks-meta-updated",
        schema_version=1,
        items=[],
    )
    first_chunk = WeReadLocalNoteChunk(
        chunk_id="weread:123:book-1:marks:item:keep",
        doc_id=metadata.doc_id,
        user_id=123,
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        ordinal=1,
        heading="Highlight 001",
        chapter_uid=11,
        chapter_title="Chapter One",
        start_item_index=1,
        end_item_index=1,
        create_time="2026-03-15T08:00:00Z",
        style=2,
        text="原有划线",
        char_count=4,
        token_estimate=4,
    )
    second_chunk = WeReadLocalNoteChunk(
        chunk_id="weread:123:book-1:marks:item:new",
        doc_id=metadata.doc_id,
        user_id=123,
        book_id="book-1",
        book_title="Sample Book",
        source_type="marks",
        ordinal=2,
        heading="Highlight 002",
        chapter_uid=11,
        chapter_title="Chapter One",
        start_item_index=2,
        end_item_index=2,
        create_time="2026-03-15T08:05:00Z",
        style=1,
        text="新增划线",
        char_count=4,
        token_estimate=4,
    )
    captured: dict[str, object] = {}

    def fake_extract_note_chunks(*, user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == metadata.doc_id
        return metadata, [first_chunk, second_chunk]

    async def fake_sync_document_index(*, metadata: WeReadLocalDocumentMetadata, chunks: list):
        assert metadata.doc_id == "weread:123:book-1:marks"
        assert len(chunks) == 2
        return WeReadLocalDocumentIndexResponse(
            doc_id=metadata.doc_id,
            book_id=metadata.book_id,
            book_title=metadata.book_title,
            source_type=metadata.source_type,
            status="updated",
            chunk_count=2,
            indexed_at="2026-03-15T08:10:00Z",
            embedding_status="pending",
        )

    async def fake_get_chunks_missing_embeddings(*, doc_id: str):
        assert doc_id == metadata.doc_id
        return [second_chunk.chunk_id]

    async def fake_embed_texts(texts: list[str]):
        captured["embedded_texts"] = texts
        return [[0.1, 0.2]]

    async def fake_sync_chunk_embeddings(*, doc_id: str, embed_model: str, embeddings: dict[str, list[float]]):
        captured["synced_embeddings"] = (doc_id, embed_model, embeddings)

    monkeypatch.setattr(
        weread_module.weread_local_document_store,
        "extract_note_chunks",
        fake_extract_note_chunks,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "sync_document_index", fake_sync_document_index)
    monkeypatch.setattr(
        weread_module.weread_local_index_store,
        "get_chunks_missing_embeddings",
        fake_get_chunks_missing_embeddings,
    )
    monkeypatch.setattr(weread_module.weread_local_index_store, "sync_chunk_embeddings", fake_sync_chunk_embeddings)
    monkeypatch.setattr(weread_module.note_embedding_service, "is_enabled", lambda: True)
    monkeypatch.setattr(weread_module.note_embedding_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(weread_module.note_embedding_service, "get_model_name", lambda: "test-embedding-model")

    result = asyncio.run(service.index_weread_local_document(user_id=123, doc_id=metadata.doc_id))

    assert captured["embedded_texts"] == ["新增划线"]
    assert captured["synced_embeddings"] == (
        metadata.doc_id,
        "test-embedding-model",
        {second_chunk.chunk_id: [0.1, 0.2]},
    )
    assert result.embedding_status == "pending"


def test_query_weread_book_notes_delegates_to_note_chunk_search(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()
    expected_response = WeReadLocalNoteChunkSearchResponse(
        book_id="book-1",
        source_type="reviews",
        query="教育",
        total_hits=1,
        hits=[
            WeReadLocalNoteChunkHit(
                chunk_id="weread:123:book-1:reviews:chunk:0001",
                doc_id="weread:123:book-1:reviews",
                book_id="book-1",
                book_title="Sample Book",
                source_type="reviews",
                ordinal=1,
                heading="Review 001",
                chapter_uid=22,
                chapter_title="第二章",
                create_time="2026-03-15T08:00:00Z",
                style=None,
                text="关于教育的想法",
                score=-1.0,
            )
        ],
    )
    captured: dict[str, object] = {}

    async def fake_search_note_chunks(
        user_id: int,
        book_id: str,
        source_type: str,
        query: str,
        top_k: int = 5,
    ):
        captured["args"] = (user_id, book_id, source_type, query, top_k)
        return expected_response

    monkeypatch.setattr(service, "search_weread_note_chunks", fake_search_note_chunks)

    result = asyncio.run(
        service.query_weread_book_notes(
            user_id=123,
            book_id="book-1",
            source_type="reviews",
            query="教育",
            top_k=4,
        )
    )

    assert captured["args"] == (123, "book-1", "reviews", "教育", 4)
    assert result == expected_response


def test_get_weread_book_marks_returns_preview_by_item_limit(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()

    class _FakeApi:
        async def get_book_info(self, book_id: str):
            assert book_id == "book-1"
            return {"title": "Sample Book"}

        async def get_chapter_info(self, book_id: str):
            assert book_id == "book-1"
            return {"1": {"chapterUid": 1, "title": "第一章"}}

        async def get_bookmark_list(self, book_id: str):
            assert book_id == "book-1"
            return [
                {"markText": "A" * 200, "chapterUid": 1, "createTime": 1, "colorStyle": 1},
                {"markText": "B" * 180, "chapterUid": 1, "createTime": 2, "colorStyle": 1},
                {"markText": "C" * 160, "chapterUid": 1, "createTime": 3, "colorStyle": 1},
            ]

    async def fake_with_api(user_id: int, operation_name: str, operation):
        assert user_id == 123
        assert operation_name == "get_weread_book_marks"
        return await operation(_FakeApi())

    monkeypatch.setattr(service, "_with_api", fake_with_api)

    result = asyncio.run(
        service.get_weread_book_marks(
            user_id=123,
            book_id="book-1",
            include_chapter=True,
            highlight_style=None,
            max_items=2,
        )
    )

    assert result.preview_mode is True
    assert result.returned_marks_count == 2
    assert result.omitted_marks_count == 1
    assert len(result.marks) == 2
    assert all(len(item.text) <= weread_module.MARK_PREVIEW_TEXT_CHARS for item in result.marks)
    assert result.next_action is not None


def test_get_weread_book_reviews_returns_preview_by_item_limit(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()

    class _FakeApi:
        async def get_book_info(self, book_id: str):
            assert book_id == "book-1"
            return {"title": "Sample Book"}

        async def get_chapter_info(self, book_id: str):
            assert book_id == "book-1"
            return {"1": {"chapterUid": 1, "title": "第一章"}}

        async def get_review_list(self, book_id: str):
            assert book_id == "book-1"
            return [
                {"content": "想法" * 80, "abstract": "原文" * 40, "chapterUid": 1, "createTime": 1},
                {"content": "第二条" * 60, "abstract": "摘录" * 30, "chapterUid": 1, "createTime": 2},
            ]

    async def fake_with_api(user_id: int, operation_name: str, operation):
        assert user_id == 123
        assert operation_name == "get_weread_book_reviews"
        return await operation(_FakeApi())

    monkeypatch.setattr(service, "_with_api", fake_with_api)

    result = asyncio.run(
        service.get_weread_book_reviews(
            user_id=123,
            book_id="book-1",
            include_chapter=True,
            max_items=1,
        )
    )

    assert result.preview_mode is True
    assert result.returned_reviews_count == 1
    assert result.omitted_reviews_count == 1
    assert len(result.reviews) == 1
    assert len(result.reviews[0].content) <= weread_module.REVIEW_PREVIEW_CONTENT_CHARS
    assert len(result.reviews[0].mark_text) <= weread_module.REVIEW_PREVIEW_MARK_TEXT_CHARS
    assert result.next_action is not None


def test_get_book_marks_and_reviews_returns_flat_preview(monkeypatch) -> None:
    weread_module = _load_weread_service_module(monkeypatch)
    service = weread_module.WeReadService()

    class _FakeApi:
        async def get_book_info(self, book_id: str):
            assert book_id == "book-1"
            return {"title": "Sample Book"}

        async def get_read_info(self, book_id: str):
            assert book_id == "book-1"
            return {}

        async def get_chapter_info(self, book_id: str):
            assert book_id == "book-1"
            return {"1": {"chapterUid": 1, "title": "第一章", "level": 1, "chapterIdx": 1}}

        async def get_bookmark_list(self, book_id: str):
            assert book_id == "book-1"
            return [
                {"markText": "A" * 200, "chapterUid": 1, "createTime": 1, "colorStyle": 1},
                {"markText": "B" * 180, "chapterUid": 1, "createTime": 2, "colorStyle": 1},
                {"markText": "C" * 160, "chapterUid": 1, "createTime": 3, "colorStyle": 1},
            ]

        async def get_review_list(self, book_id: str):
            assert book_id == "book-1"
            return [
                {"content": "想法" * 80, "abstract": "原文" * 40, "chapterUid": 1, "createTime": 1},
                {"content": "第二条" * 60, "abstract": "摘录" * 30, "chapterUid": 1, "createTime": 2},
                {"content": "第三条" * 50, "abstract": "片段" * 20, "chapterUid": 1, "createTime": 3},
            ]

    async def fake_with_api(user_id: int, operation_name: str, operation):
        assert user_id == 123
        assert operation_name == "get_book_marks_and_reviews"
        return await operation(_FakeApi())

    monkeypatch.setattr(service, "_with_api", fake_with_api)

    result = asyncio.run(
        service.get_book_marks_and_reviews(
            user_id=123,
            book_id="book-1",
            include_chapter=True,
            highlight_style=None,
            max_review_items=2,
            max_mark_items=1,
        )
    )

    assert result.preview_mode is True
    assert result.mode == "flat"
    assert result.returned_reviews_count == 2
    assert result.omitted_reviews_count == 1
    assert result.returned_marks_count == 1
    assert result.omitted_marks_count == 2
    assert len(result.chapters) == 0
    assert len(result.reviews) == 2
    assert len(result.marks) == 1
    assert all(len(item.content) <= weread_module.REVIEW_PREVIEW_CONTENT_CHARS for item in result.reviews)
    assert all(len(item.mark_text) <= weread_module.REVIEW_PREVIEW_MARK_TEXT_CHARS for item in result.reviews)
    assert all(len(item.text) <= weread_module.MARK_PREVIEW_TEXT_CHARS for item in result.marks)
