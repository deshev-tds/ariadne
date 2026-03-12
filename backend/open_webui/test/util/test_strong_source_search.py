import json
from types import SimpleNamespace

import pytest

import open_webui.retrieval.web.planner as planner
import open_webui.routers.retrieval as retrieval
import open_webui.tools.builtin as builtin_tools
from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.planner import NormalizedSource, WebSearchPlan


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


def _make_plan(*, time_sensitive: bool = True) -> WebSearchPlan:
    return WebSearchPlan(
        intent="docs_api",
        topic="software_apis_devops",
        time_sensitive=time_sensitive,
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


def _make_general_plan(*, time_sensitive: bool = True) -> WebSearchPlan:
    return WebSearchPlan(
        intent="general_info",
        topic="general",
        time_sensitive=time_sensitive,
        community_requested=False,
        selected_domains=[],
        selected_sources=[],
        base_exact_query="best local tavernas in thessaloniki",
        base_general_query="thessaloniki local tavern recommendations",
        preserve_tokens=[],
        anchors={},
        intent_requirements=[],
        topic_candidates=["general"],
        allowed_domains_ranked=[],
        planned_queries=[],
    )


def _domain_options(domains: list[tuple[str, bool, str]]):
    # (domain, is_local, source_type)
    options = []
    for idx, (domain, is_local, source_type) in enumerate(domains, start=1):
        options.append(
            {
                "domain": domain,
                "category": "software",
                "topic": "software_apis_devops",
                "source_type": source_type,
                "trust_tier": "A" if source_type != "community" else "C",
                "trust": 0.95 if source_type != "community" else 0.45,
                "is_local": is_local,
                "access": "open",
                "freshness_profile": "high",
                "default_priority": idx,
            }
        )
    return options


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
        assert kwargs["mode"] == "search"
        return {
            "phase": "completed",
            "next_action": "answer",
            "queries": ["planner quality threshold site:local.docs"],
            "items": [],
            "evidence_items": [],
            "citation_items": [],
            "candidate_count": 0,
            "evidence_count": 0,
            "citation_count": 0,
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

    assert payload["phase"] == "completed"
    assert payload["next_action"] == "answer"
    assert payload["local_phase_executed"] is True
    assert payload["brave_fallback_used"] is False


@pytest.mark.asyncio
async def test_web_research_strong_tool_returns_telemetry_schema(monkeypatch):
    async def fake_execute(_request, **kwargs):
        assert kwargs["query"] == "planner quality threshold"
        assert kwargs["mode"] == "search"
        return {
            "phase": "completed",
            "next_action": "answer",
            "queries": ["planner quality threshold site:local.docs"],
            "items": [],
            "evidence_items": [],
            "citation_items": [],
            "candidate_count": 0,
            "evidence_count": 0,
            "citation_count": 0,
            "selected_domains": ["local.docs"],
            "coverage_complete": True,
            "quality_score": 0.91,
            "local_phase_executed": True,
            "brave_fallback_used": False,
            "fallback_reason": None,
        }

    monkeypatch.setattr(builtin_tools, "execute_strong_source_search", fake_execute)
    request = _make_request()

    output = await builtin_tools.web_research_strong(
        query="planner quality threshold",
        __request__=request,
    )
    payload = json.loads(output)

    assert payload["phase"] == "completed"
    assert payload["next_action"] == "answer"
    assert payload["local_phase_executed"] is True
    assert payload["brave_fallback_used"] is False


@pytest.mark.asyncio
async def test_execute_strong_source_search_requires_category_when_low_confidence(
    monkeypatch,
):
    request = _make_request()
    monkeypatch.setattr(
        retrieval,
        "_coarse_route_category",
        lambda *_args, **_kwargs: {
            "category": "general",
            "confidence": 0.4,
            "ambiguous": True,
            "scores": {},
        },
    )

    payload = await retrieval.execute_strong_source_search(
        request, query="ambiguous prompt"
    )

    assert payload["phase"] == "awaiting_category_selection"
    assert payload["next_action"] == "select_categories"


@pytest.mark.asyncio
async def test_execute_strong_source_search_high_confidence_skips_category_to_domains(
    monkeypatch,
):
    request = _make_request()
    monkeypatch.setattr(
        retrieval,
        "_coarse_route_category",
        lambda *_args, **_kwargs: {
            "category": "software",
            "confidence": 0.9,
            "ambiguous": False,
            "scores": {"software": 3},
        },
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [
                ("local.docs", True, "primary_docs"),
                ("remote.docs", False, "primary_docs"),
            ]
        ),
    )

    payload = await retrieval.execute_strong_source_search(
        request, query="python api docs"
    )

    assert payload["phase"] == "awaiting_domain_selection"
    assert payload["selected_categories"] == ["software"]
    assert payload["next_action"] == "select_domains"
    assert len(payload["domain_options"]) >= 1
    assert len(payload["time_scope_options"]) == 3
    assert {item["scope"] for item in payload["time_scope_options"]} == {
        "evergreen",
        "recent",
        "breaking",
    }


@pytest.mark.asyncio
async def test_execute_strong_source_search_rejects_invalid_domain_selection(
    monkeypatch,
):
    request = _make_request()
    monkeypatch.setattr(
        retrieval,
        "_coarse_route_category",
        lambda *_args, **_kwargs: {
            "category": "software",
            "confidence": 0.9,
            "ambiguous": False,
            "scores": {"software": 3},
        },
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [("local.docs", True, "primary_docs")]
        ),
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="python api docs",
        selected_categories=["software"],
        selected_domains=["bad.invalid"],
    )

    assert payload["phase"] == "awaiting_domain_selection"
    assert payload["next_action"] == "fix_domain_selection"
    assert "invalid_domains" in payload["errors"]
    assert payload["invalid_domains"] == ["bad.invalid"]


