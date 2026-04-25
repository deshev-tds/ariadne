#!/usr/bin/env python3
"""Smoke tests for Ariadne's patched llama-server logprobs/tool streaming path.

Uses only the Python standard library. The tests intentionally stay small:
they validate API shape and the old llama.cpp guard, not model quality.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def request_json(url: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_sse(url: str, payload: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                events.append(json.loads(data))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    return events


def pick_loaded_model(endpoint: str, timeout: float) -> str:
    models = request_json(f"{endpoint}/v1/models", timeout=timeout).get("data", [])
    for item in models:
        status = item.get("status")
        if isinstance(status, dict) and status.get("value") == "loaded":
            return item["id"]
    raise RuntimeError("No loaded model found. Pass --model explicitly if you want to load one.")


def tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "noop",
                "description": "No-op smoke-test tool.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }
    ]


def validate_stream(events: list[dict[str, Any]], *, require_content_logprobs: bool) -> dict[str, int]:
    counters = {
        "chunks": 0,
        "content_chunks": 0,
        "content_logprob_chunks": 0,
        "tool_call_chunks": 0,
        "bad_non_content_logprobs": 0,
    }

    for event in events:
        for choice in event.get("choices", []):
            counters["chunks"] += 1
            delta = choice.get("delta") or {}
            logprobs = choice.get("logprobs")
            content = delta.get("content")
            has_content = isinstance(content, str) and bool(content)
            has_tool_calls = bool(delta.get("tool_calls"))

            if has_content:
                counters["content_chunks"] += 1
                if logprobs and logprobs.get("content"):
                    counters["content_logprob_chunks"] += 1
            elif logprobs and logprobs.get("content"):
                counters["bad_non_content_logprobs"] += 1

            if has_tool_calls:
                counters["tool_call_chunks"] += 1

    if counters["bad_non_content_logprobs"]:
        raise AssertionError("logprobs.content appeared on a non-content delta")

    if require_content_logprobs and counters["content_logprob_chunks"] == 0:
        raise AssertionError("no content chunk carried logprobs")

    return counters


def run_case(
    endpoint: str,
    model: str,
    timeout: float,
    *,
    name: str,
    messages: list[dict[str, str]],
    tools: bool,
    force_tool: bool,
    require_content_logprobs: bool,
) -> dict[str, int]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "logprobs": True,
        "top_logprobs": 3,
        "max_tokens": 8,
        "temperature": 0,
    }
    if tools:
        payload["tools"] = tool_schema()
        payload["tool_choice"] = {"type": "function", "function": {"name": "noop"}} if force_tool else "auto"

    events = post_sse(f"{endpoint}/v1/chat/completions", payload, timeout)
    counters = validate_stream(events, require_content_logprobs=require_content_logprobs)
    print(f"PASS {name}: {counters}")
    return counters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--force-tool-call",
        action="store_true",
        help="Also run a forced tool-call case. This requires a model/template with tool-call support.",
    )
    args = parser.parse_args()

    endpoint = args.endpoint.rstrip("/")
    model = args.model or pick_loaded_model(endpoint, args.timeout)
    print(f"endpoint={endpoint}")
    print(f"model={model}")

    run_case(
        endpoint,
        model,
        args.timeout,
        name="stream_logprobs_no_tools",
        messages=[{"role": "user", "content": "Say hi in two words."}],
        tools=False,
        force_tool=False,
        require_content_logprobs=True,
    )

    run_case(
        endpoint,
        model,
        args.timeout,
        name="stream_logprobs_tools_auto",
        messages=[{"role": "user", "content": "Say hi in two words. Do not call tools."}],
        tools=True,
        force_tool=False,
        require_content_logprobs=False,
    )

    if args.force_tool_call:
        run_case(
            endpoint,
            model,
            args.timeout,
            name="stream_logprobs_forced_tool_call",
            messages=[{"role": "user", "content": "Call the noop tool with empty arguments."}],
            tools=True,
            force_tool=True,
            require_content_logprobs=False,
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
