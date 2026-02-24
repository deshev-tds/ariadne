from types import SimpleNamespace

import pytest

import open_webui.routers.retrieval as retrieval_module
import open_webui.utils.middleware as middleware_module
from open_webui.retrieval.web.main import SearchResult


def make_planner_config(**overrides):
    defaults = {
        "WEB_SEARCH_ENGINE": "brave",
        "WEB_SEARCH_PLANNER_MIN_TOTAL_QUERIES": 3,
        "WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES": 10,
        "WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE": 10,
        "WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE": 0.66,
        "WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS": 3,
        "WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE": 0.56,
        "WEB_SEARCH_PLANNER_PLATEAU_DELTA": 0.02,
        "WEB_SEARCH_PLANNER_PLATEAU_STREAK": 2,
        "WEB_SEARCH_PLANNER_MODE": "rules_only",
        "WEB_SEARCH_PLANNER_ENABLE_INTENT_COVERAGE_GUARD": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_request(config):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config)),
        state=SimpleNamespace(),
    )


def make_plan(
    *,
    base_exact_query: str = "python requests timeout error",
    base_general_query: str = "python requests timeout troubleshooting guide",
    preserve_tokens: list[str] | None = None,
    selected_domains: list[str] | None = None,
    intent: str = "docs_api",
    topic: str = "software_apis_devops",
    time_sensitive: bool = False,
    community_requested: bool = False,
):
    selected_domains = selected_domains or [
        "docs.python.org",
        "developer.mozilla.org",
        "kubernetes.io",
        "github.com",
    ]
    preserve_tokens = preserve_tokens or ["requests", "timeout"]
    selected_sources = [{"domain": d, "tier": "primary"} for d in selected_domains]
    return {
        "intent": intent,
        "topic": topic,
        "time_sensitive": time_sensitive,
        "community_requested": community_requested,
        "selected_domains": selected_domains,
        "selected_sources": selected_sources,
        "base_exact_query": base_exact_query,
        "base_general_query": base_general_query,
        "preserve_tokens": preserve_tokens,
    }


@pytest.mark.asyncio
async def test_planner_stops_at_three_when_signal_is_strong(monkeypatch):
    config = make_planner_config()
    request = make_request(config)
    plan = make_plan()
    form_data = retrieval_module.SearchForm(queries=["seed"], plan=plan)

    domains = ["docs.python.org", "developer.mozilla.org", "kubernetes.io"]
    call_count = {"n": 0}

    def fake_search_web(request, engine, query, user=None):
        idx = min(call_count["n"], len(domains) - 1)
        call_count["n"] += 1
        domain = domains[idx]
        return [
            SearchResult(
                link=f"https://{domain}/guide",
                title="requests timeout guide",
                snippet="requests timeout troubleshooting",
            )
        ]

    monkeypatch.setattr(retrieval_module, "search_web", fake_search_web)

    _, executed_queries, metrics = await retrieval_module._execute_web_search_with_planner(
        request, form_data, user=None
    )

    assert len(executed_queries) == 3
    assert metrics["stop_reason"] == "quality_threshold_met"


@pytest.mark.asyncio
async def test_planner_expands_to_max_ten_when_signal_is_weak(monkeypatch):
    config = make_planner_config(
        WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES=10,
        WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE=10,
    )
    request = make_request(config)
    plan = make_plan(
        base_exact_query="error ABC-404 --debug",
        base_general_query="error ABC-404 --debug",
        preserve_tokens=["ABC-404", "--debug"],
        selected_domains=[
            "docs.python.org",
            "developer.mozilla.org",
            "kubernetes.io",
            "github.com",
            "docker.com",
            "cloud.google.com",
            "learn.microsoft.com",
            "docs.aws.amazon.com",
            "stackoverflow.com",
            "serverfault.com",
        ],
    )
    form_data = retrieval_module.SearchForm(queries=["seed"], plan=plan)

    def fake_search_web(request, engine, query, user=None):
        return [
            SearchResult(
                link="https://unknown.example.com/page",
                title="generic page",
                snippet="unrelated content",
            )
        ]

    monkeypatch.setattr(retrieval_module, "search_web", fake_search_web)

    _, executed_queries, metrics = await retrieval_module._execute_web_search_with_planner(
        request, form_data, user=None
    )

    assert len(executed_queries) == 10
    assert metrics["max_total_queries"] == 10


