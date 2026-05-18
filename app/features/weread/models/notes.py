from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NotesBookInfo(BaseModel):
    author: str = ""
    translator: str = ""
    publisher: str = ""
    publish_time: str = ""
    word_count: int = 0
    rating: float = 0.0
    category: str = ""


class NotesReadingStatus(BaseModel):
    progress: int = 0
    reading_time: int = 0
    reading_time_formatted: str = ""
    start_reading_time: str = ""
    has_started_reading: bool = False
    last_read_time: str = ""
    finish_reading: bool = False


class HighlightItem(BaseModel):
    text: str = ""
    style: int = 0
    create_time: str = ""
    chapter_uid: int | None = None
    chapter_title: str = ""


class ReviewItem(BaseModel):
    content: str = ""
    mark_text: str = ""
    create_time: str = ""
    chapter_uid: int | None = None
    chapter_title: str = ""


class UncategorizedNotes(BaseModel):
    marks: list[HighlightItem] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)


class ChapterNode(BaseModel):
    uid: int
    title: str = ""
    children: list["ChapterNode"] = Field(default_factory=list)
    marks: list[HighlightItem] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)


class BookReadingStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    book_title: str = ""
    book_info: NotesBookInfo
    reading_status: NotesReadingStatus
    last_updated: str = ""


class BookMarkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    book_title: str = ""
    include_chapter: bool = True
    highlight_style: int | None = None
    reverse_order: bool = True
    total_marks_count: int = 0
    returned_marks_count: int = 0
    omitted_marks_count: int = 0
    preview_mode: bool = False
    next_action: str | None = None
    marks: list[HighlightItem] = Field(default_factory=list)
    last_updated: str = ""


class BookReviewsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    book_title: str = ""
    include_chapter: bool = True
    reverse_order: bool = True
    total_reviews_count: int = 0
    returned_reviews_count: int = 0
    omitted_reviews_count: int = 0
    preview_mode: bool = False
    next_action: str | None = None
    reviews: list[ReviewItem] = Field(default_factory=list)
    last_updated: str = ""


class BookNotesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    total_notes: int = 0
    mark_count: int = 0
    review_count: int = 0
    last_updated: str = ""


class WeReadLocalDocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = ""
    user_id: int = 0
    provider: Literal["weread"] = "weread"
    book_id: str = ""
    book_title: str = ""
    source_type: Literal["marks", "reviews"] = "marks"
    file_path: str = ""
    generated_at: str = ""
    include_chapter: bool = True
    highlight_style: int | None = None
    item_count: int = 0
    content_hash: str = ""
    metadata_hash: str = ""
    schema_version: int = 1
    items: list["WeReadLocalDocumentItemMetadata"] = Field(default_factory=list)


class WeReadLocalDocumentItemMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    heading: str = ""
    item_fingerprint: str = ""
    chapter_uid: int | None = None
    chapter_title: str = ""
    create_time: str = ""
    style: int | None = None


class WeReadLocalDocumentMaterializationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["created", "updated", "unchanged"] = "created"
    metadata: WeReadLocalDocumentMetadata


class WeReadDocumentArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = ""
    kind: Literal["weread_markdown"] = "weread_markdown"
    doc_id: str = ""
    book_id: str = ""
    book_title: str = ""
    source_type: Literal["marks", "reviews"] = "marks"
    name: str = ""
    generated_at: str = ""


class WeReadBookNotesExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    source_type: Literal["marks", "reviews", "both"] = "marks"
    exported_documents: list[WeReadLocalDocumentMaterializationResponse] = Field(default_factory=list)
    artifacts: list[WeReadDocumentArtifact] = Field(default_factory=list)


class WeReadLocalDocumentIndexResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = ""
    book_id: str = ""
    book_title: str = ""
    source_type: Literal["marks", "reviews"] = "marks"
    status: Literal["created", "updated", "unchanged"] = "created"
    chunk_count: int = 0
    indexed_at: str = ""
    embedding_status: Literal["disabled", "pending", "ready"] = "pending"


class WeReadLocalDocumentIndexState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str = ""
    chunk_count: int = 0
    index_status: Literal["missing", "pending", "fts_ready", "ready"] = "missing"
    has_searchable_index: bool = False
    is_fresh: bool = False


class WeReadLocalNoteChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = ""
    doc_id: str = ""
    user_id: int = 0
    book_id: str = ""
    book_title: str = ""
    source_type: Literal["marks", "reviews"] = "marks"
    ordinal: int = 0
    heading: str = ""
    chapter_uid: int | None = None
    chapter_title: str = ""
    start_item_index: int = 0
    end_item_index: int = 0
    create_time: str = ""
    style: int | None = None
    text: str = ""
    char_count: int = 0
    token_estimate: int = 0


class WeReadLocalNoteChunkHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = ""
    doc_id: str = ""
    book_id: str = ""
    book_title: str = ""
    source_type: Literal["marks", "reviews"] = "marks"
    ordinal: int = 0
    heading: str = ""
    chapter_uid: int | None = None
    chapter_title: str = ""
    create_time: str = ""
    style: int | None = None
    text: str = ""
    score: float = 0.0
    text_score: float | None = None
    vector_score: float | None = None


class WeReadLocalNoteChunkSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_id: str = ""
    source_type: Literal["marks", "reviews", "both"] = "marks"
    query: str = ""
    total_hits: int = 0
    retrieval_mode: Literal["fts", "hybrid"] = "fts"
    hits: list[WeReadLocalNoteChunkHit] = Field(default_factory=list)


class BookMarksReivewsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["by_chapter", "flat"] = "flat"
    include_chapter: bool = True
    highlight_style: int | None = None

    book_id: str = ""
    book_title: str = ""
    book_info: NotesBookInfo
    reading_status: NotesReadingStatus
    total_marks_count: int = 0
    total_reviews_count: int = 0
    returned_marks_count: int = 0
    returned_reviews_count: int = 0
    omitted_marks_count: int = 0
    omitted_reviews_count: int = 0
    preview_mode: bool = False
    next_action: str | None = None
    last_updated: str = ""

    chapters: list[ChapterNode] = Field(default_factory=list)
    marks: list[HighlightItem] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)
    uncategorized: UncategorizedNotes = Field(default_factory=UncategorizedNotes)


# 解析 ChapterNode 的前向引用（children: list["ChapterNode"]），
# 确保递归模型在校验/序列化时可正常工作。
ChapterNode.model_rebuild()
