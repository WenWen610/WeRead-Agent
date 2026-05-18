import importlib
import sys
import types
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.limiter import limiter
from app.schemas.chat import ChatTurnResponse, Message, StreamEvent


@dataclass
class _FakeSession:
    id: str
    user_id: int
    name: str = ""


class _FakeAgentClient:
    async def chat(
        self,
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> ChatTurnResponse:
        _ = web_search_enabled
        raise NotImplementedError

    async def stream(
        self,
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        _ = web_search_enabled
        raise NotImplementedError
        yield  # pragma: no cover

    async def get_chat_history(self, thread_id: str):
        _ = thread_id
        return []

    async def get_thread_artifacts(self, thread_id: str):
        _ = thread_id
        return []

    async def clear_chat_history(self, thread_id: str):
        _ = thread_id
        return None


class _FakeChatStore:
    async def create_chat_item(self, **kwargs):
        _ = kwargs
        return None

    async def get_user_chat_threads(self, user_id: int, include_archived: bool = False):
        _ = (user_id, include_archived)
        return []

    async def create_chat_thread(self, thread_id: str, user_id: int, title: str = ""):
        _ = (thread_id, user_id, title)
        raise NotImplementedError

    async def get_chat_items(self, thread_id: str):
        _ = thread_id
        return []

    async def get_chat_thread(self, thread_id: str):
        _ = thread_id
        return None

    async def update_chat_thread_title(self, thread_id: str, title: str):
        _ = (thread_id, title)
        raise NotImplementedError

    async def delete_chat_thread(self, thread_id: str):
        _ = thread_id
        return True


def _build_session() -> _FakeSession:
    return _FakeSession(id="session-thread-1", user_id=123, name="test-session")


def _load_chatbot_module(monkeypatch):
    fake_auth = types.ModuleType("app.api.v1.auth")

    async def fake_get_current_session():
        return _build_session()

    fake_auth.get_current_session = fake_get_current_session

    fake_client = types.ModuleType("app.runtimes.deep_agent.client")
    fake_client.DeepAgentClient = _FakeAgentClient

    fake_chat_store = types.ModuleType("app.features.chat.store")
    fake_chat_store.chat_store = _FakeChatStore()

    monkeypatch.setitem(sys.modules, "app.api.v1.auth", fake_auth)
    monkeypatch.setitem(sys.modules, "app.runtimes.deep_agent.client", fake_client)
    monkeypatch.setitem(sys.modules, "app.features.chat.store", fake_chat_store)
    sys.modules.pop("app.api.v1.chatbot", None)

    return importlib.import_module("app.api.v1.chatbot")


def _build_test_app(chatbot_module) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(chatbot_module.router)
    return app


def test_chat_returns_answer_payload(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_chat(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> ChatTurnResponse:
        assert message == "你好"
        assert thread_id == "session-thread-1"
        assert user_id == 123
        assert web_search_enabled is None
        return ChatTurnResponse(type="answer", data={"content": "你好，我可以帮你查询微信读书数据。"})

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "chat", fake_chat)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "你好"})

    assert response.status_code == 200
    assert response.json() == {
        "type": "answer",
        "data": {"content": "你好，我可以帮你查询微信读书数据。"},
    }
    assert persisted_items == [
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "user",
            "content": "你好",
        },
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "assistant",
            "content": "你好，我可以帮你查询微信读书数据。",
        },
    ]


