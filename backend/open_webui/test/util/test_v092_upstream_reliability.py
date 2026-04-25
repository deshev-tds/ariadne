import asyncio
import json
from types import SimpleNamespace

from starlette.requests import Request

from open_webui.retrieval.web import firecrawl as firecrawl_utils
from open_webui.routers import ollama as ollama_router
from open_webui.routers import openai as openai_router
from open_webui.tools import builtin as builtin_tools
from open_webui.utils import asgi_middleware
from open_webui.utils import middleware
from open_webui.utils import oauth as oauth_utils
from open_webui.utils.mcp.client import MCPClient


class _FakeAiohttpResponse:
    def __init__(self, status, body, content_type="text/event-stream"):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}

    async def text(self):
        return self._body


class _FakeExitStack:
    def __init__(self):
        self.calls = 0

    async def aclose(self):
        self.calls += 1


def test_auth_token_middleware_accepts_custom_api_key_header(monkeypatch):
    observed = {}

    async def app(scope, receive, send):
        request = Request(scope, receive=receive)
        observed["token"] = request.state.token.credentials
        observed["enable_api_keys"] = request.state.enable_api_keys
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    monkeypatch.setattr(asgi_middleware, "CUSTOM_API_KEY_HEADER", "x-openwebui-key")

    middleware_app = asgi_middleware.AuthTokenMiddleware(
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
        "headers": [(b"x-openwebui-key", b"sk-custom")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(middleware_app(scope, receive, send))

    assert observed == {"token": "sk-custom", "enable_api_keys": True}
    assert messages[0]["type"] == "http.response.start"


def test_fetch_url_returns_empty_string_when_extractor_returns_none(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda request, url: (None, {"url": url}),
    )

    result = asyncio.run(
        builtin_tools.fetch_url("https://example.com", __request__=SimpleNamespace())
    )

    assert result == ""


def test_firecrawl_scrape_uses_v2_endpoint_and_timeout(monkeypatch):
    captured = {}

    def fake_request_firecrawl_json(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return {
            "data": {
                "markdown": "# Example",
                "metadata": {"title": "Example", "description": "Desc"},
                "url": "https://example.com/page",
            }
        }

    monkeypatch.setattr(
        firecrawl_utils, "request_firecrawl_json", fake_request_firecrawl_json
    )

    doc = firecrawl_utils.scrape_firecrawl_url(
        "https://api.firecrawl.dev",
        "fc-key",
        "https://example.com/page",
        verify_ssl=False,
        timeout=12,
        params={"onlyMainContent": True},
    )

    assert doc is not None
    assert doc.page_content == "# Example"
    assert doc.metadata["source"] == "https://example.com/page"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert captured["kwargs"]["json"]["timeout"] == 12000
    assert captured["kwargs"]["json"]["skipTlsVerification"] is True
    assert captured["kwargs"]["json"]["onlyMainContent"] is True


def test_process_tool_result_reads_resource_text_payload():
    tool_result, tool_files, tool_embeds = middleware.process_tool_result(
        request=None,
        tool_function_name="mcp.read_resource",
        tool_result=[{"type": "resource", "resource": {"text": '{"answer": 42}'}}],
        tool_type="mcp",
        metadata={},
        user=None,
    )

    assert json.loads(tool_result) == {"answer": 42}
    assert tool_files == []
    assert tool_embeds == []


def test_openai_sse_error_response_returns_json_payload():
    response = asyncio.run(
        openai_router._maybe_build_sse_error_response(
            _FakeAiohttpResponse(429, '{"error":{"message":"rate limited"}}')
        )
    )

    assert response.status_code == 429
    assert json.loads(response.body) == {"error": {"message": "rate limited"}}


def test_proxy_header_cleanup_drops_streaming_transfer_headers():
    dirty_headers = {
        "Content-Type": "text/event-stream",
        "Content-Length": "100",
        "Transfer-Encoding": "chunked",
        "X-Test": "1",
    }

    assert openai_router._clean_proxy_headers(dirty_headers) == {
        "Content-Type": "text/event-stream",
        "X-Test": "1",
    }
    assert ollama_router._clean_proxy_headers(dirty_headers) == {
        "Content-Type": "text/event-stream",
        "X-Test": "1",
    }


def test_oauth_protected_resource_helper_builds_path_specific_well_known_urls():
    urls = oauth_utils._build_well_known_urls("https://mcp.example.com/api/mcp")

    assert urls == [
        "https://mcp.example.com/.well-known/oauth-authorization-server/api/mcp",
        "https://mcp.example.com/.well-known/openid-configuration/api/mcp",
        "https://mcp.example.com/api/mcp/.well-known/openid-configuration",
        "https://mcp.example.com/.well-known/oauth-authorization-server",
        "https://mcp.example.com/.well-known/openid-configuration",
    ]


def test_mcp_client_disconnect_is_idempotent():
    client = MCPClient()

    asyncio.run(client.disconnect())

    exit_stack = _FakeExitStack()
    client.exit_stack = exit_stack
    client.session = SimpleNamespace()

    asyncio.run(client.disconnect())
    asyncio.run(client.disconnect())

    assert exit_stack.calls == 1
    assert client.session is None
