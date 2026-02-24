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


@pytest.mark.asyncio
async def test_evidence_judge_uses_active_model_then_task_fallback_and_forces_thinking_off(
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

    calls = []

    async def fake_generate_chat_completion(request, form_data, user):
        calls.append(form_data)
        if form_data["model"] == "active-model":
            raise RuntimeError("active model failed")
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"enough":true,"confidence":0.88,"missing_facets":[],"reason":"enough"}'
                    }
                }
            ]
        }

    monkeypatch.setattr(
        middleware_module, "generate_chat_completion", fake_generate_chat_completion
    )

    result = await middleware_module._run_web_search_evidence_judge(
        request,
        user=user,
        active_model_id="active-model",
        user_message="Is this enough evidence?",
        evidence_text="[1] source=https://example.com\ncontent",
        timeout_ms=1200,
        max_completion_tokens=96,
    )

    assert calls[0]["model"] == "active-model"
    assert calls[1]["model"] == "task-model"
    assert result["enough"] is True
    assert result["fallback_used"] is True
    assert result["model_used"] == "task-model"

    for payload in calls:
        assert payload["think"] is False
        assert payload["params"]["think"] is False
        assert (
            payload["params"]["custom_params"]["chat_template_kwargs"][
                "enable_thinking"
            ]
            is False
        )


@pytest.mark.asyncio
async def test_apply_web_search_evidence_saturation_respects_budget_and_stops_on_judge(
    monkeypatch,
):
    config = SimpleNamespace(
        WEB_SEARCH_EVIDENCE_MAX_TOKENS=200,
        WEB_SEARCH_EVIDENCE_CHUNK_TOKENS=60,
        WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE=3,
        WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS=2,
        WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS=2,
        WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE=0.7,
        WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS=1000,
        WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS=64,
        WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS=4096,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
        state=SimpleNamespace(),
    )
    user = SimpleNamespace(role="admin")
    sources = [
        {
            "source": {"id": "https://docs.aws.amazon.com", "name": "AWS Docs"},
            "document": ["A" * 1200],
            "metadata": [{"source": "https://docs.aws.amazon.com", "title": "AWS"}],
        },
        {
            "source": {"id": "https://github.com/aws/amazon-vpc-cni-k8s", "name": "GitHub"},
            "document": ["B" * 1200],
            "metadata": [{"source": "https://github.com/aws/amazon-vpc-cni-k8s", "title": "Issue"}],
        },
    ]

    async def fake_judge(*args, **kwargs):
        return {
            "enough": True,
            "confidence": 0.92,
            "missing_facets": [],
            "reason": "enough",
            "model_used": "active-model",
            "fallback_used": False,
            "error": None,
        }

    monkeypatch.setattr(
        middleware_module, "_run_web_search_evidence_judge", fake_judge
    )

    saturated_sources, meta = await middleware_module._apply_web_search_evidence_saturation(
        request,
        user=user,
        active_model_id="active-model",
        user_message="EKS aws-cni issue",
        sources=sources,
    )

    assert meta["chunks_selected"] >= 2
    assert meta["stop_reason"] == "judge_enough"
    assert meta["estimated_tokens_selected"] <= 200
    assert len(saturated_sources) >= 1


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


def test_save_source_registry_payload_writes_and_reloads(tmp_path, monkeypatch):
    planner_module.clear_source_registry_caches()
    target = tmp_path / "source_registry.json"
    monkeypatch.setattr(planner_module, "SOURCE_REGISTRY_PATH", target)

    payload = {
        "version": 1,
        "topics": {
            "software_apis_devops": {
                "primary": ["docs.aws.amazon.com"],
                "secondary": ["stackoverflow.com"],
                "community": ["reddit.com"],
            }
        },
    }

    validation = planner_module.save_source_registry_payload(payload)
    loaded = planner_module.load_source_registry()

    assert validation["sources"] == 3
    assert loaded["topics"]["software_apis_devops"]["primary"] == ["docs.aws.amazon.com"]


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