def test_chat_returns_clarification_payload(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_chat(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> ChatTurnResponse:
        assert message == "小王子的划线"
        assert thread_id == "custom-thread"
        assert user_id == 123
        assert web_search_enabled is None
        return ChatTurnResponse(
            type="clarification",
            data={
                "question": "你想查询哪一本？",
                "options": ["小王子", "小王子（英文版）"],
                "clarification_type": "candidate_selection",
            },
        )

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "chat", fake_chat)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        response = client.post(
            "/chat",
            json={"message": "小王子的划线", "thread_id": "custom-thread"},
        )

    assert response.status_code == 200
    assert response.json()["type"] == "clarification"
    assert response.json()["data"]["question"] == "你想查询哪一本？"
    assert persisted_items == [
        {
            "thread_id": "custom-thread",
            "user_id": 123,
            "item_type": "user",
            "content": "小王子的划线",
        },
        {
            "thread_id": "custom-thread",
            "user_id": 123,
            "item_type": "clarification",
            "content": "你想查询哪一本？",
            "item_metadata": {
                "question": "你想查询哪一本？",
                "options": ["小王子", "小王子（英文版）"],
                "clarification_type": "candidate_selection",
            },
        },
    ]


def test_chat_returns_capability_required_payload(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_chat(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> ChatTurnResponse:
        assert message == "我读过这本书吗"
        assert thread_id == "session-thread-1"
        assert user_id == 123
        assert web_search_enabled is None
        return ChatTurnResponse(
            type="capability_required",
            data={
                "provider": "weread",
                "reason": "binding_required",
                "message": "需要先连接微信读书后才能继续查询。",
                "action": "connect_weread",
            },
        )

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "chat", fake_chat)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "我读过这本书吗"})

    assert response.status_code == 200
    assert response.json()["type"] == "capability_required"
    assert response.json()["data"]["provider"] == "weread"
    assert persisted_items == [
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "user",
            "content": "我读过这本书吗",
        },
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "capability_notice",
            "content": "需要先连接微信读书后才能继续查询。",
            "item_metadata": {
                "provider": "weread",
                "reason": "binding_required",
                "message": "需要先连接微信读书后才能继续查询。",
                "action": "connect_weread",
            },
        },
    ]


def test_chat_stream_emits_chunk_status_and_done_events(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_stream(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        assert message == "这本书的划线"
        assert thread_id == "session-thread-1"
        assert user_id == 123
        assert web_search_enabled is None
        yield StreamEvent(type="status", data={"stage": "tool_query", "message": "正在查询微信读书数据"})
        yield StreamEvent(type="chunk", data={"content": "这是第一段回答。"})
        yield StreamEvent(type="done", data={})

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "stream", fake_stream)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        with client.stream("POST", "/chat/stream", json={"message": "这本书的划线"}) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert "event: status" in body
    assert '"stage": "tool_query"' in body
    assert "event: chunk" in body
    assert '"content": "这是第一段回答。"' in body
    assert "event: done" in body
    assert persisted_items == [
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "user",
            "content": "这本书的划线",
        },
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "assistant",
            "content": "这是第一段回答。",
        },
    ]


def test_chat_stream_emits_capability_required_event(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_stream(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        assert message == "我读过这本书吗"
        assert thread_id == "session-thread-1"
        assert user_id == 123
        assert web_search_enabled is None
        yield StreamEvent(
            type="capability_required",
            data={
                "provider": "weread",
                "reason": "binding_required",
                "message": "需要先连接微信读书后才能继续查询。",
                "action": "connect_weread",
            },
        )
        yield StreamEvent(type="done", data={})

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "stream", fake_stream)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        with client.stream("POST", "/chat/stream", json={"message": "我读过这本书吗"}) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert "event: capability_required" in body
    assert '"provider": "weread"' in body
    assert '"reason": "binding_required"' in body
    assert persisted_items == [
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "user",
            "content": "我读过这本书吗",
        },
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "capability_notice",
            "content": "需要先连接微信读书后才能继续查询。",
            "item_metadata": {
                "provider": "weread",
                "reason": "binding_required",
                "message": "需要先连接微信读书后才能继续查询。",
                "action": "connect_weread",
            },
        },
    ]


def test_chat_stream_emits_clarification_event(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_stream(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        assert (message, thread_id, user_id) == ("小王子的划线", "session-thread-1", 123)
        assert web_search_enabled is None
        yield StreamEvent(
            type="clarification",
            data={
                "question": "你想查询哪一本？",
                "options": ["小王子", "小王子（英文版）"],
                "clarification_type": "candidate_selection",
            },
        )
        yield StreamEvent(type="done", data={})

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "stream", fake_stream)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        with client.stream("POST", "/chat/stream", json={"message": "小王子的划线"}) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert "event: clarification" in body
    assert '"question": "你想查询哪一本？"' in body
    assert '["小王子", "小王子（英文版）"]' in body
    assert persisted_items == [
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "user",
            "content": "小王子的划线",
        },
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "clarification",
            "content": "你想查询哪一本？",
            "item_metadata": {
                "question": "你想查询哪一本？",
                "options": ["小王子", "小王子（英文版）"],
                "clarification_type": "candidate_selection",
            },
        },
    ]