@pytest.mark.asyncio
async def test_list_domains_returns_reselect_when_category_has_no_curated_domains(
    monkeypatch,
):
    request = _make_request()
    monkeypatch.setattr(
        retrieval,
        "_coarse_route_category",
        lambda *_args, **_kwargs: {
            "category": "general",
            "confidence": 0.9,
            "ambiguous": False,
            "scores": {"general": 1},
        },
    )
    monkeypatch.setattr(
        retrieval, "_build_domain_options_for_categories", lambda *_args, **_kwargs: []
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="best neighborhood tavernas",
        mode="list_domains",
        selected_categories=["general"],
    )

    assert payload["phase"] == "awaiting_category_selection"
    assert payload["next_action"] == "reselect_category"
    assert payload["fallback_reason"] == "no_curated_domains_in_category"
    assert payload["missing_curated_domains_for"] == ["general"]


@pytest.mark.asyncio
async def test_list_categories_hides_zero_domain_categories_by_default(monkeypatch):
    request = _make_request()
    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: _make_general_plan()
    )
    monkeypatch.setattr(
        retrieval,
        "_build_category_options",
        lambda include_community=False: [
            {
                "category": "news",
                "summary": "Current events",
                "topics": ["news_current_events"],
                "domain_count": 4,
                "local_domain_count": 0,
                "has_local_domains": False,
            },
            {
                "category": "general",
                "summary": "Fallback",
                "topics": ["general"],
                "domain_count": 0,
                "local_domain_count": 0,
                "has_local_domains": False,
            },
        ],
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="find local recommendations",
        mode="list_categories",
    )

    assert payload["phase"] == "awaiting_category_selection"
    assert [item["category"] for item in payload["category_options"]] == ["news"]
    assert [item["category"] for item in payload["unavailable_categories"]] == [
        "general"
    ]


