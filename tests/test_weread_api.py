import importlib
import sys
import types
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.limiter import limiter


@dataclass
class _FakeSession:
    id: str
    user_id: int
    name: str = ""


def _build_session() -> _FakeSession:
    return _FakeSession(id="session-thread-1", user_id=123, name="test-session")


class _FakeWeReadAuthService:
    async def get_binding(self, user_id: int):
        _ = user_id
        return None

    async def bind_cookie(self, user_id: int, cookie: str, source):
        _ = (user_id, cookie, source)
        raise NotImplementedError

    async def clear_binding(self, user_id: int):
        _ = user_id
        return True


class _FakeWeReadBrowserService:
    async def start_qr_login_session(self, user_id: int):
        _ = user_id
        raise NotImplementedError

    async def get_qr_login_session(self, *, user_id: int, session_id: str):
        _ = (user_id, session_id)
        return None

    async def cancel_qr_login_session(self, *, user_id: int, session_id: str):
        _ = (user_id, session_id)
        return None


class _FakeWeReadQrLoginUnavailableError(RuntimeError):
    pass


class _FakeWeReadService:
    async def ensure_binding_with_auto_validation(self, user_id: int):
        _ = user_id
        return None

    async def sync_bookshelf_snapshot(self, user_id: int):
        _ = user_id
        return None

    async def clear_cached_bookshelf(self, user_id: int):
        _ = user_id
        return None

    async def get_recent_bookshelf_snapshot(self, user_id: int, limit: int = 6, force_refresh: bool = False):
        _ = (user_id, limit, force_refresh)
        return None, False

    async def get_local_document(self, user_id: int, doc_id: str):
        _ = (user_id, doc_id)
        raise NotImplementedError


def _load_weread_module(monkeypatch):
    fake_auth = types.ModuleType("app.api.v1.auth")

    async def fake_get_current_session():
        return _build_session()

    fake_auth.get_current_session = fake_get_current_session

    fake_services = types.ModuleType("app.services")
    fake_services.__path__ = []

    fake_weread_auth = types.ModuleType("app.features.weread.services.auth")
    fake_weread_auth.weread_auth_service = _FakeWeReadAuthService()

    fake_weread_browser = types.ModuleType("app.features.weread.services.browser_login")
    fake_weread_browser.WeReadQrLoginUnavailableError = _FakeWeReadQrLoginUnavailableError
    fake_weread_browser.weread_browser_service = _FakeWeReadBrowserService()

    fake_weread_service = types.ModuleType("app.features.weread.services.weread")
    fake_weread_service.WeReadDomainError = RuntimeError
    fake_weread_service.weread_service = _FakeWeReadService()

    monkeypatch.setitem(sys.modules, "app.api.v1.auth", fake_auth)
    monkeypatch.setitem(sys.modules, "app.services", fake_services)
    monkeypatch.setitem(sys.modules, "app.features.weread.services.auth", fake_weread_auth)
    monkeypatch.setitem(sys.modules, "app.features.weread.services.browser_login", fake_weread_browser)
    monkeypatch.setitem(sys.modules, "app.features.weread.services.weread", fake_weread_service)
    sys.modules.pop("app.api.v1.weread", None)

    return importlib.import_module("app.api.v1.weread")


def _build_test_app(weread_module) -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(weread_module.router, prefix="/weread")
    return app