def test_chat_stream_emits_artifact_event_without_persisting_timeline_item(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    persisted_items: list[dict] = []

    async def fake_stream(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        assert (message, thread_id, user_id) == ("把这本书的划线导出成 md", "session-thread-1", 123)
        assert web_search_enabled is None
        yield StreamEvent(
            type="artifact",
            data={
                "artifacts": [
                    {
                        "artifact_id": "weread:123:book-1:marks",
                        "kind": "weread_markdown",
                        "doc_id": "weread:123:book-1:marks",
                        "book_id": "book-1",
                        "book_title": "测试书籍",
                        "source_type": "marks",
                        "name": "marks.md",
                        "generated_at": "2026-03-15T12:00:00Z",
                    }
                ]
            },
        )
        yield StreamEvent(type="done", data={})

    async def fake_create_chat_item(**kwargs):
        persisted_items.append(kwargs)

    monkeypatch.setattr(chatbot_module.agent_client, "stream", fake_stream)
    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_item", fake_create_chat_item)

    with TestClient(app) as client:
        with client.stream("POST", "/chat/stream", json={"message": "把这本书的划线导出成 md"}) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert "event: artifact" in body
    assert '"artifact_id": "weread:123:book-1:marks"' in body
    assert persisted_items == [
        {
            "thread_id": "session-thread-1",
            "user_id": 123,
            "item_type": "user",
            "content": "把这本书的划线导出成 md",
        }
    ]


def test_chat_passes_web_search_toggle_to_deep_agent(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)

    async def fake_chat(
        message: str,
        thread_id: str,
        user_id: int | None = None,
        *,
        web_search_enabled: bool | None = None,
    ) -> ChatTurnResponse:
        assert message == "帮我查作者近况"
        assert thread_id == "session-thread-1"
        assert user_id == 123
        assert web_search_enabled is True
        return ChatTurnResponse(type="answer", data={"content": "已启用网页搜索。"})

    monkeypatch.setattr(chatbot_module.agent_client, "chat", fake_chat)

    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "帮我查作者近况", "web_search_enabled": True})

    assert response.status_code == 200
    assert response.json() == {
        "type": "answer",
        "data": {"content": "已启用网页搜索。"},
    }


def test_get_messages_uses_agent_client_history(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)

    async def fake_get_chat_history(thread_id: str) -> list[Message]:
        assert thread_id == "session-thread-1"
        return [
            Message(role="user", content="你好"),
            Message(role="assistant", content="你好，我可以帮你查询微信读书数据。"),
        ]

    monkeypatch.setattr(chatbot_module.agent_client, "get_chat_history", fake_get_chat_history)

    with TestClient(app) as client:
        response = client.get("/messages")

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好，我可以帮你查询微信读书数据。"},
        ]
    }


def test_get_thread_artifacts_uses_agent_client_state(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)

    async def fake_get_chat_thread(thread_id: str):
        return types.SimpleNamespace(id=thread_id, user_id=123)

    async def fake_get_thread_artifacts(thread_id: str):
        assert thread_id == "session-thread-1"
        return [
            {
                "artifact_id": "weread:123:book-1:marks",
                "kind": "weread_markdown",
                "doc_id": "weread:123:book-1:marks",
                "book_id": "book-1",
                "book_title": "测试书籍",
                "source_type": "marks",
                "name": "marks.md",
                "generated_at": "2026-03-15T12:00:00Z",
            }
        ]

    monkeypatch.setattr(chatbot_module.chat_store, "get_chat_thread", fake_get_chat_thread)
    monkeypatch.setattr(chatbot_module.agent_client, "get_thread_artifacts", fake_get_thread_artifacts)

    with TestClient(app) as client:
        response = client.get("/threads/session-thread-1/artifacts")

    assert response.status_code == 200
    assert response.json() == {
        "artifacts": [
            {
                "artifact_id": "weread:123:book-1:marks",
                "kind": "weread_markdown",
                "doc_id": "weread:123:book-1:marks",
                "book_id": "book-1",
                "book_title": "测试书籍",
                "source_type": "marks",
                "name": "marks.md",
                "generated_at": "2026-03-15T12:00:00Z",
            }
        ]
    }