@pytest.mark.asyncio
async def test_search_auto_broadens_when_category_has_no_curated_domains(
    monkeypatch,
):
    request = _make_request(WEB_SEARCH_ENGINE="brave")
    plan = _make_general_plan(time_sensitive=True)
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_coarse_route_category",
        lambda *_args, **_kwargs: {
            "category": "general",
            "confidence": 0.9,
            "ambiguous": False,
            "scores": {"general": 2},
        },
    )
    monkeypatch.setattr(
        retrieval, "_build_domain_options_for_categories", lambda *_args, **_kwargs: []
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        return [
            SearchResult(
                link="https://example.com/taverna",
                title="Local taverna guide",
                snippet="Locals recommend this place",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.84,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Local taverna guide",
                    "link": "https://example.com/taverna",
                    "snippet": "Locals recommend this place",
                    "domain": "example.com",
                    "quality": 0.84,
                    "trust": 0.84,
                }
            ],
        },
    )
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
        query="summarize open source governance models",
        selected_categories=["general"],
    )

    assert payload["phase"] == "completed"
    assert payload["brave_fallback_used"] is True
    assert payload["fallback_reason"] == "no_curated_domains_in_category"
    assert payload["selected_domains"] == []
    assert len(payload["queries"]) >= 1
    assert all(engine == "brave" for engine, _ in calls)


