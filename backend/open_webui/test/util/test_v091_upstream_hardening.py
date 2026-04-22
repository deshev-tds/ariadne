import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from open_webui.routers import knowledge as knowledge_router
from open_webui.routers import openai as openai_router
from open_webui.utils import auth as auth_utils
from open_webui.utils import oauth as oauth_utils
from open_webui.utils.asgi_middleware import AuthTokenMiddleware


class _FakeKnowledge:
    def __init__(self, knowledge_id: str, user_id: str):
        self.id = knowledge_id
        self.user_id = user_id

    def model_dump(self):
        return {"id": self.id, "user_id": self.user_id, "name": "KB", "data": {}}


class _FakeResponsesForm:
    def __init__(self, model: str):
        self.model = model

    def model_dump(self, exclude_none: bool = True):
        return {"model": self.model}


def test_remove_file_from_knowledge_does_not_delete_underlying_file_for_collaborator(
    monkeypatch,
):
    deleted_files = []
    deleted_collections = []

    monkeypatch.setattr(
        knowledge_router.Knowledges,
        "get_knowledge_by_id",
        lambda id, db=None: _FakeKnowledge(id, "owner-user"),
    )
    monkeypatch.setattr(
        knowledge_router.AccessGrants,
        "has_access",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        knowledge_router.Files,
        "get_file_by_id",
        lambda file_id, db=None: SimpleNamespace(
            id=file_id, user_id="file-owner", hash="file-hash"
        ),
    )
    monkeypatch.setattr(
        knowledge_router.Knowledges,
        "has_file",
        lambda knowledge_id, file_id, db=None: True,
    )
    monkeypatch.setattr(
        knowledge_router.Knowledges,
        "remove_file_from_knowledge_by_id",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        knowledge_router.Knowledges,
        "get_file_metadatas_by_id",
        lambda knowledge_id, db=None: [],
    )
    monkeypatch.setattr(
        knowledge_router.VECTOR_DB_CLIENT,
        "delete",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        knowledge_router.VECTOR_DB_CLIENT,
        "has_collection",
        lambda collection_name: True,
    )
    monkeypatch.setattr(
        knowledge_router.VECTOR_DB_CLIENT,
        "delete_collection",
        lambda collection_name: deleted_collections.append(collection_name),
    )
    monkeypatch.setattr(
        knowledge_router.Files,
        "delete_file_by_id",
        lambda file_id, db=None: deleted_files.append(file_id),
    )
    monkeypatch.setattr(
        knowledge_router,
        "KnowledgeFilesResponse",
        lambda **kwargs: kwargs,
    )

    result = knowledge_router.remove_file_from_knowledge_by_id(
        "kb-1",
        SimpleNamespace(file_id="file-1"),
        delete_file=True,
        user=SimpleNamespace(id="collaborator", role="user"),
        db=None,
    )

    assert result["id"] == "kb-1"
    assert deleted_files == []
    assert deleted_collections == []


def test_get_current_user_by_api_key_enforces_endpoint_allowlist(monkeypatch):
    monkeypatch.setattr(
        auth_utils.Users,
        "get_user_by_api_key",
        lambda api_key: SimpleNamespace(
            id="admin-1", email="a@example.com", role="admin"
        ),
    )
    monkeypatch.setattr(
        auth_utils.Users, "update_last_active_by_id", lambda user_id: None
    )

    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/v1/chats"),
        state=SimpleNamespace(enable_api_keys=True),
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS=True,
                    API_KEYS_ALLOWED_ENDPOINTS="/api/v1/models,/health",
                    USER_PERMISSIONS={},
                )
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_utils.get_current_user_by_api_key(request, "sk-demo")

    assert exc_info.value.status_code == 403


def test_process_picture_url_rejects_invalid_url_before_fetch(monkeypatch):
    monkeypatch.setattr(
        oauth_utils,
        "validate_url",
        lambda url: (_ for _ in ()).throw(ValueError("blocked")),
    )

    class _UnexpectedClientSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network fetch should not start for blocked URL")

    monkeypatch.setattr(oauth_utils.aiohttp, "ClientSession", _UnexpectedClientSession)

    manager = object.__new__(oauth_utils.OAuthManager)
    result = asyncio.run(
        oauth_utils.OAuthManager._process_picture_url(
            manager,
            "https://[::1]/avatar.png",
        )
    )

    assert result == "/user.png"


def test_openai_responses_checks_model_access_before_proxying(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        openai_router.Models,
        "get_model_by_id",
        lambda model_id: SimpleNamespace(id=model_id, user_id="owner-1"),
    )

    def fake_check_model_access(user, model_info, bypass_filter=False, db=None):
        captured["user"] = user
        captured["model_info"] = model_info
        captured["bypass_filter"] = bypass_filter
        raise HTTPException(status_code=403, detail="Model not found")

    monkeypatch.setattr(openai_router, "check_model_access", fake_check_model_access)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            openai_router.responses(
                SimpleNamespace(),
                _FakeResponsesForm("restricted-model"),
                user=SimpleNamespace(id="user-1", role="user"),
            )
        )

    assert exc_info.value.status_code == 403
    assert captured["model_info"].id == "restricted-model"


def test_openai_proxy_is_disabled_when_passthrough_flag_is_off(monkeypatch):
    monkeypatch.setattr(openai_router, "ENABLE_OPENAI_API_PASSTHROUGH", False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            openai_router.proxy("anything", SimpleNamespace(), user=SimpleNamespace())
        )

    assert exc_info.value.status_code == 403


def test_auth_token_middleware_accepts_x_api_key_on_any_http_route():
    observed = {}

    async def app(scope, receive, send):
        request = Request(scope, receive=receive)
        observed["token"] = request.state.token.credentials
        observed["enable_api_keys"] = request.state.enable_api_keys
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = AuthTokenMiddleware(
        app,
        fastapi_app=SimpleNamespace(
            state=SimpleNamespace(config=SimpleNamespace(ENABLE_API_KEYS=True))
        ),
    )

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/models",
        "raw_path": b"/api/v1/models",
        "query_string": b"",
        "headers": [(b"x-api-key", b"sk-demo")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(scope, receive, send))

    assert observed == {"token": "sk-demo", "enable_api_keys": True}
    assert messages[0]["type"] == "http.response.start"
    header_names = {name.lower() for name, _ in messages[0]["headers"]}
    assert b"x-process-time" in header_names
