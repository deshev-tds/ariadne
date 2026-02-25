from types import SimpleNamespace

import pytest

from open_webui.routers import openai as openai_router


def test_strip_model_prefix_only_removes_leading_prefix():
    assert (
        openai_router.strip_model_prefix("demo-prefix.model-name", "demo-prefix")
        == "model-name"
    )
    assert (
        openai_router.strip_model_prefix("another.model-name", "demo-prefix")
        == "another.model-name"
    )


def test_probe_path_must_be_relative():
    assert openai_router._is_valid_moe_probe_path("/props?model={model}") is True
    assert openai_router._is_valid_moe_probe_path("//evil.host/props") is False
    assert openai_router._is_valid_moe_probe_path(
        "https://evil.host/props?model={model}"
    ) is False


def test_probe_path_model_substitution_is_url_encoded():
    probe_path = openai_router._build_moe_probe_path(
        "/props?model={model}",
        "owner/model-name",
    )
    assert probe_path == "/props?model=owner%2Fmodel-name"


def test_normalize_probe_payload_maps_canonical_fields():
    payload = {
        "supported": True,
        "current": 8,
        "default": 8,
        "presets": {
            "few": 4,
            "default": 8,
            "many": 12,
            "a_lot": 16,
        },
    }

    normalized = openai_router._normalize_moe_experts_probe_payload(
        payload,
        model_id="demo-model",
        apply_param_key="num_experts",
    )
    assert normalized["supported"] is True
    assert normalized["reason"] is None
    assert normalized["current"] == 8
    assert normalized["default"] == 8
    assert normalized["presets"]["many"] == 12


def test_normalize_probe_payload_invalid_shape_is_unsupported():
    normalized = openai_router._normalize_moe_experts_probe_payload(
        {"supported": True, "presets": {"few": 2}},
        model_id="demo-model",
        apply_param_key="num_experts",
    )
    assert normalized["supported"] is False
    assert normalized["reason"] is not None
    assert normalized["current"] is None
    assert normalized["presets"] is None


@pytest.mark.asyncio
async def test_apply_moe_experts_default_level_emits_no_override():
    payload = {
        "model": "demo-model",
        "moe_experts_level": "default",
        "num_experts": 99,
    }

    result = await openai_router.apply_moe_experts_level_to_payload(
        request=SimpleNamespace(),
        user=SimpleNamespace(),
        payload=payload,
        requested_model_id="demo-model",
        idx=0,
        url="http://localhost:1234",
        key="",
        api_config={"moe_experts": {"apply_param_key": "num_experts"}},
        raw_model_id="demo-model",
    )

    assert "moe_experts_level" not in result
    assert "num_experts" not in result


@pytest.mark.asyncio
async def test_apply_moe_experts_non_default_uses_mapped_key(monkeypatch):
    async def fake_probe(*args, **kwargs):
        return {
            "supported": True,
            "reason": None,
            "model_id": "demo-model",
            "current": 8,
            "default": 8,
            "presets": {
                "few": 4,
                "default": 8,
                "many": 12,
                "a_lot": 16,
            },
            "apply_param_key": "n_expert",
        }

    monkeypatch.setattr(openai_router, "probe_moe_experts_for_connection", fake_probe)

    payload = {
        "model": "demo-model",
        "moe_experts_level": "many",
        "num_experts": 99,
        "n_expert": 77,
    }

    result = await openai_router.apply_moe_experts_level_to_payload(
        request=SimpleNamespace(),
        user=SimpleNamespace(),
        payload=payload,
        requested_model_id="demo-model",
        idx=0,
        url="http://localhost:1234",
        key="",
        api_config={"moe_experts": {"apply_param_key": "n_expert"}},
        raw_model_id="demo-model",
    )

    assert "moe_experts_level" not in result
    assert "num_experts" not in result
    assert result["n_expert"] == 12


@pytest.mark.asyncio
async def test_apply_moe_experts_non_default_probe_failure_sends_no_override(monkeypatch):
    async def fake_probe(*args, **kwargs):
        return {
            "supported": False,
            "reason": "Probe timed out",
            "model_id": "demo-model",
            "current": None,
            "default": None,
            "presets": None,
            "apply_param_key": "n_expert",
        }

    monkeypatch.setattr(openai_router, "probe_moe_experts_for_connection", fake_probe)

    payload = {
        "model": "demo-model",
        "moe_experts_level": "a_lot",
        "num_experts": 99,
    }

    result = await openai_router.apply_moe_experts_level_to_payload(
        request=SimpleNamespace(),
        user=SimpleNamespace(),
        payload=payload,
        requested_model_id="demo-model",
        idx=0,
        url="http://localhost:1234",
        key="",
        api_config={"moe_experts": {"apply_param_key": "n_expert"}},
        raw_model_id="demo-model",
    )

    assert "moe_experts_level" not in result
    assert "num_experts" not in result
    assert "n_expert" not in result
