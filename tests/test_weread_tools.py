import importlib.util
import sys
import types
from pathlib import Path

from app.features.weread.models import (
    WeReadBookNotesExportResponse,
    WeReadLocalNoteChunkHit,
    WeReadLocalNoteChunkSearchResponse,
    WeReadLocalDocumentMaterializationResponse,
    WeReadLocalDocumentMetadata,
)


def _load_weread_tools_module(monkeypatch):
    fake_services = types.ModuleType("app.services")
    fake_services.__path__ = []

    fake_weread = types.ModuleType("app.features.weread.services.weread")

    class _FakeWeReadDomainError(RuntimeError):
        pass

    fake_weread.WeReadDomainError = _FakeWeReadDomainError
    fake_weread.MARK_PREVIEW_DEFAULT_ITEMS = 4
    fake_weread.REVIEW_PREVIEW_DEFAULT_ITEMS = 3
    fake_weread.MARKS_AND_REVIEWS_PREVIEW_DEFAULT_REVIEW_ITEMS = 3
    fake_weread.MARKS_AND_REVIEWS_PREVIEW_DEFAULT_MARK_ITEMS = 2
    fake_weread.weread_service = types.SimpleNamespace()

    fake_weread_auth = types.ModuleType("app.features.weread.services.auth")

    class _FakeBindingRequiredError(RuntimeError):
        pass

    class _FakeReauthRequiredError(RuntimeError):
        pass

    fake_weread_auth.WeReadBindingRequiredError = _FakeBindingRequiredError
    fake_weread_auth.WeReadReauthRequiredError = _FakeReauthRequiredError

    monkeypatch.setitem(sys.modules, "app.services", fake_services)
    monkeypatch.setitem(sys.modules, "app.features.weread.services.weread", fake_weread)
    monkeypatch.setitem(sys.modules, "app.features.weread.services.auth", fake_weread_auth)
    sys.modules.pop("app.features.weread.tools", None)

    module_name = "app.features.weread.tools"
    module_path = Path(__file__).resolve().parents[1] / "app" / "features" / "weread" / "tools.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_build_weread_tools_includes_export_tool(monkeypatch) -> None:
    weread_tools_module = _load_weread_tools_module(monkeypatch)

    tool_names = [tool.name for tool in weread_tools_module.build_weread_tools()]

    assert "export_weread_book_notes_to_markdown" in tool_names
    assert "query_weread_book_notes" in tool_names
    assert "materialize_weread_book_marks" not in tool_names
    assert "materialize_weread_book_reviews" not in tool_names


def test_summarize_tool_payload_includes_nested_materialization_metadata(monkeypatch) -> None:
    weread_tools_module = _load_weread_tools_module(monkeypatch)
    payload = WeReadLocalDocumentMaterializationResponse(
        status="created",
        metadata=WeReadLocalDocumentMetadata(
            doc_id="weread:123:book-1:marks",
            user_id=123,
            provider="weread",
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            file_path="weread/users/123/books/book-1/marks.md",
            generated_at="2026-03-15T12:00:00Z",
            include_chapter=True,
            highlight_style=None,
            item_count=3,
            content_hash="sha256:content",
            metadata_hash="sha256:meta",
            schema_version=1,
            items=[],
        ),
    )

    summary = weread_tools_module._summarize_tool_payload(payload)

    assert summary["status"] == "created"
    assert summary["metadata_book_id"] == "book-1"
    assert summary["metadata_source_type"] == "marks"
    assert summary["metadata_file_path"] == "weread/users/123/books/book-1/marks.md"
    assert summary["metadata_item_count"] == 3


def test_summarize_tool_payload_includes_exported_documents_count(monkeypatch) -> None:
    weread_tools_module = _load_weread_tools_module(monkeypatch)
    materialized = WeReadLocalDocumentMaterializationResponse(
        status="created",
        metadata=WeReadLocalDocumentMetadata(
            doc_id="weread:123:book-1:marks",
            user_id=123,
            provider="weread",
            book_id="book-1",
            book_title="Sample Book",
            source_type="marks",
            file_path="weread/users/123/books/book-1/marks.md",
            generated_at="2026-03-15T12:00:00Z",
            include_chapter=True,
            highlight_style=None,
            item_count=3,
            content_hash="sha256:content",
            metadata_hash="sha256:meta",
            schema_version=1,
            items=[],
        ),
    )
    payload = WeReadBookNotesExportResponse(
        book_id="book-1",
        source_type="both",
        exported_documents=[materialized],
    )

    summary = weread_tools_module._summarize_tool_payload(payload)

    assert summary["book_id"] == "book-1"
    assert summary["source_type"] == "both"
    assert summary["exported_documents_count"] == 1


def test_summarize_tool_payload_includes_note_query_hit_counts(monkeypatch) -> None:
    weread_tools_module = _load_weread_tools_module(monkeypatch)
    payload = WeReadLocalNoteChunkSearchResponse(
        book_id="book-1",
        source_type="marks",
        query="历史",
        total_hits=2,
        hits=[
            WeReadLocalNoteChunkHit(
                chunk_id="weread:123:book-1:marks:chunk:0001",
                doc_id="weread:123:book-1:marks",
                book_id="book-1",
                book_title="Sample Book",
                source_type="marks",
                ordinal=1,
                heading="Highlight 001",
                chapter_uid=11,
                chapter_title="Chapter One",
                create_time="2026-03-15T12:00:00Z",
                style=2,
                text="这是一段测试划线内容。",
                score=-1.0,
            )
        ],
    )

    summary = weread_tools_module._summarize_tool_payload(payload)

    assert summary["book_id"] == "book-1"
    assert summary["source_type"] == "marks"
    assert summary["total_hits"] == 2
    assert summary["hits_count"] == 1
