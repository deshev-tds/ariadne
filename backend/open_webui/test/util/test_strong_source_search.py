import json
from types import SimpleNamespace

import pytest

import open_webui.retrieval.web.planner as planner
import open_webui.routers.retrieval as retrieval
import open_webui.tools.builtin as builtin_tools
from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.planner import (
    NormalizedSource,
    SelectedSource,
    WebSearchPlan,
)


def _make_request(**config_overrides):
    config = SimpleNamespace(
        WEB_SEARCH_ENGINE="duckduckgo",
        WEB_SEARCH_RESULT_COUNT=5,
        WEB_SEARCH_LOCAL_FIRST=True,
        WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=2,
        WEB_SEARCH_BRAVE_FALLBACK=True,
        WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES=2,
        WEB_SEARCH_BRAVE_MIN_INTERVAL_MS=1000,
        WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE=0.66,
        WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE=4,
    )
    for key, value in config_overrides.items():
        setattr(config, key, value)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _make_plan() -> WebSearchPlan:
    return WebSearchPlan(
        intent="docs_api",
        topic="software_apis_devops",
        time_sensitive=True,
        community_requested=False,
        selected_domains=["local.docs", "remote.docs"],
        selected_sources=[],
        base_exact_query="open webui planner behavior",
        base_general_query="open webui planner behavior overview",
        preserve_tokens=[],
        anchors={},
        intent_requirements=[],
        topic_candidates=["software_apis_devops"],
        allowed_domains_ranked=["local.docs", "remote.docs", "vendor.docs"],
        planned_queries=[],
    )


def test_select_sources_for_topic_local_first_prioritizes_local(monkeypatch):
    sources = [
        NormalizedSource(
            domain="remote.docs",
            topic="software_apis_devops",
            family="primary_docs",
            source_type="primary_docs",
            trust_tier="A",
            trust_score=1.0,
            is_local=False,
            access="open",
            freshness_profile="medium",
            default_priority=1,
            allow_site_constraint=True,
            prefer_for_time_sensitive=False,
            prefer_for_exact_facts=True,
            prefer_for_community_signals=False,
        ),
        NormalizedSource(
            domain="local.docs",
            topic="software_apis_devops",
            family="primary_docs",
            source_type="primary_docs",
            trust_tier="B",
            trust_score=0.72,
            is_local=True,
            access="open",
            freshness_profile="high",
            default_priority=2,
            allow_site_constraint=True,
            prefer_for_time_sensitive=True,
            prefer_for_exact_facts=True,
            prefer_for_community_signals=False,
        ),
    ]
    monkeypatch.setattr(planner, "load_normalized_source_registry", lambda: sources)

    ranked_default = planner.select_sources_for_topic(
        topic="software_apis_devops",
        max_targeted_domains=2,
        local_first=False,
    )
    ranked_local_first = planner.select_sources_for_topic(
        topic="software_apis_devops",
        max_targeted_domains=2,
        local_first=True,
    )

    assert ranked_default[0].domain == "remote.docs"
    assert ranked_local_first[0].domain == "local.docs"
    assert ranked_local_first[0].is_local is True


@pytest.mark.asyncio
async def test_search_strong_sources_tool_returns_telemetry_schema(monkeypatch):
    async def fake_execute(_request, **kwargs):
        assert kwargs["query"] == "planner quality threshold"
        return {
            "queries": ["planner quality threshold site:local.docs"],
            "items": [],
            "selected_domains": ["local.docs"],
            "coverage_complete": True,
            "quality_score": 0.91,
            "local_phase_executed": True,
            "brave_fallback_used": False,
            "fallback_reason": None,
        }

    monkeypatch.setattr(builtin_tools, "execute_strong_source_search", fake_execute)
    request = _make_request()

    output = await builtin_tools.search_strong_sources(
        query="planner quality threshold",
        __request__=request,
    )
    payload = json.loads(output)

    assert payload["queries"][0].startswith("planner quality threshold")
    assert payload["local_phase_executed"] is True
    assert payload["brave_fallback_used"] is False
    assert "quality_score" in payload