def test_clear_messages_uses_agent_client_history_clear(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    called: dict[str, str] = {}

    async def fake_clear_chat_history(thread_id: str) -> None:
        called["thread_id"] = thread_id

    monkeypatch.setattr(chatbot_module.agent_client, "clear_chat_history", fake_clear_chat_history)

    with TestClient(app) as client:
        response = client.delete("/messages")

    assert response.status_code == 200
    assert response.json() == {"message": "Chat history cleared successfully"}
    assert called == {"thread_id": "session-thread-1"}


def test_list_chat_threads_returns_persistent_threads(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    now = datetime.now(UTC)
    fake_thread = types.SimpleNamespace(
        id="thread-1",
        title="第一段对话",
        last_item_preview="最后一条回答",
        last_item_type="assistant",
        last_message_at=now,
        item_count=2,
        created_at=now,
        updated_at=now,
        archived_at=None,
        user_id=123,
    )

    async def fake_get_user_chat_threads(user_id: int, include_archived: bool = False):
        assert user_id == 123
        assert include_archived is False
        return [fake_thread]

    monkeypatch.setattr(chatbot_module.chat_store, "get_user_chat_threads", fake_get_user_chat_threads)

    with TestClient(app) as client:
        response = client.get("/threads")

    assert response.status_code == 200
    assert response.json()["threads"][0]["thread_id"] == "thread-1"
    assert response.json()["threads"][0]["title"] == "第一段对话"


def test_create_chat_thread_creates_persistent_thread(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    now = datetime.now(UTC)

    async def fake_create_chat_thread(thread_id: str, user_id: int, title: str = ""):
        assert user_id == 123
        assert title == "新对话"
        return types.SimpleNamespace(
            id=thread_id,
            title=title,
            last_item_preview="",
            last_item_type=None,
            last_message_at=None,
            item_count=0,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )

    monkeypatch.setattr(chatbot_module.chat_store, "create_chat_thread", fake_create_chat_thread)

    with TestClient(app) as client:
        response = client.post("/threads", json={"title": "新对话"})

    assert response.status_code == 200
    assert response.json()["title"] == "新对话"
    assert response.json()["thread_id"]


def test_get_chat_thread_items_returns_persistent_timeline(monkeypatch) -> None:
    chatbot_module = _load_chatbot_module(monkeypatch)
    app = _build_test_app(chatbot_module)
    now = datetime.now(UTC)
    fake_thread = types.SimpleNamespace(id="thread-1", user_id=123)
    fake_item = types.SimpleNamespace(
        id=1,
        thread_id="thread-1",
        seq=1,
        item_type="clarification",
        content="你想查询哪一本？",
        item_metadata={"options": ["小王子", "小王子（英文版）"]},
        created_at=now,
    )

    async def fake_get_chat_thread(thread_id: str):
        assert thread_id == "thread-1"
        return fake_thread

    async def fake_get_chat_items(thread_id: str):
        assert thread_id == "thread-1"
        return [fake_item]

    monkeypatch.setattr(chatbot_module.chat_store, "get_chat_thread", fake_get_chat_thread)
    monkeypatch.setattr(chatbot_module.chat_store, "get_chat_items", fake_get_chat_items)

    with TestClient(app) as client:
        response = client.get("/threads/thread-1/items")

    assert response.status_code == 200
    assert response.json()["items"][0]["item_type"] == "clarification"
    assert response.json()["items"][0]["metadata"] == {"options": ["小王子", "小王子（英文版）"]}