@pytest.mark.asyncio
async def test_search_auto_broadens_when_inferred_category_has_no_curated_domains(
    monkeypatch,
):
    request = _make_request(WEB_SEARCH_ENGINE="duckduckgo")
    plan = _make_general_plan(time_sensitive=True)
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_coarse_route_category",
        lambda *_args, **_kwargs: {
            "category": "general",
            "confidence": 0.92,
            "ambiguous": False,
            "scores": {"general": 3},
        },
    )
    monkeypatch.setattr(
        retrieval,
        "_build_category_options",
        lambda include_community=False: [
            {
                "category": "general",
                "summary": "Fallback",
                "topics": ["general"],
                "domain_count": 0,
                "local_domain_count": 0,
                "has_local_domains": False,
            }
        ],
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        return [
            SearchResult(
                link="https://example.com/result",
                title="Result",
                snippet="Broader result",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.81,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Result",
                    "link": "https://example.com/result",
                    "snippet": "Broader result",
                    "domain": "example.com",
                    "quality": 0.81,
                    "trust": 0.81,
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
        query="recommend good places",
    )

    assert payload["phase"] == "completed"
    assert payload["fallback_reason"] == "no_curated_domains_for_inferred_category"
    assert payload["selected_categories"] == ["general"]
    assert len(payload["queries"]) >= 1
    assert all(engine == "duckduckgo" for engine, _ in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_local_phase_only(monkeypatch):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan()
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [
                ("local.docs", True, "primary_docs"),
                ("local.mirror", True, "primary_docs"),
            ]
        ),
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
        selected_categories=["software"],
        selected_domains=["local.docs", "local.mirror"],
    )

    assert payload["phase"] == "completed"
    assert payload["local_phase_executed"] is True
    assert payload["brave_fallback_used"] is False
    assert all("site:" in query for query in payload["queries"])
    assert all(engine == "duckduckgo" for engine, _ in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_evergreen_scope_suppresses_recency(
    monkeypatch,
):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan(time_sensitive=False)
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options([("local.docs", True, "primary_docs")]),
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        return [
            SearchResult(
                link="https://local.docs/evergreen",
                title="Evergreen result",
                snippet="Stable domain knowledge",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.92,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Evergreen result",
                    "link": "https://local.docs/evergreen",
                    "snippet": "Stable domain knowledge",
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
        query="penicillin beta lactam mechanism",
        recency_days=365,
        selected_categories=["science"],
        selected_domains=["local.docs"],
        selected_time_scope="evergreen",
    )

    assert payload["effective_recency_days"] is None
    assert payload["selected_time_scope"] == "evergreen"
    assert all("last " not in query for query in payload["queries"])
    assert all("last " not in query for _, query in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_recent_scope_applies_recency(monkeypatch):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan(time_sensitive=False)
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options([("local.docs", True, "primary_docs")]),
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        return [
            SearchResult(
                link="https://local.docs/recent",
                title="Recent result",
                snippet="Recent signals",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.92,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Recent result",
                    "link": "https://local.docs/recent",
                    "snippet": "Recent signals",
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
        query="penicillin beta lactam mechanism",
        recency_days=120,
        selected_categories=["science"],
        selected_domains=["local.docs"],
        selected_time_scope="recent",
    )

    assert payload["effective_recency_days"] == 120
    assert payload["selected_time_scope"] == "recent"
    assert all("last 120 days" in query for query in payload["queries"])
    assert all("last 120 days" in query for _, query in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_default_scope_uses_evergreen_when_not_time_sensitive(
    monkeypatch,
):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan(time_sensitive=False)
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options([("local.docs", True, "primary_docs")]),
    )

    def fake_search_web(_request, engine, query, _user=None):
        calls.append((engine, query))
        return [
            SearchResult(
                link="https://local.docs/default",
                title="Default scope",
                snippet="Evergreen default",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.92,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Default scope",
                    "link": "https://local.docs/default",
                    "snippet": "Evergreen default",
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
        query="penicillin beta lactam mechanism",
        recency_days=365,
        selected_categories=["science"],
        selected_domains=["local.docs"],
    )

    assert payload["selected_time_scope"] == "evergreen"
    assert payload["effective_recency_days"] is None
    assert all("last " not in query for query in payload["queries"])
    assert all("last " not in query for _, query in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_uses_fallback_engine_with_limit(
    monkeypatch,
):
    request = _make_request(
        WEB_SEARCH_ENGINE="brave",
        WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES=2,
        WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=2,
    )
    plan = _make_plan()
    calls = []
    quality_call = {"count": 0}

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [
                ("local.docs", True, "primary_docs"),
                ("remote.docs", False, "primary_docs"),
                ("vendor.docs", False, "primary_docs"),
            ]
        ),
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
        selected_categories=["software"],
        selected_domains=["local.docs", "remote.docs", "vendor.docs"],
    )

    brave_calls = [entry for entry in calls if entry[0] == "brave"]
    fallback_brave_calls = [
        entry for entry in brave_calls if "site:local.docs" not in entry[1]
    ]
    assert payload["brave_fallback_used"] is True
    assert len(fallback_brave_calls) <= 2
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


@pytest.mark.asyncio
async def test_execute_strong_source_search_applies_citation_filter(monkeypatch):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan()

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [
                ("local.docs", True, "primary_docs"),
                ("community.docs", True, "community"),
                ("lowtrust.docs", True, "primary_docs"),
            ]
        ),
    )
    monkeypatch.setattr(
        retrieval,
        "search_web",
        lambda *_args, **_kwargs: [
            SearchResult(link="https://local.docs/a", title="A", snippet="Strong"),
            SearchResult(
                link="https://community.docs/b", title="B", snippet="Community"
            ),
            SearchResult(
                link="https://lowtrust.docs/c", title="C", snippet="Low trust"
            ),
        ],
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.8,
            "trusted_unique_domains": 2,
            "scored_items": [
                {
                    "title": "A",
                    "link": "https://local.docs/a",
                    "snippet": "Strong",
                    "domain": "local.docs",
                    "quality": 0.9,
                    "trust": 0.95,
                },
                {
                    "title": "B",
                    "link": "https://community.docs/b",
                    "snippet": "Community",
                    "domain": "community.docs",
                    "quality": 0.8,
                    "trust": 0.95,
                },
                {
                    "title": "C",
                    "link": "https://lowtrust.docs/c",
                    "snippet": "Low trust",
                    "domain": "lowtrust.docs",
                    "quality": 0.4,
                    "trust": 0.70,
                },
            ],
        },
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )
    monkeypatch.setattr(
        retrieval,
        "infer_domain_source_type",
        lambda domain, _plan: (
            "community" if domain == "community.docs" else "primary_docs"
        ),
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        max_queries=1,
        selected_categories=["software"],
        selected_domains=["local.docs", "community.docs", "lowtrust.docs"],
        max_domains=4,
    )

    citation_domains = {item["domain"] for item in payload["citation_items"]}
    assert "local.docs" in citation_domains
    assert "community.docs" not in citation_domains
    assert "lowtrust.docs" not in citation_domains
    assert payload["citation_count"] == len(payload["citation_items"])


@pytest.mark.asyncio
async def test_execute_strong_source_search_emits_done_status(monkeypatch):
    request = _make_request(WEB_SEARCH_LOCAL_MIN_PRIMARY_HITS=1)
    plan = _make_plan()

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [("local.docs", True, "primary_docs")]
        ),
    )
    monkeypatch.setattr(
        retrieval,
        "search_web",
        lambda *_args, **_kwargs: [
            SearchResult(
                link="https://local.docs/a",
                title="Local result",
                snippet="Strong local evidence",
            )
        ],
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: {
            "avg_top_score": 0.9,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Local result",
                    "link": "https://local.docs/a",
                    "snippet": "Strong local evidence",
                    "domain": "local.docs",
                    "quality": 0.9,
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

    events = []

    async def event_emitter(event):
        events.append(event)

    await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        max_queries=1,
        selected_categories=["software"],
        selected_domains=["local.docs"],
        event_emitter=event_emitter,
    )

    status_events = [event for event in events if event.get("type") == "status"]
    assert len(status_events) >= 2
    assert status_events[0]["data"]["done"] is False
    assert status_events[-1]["data"]["done"] is True


@pytest.mark.asyncio
async def test_execute_strong_source_search_emits_fallback_status(monkeypatch):
    request = _make_request(WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES=1)
    plan = _make_plan()
    quality_call = {"count": 0}

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(
        retrieval,
        "_build_domain_options_for_categories",
        lambda *_args, **_kwargs: _domain_options(
            [
                ("local.docs", True, "primary_docs"),
                ("remote.docs", False, "primary_docs"),
            ]
        ),
    )
    monkeypatch.setattr(
        retrieval,
        "search_web",
        lambda _request, engine, _query, _user=None: [
            SearchResult(
                link=f"https://{'local.docs' if engine != 'brave' else 'remote.docs'}/result",
                title="Result",
                snippet="Evidence",
            )
        ],
    )

    def fake_quality(_items, _plan):
        quality_call["count"] += 1
        if quality_call["count"] == 1:
            return {
                "avg_top_score": 0.2,
                "trusted_unique_domains": 1,
                "scored_items": [
                    {
                        "title": "Weak local",
                        "link": "https://local.docs/result",
                        "snippet": "weak",
                        "domain": "local.docs",
                        "quality": 0.2,
                        "trust": 0.95,
                    }
                ],
            }
        return {
            "avg_top_score": 0.9,
            "trusted_unique_domains": 2,
            "scored_items": [
                {
                    "title": "Recovered",
                    "link": "https://remote.docs/result",
                    "snippet": "strong",
                    "domain": "remote.docs",
                    "quality": 0.9,
                    "trust": 0.95,
                }
            ],
        }

    monkeypatch.setattr(retrieval, "evaluate_signal_quality", fake_quality)
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )

    async def fake_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(retrieval, "_wait_for_brave_fallback_slot", fake_wait)

    events = []

    async def event_emitter(event):
        events.append(event)

    payload = await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        max_queries=1,
        selected_categories=["software"],
        selected_domains=["local.docs", "remote.docs"],
        event_emitter=event_emitter,
    )

    assert payload["brave_fallback_used"] is True
    status_messages = [
        event.get("data", {}).get("description")
        for event in events
        if event.get("type") == "status"
    ]
    assert (
        "Focused search did not return enough evidence, trying broader search now"
        in status_messages
    )