@pytest.mark.asyncio
async def test_execute_strong_source_search_local_phase_only(monkeypatch):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan()
    calls = []

    monkeypatch.setattr(retrieval, "build_web_search_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        retrieval,
        "select_sources_for_topic",
        lambda *args, **kwargs: [
            SelectedSource(domain="local.docs", tier="primary_docs", is_local=True),
            SelectedSource(domain="local.mirror", tier="primary_docs", is_local=True),
        ],
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        return [
            SearchResult(
                link=f"https://local.docs/{len(calls)}",
                title="Local result",
                snippet="High-confidence local evidence",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.92,
            "trusted_unique_domains": 2,
            "scored_items": [
                {
                    "title": "Local result",
                    "link": "https://local.docs/1",
                    "snippet": "High-confidence local evidence",
                    "domain": "local.docs",
                    "quality": 0.92,
                    "trust": 0.95,
                }
            ],
        },
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        max_queries=2,
    )

    assert payload["local_phase_executed"] is True
    assert payload["brave_fallback_used"] is False
    assert all("site:" in query for query in payload["queries"])
    assert all(engine == "duckduckgo" for engine, _ in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_uses_brave_fallback_with_limit(monkeypatch):
    request = _make_request(WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES=2)
    plan = _make_plan()
    calls = []
    quality_call = {"count": 0}

    monkeypatch.setattr(retrieval, "build_web_search_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(
        retrieval,
        "select_sources_for_topic",
        lambda *args, **kwargs: [
            SelectedSource(domain="local.docs", tier="primary_docs", is_local=True),
            SelectedSource(domain="remote.docs", tier="primary_docs", is_local=False),
            SelectedSource(domain="vendor.docs", tier="primary_docs", is_local=False),
        ],
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        domain = "local.docs" if "local.docs" in query else "remote.docs"
        return [
            SearchResult(
                link=f"https://{domain}/result-{len(calls)}",
                title="Result",
                snippet="Evidence",
            )
        ]

    def fake_quality(_items, _plan):
        quality_call["count"] += 1
        if quality_call["count"] == 1:
            return {
                "avg_top_score": 0.40,
                "trusted_unique_domains": 1,
                "scored_items": [
                    {
                        "title": "Weak local",
                        "link": "https://local.docs/result-1",
                        "snippet": "weak",
                        "domain": "local.docs",
                        "quality": 0.40,
                        "trust": 0.95,
                    }
                ],
            }
        return {
            "avg_top_score": 0.88,
            "trusted_unique_domains": 2,
            "scored_items": [
                {
                    "title": "Recovered",
                    "link": "https://remote.docs/result-2",
                    "snippet": "strong",
                    "domain": "remote.docs",
                    "quality": 0.88,
                    "trust": 0.95,
                }
            ],
        }

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(retrieval, "evaluate_signal_quality", fake_quality)
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )

    async def fake_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(retrieval, "_wait_for_brave_fallback_slot", fake_wait)

    payload = await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        max_queries=1,
    )

    brave_calls = [entry for entry in calls if entry[0] == "brave"]
    assert payload["brave_fallback_used"] is True
    assert len(brave_calls) <= 2
    assert all("site:" in query for _, query in brave_calls)


@pytest.mark.asyncio
async def test_wait_for_brave_fallback_slot_respects_interval(monkeypatch):
    request = _make_request()
    sleeps = []
    monotonic_values = [0.0, 0.0, 0.2, 0.2, 0.2, 0.2]

    def fake_monotonic():
        if monotonic_values:
            return monotonic_values.pop(0)
        return 0.2

    monkeypatch.setattr(retrieval.time, "monotonic", fake_monotonic)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(retrieval.asyncio, "sleep", fake_sleep)

    await retrieval._wait_for_brave_fallback_slot(request, 1000)
    await retrieval._wait_for_brave_fallback_slot(request, 1000)

    assert len(sleeps) == 1
    assert sleeps[0] == pytest.approx(0.8, abs=0.01)
