from types import SimpleNamespace

import pytest

from open_webui.utils.deep_research import (
    LocalDeepResearchAuthError,
    LocalDeepResearchClient,
    LocalDeepResearchError,
)


class FakeResponse:
    def __init__(self, *, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._body = body

    async def read(self):
        return self._body

    async def text(self):
        return self._body.decode("utf-8", "replace")


class FakeRequestContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    def __init__(self, *, get_responses=None, post_responses=None, request_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.request_responses = list(request_responses or [])
        self.closed = False

    def get(self, *args, **kwargs):
        return FakeRequestContext(self.get_responses.pop(0))

    def post(self, *args, **kwargs):
        return FakeRequestContext(self.post_responses.pop(0))

    def request(self, *args, **kwargs):
        return FakeRequestContext(self.request_responses.pop(0))

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_deep_research_login_fetches_html_csrf_and_api_csrf(monkeypatch):
    client = LocalDeepResearchClient(
        base_url="http://ldr.test",
        username="demo",
        password="secret",
    )
    fake_session = FakeSession(
        get_responses=[
            FakeResponse(
                status=200,
                headers={"Content-Type": "text/html"},
                body=b'<input type="hidden" name="csrf_token" value="login-token" />',
            ),
            FakeResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b'{"csrf_token":"api-token"}',
            ),
        ],
        post_responses=[
            FakeResponse(
                status=200,
                headers={"Content-Type": "text/html"},
                body=b"ok",
            )
        ],
    )

    async def fake_fresh_session():
        client._session = fake_session
        client._csrf_token = None
        client._logged_in = False
        return fake_session

    monkeypatch.setattr(client, "_fresh_session", fake_fresh_session)

    await client.login()

    assert client._logged_in is True
    assert client._csrf_token == "api-token"


@pytest.mark.asyncio
async def test_deep_research_request_reauthenticates_on_login_redirect(monkeypatch):
    client = LocalDeepResearchClient(
        base_url="http://ldr.test",
        username="demo",
        password="secret",
    )
    first_session = FakeSession(
        request_responses=[
            FakeResponse(status=302, headers={"Location": "/auth/login"}, body=b"")
        ]
    )
    second_session = FakeSession(
        request_responses=[
            FakeResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b'{"status":"queued"}',
            )
        ]
    )

    client._session = first_session
    client._logged_in = True
    client._csrf_token = "csrf-token"

    async def fake_ensure_session():
        return client._session

    reauth_calls = []

    async def fake_reauthenticate():
        reauth_calls.append(True)
        client._session = second_session
        client._logged_in = True
        client._csrf_token = "fresh-token"

    monkeypatch.setattr(client, "_ensure_session", fake_ensure_session)
    monkeypatch.setattr(client, "reauthenticate", fake_reauthenticate)

    payload = await client.get_research_status("research-1")

    assert payload["status"] == "queued"
    assert reauth_calls == [True]


@pytest.mark.asyncio
async def test_deep_research_request_raises_text_error_for_non_json_error(monkeypatch):
    client = LocalDeepResearchClient(
        base_url="http://ldr.test",
        username="demo",
        password="secret",
    )
    client._session = FakeSession(
        request_responses=[
            FakeResponse(
                status=500,
                headers={"Content-Type": "text/plain"},
                body=b"sidecar exploded",
            )
        ]
    )
    client._logged_in = True
    client._csrf_token = "csrf-token"

    async def fake_ensure_session():
        return client._session

    monkeypatch.setattr(client, "_ensure_session", fake_ensure_session)

    with pytest.raises(LocalDeepResearchError, match="sidecar exploded"):
        await client.get_research_status("research-1")


def test_extract_login_csrf_token_requires_hidden_input():
    with pytest.raises(LocalDeepResearchAuthError, match="CSRF token"):
        LocalDeepResearchClient.extract_login_csrf_token("<html></html>")
