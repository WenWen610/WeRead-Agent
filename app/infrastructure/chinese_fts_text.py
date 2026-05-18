"""Jieba segmentation + FTS5 MATCH helpers for Chinese full-text search."""

from __future__ import annotations

import jieba
import re
import unicodedata

# Limit MATCH token count to avoid huge queries
_MAX_FTS_MATCH_TOKENS = 24
_MIN_EN_TOKEN_LENGTH = 3
_EN_ONLY_RE = re.compile(r"^[a-zA-Z]+$")
_NUMBER_ONLY_RE = re.compile(r"^\d+$")

# OpenClaw-style stop words (subset tuned for Chinese conversational recall queries).
_STOP_WORDS_ZH = {
    "我", "我们", "你", "你们", "他", "她", "它", "他们",
    "这", "那", "这个", "那个", "这些", "那些",
    "的", "了", "着", "过", "得", "地", "吗", "呢", "吧", "啊", "呀", "嘛", "啦",
    "是", "有", "在", "被", "把", "给", "让", "用", "到", "去", "来", "做", "说", "看", "找", "想", "要", "能", "会", "可以",
    "和", "与", "或", "但", "但是", "因为", "所以", "如果", "虽然", "而", "也", "都", "就", "还", "又", "再", "才", "只",
    "之前", "以前", "之后", "以后", "刚才", "现在", "昨天", "今天", "明天", "最近",
    "东西", "事情", "事", "什么", "哪个", "哪些", "怎么", "为什么", "多少",
    "请", "帮", "帮忙", "告诉",
}
_STOP_WORDS_EN = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "for", "with", "from", "by",
    "and", "or", "but", "if", "then", "so", "than",
    "i", "we", "you", "he", "she", "it", "they",
    "this", "that", "these", "those", "what", "which", "who", "whom", "whose", "how", "why",
    "please", "help", "tell",
}


def _is_all_punct_or_symbol(token: str) -> bool:
    if not token:
        return True
    for char in token:
        category = unicodedata.category(char)
        if not (category.startswith("P") or category.startswith("S")):
            return False
    return True


def _is_query_stop_word_token(token: str) -> bool:
    lowered = token.lower()
    return token in _STOP_WORDS_ZH or lowered in _STOP_WORDS_EN


def _is_valid_keyword(token: str) -> bool:
    if not token:
        return False
    if _EN_ONLY_RE.fullmatch(token) and len(token) < _MIN_EN_TOKEN_LENGTH:
        return False
    if _NUMBER_ONLY_RE.fullmatch(token):
        return False
    if _is_all_punct_or_symbol(token):
        return False
    return True


def segment_text_for_fts(text: str) -> str:
    """Segment text with jieba; return space-separated tokens for unicode61 FTS indexing."""
    cleaned = " ".join(str(text).strip().split())
    if not cleaned:
        return ""
    # cut_for_search yields finer tokens (e.g. 婚姻 + 婚姻制度) so queries like 婚姻 still match
    tokens = [t.strip() for t in jieba.cut_for_search(cleaned) if t.strip()]
    return " ".join(tokens)


def extract_keywords_for_fts(raw: str, *, max_keywords: int = _MAX_FTS_MATCH_TOKENS) -> list[str]:
    """Extract OpenClaw-style query keywords from jieba tokens for FTS search."""
    segmented = segment_text_for_fts(raw)
    if not segmented:
        return []

    keywords: list[str] = []
    seen: set[str] = set()
    for raw_token in segmented.split():
        token = raw_token.strip()
        if not token:
            continue
        normalized = token.lower()
        if _is_query_stop_word_token(normalized):
            continue
        if not _is_valid_keyword(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(normalized)
        if len(keywords) >= max(1, max_keywords):
            break
    return keywords


def build_fts5_match_query(raw: str) -> str | None:
    """Build an FTS5 MATCH string: quoted tokens joined with AND (OpenClaw buildFtsQuery style)."""
    tokens = extract_keywords_for_fts(raw, max_keywords=_MAX_FTS_MATCH_TOKENS)
    if not tokens:
        return None
    parts: list[str] = []
    for token in tokens:
        escaped = token.replace('"', '""')
        parts.append(f'"{escaped}"')
    return " AND ".join(parts)


def fts_virtual_table_uses_trigram(sql: str | None) -> bool:
    if not sql:
        return False
    return "tokenize = 'trigram'" in sql or 'tokenize="trigram"' in sql.replace(" ", "")
