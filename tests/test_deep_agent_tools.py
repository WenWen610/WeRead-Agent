"""Tests for Deep Agent tool assembly."""

from app.runtimes.deep_agent.tools import (
    build_deep_agent_tools,
    build_weread_analysis_tools,
)


def _tool_names(tools) -> set[str]:
    return {t.name for t in tools}


class TestBuildDeepAgentTools:
    def test_includes_clarification_tool(self):
        tools = build_deep_agent_tools()
        names = _tool_names(tools)
        assert "ask_clarification" in names

    def test_includes_connect_weread_tool(self):
        tools = build_deep_agent_tools()
        names = _tool_names(tools)
        assert "connect_weread" in names

    def test_includes_weread_tools(self):
        tools = build_deep_agent_tools()
        names = _tool_names(tools)
        assert "resolve_weread_book" in names
        assert "get_weread_bookshelf_snapshot" in names
        assert "search_weread_bookshelf" in names
        assert "get_weread_book_reading_status" in names
        assert "get_weread_book_note_counts" in names

    def test_includes_present_document_tool(self):
        tools = build_deep_agent_tools()
        names = _tool_names(tools)
        assert "present_weread_book_document" in names

    def test_includes_search_notes_tool(self):
        tools = build_deep_agent_tools()
        names = _tool_names(tools)
        assert "search_weread_notes" in names

    def test_web_search_disabled_by_default(self):
        tools = build_deep_agent_tools()
        names = _tool_names(tools)
        assert "duckduckgo_search" not in names

    def test_web_search_enabled(self):
        tools = build_deep_agent_tools(web_search_enabled=True)
        names = _tool_names(tools)
        assert "duckduckgo_results_json" in names

    def test_total_tool_count_without_web_search(self):
        tools = build_deep_agent_tools()
        # ask_clarification + connect_weread + 5 weread tools + search_weread_notes + present_weread_book_document = 9
        assert len(tools) == 9

    def test_total_tool_count_with_web_search(self):
        tools = build_deep_agent_tools(web_search_enabled=True)
        assert len(tools) == 10


class TestBuildWereadAnalysisTools:
    def test_includes_resolve_and_document_tools(self):
        tools = build_weread_analysis_tools()
        names = _tool_names(tools)
        assert "resolve_weread_book" in names
        assert "ensure_weread_book_document" in names
        assert "read_weread_document_outline" in names
        assert "read_weread_document_section" in names

    def total_tool_count(self):
        tools = build_weread_analysis_tools()
        assert len(tools) == 4
