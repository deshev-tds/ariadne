from types import SimpleNamespace

import pytest

from open_webui.routers import models as models_router


@pytest.mark.asyncio
async def test_models_router_moe_experts_requires_model_id():
    response = await models_router.get_moe_experts(
        request=SimpleNamespace(),
        model_id=None,
        user=SimpleNamespace(),
    )
    assert response["supported"] is False
    assert response["reason"] == "model_id is required"


@pytest.mark.asyncio
async def test_models_router_moe_experts_strips_internal_fields(monkeypatch):
    async def fake_probe(*args, **kwargs):
        return {
            "supported": True,
            "reason": None,
            "model_id": "demo-model",
            "current": 8,
            "default": 8,
            "presets": {"few": 4, "default": 8, "many": 12, "a_lot": 16},
            "apply_param_key": "n_expert",
        }

    monkeypatch.setattr(models_router, "probe_moe_experts_for_model", fake_probe)

    response = await models_router.get_moe_experts(
        request=SimpleNamespace(),
        model_id="demo-model",
        user=SimpleNamespace(),
    )
    assert response["supported"] is True
    assert response["model_id"] == "demo-model"
    assert "apply_param_key" not in response
