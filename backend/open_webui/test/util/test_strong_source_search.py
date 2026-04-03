import json
from types import SimpleNamespace

import pytest

import open_webui.config as config
import open_webui.retrieval.web.planner as planner
import open_webui.routers.retrieval as retrieval
import open_webui.tools.builtin as builtin_tools
from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.planner import WebSearchPlan


def _make_request(**config_overrides):
    config = SimpleNamespace(
        WEB_SEARCH_ENGINE="duckduckgo",
        WEB_SEARCH_RESULT_COUNT=5,
        WEB_SEARCH_DOMAIN_FILTER_LIST=[],
        WEB_SEARCH_BRAVE_FALLBACK=True,
        WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES=2,
        WEB_SEARCH_BRAVE_MIN_INTERVAL_MS=1000,
        WEB_SEARCH_PLANNER_PRIMARY_STOP_SCORE=0.66,
        WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE=4,
        WEB_SEARCH_STRONG_FETCH_RERANK_CHUNK_SIZE=1200,
        WEB_SEARCH_STRONG_FETCH_RERANK_CHUNK_OVERLAP=160,
        WEB_SEARCH_STRONG_FETCH_RERANK_EXCERPT_CHARS=900,
        ENABLE_CORPUS_EVIDENCE_RERANKING=False,
        CORPUS_EVIDENCE_RERANKING_MODEL="BAAI/bge-reranker-v2-m3",
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
        selected_domains=[],
        selected_sources=[],
        base_exact_query="open webui planner behavior",
        base_general_query="open webui planner behavior overview",
        preserve_tokens=[],
        anchors={},
        intent_requirements=[],
        topic_candidates=["software_apis_devops"],
        allowed_domains_ranked=[],
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
        base_general_query="thessaloniki local tavern recommendations overview",
        preserve_tokens=[],
        anchors={},
        intent_requirements=[],
        topic_candidates=["general"],
        allowed_domains_ranked=[],
        planned_queries=[],
    )


def _quality_payload(
    *,
    link: str,
    title: str = "Result",
    snippet: str = "Evidence",
    quality: float = 0.91,
    trust: float = 0.91,
    trusted_unique_domains: int = 1,
):
    domain = link.split("/")[2]
    return {
        "avg_top_score": quality,
        "trusted_unique_domains": trusted_unique_domains,
        "scored_items": [
            {
                "title": title,
                "link": link,
                "snippet": snippet,
                "domain": domain,
                "quality": quality,
                "trust": trust,
            }
        ],
    }


def test_infer_domain_trust_score_prefers_explicit_allowed_domains(monkeypatch):
    monkeypatch.setattr(planner, "load_normalized_source_registry", lambda: [])
    plan = WebSearchPlan(
        intent="docs_api",
        topic="software_apis_devops",
        time_sensitive=False,
        community_requested=False,
        selected_domains=["custom.docs"],
        selected_sources=[],
        base_exact_query="custom docs",
        base_general_query="custom docs overview",
        preserve_tokens=[],
        anchors={},
        intent_requirements=[],
        topic_candidates=["software_apis_devops"],
        allowed_domains_ranked=["custom.docs"],
        planned_queries=[],
    )

    assert planner.infer_domain_trust_score("custom.docs", plan) == pytest.approx(0.85)
    assert planner.infer_domain_source_type("custom.docs", plan) == "allowed_domain"


def test_default_tool_prompt_guides_strong_search_then_fetch_url():
    template = config.DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    assert "prefer `web_research_strong`" in template
    assert "fetch_url` only after selecting concrete URLs" in template
    assert "snippet-first evidence pass" in template
    assert "use `fetch_url` on the most relevant cited URL" in template
    assert "full source was inspected server-side" in template


def test_web_research_strong_docstring_describes_fetch_url_fallback():
    doc = builtin_tools.web_research_strong.__doc__ or ""

    assert "ranked evidence snippets and" in doc
    assert "not full-page fetches" in doc
    assert "full source was inspected server-side" in doc
    assert "call `fetch_url` on the chosen citation URL" in doc


