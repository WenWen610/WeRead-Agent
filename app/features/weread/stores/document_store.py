"""Persist WeRead marks and reviews as local markdown documents."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.infrastructure.config import settings
from app.infrastructure.logging import get_logger
from app.features.weread.models import (
    BookMarkResponse,
    BookReviewsResponse,
    HighlightItem,
    ReviewItem,
    WeReadDocumentArtifact,
    WeReadLocalDocumentItemMetadata,
    WeReadLocalNoteChunk,
    WeReadLocalDocumentMaterializationResponse,
    WeReadLocalDocumentMetadata,
)

logger = get_logger(__name__)

DocumentSourceType = Literal["marks", "reviews"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_text(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _yaml_scalar(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_frontmatter(metadata: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _content_hash(markdown_content: str) -> str:
    return f"sha256:{hashlib.sha256(markdown_content.encode('utf-8')).hexdigest()}"


def _metadata_hash(items: list[WeReadLocalDocumentItemMetadata]) -> str:
    serialized = json.dumps(
        [item.model_dump(mode="json", exclude_none=True) for item in items],
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def _fingerprint(*parts: object) -> str:
    normalized_parts = [str(part or "").strip() for part in parts]
    serialized = json.dumps(normalized_parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


class WeReadLocalDocumentStore:
    """Store normalized WeRead note content as markdown plus metadata."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.local_knowledge_dir = Path(root_dir or settings.LOCAL_KNOWLEDGE_DIR)
        self.root_dir = self.local_knowledge_dir / "weread"

    def _book_dir(self, user_id: int, book_id: str) -> Path:
        return self.root_dir / "users" / str(user_id) / "books" / str(book_id)

    def list_user_books(self, user_id: int) -> list[dict]:
        user_books_dir = self.root_dir / "users" / str(user_id) / "books"
        if not user_books_dir.exists():
            return []
        books: list[dict] = []
        for book_dir in sorted(user_books_dir.iterdir()):
            if not book_dir.is_dir():
                continue
            book_id = book_dir.name
            docs: list[dict] = []
            book_title = ""
            author = ""
            for meta_path in sorted(book_dir.glob("*.meta.json")):
                import json as _json
                try:
                    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                book_title = meta.get("book_title", "") or book_title
                author = meta.get("author", "") or author
                docs.append({
                    "doc_id": meta.get("doc_id", ""),
                    "book_title": meta.get("book_title", ""),
                    "source_type": meta.get("source_type", ""),
                    "item_count": meta.get("item_count", 0),
                    "generated_at": meta.get("generated_at", ""),
                })
            if docs:
                books.append({
                    "book_id": book_id,
                    "book_title": book_title,
                    "author": author,
                    "documents": docs,
                })
        return books

    def _document_basename(
        self,
        *,
        source_type: DocumentSourceType,
        include_chapter: bool,
        highlight_style: int | None,
    ) -> str:
        parts = [source_type]
        if not include_chapter:
            parts.append("flat")
        if source_type == "marks" and highlight_style is not None:
            parts.append(f"style_{highlight_style}")
        return "_".join(parts)

    def _markdown_path(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: DocumentSourceType,
        include_chapter: bool,
        highlight_style: int | None,
    ) -> Path:
        basename = self._document_basename(
            source_type=source_type,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
        )
        return self._book_dir(user_id, book_id) / f"{basename}.md"

    def _meta_path(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: DocumentSourceType,
        include_chapter: bool,
        highlight_style: int | None,
    ) -> Path:
        basename = self._document_basename(
            source_type=source_type,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
        )
        return self._book_dir(user_id, book_id) / f"{basename}.meta.json"

    def _doc_id(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: DocumentSourceType,
        include_chapter: bool,
        highlight_style: int | None,
    ) -> str:
        parts = ["weread", str(user_id), book_id, source_type]
        if not include_chapter:
            parts.append("flat")
        if source_type == "marks" and highlight_style is not None:
            parts.append(f"style_{highlight_style}")
        return ":".join(parts)

    def _relative_file_path(self, path: Path) -> str:
        return str(path.relative_to(self.local_knowledge_dir))

    def _parse_doc_id(
        self,
        doc_id: str,
    ) -> tuple[int, str, DocumentSourceType, bool, int | None] | None:
        parts = doc_id.split(":")
        if len(parts) < 4 or parts[0] != "weread":
            return None

        try:
            user_id = int(parts[1])
        except ValueError:
            return None

        book_id = parts[2].strip()
        source_type = parts[3].strip()
        if not book_id or source_type not in {"marks", "reviews"}:
            return None

        include_chapter = True
        highlight_style: int | None = None
        for part in parts[4:]:
            normalized_part = part.strip().lower()
            if normalized_part == "flat":
                include_chapter = False
                continue
            if normalized_part.startswith("style_") and source_type == "marks":
                style_value = normalized_part.removeprefix("style_")
                if not style_value.isdigit():
                    return None
                highlight_style = int(style_value)
                continue
            return None

        return user_id, book_id, source_type, include_chapter, highlight_style

    def build_artifact(self, metadata: WeReadLocalDocumentMetadata) -> WeReadDocumentArtifact:
        file_name = Path(metadata.file_path).name
        return WeReadDocumentArtifact(
            artifact_id=metadata.doc_id,
            kind="weread_markdown",
            doc_id=metadata.doc_id,
            book_id=metadata.book_id,
            book_title=metadata.book_title,
            source_type=metadata.source_type,
            name=file_name,
            generated_at=metadata.generated_at,
        )

    def build_doc_id(
        self,
        *,
        user_id: int,
        book_id: str,
        source_type: DocumentSourceType,
        include_chapter: bool = True,
        highlight_style: int | None = None,
    ) -> str:
        return self._doc_id(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
        )

    def get_document_metadata(self, *, user_id: int, doc_id: str) -> WeReadLocalDocumentMetadata | None:
        parsed = self._parse_doc_id(doc_id)
        if parsed is None:
            return None

        doc_user_id, book_id, source_type, include_chapter, highlight_style = parsed
        if doc_user_id != user_id:
            return None

        meta_path = self._meta_path(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
        )
        metadata = self._load_existing_metadata(meta_path)
        if metadata is None or metadata.doc_id != doc_id:
            return None
        return metadata

    def resolve_document_path(self, *, user_id: int, doc_id: str) -> tuple[WeReadLocalDocumentMetadata, Path] | None:
        metadata = self.get_document_metadata(user_id=user_id, doc_id=doc_id)
        if metadata is None:
            return None

        actual_path = (self.local_knowledge_dir / metadata.file_path).resolve()
        try:
            actual_path.relative_to(self.local_knowledge_dir.resolve())
        except ValueError:
            return None

        if not actual_path.exists() or not actual_path.is_file():
            return None

        return metadata, actual_path

    @staticmethod
    def _strip_frontmatter(markdown_content: str) -> str:
        if not markdown_content.startswith("---\n"):
            return markdown_content

        closing_marker = markdown_content.find("\n---\n", 4)
        if closing_marker == -1:
            return markdown_content
        return markdown_content[closing_marker + len("\n---\n") :]

    @staticmethod
    def _trim_blank_lines(lines: list[str]) -> list[str]:
        trimmed = list(lines)
        while trimmed and not trimmed[0].strip():
            trimmed.pop(0)
        while trimmed and not trimmed[-1].strip():
            trimmed.pop()
        return trimmed

    def _extract_item_bodies(self, markdown_content: str) -> dict[str, dict[str, str]]:
        content = self._strip_frontmatter(markdown_content)
        current_chapter = ""
        current_heading: str | None = None
        current_body: list[str] = []
        extracted_items: list[dict[str, str]] = []

        def flush_current_item() -> None:
            if current_heading is None:
                return
            extracted_items.append(
                {
                    "heading": current_heading,
                    "chapter_title": current_chapter,
                    "body": "\n".join(self._trim_blank_lines(current_body)).strip(),
                }
            )

        for raw_line in content.splitlines():
            line = raw_line.rstrip()
            if line.startswith("## Chapter: "):
                flush_current_item()
                current_heading = None
                current_body = []
                current_chapter = line.removeprefix("## Chapter: ").strip()
                continue

            if line.startswith("### "):
                flush_current_item()
                current_heading = line.removeprefix("### ").strip()
                current_body = []
                continue

            if line.startswith("# ") or line in {"## Marks", "## Reviews"}:
                continue

            if current_heading is not None:
                current_body.append(line)

        flush_current_item()
        return {item["heading"]: item for item in extracted_items}

    @staticmethod
    def _normalize_chunk_text(markdown_body: str) -> str:
        normalized_lines: list[str] = []
        for raw_line in markdown_body.splitlines():
            stripped_line = raw_line.strip()
            if stripped_line.startswith(">"):
                stripped_line = stripped_line.removeprefix(">").strip()
            normalized_lines.append(stripped_line)
        return "\n".join(WeReadLocalDocumentStore._trim_blank_lines(normalized_lines)).strip()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _split_large_item_text(self, *, text: str, max_chars: int) -> list[str]:
        normalized_text = _normalize_text(text)
        if not normalized_text or len(normalized_text) <= max_chars:
            return [normalized_text]

        paragraphs = [segment.strip() for segment in normalized_text.split("\n\n") if segment.strip()]
        if not paragraphs:
            paragraphs = [normalized_text]

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        def flush_current_chunk() -> None:
            nonlocal current_chunk, current_length
            if not current_chunk:
                return
            chunks.append("\n\n".join(current_chunk).strip())
            current_chunk = []
            current_length = 0

        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                flush_current_chunk()
                for index in range(0, len(paragraph), max_chars):
                    chunks.append(paragraph[index : index + max_chars].strip())
                continue

            candidate_length = current_length + len(paragraph) + (2 if current_chunk else 0)
            if current_chunk and candidate_length > max_chars:
                flush_current_chunk()

            current_chunk.append(paragraph)
            current_length += len(paragraph) + (2 if len(current_chunk) > 1 else 0)

        flush_current_chunk()
        return chunks or [normalized_text]

    @staticmethod
    def _build_mark_item_fingerprint(mark: HighlightItem) -> str:
        return _fingerprint(
            "marks",
            _normalize_text(mark.text),
            mark.create_time,
            mark.chapter_uid,
            mark.style,
        )

    @staticmethod
    def _build_review_item_fingerprint(review: ReviewItem) -> str:
        return _fingerprint(
            "reviews",
            _normalize_text(review.mark_text),
            _normalize_text(review.content),
            review.create_time,
            review.chapter_uid,
        )

    @staticmethod
    def _fallback_item_fingerprint(
        *,
        source_type: DocumentSourceType,
        item: WeReadLocalDocumentItemMetadata,
        body_text: str,
    ) -> str:
        return _fingerprint(
            source_type,
            body_text,
            item.create_time,
            item.chapter_uid,
            item.style,
        )

    @staticmethod
    def _build_chunk_id(
        *,
        doc_id: str,
        item_fingerprint: str,
        item_part_index: int,
        item_parts_count: int,
    ) -> str:
        if item_parts_count <= 1:
            return f"{doc_id}:item:{item_fingerprint}"
        return f"{doc_id}:item:{item_fingerprint}:part:{item_part_index:02d}"

    def extract_note_chunks(
        self,
        *,
        user_id: int,
        doc_id: str,
        max_review_chars: int = 1200,
    ) -> tuple[WeReadLocalDocumentMetadata, list[WeReadLocalNoteChunk]] | None:
        resolved = self.resolve_document_path(user_id=user_id, doc_id=doc_id)
        if resolved is None:
            return None

        metadata, document_path = resolved
        markdown_content = document_path.read_text(encoding="utf-8")
        extracted_items = self._extract_item_bodies(markdown_content)

        chunks: list[WeReadLocalNoteChunk] = []
        ordinal = 1
        for item in metadata.items:
            extracted_item = extracted_items.get(item.heading)
            if extracted_item is None:
                continue

            body_text = self._normalize_chunk_text(extracted_item["body"])
            item_fingerprint = item.item_fingerprint or self._fallback_item_fingerprint(
                source_type=metadata.source_type,
                item=item,
                body_text=body_text,
            )
            item_texts = [body_text]
            if metadata.source_type == "reviews":
                item_texts = self._split_large_item_text(text=body_text, max_chars=max_review_chars)

            for item_part_index, item_text in enumerate(item_texts, start=1):
                heading = item.heading
                if len(item_texts) > 1:
                    heading = f"{item.heading} (Part {item_part_index})"

                chunks.append(
                    WeReadLocalNoteChunk(
                        chunk_id=self._build_chunk_id(
                            doc_id=metadata.doc_id,
                            item_fingerprint=item_fingerprint,
                            item_part_index=item_part_index,
                            item_parts_count=len(item_texts),
                        ),
                        doc_id=metadata.doc_id,
                        user_id=metadata.user_id,
                        book_id=metadata.book_id,
                        book_title=metadata.book_title,
                        source_type=metadata.source_type,
                        ordinal=ordinal,
                        heading=heading,
                        chapter_uid=item.chapter_uid,
                        chapter_title=item.chapter_title or extracted_item["chapter_title"],
                        start_item_index=item.index,
                        end_item_index=item.index,
                        create_time=item.create_time,
                        style=item.style,
                        text=item_text,
                        char_count=len(item_text),
                        token_estimate=self._estimate_tokens(item_text),
                    )
                )
                ordinal += 1

        return metadata, chunks

    def _group_items_by_chapter(
        self,
        *,
        items: list[HighlightItem] | list[ReviewItem],
        include_chapter: bool,
        reverse_order: bool = True,
    ) -> list[tuple[str, int | None, list[HighlightItem] | list[ReviewItem]]]:
        if not items:
            return []

        if not include_chapter:
            return [("All Content", None, items)]

        grouped_items: dict[tuple[int | None, str], list[HighlightItem] | list[ReviewItem]] = {}
        ordered_keys: list[tuple[int | None, str]] = []

        source = reversed(list(items)) if reverse_order else items
        for item in source:
            chapter_title = _normalize_text(getattr(item, "chapter_title", ""))
            chapter_uid = getattr(item, "chapter_uid", None)
            if chapter_title:
                chapter_label = chapter_title
            elif chapter_uid is not None:
                chapter_label = f"Chapter {chapter_uid}"
            else:
                chapter_label = "Uncategorized"

            group_key = (chapter_uid, chapter_label)
            if group_key not in grouped_items:
                grouped_items[group_key] = []
                ordered_keys.append(group_key)
            grouped_items[group_key].append(item)

        return [(chapter_label, chapter_uid, grouped_items[(chapter_uid, chapter_label)]) for chapter_uid, chapter_label in ordered_keys]

    def _render_markdown(
        self,
        *,
        doc_id: str,
        user_id: int,
        book_id: str,
        book_title: str,
        source_type: DocumentSourceType,
        include_chapter: bool,
        highlight_style: int | None,
        item_count: int,
        item_blocks: list[str],
    ) -> str:
        frontmatter = _render_frontmatter(
            {
                "doc_id": doc_id,
                "user_id": user_id,
                "provider": "weread",
                "book_id": book_id,
                "book_title": book_title,
                "source_type": source_type,
                "include_chapter": include_chapter,
                "highlight_style": highlight_style,
                "item_count": item_count,
                "schema_version": 1,
            }
        )
        lines = [
            frontmatter,
            "",
            f"# {book_title}",
            "",
            f"## {source_type.title()}",
        ]
        if item_blocks:
            for item_block in item_blocks:
                lines.extend(["", item_block])
        else:
            lines.extend(["", "_No content materialized._"])
        return "\n".join(lines).strip() + "\n"

    def _render_mark_item(self, heading: str, mark: HighlightItem) -> str:
        lines = [f"### {heading}"]
        lines.extend(["", _normalize_text(mark.text) or "_empty_mark_"])
        return "\n".join(lines)

    def _render_quote_block(self, text: str) -> list[str]:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            return []

        quote_lines = []
        for line in normalized_text.splitlines():
            quote_lines.append("> " if not line else f"> {line}")
        return quote_lines

    def _render_review_item(self, heading: str, review: ReviewItem) -> str:
        lines = [f"### {heading}"]
        quote_block = self._render_quote_block(review.mark_text)
        if quote_block:
            lines.extend(["", *quote_block])
        lines.extend(["", _normalize_text(review.content) or "_empty_note_"])
        return "\n".join(lines)

    def _render_mark_sections(
        self,
        *,
        marks: list[HighlightItem],
        include_chapter: bool,
        reverse_order: bool = True,
    ) -> tuple[list[str], list[WeReadLocalDocumentItemMetadata]]:
        if not marks:
            return [], []

        sections: list[str] = []
        items_metadata: list[WeReadLocalDocumentItemMetadata] = []
        groups = self._group_items_by_chapter(items=marks, include_chapter=include_chapter, reverse_order=reverse_order)
        item_index = 1
        for chapter_label, chapter_uid, chapter_marks in groups:
            if include_chapter:
                section_lines = [f"## Chapter: {chapter_label}"]
                for mark in chapter_marks:
                    heading = f"Highlight {item_index:03d}"
                    items_metadata.append(
                        WeReadLocalDocumentItemMetadata(
                            index=item_index,
                            heading=heading,
                            item_fingerprint=self._build_mark_item_fingerprint(mark),
                            chapter_uid=chapter_uid,
                            chapter_title=chapter_label,
                            create_time=mark.create_time,
                            style=mark.style,
                        )
                    )
                    section_lines.extend(["", self._render_mark_item(heading, mark)])
                    item_index += 1
                sections.append("\n".join(section_lines))
                continue

            for mark in chapter_marks:
                heading = f"Highlight {item_index:03d}"
                items_metadata.append(
                    WeReadLocalDocumentItemMetadata(
                        index=item_index,
                        heading=heading,
                        item_fingerprint=self._build_mark_item_fingerprint(mark),
                        chapter_uid=chapter_uid,
                        chapter_title=chapter_label,
                        create_time=mark.create_time,
                        style=mark.style,
                    )
                )
                sections.append(self._render_mark_item(heading, mark))
                item_index += 1

        return sections, items_metadata

    def _render_review_sections(
        self,
        *,
        reviews: list[ReviewItem],
        include_chapter: bool,
        reverse_order: bool = True,
    ) -> tuple[list[str], list[WeReadLocalDocumentItemMetadata]]:
        if not reviews:
            return [], []

        sections: list[str] = []
        items_metadata: list[WeReadLocalDocumentItemMetadata] = []
        groups = self._group_items_by_chapter(items=reviews, include_chapter=include_chapter, reverse_order=reverse_order)
        item_index = 1
        for chapter_label, chapter_uid, chapter_reviews in groups:
            if include_chapter:
                section_lines = [f"## Chapter: {chapter_label}"]
                for review in chapter_reviews:
                    heading = f"Review {item_index:03d}"
                    items_metadata.append(
                        WeReadLocalDocumentItemMetadata(
                            index=item_index,
                            heading=heading,
                            item_fingerprint=self._build_review_item_fingerprint(review),
                            chapter_uid=chapter_uid,
                            chapter_title=chapter_label,
                            create_time=review.create_time,
                            style=None,
                        )
                    )
                    section_lines.extend(["", self._render_review_item(heading, review)])
                    item_index += 1
                sections.append("\n".join(section_lines))
                continue

            for review in chapter_reviews:
                heading = f"Review {item_index:03d}"
                items_metadata.append(
                    WeReadLocalDocumentItemMetadata(
                        index=item_index,
                        heading=heading,
                        item_fingerprint=self._build_review_item_fingerprint(review),
                        chapter_uid=chapter_uid,
                        chapter_title=chapter_label,
                        create_time=review.create_time,
                        style=None,
                    )
                )
                sections.append(self._render_review_item(heading, review))
                item_index += 1

        return sections, items_metadata

    def _load_existing_metadata(self, meta_path: Path) -> WeReadLocalDocumentMetadata | None:
        if not meta_path.exists():
            return None

        try:
            raw_data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        try:
            return WeReadLocalDocumentMetadata.model_validate(raw_data)
        except Exception:
            return None

    def _write_materialized_document(
        self,
        *,
        user_id: int,
        book_id: str,
        book_title: str,
        source_type: DocumentSourceType,
        include_chapter: bool,
        highlight_style: int | None,
        item_count: int,
        markdown_content: str,
        items_metadata: list[WeReadLocalDocumentItemMetadata],
    ) -> WeReadLocalDocumentMaterializationResponse:
        markdown_path = self._markdown_path(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
        )
        meta_path = self._meta_path(
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
        )
        markdown_path.parent.mkdir(parents=True, exist_ok=True)

        content_hash = _content_hash(markdown_content)
        metadata_hash = _metadata_hash(items_metadata)
        existing_metadata = self._load_existing_metadata(meta_path)
        if (
            existing_metadata is not None
            and markdown_path.exists()
            and existing_metadata.content_hash == content_hash
            and existing_metadata.metadata_hash == metadata_hash
        ):
            status = "unchanged"
            metadata = existing_metadata
        else:
            status = "created" if not markdown_path.exists() else "updated"
            metadata = WeReadLocalDocumentMetadata(
                doc_id=self._doc_id(
                    user_id=user_id,
                    book_id=book_id,
                    source_type=source_type,
                    include_chapter=include_chapter,
                    highlight_style=highlight_style,
                ),
                user_id=user_id,
                provider="weread",
                book_id=book_id,
                book_title=book_title,
                source_type=source_type,
                file_path=self._relative_file_path(markdown_path),
                generated_at=_now_iso(),
                include_chapter=include_chapter,
                highlight_style=highlight_style,
                item_count=item_count,
                content_hash=content_hash,
                metadata_hash=metadata_hash,
                schema_version=1,
                items=items_metadata,
            )
            markdown_path.write_text(markdown_content, encoding="utf-8")
            meta_path.write_text(
                json.dumps(metadata.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        logger.info(
            "weread_local_document_materialized",
            user_id=user_id,
            book_id=book_id,
            source_type=source_type,
            status=status,
            item_count=item_count,
            include_chapter=include_chapter,
            highlight_style=highlight_style,
            file_path=metadata.file_path,
        )
        return WeReadLocalDocumentMaterializationResponse(status=status, metadata=metadata)

    async def save_marks_document(
        self,
        *,
        user_id: int,
        document: BookMarkResponse,
    ) -> WeReadLocalDocumentMaterializationResponse:
        return await asyncio.to_thread(self._save_marks_document, user_id, document)

    def _save_marks_document(
        self,
        user_id: int,
        document: BookMarkResponse,
    ) -> WeReadLocalDocumentMaterializationResponse:
        doc_id = self._doc_id(
            user_id=user_id,
            book_id=document.book_id,
            source_type="marks",
            include_chapter=document.include_chapter,
            highlight_style=document.highlight_style,
        )
        item_blocks, items_metadata = self._render_mark_sections(
            marks=document.marks,
            include_chapter=document.include_chapter,
            reverse_order=document.reverse_order,
        )
        markdown_content = self._render_markdown(
            doc_id=doc_id,
            user_id=user_id,
            book_id=document.book_id,
            book_title=document.book_title,
            source_type="marks",
            include_chapter=document.include_chapter,
            highlight_style=document.highlight_style,
            item_count=document.total_marks_count,
            item_blocks=item_blocks,
        )
        return self._write_materialized_document(
            user_id=user_id,
            book_id=document.book_id,
            book_title=document.book_title,
            source_type="marks",
            include_chapter=document.include_chapter,
            highlight_style=document.highlight_style,
            item_count=document.total_marks_count,
            markdown_content=markdown_content,
            items_metadata=items_metadata,
        )

    async def save_reviews_document(
        self,
        *,
        user_id: int,
        document: BookReviewsResponse,
    ) -> WeReadLocalDocumentMaterializationResponse:
        return await asyncio.to_thread(self._save_reviews_document, user_id, document)

    def _save_reviews_document(
        self,
        user_id: int,
        document: BookReviewsResponse,
    ) -> WeReadLocalDocumentMaterializationResponse:
        doc_id = self._doc_id(
            user_id=user_id,
            book_id=document.book_id,
            source_type="reviews",
            include_chapter=document.include_chapter,
            highlight_style=None,
        )
        item_blocks, items_metadata = self._render_review_sections(
            reviews=document.reviews,
            include_chapter=document.include_chapter,
            reverse_order=document.reverse_order,
        )
        markdown_content = self._render_markdown(
            doc_id=doc_id,
            user_id=user_id,
            book_id=document.book_id,
            book_title=document.book_title,
            source_type="reviews",
            include_chapter=document.include_chapter,
            highlight_style=None,
            item_count=document.total_reviews_count,
            item_blocks=item_blocks,
        )
        return self._write_materialized_document(
            user_id=user_id,
            book_id=document.book_id,
            book_title=document.book_title,
            source_type="reviews",
            include_chapter=document.include_chapter,
            highlight_style=None,
            item_count=document.total_reviews_count,
            markdown_content=markdown_content,
            items_metadata=items_metadata,
        )


weread_local_document_store = WeReadLocalDocumentStore()
