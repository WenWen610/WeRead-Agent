"""Tests for DeepAgentClient static methods (no agent required)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app.runtimes.deep_agent.client import DeepAgentClient
from app.schemas.chat import Message, StreamEvent


class TestExtractTextContent:
    def test_string_content(self):
        assert DeepAgentClient._extract_text_content("hello") == "hello"

    def test_list_content_with_text_dicts(self):
        content = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
        assert DeepAgentClient._extract_text_content(content) == "hello world"

    def test_list_content_mixed(self):
        content = ["plain ", {"type": "text", "text": "rich"}]
        assert DeepAgentClient._extract_text_content(content) == "plain rich"

    def test_empty_content(self):
        assert DeepAgentClient._extract_text_content("") == ""

    def test_none_content(self):
        assert DeepAgentClient._extract_text_content(None) == ""

    def test_list_with_non_text_dicts(self):
        content = [{"type": "image_url", "image_url": {"url": "..."}}]
        assert DeepAgentClient._extract_text_content(content) == ""

    def test_empty_list(self):
        assert DeepAgentClient._extract_text_content([]) == ""


class TestIsAssistantTextChunk:
    def test_ai_message_chunk(self):
        chunk = AIMessageChunk(content="hello")
        assert DeepAgentClient._is_assistant_text_chunk(chunk) is True

    def test_tool_message_chunk(self):
        chunk = ToolMessage(content="result", tool_call_id="123")
        assert DeepAgentClient._is_assistant_text_chunk(chunk) is False

    def test_ai_message(self):
        chunk = AIMessage(content="hello")
        assert DeepAgentClient._is_assistant_text_chunk(chunk) is True

    def test_human_message(self):
        chunk = HumanMessage(content="hello")
        assert DeepAgentClient._is_assistant_text_chunk(chunk) is False

    def test_custom_message_with_toolmessage_in_name(self):
        class FakeToolMessage:
            type = "ToolMessageChunk"

        assert DeepAgentClient._is_assistant_text_chunk(FakeToolMessage()) is False

    def test_custom_message_with_aimessage_in_name(self):
        class FakeAIMessage:
            type = "CustomAIMessage"

        assert DeepAgentClient._is_assistant_text_chunk(FakeAIMessage()) is True


class TestHasToolCallPayload:
    def test_tool_call_chunks(self):
        chunk = AIMessageChunk(content="", tool_call_chunks=[{"id": "call_1", "name": "test", "args": "{}"}])
        assert DeepAgentClient._has_tool_call_payload(chunk) is True

    def test_no_tool_calls(self):
        chunk = AIMessageChunk(content="hello")
        assert DeepAgentClient._has_tool_call_payload(chunk) is False

    def test_additional_kwargs_tool_calls(self):
        chunk = AIMessageChunk(content="", additional_kwargs={"tool_calls": [{"function": {"name": "test"}}]})
        assert DeepAgentClient._has_tool_call_payload(chunk) is True

    def test_tool_calls_attribute(self):
        chunk = AIMessage(content="", tool_calls=[{"id": "call_1", "name": "test", "args": {}}])
        assert DeepAgentClient._has_tool_call_payload(chunk) is True

    def test_empty_additional_kwargs(self):
        chunk = AIMessageChunk(content="", additional_kwargs={})
        assert DeepAgentClient._has_tool_call_payload(chunk) is False


class TestIterUpdateItems:
    def test_dict_chunk(self):
        chunk = {"agent": {"messages": ["msg1"]}, "tools": {"result": "ok"}}
        items = DeepAgentClient._iter_update_items(chunk)
        assert items == [("agent", {"messages": ["msg1"]}), ("tools", {"result": "ok"})]

    def test_non_dict_chunk(self):
        assert DeepAgentClient._iter_update_items("not a dict") == []

    def test_empty_dict(self):
        assert DeepAgentClient._iter_update_items({}) == []

    def test_non_dict_values_are_skipped(self):
        chunk = {"agent": "just a string", "tools": {"result": "ok"}}
        items = DeepAgentClient._iter_update_items(chunk)
        assert items == [("tools", {"result": "ok"})]


class TestExtractArtifacts:
    def test_artifacts_in_update(self):
        seen: set[str] = set()
        artifacts = [{"artifact_id": "a1", "name": "Artifact 1"}, {"artifact_id": "a2", "name": "Artifact 2"}]
        result = DeepAgentClient._extract_artifacts({"artifacts": artifacts}, seen)
        assert len(result) == 2
        assert seen == {"a1", "a2"}

    def test_duplicate_artifacts_skipped(self):
        seen: set[str] = {"a1"}
        artifacts = [{"artifact_id": "a1", "name": "Already seen"}, {"artifact_id": "a2", "name": "New"}]
        result = DeepAgentClient._extract_artifacts({"artifacts": artifacts}, seen)
        assert len(result) == 1
        assert result[0]["artifact_id"] == "a2"
        assert seen == {"a1", "a2"}

    def test_artifacts_from_json_in_messages(self):
        seen: set[str] = set()
        msg_content = json.dumps({"artifacts": [{"artifact_id": "a1", "name": "From JSON"}]})
        update = {"messages": [AIMessage(content=msg_content)]}
        result = DeepAgentClient._extract_artifacts(update, seen)
        assert len(result) == 1
        assert result[0]["name"] == "From JSON"

    def test_no_artifacts(self):
        seen: set[str] = set()
        assert DeepAgentClient._extract_artifacts({"messages": []}, seen) == []

    def test_non_list_artifacts(self):
        seen: set[str] = set()
        assert DeepAgentClient._extract_artifacts({"artifacts": "not a list"}, seen) == []

    def test_invalid_json_in_messages(self):
        seen: set[str] = set()
        update = {"messages": [AIMessage(content="not json")]}
        assert DeepAgentClient._extract_artifacts(update, seen) == []


class TestBuildClarificationEvent:
    def test_valid_clarification(self):
        update = {
            "pending_clarification": {
                "clarification_type": "missing_info",
                "question": "Which book?",
                "options": ["Book A", "Book B"],
            }
        }
        event = DeepAgentClient._build_clarification_event(update)
        assert event is not None
        assert event.type == "clarification"
        assert event.data["question"] == "Which book?"
        assert event.data["options"] == ["Book A", "Book B"]
        assert event.data["clarification_type"] == "missing_info"

    def test_missing_question(self):
        update = {"pending_clarification": {"clarification_type": "missing_info", "question": ""}}
        assert DeepAgentClient._build_clarification_event(update) is None

    def test_non_dict_pending(self):
        assert DeepAgentClient._build_clarification_event({"pending_clarification": "not dict"}) is None

    def test_no_clarification(self):
        assert DeepAgentClient._build_clarification_event({}) is None


class TestBuildCapabilityRequiredEvent:
    def test_valid_capability(self):
        update = {
            "pending_capability_requirement": {
                "provider": "weread",
                "reason": "binding_required",
                "message": "Please connect WeRead.",
            }
        }
        event = DeepAgentClient._build_capability_required_event(update)
        assert event is not None
        assert event.type == "capability_required"
        assert event.data["provider"] == "weread"
        assert event.data["reason"] == "binding_required"

    def test_missing_provider(self):
        update = {
            "pending_capability_requirement": {
                "reason": "binding_required",
                "message": "Please connect WeRead.",
            }
        }
        assert DeepAgentClient._build_capability_required_event(update) is None

    def test_missing_reason(self):
        update = {
            "pending_capability_requirement": {
                "provider": "weread",
                "message": "Please connect WeRead.",
            }
        }
        assert DeepAgentClient._build_capability_required_event(update) is None

    def test_missing_message(self):
        update = {
            "pending_capability_requirement": {
                "provider": "weread",
                "reason": "binding_required",
            }
        }
        assert DeepAgentClient._build_capability_required_event(update) is None

    def test_with_action_and_source(self):
        update = {
            "pending_capability_requirement": {
                "provider": "weread",
                "reason": "reauth_required",
                "message": "Reauth needed.",
                "action": "connect_weread",
                "source_tool": "resolve_weread_book",
            }
        }
        event = DeepAgentClient._build_capability_required_event(update)
        assert event is not None
        assert event.data["action"] == "connect_weread"
        assert event.data["source_tool"] == "resolve_weread_book"

    def test_non_dict_pending(self):
        assert DeepAgentClient._build_capability_required_event({"pending_capability_requirement": "x"}) is None

    def test_no_pending(self):
        assert DeepAgentClient._build_capability_required_event({}) is None


class TestBuildBookResolvedStatus:
    def test_valid_book(self):
        update = {"current_book": {"book_id": "123", "title": "Book Title"}}
        event = DeepAgentClient._build_book_resolved_status(update)
        assert event is not None
        assert event.type == "status"
        assert event.data["stage"] == "book_resolved"
        assert event.data["book_id"] == "123"
        assert event.data["title"] == "Book Title"
        assert "Book Title" in event.data["message"]

    def test_missing_book_id(self):
        update = {"current_book": {"title": "Book Title"}}
        assert DeepAgentClient._build_book_resolved_status(update) is None

    def test_missing_title(self):
        update = {"current_book": {"book_id": "123"}}
        assert DeepAgentClient._build_book_resolved_status(update) is None

    def test_empty_book_id(self):
        update = {"current_book": {"book_id": "", "title": "Book Title"}}
        assert DeepAgentClient._build_book_resolved_status(update) is None

    def test_non_dict_current_book(self):
        update = {"current_book": "not dict"}
        assert DeepAgentClient._build_book_resolved_status(update) is None

    def test_no_current_book(self):
        assert DeepAgentClient._build_book_resolved_status({}) is None


class TestExtractTodos:
    def test_todos_key(self):
        update = {"todos": [{"id": "1", "task": "Test"}]}
        result = DeepAgentClient._extract_todos(update)
        assert result == [{"id": "1", "task": "Test"}]

    def test_todo_key(self):
        update = {"todo": [{"id": "1"}]}
        result = DeepAgentClient._extract_todos(update)
        assert result == [{"id": "1"}]

    def test_todo_list_key(self):
        update = {"todo_list": [{"id": "1"}]}
        result = DeepAgentClient._extract_todos(update)
        assert result == [{"id": "1"}]

    def test_no_todo_keys(self):
        assert DeepAgentClient._extract_todos({"other": "data"}) is None

    def test_non_list_value(self):
        assert DeepAgentClient._extract_todos({"todos": "not list"}) is None

    def test_filters_non_dicts(self):
        update = {"todos": [{"id": "1"}, "not a dict"]}
        result = DeepAgentClient._extract_todos(update)
        assert result == [{"id": "1"}]


class TestResolveWebSearchEnabled:
    def test_explicit_value_takes_precedence(self):
        assert DeepAgentClient._resolve_web_search_enabled(True) is True
        assert DeepAgentClient._resolve_web_search_enabled(False) is False

    def test_none_falls_back_to_setting(self, monkeypatch):
        monkeypatch.setattr("app.infrastructure.config.settings.DEEP_AGENT_WEB_SEARCH_ENABLED", True)
        assert DeepAgentClient._resolve_web_search_enabled(None) is True


class TestProcessMessages:
    def test_human_and_ai_messages(self):
        messages = [
            HumanMessage(content="user query"),
            AIMessage(content="assistant response"),
        ]
        result = DeepAgentClient._process_messages(messages)
        assert result == [
            Message(role="user", content="user query"),
            Message(role="assistant", content="assistant response"),
        ]

    def test_skips_empty_content(self):
        messages = [
            HumanMessage(content=""),
            AIMessage(content="response"),
        ]
        result = DeepAgentClient._process_messages(messages)
        assert result == [Message(role="assistant", content="response")]

    def test_skips_tool_messages(self):
        messages = [
            HumanMessage(content="query"),
            ToolMessage(content="tool result", tool_call_id="123"),
        ]
        result = DeepAgentClient._process_messages(messages)
        assert result == [Message(role="user", content="query")]

    def test_empty_list(self):
        assert DeepAgentClient._process_messages([]) == []


class TestEncodeSSE:
    def test_basic_sse(self):
        event = StreamEvent(type="chunk", data={"content": "hello"})
        encoded = DeepAgentClient.encode_sse(event)
        expected = f"data: {json.dumps({'type': 'chunk', 'data': {'content': 'hello'}}, ensure_ascii=False)}\n\n"
        assert encoded == expected

    def test_sse_with_unicode(self):
        event = StreamEvent(type="chunk", data={"content": "你好世界"})
        encoded = DeepAgentClient.encode_sse(event)
        assert "你好世界" in encoded
