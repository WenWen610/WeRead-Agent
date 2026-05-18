from app.infrastructure.chinese_fts_text import (
    build_fts5_match_query,
    extract_keywords_for_fts,
)


def test_extract_keywords_for_fts_filters_stopwords_and_noise() -> None:
    keywords = extract_keywords_for_fts("请帮我找一下之前讨论的 API 方案，2024 的那个。")
    assert "请" not in keywords
    assert "帮" not in keywords
    assert "我" not in keywords
    assert "之前" not in keywords
    assert "2024" not in keywords
    assert "api" in keywords
    assert "方案" in keywords


def test_extract_keywords_for_fts_deduplicates_and_lowercases_english() -> None:
    keywords = extract_keywords_for_fts("API api Api 鉴权 鉴权")
    assert keywords.count("api") == 1
    assert keywords.count("鉴权") == 1


def test_extract_keywords_for_fts_keeps_informative_single_char_chinese() -> None:
    keywords = extract_keywords_for_fts("税 法 的 了")
    assert "税" in keywords
    assert "法" in keywords
    assert "的" not in keywords
    assert "了" not in keywords


def test_build_fts5_match_query_uses_filtered_keywords_and_and_join() -> None:
    query = build_fts5_match_query("请帮我找 API 方案")
    assert query == '"api" AND "方案"'


def test_build_fts5_match_query_returns_none_when_all_tokens_filtered() -> None:
    query = build_fts5_match_query("请 帮 我 的 了")
    assert query is None
