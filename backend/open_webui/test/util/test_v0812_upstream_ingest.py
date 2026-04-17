import json

from starlette.responses import HTMLResponse

from open_webui.routers.terminals import _sanitize_proxy_path
from open_webui.utils.misc import (
    convert_output_to_messages,
    sanitize_historical_message_for_llm,
)
from open_webui.utils.middleware import process_tool_result


def test_sanitize_proxy_path_rejects_traversal_and_preserves_safe_paths():
    assert _sanitize_proxy_path("api/files") == "api/files"
    assert _sanitize_proxy_path("/api/files/../notes") == "api/notes"
    assert _sanitize_proxy_path("nested/path/") == "nested/path/"
    assert _sanitize_proxy_path("../../etc/passwd") is None
    assert _sanitize_proxy_path("%2e%2e/%2e%2e/etc/passwd") is None


def test_convert_output_to_messages_preserves_tool_images():
    messages = convert_output_to_messages(
        [
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": [
                    {"type": "input_text", "text": "Tool output"},
                    {"type": "input_image", "image_url": "data:image/png;base64,abc"},
                ],
            }
        ],
        raw=True,
    )

    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": [
                {"type": "input_text", "text": "Tool output"},
                {"type": "input_image", "image_url": "data:image/png;base64,abc"},
            ],
        }
    ]


def test_process_tool_result_preserves_inline_html_context():
    tool_result, tool_files, tool_embeds = process_tool_result(
        None,
        "demo_tool",
        (
            HTMLResponse(
                "<html><body>ok</body></html>",
                status_code=200,
                headers={"Content-Disposition": "inline"},
            ),
            {"status": "success", "message": "html context"},
        ),
        "external",
    )

    assert json.loads(tool_result) == {"status": "success", "message": "html context"}
    assert tool_files == []
    assert tool_embeds == ["<html><body>ok</body></html>"]


def test_process_tool_result_promotes_data_images_to_files():
    tool_result, tool_files, tool_embeds = process_tool_result(
        None,
        "read_image",
        "data:image/png;base64,abc",
        "external",
    )

    assert tool_result == "read_image: Image file read successfully."
    assert tool_files == [{"type": "image", "url": "data:image/png;base64,abc"}]
    assert tool_embeds == []


def test_sanitize_historical_message_for_llm_strips_legacy_tool_call_details():
    message = {
        "role": "assistant",
        "content": (
            "I will inspect the system.\n"
            '<details type="tool_calls" done="true" id="call-1" result="&quot;very large output&quot;">\n'
            "<summary>Tool Executed</summary>\n"
            "</details>\n"
            "Final answer."
        ),
    }

    sanitized = sanitize_historical_message_for_llm(message)

    assert sanitized["content"] == "I will inspect the system.\n\nFinal answer."


def test_sanitize_historical_message_for_llm_replaces_tool_only_messages_with_marker():
    message = {
        "role": "assistant",
        "content": (
            '<details type="tool_calls" done="true" id="call-1" result="&quot;very large output&quot;">\n'
            "<summary>Tool Executed</summary>\n"
            "</details>\n"
        ),
    }

    sanitized = sanitize_historical_message_for_llm(message)

    assert "[prior tool output omitted from cross-turn replay]" in sanitized["content"]
