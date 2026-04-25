"""
Pure-ASGI replacements for the project's previous HTTP middleware wrappers.

Using plain ASGI avoids the extra cancellation scope introduced by
BaseHTTPMiddleware, which can otherwise inject CancelledError into in-flight
DB calls and long-running awaits when requests complete or disconnect.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs, urlencode

from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from open_webui.env import CUSTOM_API_KEY_HEADER
from open_webui.internal.db import ScopedSession
from open_webui.utils.auth import get_http_authorization_cred

log = logging.getLogger(__name__)


class CommitSessionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except BaseException:
            try:
                ScopedSession.rollback()
            except Exception:
                log.exception(
                    "CommitSessionMiddleware: rollback failed after downstream error"
                )
            finally:
                ScopedSession.remove()
            raise

        try:
            ScopedSession.commit()
        except Exception:
            log.exception(
                "CommitSessionMiddleware: post-request commit failed; "
                "response was already sent to client"
            )
            try:
                ScopedSession.rollback()
            except Exception:
                log.exception(
                    "CommitSessionMiddleware: rollback failed after commit failure"
                )
            raise
        finally:
            ScopedSession.remove()


class AuthTokenMiddleware:
    def __init__(self, app: ASGIApp, *, fastapi_app) -> None:
        self.app = app
        self._fastapi_app = fastapi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.monotonic()
        request = Request(scope)

        token = get_http_authorization_cred(request.headers.get("Authorization"))
        if token is None:
            cookie_token = request.cookies.get("token")
            if cookie_token:
                token = HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=cookie_token
                )
        if token is None:
            api_key = request.headers.get(CUSTOM_API_KEY_HEADER)
            if api_key:
                token = HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=api_key
                )

        request.state.token = token
        request.state.enable_api_keys = self._fastapi_app.state.config.ENABLE_API_KEYS

        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start":
                process_time = int(time.monotonic() - start_time)
                headers = MutableHeaders(scope=message)
                headers["X-Process-Time"] = str(process_time)
            await send(message)

        await self.app(scope, receive, send_with_timing)


class WebsocketUpgradeGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if "/ws/socket.io" in path:
            query_string = scope.get("query_string", b"").decode(
                "latin-1", errors="replace"
            )
            query_params = parse_qs(query_string)
            if query_params.get("transport", [""])[0] == "websocket":
                headers = _scope_headers(scope)
                upgrade = headers.get("upgrade", "").lower()
                connection_tokens = [
                    token.strip()
                    for token in headers.get("connection", "").lower().split(",")
                ]
                if upgrade != "websocket" or "upgrade" not in connection_tokens:
                    response = JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid WebSocket upgrade request"},
                    )
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)


class RedirectMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "").upper() != "GET":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        query_string = scope.get("query_string", b"").decode(
            "latin-1", errors="replace"
        )
        query_params = parse_qs(query_string)

        redirect_params: dict[str, str] = {}
        if path.endswith("/watch") and "v" in query_params and query_params["v"]:
            redirect_params["youtube"] = query_params["v"][0]

        if "shared" in query_params and query_params["shared"]:
            text = query_params["shared"][0]
            if text:
                url_match = re.match(r"https://\S+", text)
                if url_match:
                    from open_webui.retrieval.loaders.youtube import _parse_video_id

                    youtube_video_id = _parse_video_id(url_match[0])
                    if youtube_video_id:
                        redirect_params["youtube"] = youtube_video_id
                    else:
                        redirect_params["load-url"] = url_match[0]
                else:
                    redirect_params["q"] = text

        if redirect_params:
            response = RedirectResponse(url=f"/?{urlencode(redirect_params)}")
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _scope_headers(scope: Scope) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers", []):
        key = raw_key.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        if key in decoded:
            decoded[key] = f"{decoded[key]}, {value}"
        else:
            decoded[key] = value
    return decoded
