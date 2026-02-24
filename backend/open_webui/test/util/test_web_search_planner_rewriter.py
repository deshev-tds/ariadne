from types import SimpleNamespace

import pytest

import open_webui.retrieval.web.planner as planner_module
import open_webui.utils.middleware as middleware_module
from open_webui.retrieval.web.planner import PlannedQuery


@pytest.mark.asyncio
async def test_rewriter_uses_active_model_then_task_fallback_and_forces_thinking_off(
    monkeypatch,
):
    models = {
        "active-model": {"id": "active-model", "connection_type": "local"},
        "task-model": {"id": "task-model", "connection_type": "local"},
    }
    config = SimpleNamespace(TASK_MODEL="task-model", TASK_MODEL_EXTERNAL="")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config, MODELS=models)),
        state=SimpleNamespace(),
    )
    user = SimpleNamespace(role="admin")

    plan = planner_module.build_web_search_plan(
        "EKS aws-vpc-cni failed to assign an IP address to container",
        max_targeted_domains=4,
    )

    calls = []

    async def fake_generate_chat_completion(request, form_data, user):
        calls.append(form_data)
        if form_data["model"] == "active-model":
            raise RuntimeError("active model failed")

        return {
            "choices": [
                {
                    "message": {
                        "content": '{"queries":[{"kind":"exact","query":"EKS aws-vpc-cni failed to assign an IP address to container"},{"kind":"targeted","query":"aws-vpc-cni docs site:docs.aws.amazon.com","domain":"docs.aws.amazon.com"}]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(
        middleware_module,
        "generate_chat_completion",
        fake_generate_chat_completion,
    )

    rewriter_queries, meta = await middleware_module._run_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message="EKS aws-vpc-cni failed to assign an IP address to container",
        plan=plan,
        max_queries=4,
        timeout_ms=2000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
    )

    assert calls[0]["model"] == "active-model"
    assert calls[1]["model"] == "task-model"
    assert meta["model_used"] == "task-model"
    assert meta["fallback_used"] is True
    assert rewriter_queries

    for payload in calls:
        assert payload["think"] is False
        assert payload["params"]["think"] is False
        assert (
            payload["params"]["custom_params"]["chat_template_kwargs"][
                "enable_thinking"
            ]
            is False
        )


def test_validate_or_repair_rewriter_queries_rejects_fluff_only_queries():
    plan = planner_module.build_web_search_plan(
        "kubernetes networking issue", max_targeted_domains=4
    )

    with pytest.raises(ValueError):
        planner_module.validate_or_repair_rewriter_queries(
            [PlannedQuery(kind="general", query="can you help me with this")],
            plan,
            max_queries=3,
            max_repair_attempts=0,
        )


def test_registry_normalization_supports_legacy_schema(monkeypatch):
    planner_module.load_normalized_source_registry.cache_clear()

    legacy_registry = {
        "version": 1,
        "topics": {
            "software_apis_devops": {
                "primary": ["docs.aws.amazon.com", "kubernetes.io"],
                "secondary": ["stackoverflow.com"],
                "community": ["reddit.com"],
            }
        },
    }

    monkeypatch.setattr(planner_module, "load_source_registry", lambda: legacy_registry)

    normalized = planner_module.load_normalized_source_registry()

    assert any(source.domain == "docs.aws.amazon.com" for source in normalized)
    assert any(source.domain == "reddit.com" for source in normalized)


@pytest.mark.parametrize(
    "rich_registry",
    [
        {
            "version": "2026-02-registry-v1",
            "schema": {"domain": "string"},
            "topics": {
                "software_apis_devops": {
                    "primary_docs": [
                        {
                            "domain": "docs.aws.amazon.com",
                            "source_type": "primary_docs",
                            "trust_tier": "D",
                            "access": "open",
                            "freshness_profile": "high",
                        }
                    ]
                }
            },
        },
        {
            "version": "2026-02-registry-v1",
            "schema": {"domain": "string"},
            "topics": {
                "software_apis_devops": {
                    "primary_docs": [
                        {
                            "domain": "docs.aws.amazon.com",
                            "source_type": "secondary_analysis",
                            "trust_tier": "A",
                            "access": "open",
                            "freshness_profile": "high",
                        }
                    ]
                }
            },
        },
    ],
)
def test_registry_normalization_rejects_invalid_rich_schema(monkeypatch, rich_registry):
    planner_module.load_normalized_source_registry.cache_clear()
    monkeypatch.setattr(planner_module, "load_source_registry", lambda: rich_registry)

    with pytest.raises(ValueError):
        planner_module.load_normalized_source_registry()
