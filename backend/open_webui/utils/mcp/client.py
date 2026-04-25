import asyncio
from contextlib import AsyncExitStack
import logging
from typing import Optional

import anyio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import httpx

from open_webui.env import AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL

log = logging.getLogger(__name__)


def _build_httpx_client(headers=None, timeout=None, auth=None, verify=True):
    kwargs = {
        "follow_redirects": True,
        "verify": verify,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def create_httpx_client(headers=None, timeout=None, auth=None):
    return _build_httpx_client(
        headers=headers,
        timeout=timeout,
        auth=auth,
        verify=True,
    )


def create_insecure_httpx_client(headers=None, timeout=None, auth=None):
    return _build_httpx_client(
        headers=headers,
        timeout=timeout,
        auth=auth,
        verify=False,
    )


class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack: Optional[AsyncExitStack] = None
        self._streams_context = None
        self._session_context = None

    async def connect(self, url: str, headers: Optional[dict] = None):
        async with AsyncExitStack() as exit_stack:
            try:
                httpx_client_factory = (
                    create_httpx_client
                    if AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL
                    else create_insecure_httpx_client
                )
                self._streams_context = streamablehttp_client(
                    url,
                    headers=headers,
                    httpx_client_factory=httpx_client_factory,
                )

                transport = await exit_stack.enter_async_context(self._streams_context)
                read_stream, write_stream, _ = transport

                self._session_context = ClientSession(
                    read_stream, write_stream
                )  # pylint: disable=W0201

                self.session = await exit_stack.enter_async_context(
                    self._session_context
                )
                with anyio.fail_after(10):
                    await self.session.initialize()
                self.exit_stack = exit_stack.pop_all()
            except Exception:
                await asyncio.shield(self.disconnect())
                raise

    async def list_tool_specs(self) -> Optional[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result = await self.session.list_tools()
        tools = result.tools

        tool_specs = []
        for tool in tools:
            name = tool.name
            description = tool.description

            inputSchema = tool.inputSchema

            # TODO: handle outputSchema if needed
            outputSchema = getattr(tool, "outputSchema", None)

            tool_specs.append(
                {"name": name, "description": description, "parameters": inputSchema}
            )

        return tool_specs

    async def call_tool(
        self, function_name: str, function_args: dict
    ) -> Optional[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result = await self.session.call_tool(function_name, function_args)
        if not result:
            raise Exception("No result returned from MCP tool call.")

        result_dict = result.model_dump(mode="json")
        result_content = result_dict.get("content", {})

        if result.isError:
            raise Exception(result_content)
        else:
            return result_content

    async def list_resources(self, cursor: Optional[str] = None) -> Optional[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result = await self.session.list_resources(cursor=cursor)
        if not result:
            raise Exception("No result returned from MCP list_resources call.")

        result_dict = result.model_dump()
        resources = result_dict.get("resources", [])

        return resources

    async def read_resource(self, uri: str) -> Optional[dict]:
        if not self.session:
            raise RuntimeError("MCP client is not connected.")

        result = await self.session.read_resource(uri)
        if not result:
            raise Exception("No result returned from MCP read_resource call.")
        result_dict = result.model_dump()

        return result_dict

    async def disconnect(self):
        exit_stack, self.exit_stack = self.exit_stack, None
        self.session = None
        self._session_context = None
        self._streams_context = None

        if exit_stack is None:
            return

        try:
            await exit_stack.aclose()
        except TimeoutError:
            log.warning("Timed out while closing MCP client resources.")
        except RuntimeError as exc:
            if "generator didn't stop after athrow()" in str(exc):
                log.warning("Suppressed MCP shutdown race: %s", exc)
            else:
                raise
        except Exception:
            log.exception("Error while disconnecting MCP client.")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.disconnect()