def test_get_weread_binding_returns_connected_binding(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    now = datetime.now(UTC)

    async def fake_ensure_binding_with_auto_validation(user_id: int):
        assert user_id == 123
        return types.SimpleNamespace(
            status="active",
            source="qr",
            last_error=None,
            last_validated_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(
        weread_module.weread_service,
        "ensure_binding_with_auto_validation",
        fake_ensure_binding_with_auto_validation,
    )

    with TestClient(app) as client:
        response = client.get("/weread/binding")

    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["status"] == "active"
    assert response.json()["source"] == "qr"


def test_bind_weread_cookie_returns_binding_status(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    now = datetime.now(UTC)

    async def fake_bind_cookie(user_id: int, cookie: str, source):
        assert user_id == 123
        assert cookie == "wr_skey=demo"
        assert source.value == "manual"
        return types.SimpleNamespace(
            status="active",
            source="manual",
            last_error=None,
            last_validated_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(weread_module.weread_auth_service, "bind_cookie", fake_bind_cookie)

    synced: dict[str, int] = {"user_id": 0}

    async def fake_sync_bookshelf_snapshot(user_id: int):
        synced["user_id"] = user_id
        return None

    monkeypatch.setattr(weread_module.weread_service, "sync_bookshelf_snapshot", fake_sync_bookshelf_snapshot)

    with TestClient(app) as client:
        response = client.post("/weread/binding/cookie", json={"cookie": "wr_skey=demo", "source": "manual"})

    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert response.json()["source"] == "manual"
    assert synced["user_id"] == 123


def test_start_weread_qr_login_returns_session(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    now = datetime.now(UTC)

    async def fake_start_qr_login_session(user_id: int):
        assert user_id == 123
        return types.SimpleNamespace(
            id="qr-session-1",
            status="qr_ready",
            qr_image_base64="ZmFrZS1xcg==",
            last_error=None,
            expires_at=now,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )

    monkeypatch.setattr(weread_module.weread_browser_service, "start_qr_login_session", fake_start_qr_login_session)

    with TestClient(app) as client:
        response = client.post("/weread/binding/qr/session")

    assert response.status_code == 200
    assert response.json()["session_id"] == "qr-session-1"
    assert response.json()["status"] == "qr_ready"
    assert response.json()["qr_image_base64"] == "ZmFrZS1xcg=="


def test_get_weread_qr_login_session_returns_existing_session(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    now = datetime.now(UTC)

    async def fake_get_qr_login_session(*, user_id: int, session_id: str):
        assert user_id == 123
        assert session_id == "qr-session-1"
        return types.SimpleNamespace(
            id="qr-session-1",
            status="success",
            qr_image_base64=None,
            last_error=None,
            expires_at=now,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )

    monkeypatch.setattr(weread_module.weread_browser_service, "get_qr_login_session", fake_get_qr_login_session)

    with TestClient(app) as client:
        response = client.get("/weread/binding/qr/session/qr-session-1")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["completed_at"] is not None


def test_cancel_weread_qr_login_session_returns_success(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    now = datetime.now(UTC)

    async def fake_cancel_qr_login_session(*, user_id: int, session_id: str):
        assert user_id == 123
        assert session_id == "qr-session-1"
        return types.SimpleNamespace(
            id="qr-session-1",
            status="cancelled",
            qr_image_base64=None,
            last_error=None,
            expires_at=now,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )

    monkeypatch.setattr(weread_module.weread_browser_service, "cancel_qr_login_session", fake_cancel_qr_login_session)

    with TestClient(app) as client:
        response = client.delete("/weread/binding/qr/session/qr-session-1")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "WeRead QR login session cancelled"}


def test_get_recent_weread_books_returns_cached_books(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)

    async def fake_get_binding(user_id: int):
        assert user_id == 123
        return types.SimpleNamespace(status="active")

    async def fake_get_recent_bookshelf_snapshot(user_id: int, limit: int = 6, force_refresh: bool = False):
        assert user_id == 123
        assert limit == 3
        assert force_refresh is False
        snapshot = types.SimpleNamespace(
            sync_key=123,
            generated_at="2026-03-14T10:00:00Z",
            books=[
                {
                    "book_id": "book-1",
                    "title": "测试书籍",
                    "author": "测试作者",
                    "translator": "",
                    "cover": "",
                    "format": "epub",
                    "categories": ["社会"],
                    "book_lists": [],
                    "publish_time": "2023-01-01",
                    "finish_reading": False,
                    "paid": True,
                    "is_imported": False,
                    "price": 0,
                    "progress": 42,
                    "reading_time": 3600,
                    "reading_time_formatted": "1小时",
                    "last_read_time": "2026-03-13T10:00:00Z",
                }
            ],
        )
        return snapshot, False

    monkeypatch.setattr(weread_module.weread_auth_service, "get_binding", fake_get_binding)
    monkeypatch.setattr(
        weread_module.weread_service,
        "get_recent_bookshelf_snapshot",
        fake_get_recent_bookshelf_snapshot,
    )

    with TestClient(app) as client:
        response = client.get("/weread/bookshelf/recent?limit=3")

    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["refreshed"] is False
    assert body["sync_key"] == 123
    assert len(body["books"]) == 1
    assert body["books"][0]["book_id"] == "book-1"


def test_get_weread_local_document_returns_markdown_preview(monkeypatch, tmp_path) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    markdown_path = tmp_path / "marks.md"
    markdown_path.write_text("# Demo\n\n内容", encoding="utf-8")

    metadata = types.SimpleNamespace(
        doc_id="weread:123:book-1:marks",
        source_type="marks",
    )

    async def fake_get_local_document(user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == "weread:123:book-1:marks"
        return metadata, markdown_path

    monkeypatch.setattr(weread_module.weread_service, "get_local_document", fake_get_local_document)

    with TestClient(app) as client:
        response = client.get("/weread/documents/weread:123:book-1:marks")

    assert response.status_code == 200
    assert response.text == "# Demo\n\n内容"
    assert response.headers["content-type"].startswith("text/markdown")


def test_get_weread_local_document_supports_download(monkeypatch, tmp_path) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)
    markdown_path = tmp_path / "reviews.md"
    markdown_path.write_text("# Notes\n", encoding="utf-8")

    metadata = types.SimpleNamespace(
        doc_id="weread:123:book-1:reviews",
        source_type="reviews",
    )

    async def fake_get_local_document(user_id: int, doc_id: str):
        assert user_id == 123
        assert doc_id == "weread:123:book-1:reviews"
        return metadata, markdown_path

    monkeypatch.setattr(weread_module.weread_service, "get_local_document", fake_get_local_document)

    with TestClient(app) as client:
        response = client.get("/weread/documents/weread:123:book-1:reviews?download=true")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]


def test_clear_weread_binding_clears_cached_bookshelf(monkeypatch) -> None:
    weread_module = _load_weread_module(monkeypatch)
    app = _build_test_app(weread_module)

    cleared: dict[str, int] = {"user_id": 0}

    async def fake_clear_cached_bookshelf(user_id: int):
        cleared["user_id"] = user_id

    monkeypatch.setattr(weread_module.weread_service, "clear_cached_bookshelf", fake_clear_cached_bookshelf)

    with TestClient(app) as client:
        response = client.delete("/weread/binding")

    assert response.status_code == 200
    assert cleared["user_id"] == 123