@pytest.mark.asyncio
async def test_search_strong_sources_tool_uses_allowed_domains(monkeypatch):
    async def fake_execute(_request, **kwargs):
        assert kwargs["query"] == "planner quality threshold"
        assert kwargs["allowed_domains"] == ["local.docs"]
        assert "selected_domains" not in kwargs
        return {
            "phase": "completed",
            "next_action": "answer",
            "allowed_domains": ["local.docs"],
            "queries": ["planner quality threshold site:local.docs"],
            "items": [],
            "evidence_items": [],
            "citation_items": [],
            "candidate_count": 0,
            "evidence_count": 0,
            "citation_count": 0,
            "coverage_complete": True,
            "quality_score": 0.91,
            "targeted_phase_executed": True,
            "local_phase_executed": True,
            "brave_fallback_used": False,
            "fallback_reason": None,
            "topic": "software_apis_devops",
            "trusted_domains": 1,
            "blocked_domains_applied": [],
            "time_scope_options": [],
            "selected_time_scope": "recent",
            "effective_recency_days": 365,
            "recency_policy_reason": "scope_recent_default",
            "message": "",
            "category_options": [],
            "domain_options": [],
            "selected_categories": [],
            "unavailable_categories": [],
            "local_primary_hits": 0,
        }

    monkeypatch.setattr(builtin_tools, "execute_strong_source_search", fake_execute)
    request = _make_request()

    output = await builtin_tools.search_strong_sources(
        query="planner quality threshold",
        allowed_domains=["local.docs"],
        __request__=request,
    )
    payload = json.loads(output)

    assert payload["allowed_domains"] == ["local.docs"]
    assert payload["targeted_phase_executed"] is True


@pytest.mark.asyncio
async def test_web_research_strong_tool_uses_allowed_domains(monkeypatch):
    async def fake_execute(_request, **kwargs):
        assert kwargs["query"] == "planner quality threshold"
        assert kwargs["allowed_domains"] == ["vendor.docs"]
        return {
            "phase": "completed",
            "next_action": "answer",
            "allowed_domains": ["vendor.docs"],
            "queries": ["planner quality threshold site:vendor.docs"],
            "items": [],
            "evidence_items": [],
            "citation_items": [],
            "candidate_count": 0,
            "evidence_count": 0,
            "citation_count": 0,
            "coverage_complete": True,
            "quality_score": 0.91,
            "targeted_phase_executed": True,
            "local_phase_executed": True,
            "brave_fallback_used": False,
            "fallback_reason": None,
            "topic": "software_apis_devops",
            "trusted_domains": 1,
            "blocked_domains_applied": [],
            "time_scope_options": [],
            "selected_time_scope": "recent",
            "effective_recency_days": 365,
            "recency_policy_reason": "scope_recent_default",
            "message": "",
            "category_options": [],
            "domain_options": [],
            "selected_categories": [],
            "unavailable_categories": [],
            "local_primary_hits": 0,
        }

    monkeypatch.setattr(builtin_tools, "execute_strong_source_search", fake_execute)
    request = _make_request()

    output = await builtin_tools.web_research_strong(
        query="planner quality threshold",
        allowed_domains=["vendor.docs"],
        __request__=request,
    )
    payload = json.loads(output)

    assert payload["allowed_domains"] == ["vendor.docs"]
    assert payload["targeted_phase_executed"] is True


@pytest.mark.asyncio
async def test_execute_strong_source_search_retires_category_mode(monkeypatch):
    request = _make_request()
    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: _make_general_plan()
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="ambiguous prompt",
        mode="list_categories",
    )

    assert payload["phase"] == "completed"
    assert payload["next_action"] == "search"
    assert payload["allowed_domains"] == []
    assert "retired" in payload["message"].lower()


@pytest.mark.asyncio
async def test_execute_strong_source_search_uses_allowed_domains_and_admin_blacklist(
    monkeypatch,
):
    request = _make_request(WEB_SEARCH_DOMAIN_FILTER_LIST=["!quora.com"])
    calls = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: _make_plan()
    )

    def fake_search_web(_request, engine, query, _user=None, filter_list=None):
        calls.append((engine, query, list(filter_list or [])))
        return [
            SearchResult(
                link="https://local.docs/guide",
                title="Guide",
                snippet="Strong local evidence",
            )
        ]

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(
        retrieval,
        "evaluate_signal_quality",
        lambda _items, _plan: _quality_payload(link="https://local.docs/guide"),
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        allowed_domains=["local.docs"],
        max_queries=1,
    )

    assert payload["phase"] == "completed"
    assert payload["allowed_domains"] == ["local.docs"]
    assert payload["blocked_domains_applied"] == ["quora.com"]
    assert payload["targeted_phase_executed"] is True
    assert payload["brave_fallback_used"] is False
    assert payload["queries"] == ["open webui planner behavior site:local.docs"]
    assert len(calls) == 1
    assert calls[0][0] == "duckduckgo"
    assert calls[0][2] == ["!quora.com", "local.docs"]


