from open_webui.routers.openai import (
    convert_to_responses_payload,
    get_azure_allowed_params,
)


def test_azure_allowed_params_include_logprobs():
    allowed_params = get_azure_allowed_params("2024-10-01-preview")
    assert "logprobs" in allowed_params
    assert "log_probs" in allowed_params
    assert "top_logprobs" in allowed_params


def test_convert_to_responses_payload_strips_logprob_fields():
    payload = {
        "model": "gpt-4.1",
        "messages": [{"role": "user", "content": "hello"}],
        "logprobs": True,
        "log_probs": True,
        "top_logprobs": 5,
    }

    converted = convert_to_responses_payload(dict(payload))

    assert "logprobs" not in converted
    assert "log_probs" not in converted
    assert "top_logprobs" not in converted
