import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import aiohttp

from open_webui.env import AIOHTTP_CLIENT_TIMEOUT

log = logging.getLogger(__name__)

_LOGIN_CSRF_PATTERN = re.compile(
    r'<input[^>]*name="csrf_token"[^>]*value="([^"]*)"', re.IGNORECASE
)
_CONTENT_DISPOSITION_FILENAME_PATTERN = re.compile(
    r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE
)


class LocalDeepResearchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.payload = payload


class LocalDeepResearchAuthError(LocalDeepResearchError):
    pass


@dataclass(slots=True)
class LocalDeepResearchExport:
    content: bytes
    content_type: str
    filename: str


class LocalDeepResearchClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        request_timeout_seconds: Optional[float] = None,
    ) -> None:
        normalized = str(base_url or "").strip().rstrip("/")
        if not normalized:
            raise LocalDeepResearchError("Deep research sidecar URL is not configured.")
        if not str(username or "").strip() or not str(password or "").strip():
            raise LocalDeepResearchError(
                "Deep research sidecar credentials are not configured."
            )

        self._base_url = normalized
        self._username = str(username)
        self._password = str(password)
        self._request_timeout_seconds = request_timeout_seconds or AIOHTTP_CLIENT_TIMEOUT or 30
        self._session: Optional[aiohttp.ClientSession] = None
        self._csrf_token: Optional[str] = None
        self._logged_in = False

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
        self._session = None
        self._csrf_token = None
        self._logged_in = False

    async def login(self) -> None:
        session = await self._fresh_session()

        login_page_url = self._url("/auth/login")
        async with session.get(
            login_page_url,
            allow_redirects=True,
            timeout=self._timeout(10),
        ) as response:
            html = await response.text()
            if response.status >= 400:
                raise LocalDeepResearchAuthError(
                    f"Failed to load deep research login page ({response.status}).",
                    status_code=response.status,
                )

        login_csrf = self.extract_login_csrf_token(html)
        data = aiohttp.FormData()
        data.add_field("username", self._username)
        data.add_field("password", self._password)
        data.add_field("csrf_token", login_csrf)

        async with session.post(
            login_page_url,
            data=data,
            allow_redirects=True,
            timeout=self._timeout(15),
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise LocalDeepResearchAuthError(
                    self._extract_error_detail(
                        status=response.status,
                        payload=None,
                        text=body,
                        fallback="Deep research login failed.",
                    ),
                    status_code=response.status,
                )
            if "invalid username or password" in body.lower():
                raise LocalDeepResearchAuthError("Deep research login failed.")

        async with session.get(
            self._url("/auth/csrf-token"),
            allow_redirects=False,
            timeout=self._timeout(10),
        ) as response:
            body = await response.read()
            body_text = body.decode("utf-8", "replace")
            if self._is_auth_drift(response, body_text):
                raise LocalDeepResearchAuthError("Deep research login did not persist.")
            try:
                csrf_payload = json.loads(body_text)
            except json.JSONDecodeError as exc:
                raise LocalDeepResearchAuthError(
                    "Deep research login did not return a CSRF token."
                ) from exc

        self._csrf_token = str(csrf_payload.get("csrf_token") or "").strip() or None
        self._logged_in = True

    async def start_research(self, query: str, *, mode: str = "detailed") -> dict[str, Any]:
        payload = await self._request_json(
            "POST",
            "/api/start_research",
            json_body={"query": query, "mode": mode},
            timeout_seconds=15,
        )

        research_id = payload.get("research_id")
        if not research_id:
            raise LocalDeepResearchError(
                self._extract_error_detail(
                    status=200,
                    payload=payload,
                    text="",
                    fallback="Deep research did not return a research ID.",
                ),
                payload=payload,
            )
        return payload

    async def get_research_status(self, research_id: str) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            f"/api/research/{research_id}/status",
            timeout_seconds=10,
        )

    async def get_report(self, research_id: str) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            f"/api/report/{research_id}",
            timeout_seconds=20,
        )

    async def export_report(
        self, research_id: str, export_format: str
    ) -> LocalDeepResearchExport:
        response, body = await self._request(
            "POST",
            f"/api/v1/research/{research_id}/export/{export_format}",
            timeout_seconds=60,
        )
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        filename = self._extract_filename(
            response.headers.get("Content-Disposition"),
            default=f"report.{export_format}",
        )
        return LocalDeepResearchExport(
            content=body,
            content_type=content_type,
            filename=filename,
        )

    async def terminate_research(self, research_id: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            f"/api/terminate/{research_id}",
            timeout_seconds=15,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        data: Any = None,
        include_csrf: bool = True,
        allow_auth_retry: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> dict[str, Any]:
        response, body = await self._request(
            method,
            path,
            json_body=json_body,
            data=data,
            include_csrf=include_csrf,
            allow_auth_retry=allow_auth_retry,
            timeout_seconds=timeout_seconds,
        )

        body_text = body.decode("utf-8", "replace")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            if response.status >= 400:
                raise LocalDeepResearchError(
                    self._extract_error_detail(
                        status=response.status,
                        payload=None,
                        text=body_text,
                        fallback=f"Deep research request failed ({response.status}).",
                    ),
                    status_code=response.status,
                    retryable=response.status >= 500,
                ) from exc
            raise LocalDeepResearchError(
                f"Invalid JSON response from deep research sidecar for {path}.",
                status_code=response.status,
            ) from exc

        if response.status >= 400:
            raise LocalDeepResearchError(
                self._extract_error_detail(
                    status=response.status,
                    payload=payload,
                    text=body_text,
                    fallback=f"Deep research request failed ({response.status}).",
                ),
                status_code=response.status,
                payload=payload,
                retryable=response.status >= 500,
            )

        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        data: Any = None,
        include_csrf: bool = True,
        allow_auth_retry: bool = True,
        timeout_seconds: Optional[float] = None,
    ) -> tuple[aiohttp.ClientResponse, bytes]:
        if not self._logged_in:
            await self.login()

        session = await self._ensure_session()
        headers = {}
        if include_csrf and self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token

        try:
            async with session.request(
                method,
                self._url(path),
                json=json_body,
                data=data,
                headers=headers,
                allow_redirects=False,
                timeout=self._timeout(timeout_seconds),
            ) as response:
                body = await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise LocalDeepResearchError(
                f"Deep research sidecar request failed: {exc}",
                retryable=True,
            ) from exc

        body_text = body.decode("utf-8", "replace")
        if self._is_auth_drift(response, body_text):
            if allow_auth_retry:
                await self.reauthenticate()
                return await self._request(
                    method,
                    path,
                    json_body=json_body,
                    data=data,
                    include_csrf=include_csrf,
                    allow_auth_retry=False,
                    timeout_seconds=timeout_seconds,
                )
            raise LocalDeepResearchAuthError(
                "Deep research authentication expired.",
                status_code=response.status,
            )

        if self._is_csrf_drift(response.status, body_text):
            if allow_auth_retry:
                await self.reauthenticate()
                return await self._request(
                    method,
                    path,
                    json_body=json_body,
                    data=data,
                    include_csrf=include_csrf,
                    allow_auth_retry=False,
                    timeout_seconds=timeout_seconds,
                )
            raise LocalDeepResearchAuthError(
                "Deep research CSRF token expired.",
                status_code=response.status,
            )

        return response, body

    async def reauthenticate(self) -> None:
        await self.close()
        await self.login()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            await self._fresh_session()
        return self._session

    async def _fresh_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            await self._session.close()

        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(),
            trust_env=True,
            timeout=self._timeout(self._request_timeout_seconds),
        )
        self._csrf_token = None
        self._logged_in = False
        return self._session

    def _url(self, path: str) -> str:
        return urljoin(f"{self._base_url}/", path.lstrip("/"))

    def _timeout(self, total: Optional[float]) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=total or self._request_timeout_seconds)

    @staticmethod
    def extract_login_csrf_token(html: str) -> str:
        csrf_match = _LOGIN_CSRF_PATTERN.search(html or "")
        if not csrf_match:
            raise LocalDeepResearchAuthError(
                "Could not find a CSRF token on the deep research login page."
            )
        return csrf_match.group(1)

    @staticmethod
    def _extract_error_detail(
        *,
        status: int,
        payload: Any,
        text: str,
        fallback: str,
    ) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = (
                    error.get("message")
                    or error.get("detail")
                    or error.get("error")
                )
                if detail:
                    return str(detail)
            if error:
                return str(error)

            detail = payload.get("detail") or payload.get("message") or payload.get("status")
            if detail:
                return str(detail)

        stripped_text = str(text or "").strip()
        if stripped_text:
            return stripped_text[:500]
        return fallback if fallback else f"Deep research request failed ({status})."

    @staticmethod
    def _is_auth_drift(response: aiohttp.ClientResponse, body_text: str) -> bool:
        location = response.headers.get("Location", "")
        content_type = response.headers.get("Content-Type", "")
        if response.status in {301, 302, 303, 307, 308} and "/auth/login" in location:
            return True
        if response.status in {401, 403}:
            return True
        if "text/html" in content_type and "/auth/login" in body_text:
            return True
        return False

    @staticmethod
    def _is_csrf_drift(status_code: int, body_text: str) -> bool:
        if status_code not in {400, 403}:
            return False
        return "csrf" in str(body_text or "").lower()

    @staticmethod
    def _extract_filename(content_disposition: Optional[str], *, default: str) -> str:
        match = _CONTENT_DISPOSITION_FILENAME_PATTERN.search(content_disposition or "")
        if not match:
            return default
        return match.group(1).strip().strip('"') or default