@pytest.mark.asyncio
async def test_execute_strong_source_search_open_mode_runs_bounded_discovery(monkeypatch):
    request = _make_request()
    calls = []

    monkeypatch.setattr(
        retrieval,
        "build_web_search_plan",
        lambda *args, **kwargs: _make_general_plan(time_sensitive=False),
    )

    def fake_search_web(_request, engine, query, _user=None, filter_list=None):
        calls.append((engine, query, list(filter_list or [])))
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
        lambda _items, _plan: _quality_payload(link="https://example.com/taverna"),
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )

    payload = await retrieval.execute_strong_source_search(
        request,
        query="recommend good tavernas",
        max_queries=2,
    )

    assert payload["phase"] == "completed"
    assert payload["allowed_domains"] == []
    assert payload["targeted_phase_executed"] is False
    assert payload["brave_fallback_used"] is False
    assert len(payload["queries"]) == 2
    assert all("site:" not in query for query in payload["queries"])
    assert all(call[2] == [] for call in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_recent_scope_applies_recency(monkeypatch):
    request = _make_request()
    calls = []

    monkeypatch.setattr(
        retrieval,
        "build_web_search_plan",
        lambda *args, **kwargs: _make_plan(time_sensitive=False),
    )

    def fake_search_web(_request, engine, query, _user=None, filter_list=None):
        calls.append(query)
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
        lambda _items, _plan: _quality_payload(link="https://local.docs/recent"),
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
        selected_time_scope="recent",
        allowed_domains=["local.docs"],
        max_queries=1,
    )

    assert payload["selected_time_scope"] == "recent"
    assert payload["effective_recency_days"] == 120
    assert all("last 120 days" in query for query in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_evergreen_scope_suppresses_recency(monkeypatch):
    request = _make_request()
    calls = []

    monkeypatch.setattr(
        retrieval,
        "build_web_search_plan",
        lambda *args, **kwargs: _make_plan(time_sensitive=False),
    )

    def fake_search_web(_request, engine, query, _user=None, filter_list=None):
        calls.append(query)
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
        lambda _items, _plan: _quality_payload(link="https://local.docs/evergreen"),
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
        selected_time_scope="evergreen",
        allowed_domains=["local.docs"],
        max_queries=1,
    )

    assert payload["selected_time_scope"] == "evergreen"
    assert payload["effective_recency_days"] is None
    assert all("last " not in query for query in calls)


@pytest.mark.asyncio
async def test_execute_strong_source_search_fallbacks_when_allowed_domain_phase_is_weak(
    monkeypatch,
):
    request = _make_request(WEB_SEARCH_ENGINE="brave", WEB_SEARCH_BRAVE_FALLBACK_MAX_QUERIES=1)
    calls = []
    quality_call = {"count": 0}

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: _make_plan()
    )

    def fake_search_web(_request, engine, query, _user=None, filter_list=None):
        calls.append((engine, query, list(filter_list or [])))
        if "site:local.docs" in query:
            return [
                SearchResult(
                    link="https://local.docs/weak",
                    title="Weak local",
                    snippet="weak",
                )
            ]
        return [
            SearchResult(
                link="https://local.docs/recovered",
                title="Recovered local",
                snippet="strong",
            )
        ]

    def fake_quality(_items, _plan):
        quality_call["count"] += 1
        if quality_call["count"] == 1:
            return _quality_payload(
                link="https://local.docs/weak",
                quality=0.35,
                trust=0.91,
                trusted_unique_domains=1,
            )
        return _quality_payload(
            link="https://local.docs/recovered",
            quality=0.88,
            trust=0.91,
            trusted_unique_domains=1,
        )

    async def fake_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(retrieval, "search_web", fake_search_web)
    monkeypatch.setattr(retrieval, "evaluate_signal_quality", fake_quality)
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )
    monkeypatch.setattr(retrieval, "_wait_for_brave_fallback_slot", fake_wait)

    payload = await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        allowed_domains=["local.docs"],
        max_queries=1,
    )

    assert payload["brave_fallback_used"] is True
    assert payload["fallback_reason"] == "insufficient_allowed_domain_quality"
    assert len(payload["queries"]) == 2
    assert calls[0][2] == ["local.docs"]
    assert calls[1][2] == ["local.docs"]