@pytest.mark.asyncio
async def test_planner_first_five_queries_follow_exact_targeted_general_order(monkeypatch):
    config = make_planner_config(
        WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES=5,
        WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE=1.0,
        WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS=999,
        WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE=1.0,
    )
    request = make_request(config)
    plan = make_plan(
        base_exact_query="how to reset bios",
        base_general_query="how to reset bios guide",
        preserve_tokens=[],
        selected_domains=["intel.com", "amd.com", "nvidia.com", "github.com"],
        topic="hardware_components",
        intent="hardware_components",
    )
    form_data = retrieval_module.SearchForm(queries=["seed"], plan=plan)

    def fake_search_web(request, engine, query, user=None):
        return [
            SearchResult(
                link="https://unknown.example.com/page",
                title="generic page",
                snippet="generic snippet",
            )
        ]

    monkeypatch.setattr(retrieval_module, "search_web", fake_search_web)

    _, executed_queries, _ = await retrieval_module._execute_web_search_with_planner(
        request, form_data, user=None
    )

    assert executed_queries[:5] == [
        "how to reset bios",
        "how to reset bios site:intel.com",
        "how to reset bios site:amd.com",
        "how to reset bios site:nvidia.com",
        "how to reset bios guide",
    ]


@pytest.mark.asyncio
async def test_planner_prioritizes_freshness_over_community_for_single_extra_slot(
    monkeypatch,
):
    config = make_planner_config(
        WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES=6,
        WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE=1.0,
        WEB_SEARCH_PLANNER_PRIMARY_STOP_TRUSTED_DOMAINS=999,
        WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE=1.0,
    )
    request = make_request(config)
    plan = make_plan(
        selected_domains=["docs.python.org", "developer.mozilla.org", "kubernetes.io"],
        time_sensitive=True,
        community_requested=True,
    )
    form_data = retrieval_module.SearchForm(queries=["seed"], plan=plan)

    def fake_search_web(request, engine, query, user=None):
        return [
            SearchResult(
                link="https://unknown.example.com/page",
                title="generic page",
                snippet="generic snippet",
            )
        ]

    monkeypatch.setattr(retrieval_module, "search_web", fake_search_web)

    _, executed_queries, _ = await retrieval_module._execute_web_search_with_planner(
        request, form_data, user=None
    )

    assert len(executed_queries) == 6
    assert "latest updates" in executed_queries[5]


@pytest.mark.asyncio
async def test_intent_coverage_guard_blocks_primary_stop_until_evidence_is_present(
    monkeypatch,
):
    config = make_planner_config(
        WEB_SEARCH_PLANNER_MAX_TOTAL_QUERIES=6,
        WEB_SEARCH_PLANNER_PLATEAU_FLOOR_SCORE=1.0,
    )
    request = make_request(config)
    plan = make_plan(
        selected_domains=["docs.aws.amazon.com", "kubernetes.io", "github.com"],
        preserve_tokens=["eks", "aws-cni"],
    )
    plan["intent_requirements"] = ["github_issues"]
    form_data = retrieval_module.SearchForm(queries=["seed"], plan=plan)

    def fake_search_web(request, engine, query, user=None):
        if "site:github.com" in query:
            return [
                SearchResult(
                    link="https://github.com/aws/amazon-vpc-cni-k8s/issues/2749",
                    title="failed to assign an IP address to container - Issue #2749",
                    snippet="plugin type aws-cni failed add",
                )
            ]
        return [
            SearchResult(
                link="https://docs.aws.amazon.com/eks/latest/userguide/managing-vpc-cni.html",
                title="Amazon VPC CNI - Amazon EKS",
                snippet="official guide for aws-cni on eks",
            )
        ]

    monkeypatch.setattr(retrieval_module, "search_web", fake_search_web)

    _, executed_queries, metrics = await retrieval_module._execute_web_search_with_planner(
        request, form_data, user=None
    )

    assert len(executed_queries) >= 3
    assert metrics["intent_coverage_history"][-1]["covered"]["issues"] is True
    assert metrics["stop_reason"] in {"quality_threshold_met", "quality_plateau"}


@pytest.mark.asyncio
async def test_middleware_falls_back_to_legacy_queries_when_planner_fails(monkeypatch):
    request = make_request(
        SimpleNamespace(
            ENABLE_WEB_SEARCH_PLANNER=True,
            WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE=4,
        )
    )
    user = SimpleNamespace(id="user-id", role="admin")

    async def fake_event_emitter(event):
        return None

    async def fake_generate_queries(request, form_data, user):
        return {"choices": [{"message": {"content": '{"queries":["legacy query"]}'}}]}

    captured = {}

    async def fake_process_web_search(request, form_data, user=None):
        captured["queries"] = form_data.queries
        captured["plan"] = form_data.plan
        return {
            "docs": [
                {
                    "content": "doc",
                    "metadata": {"source": "https://example.com", "title": "Example"},
                }
            ],
            "filenames": ["https://example.com"],
            "queries": form_data.queries,
            "items": [],
        }

    monkeypatch.setattr(
        middleware_module,
        "build_web_search_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("planner failed")),
    )
    monkeypatch.setattr(middleware_module, "generate_queries", fake_generate_queries)
    monkeypatch.setattr(middleware_module, "process_web_search", fake_process_web_search)

    form_data = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "please search this"}],
    }
    extra_params = {"__event_emitter__": fake_event_emitter}

    await middleware_module.chat_web_search_handler(request, form_data, extra_params, user)

    assert captured["plan"] is None
    assert captured["queries"] == ["legacy query"]
