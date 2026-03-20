import open_webui.utils.middleware as middleware
from open_webui.utils.task import RUNTIME_TIME_AUTHORITY_MARKER


def test_inject_runtime_timestamp_once_adds_system_message():
    messages = [{"role": "user", "content": "hello"}]

    updated = middleware._inject_runtime_timestamp_once(messages)

    assert updated[0]["role"] == "system"
    assert middleware.RUNTIME_TIMESTAMP_MARKER in updated[0]["content"]
    assert RUNTIME_TIME_AUTHORITY_MARKER in updated[0]["content"]
    assert "Do not treat it as a hypothetical, simulation, or test hint." in updated[0]["content"]
    assert updated[1]["role"] == "user"


def test_inject_runtime_timestamp_once_deduplicates_marker():
    messages = [
        {
            "role": "system",
            "content": (
                "Base system prompt\n"
                "Current runtime timestamp: 2026-03-13T10:00:00Z"
            ),
        },
        {"role": "user", "content": "hello"},
    ]

    updated = middleware._inject_runtime_timestamp_once(messages)

    assert updated == messages


def test_inject_runtime_timestamp_once_skips_later_turns():
    messages = [
        {"role": "user", "content": "first turn"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second turn"},
    ]

    updated = middleware._inject_runtime_timestamp_once(messages)

    assert updated == messages