@pytest.mark.asyncio
async def test_execute_strong_source_search_emits_done_status(monkeypatch):
    request = _make_request()
    events = []

    monkeypatch.setattr(
        retrieval, "build_web_search_plan", lambda *args, **kwargs: _make_plan()
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
        lambda _items, _plan: _quality_payload(link="https://local.docs/a"),
    )
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )

    async def event_emitter(event):
        events.append(event)

    await retrieval.execute_strong_source_search(
        request,
        query="planner quality threshold",
        allowed_domains=["local.docs"],
        max_queries=1,
        event_emitter=event_emitter,
    )

    status_events = [event for event in events if event.get("type") == "status"]
    assert len(status_events) >= 2
    assert status_events[0]["data"]["done"] is False
    assert status_events[-1]["data"]["done"] is True


@pytest.mark.asyncio
async def test_execute_strong_source_search_uses_post_fetch_reranked_excerpts(
    monkeypatch,
):
    request = _make_request(ENABLE_CORPUS_EVIDENCE_RERANKING=True)

    monkeypatch.setattr(
        retrieval,
        "build_web_search_plan",
        lambda *args, **kwargs: _make_plan(time_sensitive=False),
    )
    monkeypatch.setattr(
        retrieval,
        "search_web",
        lambda *_args, **_kwargs: [
            SearchResult(
                link="https://docs.python.org/3/howto/descriptor.html",
                title="Descriptor Guide",
                snippet="Custom validators need to inherit from Validator.",
            ),
            SearchResult(
                link="https://docs.pydantic.dev/latest/concepts/validators/",
                title="Validators - Pydantic",
                snippet="Annotated validators, field validators, and model validators.",
            ),
        ],
    )

    def fake_quality(_items, _plan):
        return {
            "avg_top_score": 0.58,
            "trusted_unique_domains": 1,
            "scored_items": [
                {
                    "title": "Descriptor Guide",
                    "link": "https://docs.python.org/3/howto/descriptor.html",
                    "snippet": "Custom validators need to inherit from Validator.",
                    "domain": "docs.python.org",
                    "quality": 0.58,
                    "trust": 0.92,
                },
                {
                    "title": "Validators - Pydantic",
                    "link": "https://docs.pydantic.dev/latest/concepts/validators/",
                    "snippet": "Annotated validators, field validators, and model validators.",
                    "domain": "docs.pydantic.dev",
                    "quality": 0.56,
                    "trust": 0.55,
                },
            ],
        }

    monkeypatch.setattr(retrieval, "evaluate_signal_quality", fake_quality)
    monkeypatch.setattr(
        retrieval,
        "evaluate_intent_coverage",
        lambda _items, _plan: {"required": {}, "covered": {}, "complete": True},
    )
    monkeypatch.setattr(
        retrieval,
        "get_content_from_url",
        lambda _request, url: (
            (
                "This page explains Python descriptors and binding behavior."
                if "python.org" in url
                else "Pydantic v2 recommends the Annotated pattern together with Field, "
                "BeforeValidator, and field_validator for structured validation."
            ),
            [],
        ),
    )

    def fake_rerank_items(*, query, items, config_or_path, text_getter):
        ranked = []
        for item in items:
            ranked_item = dict(item)
            ranked_item["rerank_score"] = (
                0.97 if "Pydantic" in text_getter(item) else 0.21
            )
            ranked.append(ranked_item)
        ranked.sort(key=lambda item: -float(item["rerank_score"]))
        return (ranked, "BAAI/bge-reranker-v2-m3")

    monkeypatch.setattr(retrieval, "rerank_items", fake_rerank_items)

    payload = await retrieval.execute_strong_source_search(
        request,
        query="Find the currently recommended Pydantic v2 pattern for structured validation",
        allowed_domains=["docs.python.org", "pydantic.dev"],
        max_queries=2,
    )

    assert payload["post_fetch_rerank_used"] is True
    assert payload["post_fetch_reranker_model"] == "BAAI/bge-reranker-v2-m3"
    assert payload["fetched_url_count"] >= 1
    assert payload["evidence_view_note"]
    assert payload["citation_items"][0]["full_document_fetched"] is True
    assert payload["citation_items"][0]["returned_mode"] == "relevant_excerpt"
    assert "pydantic" in payload["citation_items"][0]["link"]
    assert "Annotated pattern" in payload["citation_items"][0]["snippet"]
