import json
from types import SimpleNamespace

import pytest

import open_webui.tools.builtin as builtin_tools
import open_webui.utils.web_evidence_store as web_store


def _request_with_retrieval_mode(
    mode: str = "legacy_store_retrieval",
    *,
    concept_alignment_enabled: bool = False,
):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    WEB_EVIDENCE_RETRIEVAL_MODE=mode,
                    ENABLE_WEB_EVIDENCE_CONCEPT_ALIGNMENT=concept_alignment_enabled,
                ),
                EMBEDDING_FUNCTION=None,
                RERANKING_FUNCTION=None,
            )
        )
    )


def test_store_and_query_web_evidence_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    stored = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-1",
        url="https://example.org/page",
        title="Example Page",
        content=(
            "Evolutionary algorithms use mutation and crossover in a population-based "
            "search process. Genetic algorithms are a major class of evolutionary methods."
        ),
    )

    assert stored["status"] == "stored"
    assert stored["artifact_id"].startswith("wp_")

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        message_id="msg-1",
        query="genetic algorithms mutation crossover",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert queried["scope_mode"] == "explicit"
    assert queried["searched_artifact_count"] == 1
    assert queried["evidence_strength"] in {"adequate", "strong"}
    assert queried["narrow_count"] >= 1
    assert len(queried["snippets"]) >= 1
    first = queried["snippets"][0]
    assert first["artifact_id"] == stored["artifact_id"]
    assert "genetic" in first["text"].lower()


def test_query_web_evidence_store_implicit_scope_uses_exact_message(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    first = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-1",
        url="https://example.org/first",
        title="First",
        content="Hormuz chokepoint logistics and shipping insurance matter here.",
    )
    second = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-2",
        url="https://example.org/second",
        title="Second",
        content="This page only discusses a different region and topic.",
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        message_id="msg-1",
        query="Hormuz shipping insurance logistics",
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert queried["scope_mode"] == "implicit_current_message"
    assert queried["searched_artifact_ids"] == [first["artifact_id"]]
    assert second["artifact_id"] not in queried["searched_artifact_ids"]
    assert queried["searched_domains"] == ["example.org"]
    assert queried["suggested_next_action"] in {
        "refine_query",
        "answer_with_current_evidence",
        "broaden_discovery",
    }


def test_query_web_evidence_store_empty_implicit_scope_is_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        message_id="msg-empty",
        query="Hormuz shipping insurance logistics",
    )

    assert queried["status"] in {"ok", "not_found"}
    assert queried["searched_artifact_count"] == 0
    assert queried["evidence_strength"] == "weak"
    assert queried["suggested_next_action"] == "fetch_more"


def test_query_web_evidence_store_explains_search_source_keys_are_not_stored_artifacts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        message_id="msg-empty",
        query="sleep onset latency",
        artifact_ids=["web:example-one", "web:example-two"],
    )

    assert queried["status"] == "not_found"
    assert queried["suggested_next_action"] == "fetch_store_then_query"
    assert "call fetch_url(url, mode=\"store\") first" in queried["message"]


@pytest.mark.asyncio
async def test_search_web_marks_results_as_excerpts(monkeypatch):
    class _Result:
        def __init__(self, title, link, snippet):
            self.title = title
            self.link = link
            self.snippet = snippet

    def _fake_search_web(_request, _engine, _query, _user):
        return [_Result("Example", "https://example.org/page", "snippet text")]

    monkeypatch.setattr(builtin_tools, "_search_web", _fake_search_web)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    WEB_SEARCH_ENGINE="test-engine",
                    WEB_SEARCH_RESULT_COUNT=None,
                )
            )
        )
    )

    payload = await builtin_tools.search_web(
        "sleep onset latency",
        __request__=request,
        __user__=None,
    )
    parsed = json.loads(payload)
    assert parsed[0]["snippet_is_excerpt"] is True
    assert parsed[0]["full_text_requires_fetch"] is True
    assert parsed[0]["query_web_evidence_ready"] is False


@pytest.mark.asyncio
async def test_search_web_collapses_same_article_mirrors(monkeypatch):
    class _Result:
        def __init__(self, title, link, snippet):
            self.title = title
            self.link = link
            self.snippet = snippet

    def _fake_search_web(_request, _engine, _query, _user):
        return [
            _Result(
                "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes: a systematic review and meta-analysis of randomized controlled crossover trials",
                "https://www.frontiersin.org/journals/neurology/articles/10.3389/fneur.2025.1699303/full",
                "Systematic review and meta-analysis of adults with sleep onset latency outcomes.",
            ),
            _Result(
                "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes: a systematic review and meta-analysis of randomized controlled crossover trials",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
                "Mirror page for the same systematic review and meta-analysis.",
            ),
            _Result(
                "Effect of evening blue light blocking glasses on subjective and objective sleep in healthy adults",
                "https://example.org/independent-rct",
                "Randomized controlled trial in healthy adults.",
            ),
        ]

    monkeypatch.setattr(builtin_tools, "_search_web", _fake_search_web)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    WEB_SEARCH_ENGINE="test-engine",
                    WEB_SEARCH_RESULT_COUNT=None,
                )
            )
        )
    )

    payload = await builtin_tools.search_web(
        "blue light blocking glasses sleep latency adults",
        __request__=request,
        __user__=None,
    )
    parsed = json.loads(payload)
    assert len(parsed) == 2
    assert parsed[0]["mirror_family_collapsed"] is True
    assert parsed[0]["collapsed_mirror_count"] == 2
    assert len(parsed[0]["mirror_urls"]) == 2
    assert parsed[0]["independence_hint"] == "same_article_mirror_collapsed"


def test_query_web_evidence_store_large_artifact_returns_multiple_relevant_chunks(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Large Artifact Test",
    )

    filler_a = "alpha filler " * 450
    filler_b = "beta filler " * 450
    content = (
        f"{filler_a}\n\n"
        "BOXED WARNING: tirzepatide causes thyroid C-cell tumors in rats.\n\n"
        f"{filler_b}\n\n"
        "The recommended starting dosage is 2.5 mg injected subcutaneously once weekly.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-large",
        message_id="msg-large",
        url="https://example.org/large",
        title="Large Example",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-large",
        message_id="msg-large",
        query="thyroid tumors recommended starting dosage",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert len(queried["snippets"]) >= 2
    assert all(snippet["artifact_id"] == stored["artifact_id"] for snippet in queried["snippets"])
    assert any("thyroid c-cell tumors" in snippet["text"].lower() for snippet in queried["snippets"])
    assert any("recommended starting dosage is 2.5 mg" in snippet["text"].lower() for snippet in queried["snippets"])
    assert any(snippet.get("chunked") for snippet in queried["snippets"])


def test_query_web_evidence_store_single_artifact_compaction_surfaces_result_snippets(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Result Intent Test",
    )

    filler_a = ("blue light blocking glasses sleep latency meta analysis filler " * 180).strip()
    filler_b = ("methods crossover trial actigraphy outcome filler " * 220).strip()
    content = (
        "Abstract\n"
        "Blue-light blocking glasses are proposed to improve sleep onset latency, "
        "but evidence is inconsistent in adults.\n\n"
        f"{filler_a}\n\n"
        "Materials and methods\n"
        "This systematic review and meta-analysis evaluated randomized crossover trials.\n\n"
        f"{filler_b}\n\n"
        "Results\n"
        "All three studies provided data for sleep onset latency. "
        "The pooled mean difference was -4.86 minutes "
        "(95% confidence interval -20.23 to 10.52), not statistically significant.\n\n"
        "Conclusion\n"
        "Current evidence does not support a significant effect.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-result-intent",
        message_id="msg-result-intent",
        url="https://example.org/bbg-meta-analysis",
        title=(
            "Efficacy of blue-light blocking glasses on sleep outcomes: "
            "a systematic review and meta-analysis"
        ),
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-result-intent",
        message_id="msg-result-intent",
        query=(
            "sleep onset latency SOL blue-light blocking glasses "
            "effect size results meta-analysis"
        ),
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        window_chars=320,
    )

    assert queried["status"] == "ok"
    assert queried["query_compaction_applied"] is True
    assert queried["normalized_query"] != queried["query"]
    assert queried["expanded_context_count"] >= 1
    assert queried["truncation_trust_hits"] >= 1
    top_three = queried["snippets"][:3]
    assert any(
        "pooled mean difference" in snippet["text"].lower()
        or "95% confidence interval" in snippet["text"].lower()
        or "not statistically significant" in snippet["text"].lower()
        for snippet in top_three
    )
    expanded = next(
        snippet for snippet in top_three if snippet.get("expanded_context_applied")
    )
    assert expanded["snippet_truncated"] is True
    assert int(expanded["effective_window_chars"]) >= 2500
    assert any(snippet.get("result_clause_complete") for snippet in top_three)
    assert any(snippet.get("truncation_trust_hint") for snippet in top_three)


def test_expand_context_window_uses_large_hit_centered_local_section():
    content = ("abcdefghij " * 2000).strip()
    start = 9000
    end = 9200
    anchor = 9100

    new_start, new_end, expanded = web_store._expand_context_window(
        content,
        start=start,
        end=end,
        anchor_index=anchor,
    )

    assert new_start <= anchor - 2500
    assert new_end >= anchor + 3100
    assert len(expanded) >= 5500


def test_query_web_evidence_store_segmented_concept_alignment_prefers_exact_outcome(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Concept Alignment Test",
    )

    content = (
        "# Outcomes\n"
        "Sleep onset latency (SOL), sleep efficiency (SE), total sleep time (TST), and "
        "wake after sleep onset (WASO) were assessed.\n\n"
        "# Results\n"
        "No significant effects were found for SE (MD = -0.61; 95% CI -7.58 to 6.35; p = 0.86) "
        "or WASO (MD = -1.47; 95% CI -14.94 to 11.99; p = 0.83).\n"
        "For sleep onset latency (SOL), the pooled mean difference was -4.86 minutes "
        "(95% CI -20.23 to 10.52; p = 0.54).\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-concept",
        message_id="msg-concept",
        url="https://example.org/review",
        title="Systematic review of evening light interventions",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-concept",
        message_id="msg-concept",
        query="sleep onset latency",
        artifact_ids=[stored["artifact_id"]],
        top_k=6,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
        concept_alignment_enabled=True,
    )

    assert queried["status"] == "ok"
    assert queried["concept_alignment_enabled"] is True
    assert queried["concept_alignment_serving_path"] == "concept"
    assert queried["suggested_next_action"] == "answer_with_current_evidence"
    top = queried["snippets"][0]
    assert "-4.86" in top["text"]
    assert top["alignment_strength"] in {"exact", "strong"}
    assert queried["concept_aligned_trust_hits"] >= 1
    assert any(
        "-0.61" in snippet["text"] and not snippet.get("truncation_trust_hint")
        for snippet in queried["snippets"]
    )


def test_query_web_evidence_store_segmented_concept_alignment_uses_table_caption_linkage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Table Alignment Test",
    )

    content = (
        "# Synthesized findings\n"
        "Table 2. Sleep onset latency (SOL) pooled outcome\n"
        "Metric | MD | 95% interval | p\n"
        "Pooled result | -4.86 | -20.23 to 10.52 | 0.54\n"
        "Comparator row | -1.47 | -14.94 to 11.99 | 0.83\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-table",
        message_id="msg-table",
        url="https://example.org/table",
        title="Outcome Table",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-table",
        message_id="msg-table",
        query="sleep onset latency",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
        concept_alignment_enabled=True,
    )

    assert queried["status"] == "ok"
    top = queried["snippets"][0]
    assert top["alignment_strength"] in {"strong", "exact"}
    assert top["alignment_evidence"] in {"table_caption_row", "adjacent_sentence", "same_sentence"}
    assert queried["suggested_next_action"] == "answer_with_current_evidence"


def test_query_web_evidence_store_segmented_shadow_diff_runs_when_concept_path_is_shadow(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Shadow Comparator Test",
    )

    content = (
        "# Outcomes\n"
        "Sleep onset latency (SOL), sleep efficiency (SE), and wake after sleep onset (WASO) were assessed.\n\n"
        "# Results\n"
        "No significant effects were found for SE (MD = -0.61; 95% CI -7.58 to 6.35; p = 0.86) "
        "or WASO (MD = -1.47; 95% CI -14.94 to 11.99; p = 0.83).\n"
        "For sleep onset latency (SOL), the pooled mean difference was -4.86 minutes "
        "(95% CI -20.23 to 10.52; p = 0.54).\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-shadow",
        message_id="msg-shadow",
        url="https://example.org/shadow",
        title="Shadow Example",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-shadow",
        message_id="msg-shadow",
        query="sleep onset latency",
        artifact_ids=[stored["artifact_id"]],
        top_k=6,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
        concept_alignment_enabled=False,
    )

    assert queried["status"] == "ok"
    assert queried["concept_alignment_serving_path"] == "legacy"
    assert queried["concept_alignment_shadow"]["ran"] is True
    assert queried["concept_alignment_shadow"]["shadow_top_hit"][0] == stored["artifact_id"]


def test_query_web_evidence_store_shadow_conflict_downgrades_agent_guidance(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Shadow Downgrade Test",
    )

    content = (
        "# Results\n"
        "No significant effects were found for SE (MD = -0.61; 95% CI -7.58 to 6.35; p = 0.86) "
        "or WASO (MD = -1.47; 95% CI -14.94 to 11.99; p = 0.83).\n"
        "Conclusion: BBGs may provide small improvements in sleep, but current evidence from RCTs "
        "does not support significant effects.\n\n"
        "# Introduction\n"
        "Blue light can delay sleep onset latency in theory, and sleep onset latency (SOL) is an "
        "important outcome for evening interventions.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-shadow-downgrade",
        message_id="msg-shadow-downgrade",
        url="https://example.org/shadow-downgrade",
        title="Shadow Downgrade Example",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-shadow-downgrade",
        message_id="msg-shadow-downgrade",
        query="sleep onset latency",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
        concept_alignment_enabled=False,
    )

    assert queried["status"] == "ok"
    assert queried["concept_alignment_shadow"]["ran"] is True
    assert queried["exact_target_match_found"] is False
    assert queried["agent_guidance"] == "refine_within_same_source"
    assert queried["suggested_next_action"] == "refine_within_same_source"
    assert queried["truncation_trust_hits"] == 0
    assert queried["serving_confidence_downgraded"] is True
    assert "shadow_action_disagreement" in queried["agent_guidance_reason_codes"]
    assert all(not snippet.get("truncation_trust_hint") for snippet in queried["snippets"])


def test_query_web_evidence_store_segmented_mode_uses_focus_retrieval_for_large_document(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Segmented Evidence Test",
    )

    filler_a = "alpha filler " * 520
    filler_b = "beta filler " * 520
    content = (
        "# BOXED WARNING\n"
        "BOXED WARNING: tirzepatide causes thyroid C-cell tumors in rats.\n\n"
        f"{filler_a}\n\n"
        "# CONTRAINDICATIONS\n"
        "Mounjaro is contraindicated in patients with a personal or family history of medullary thyroid carcinoma.\n\n"
        f"{filler_b}\n\n"
        "# DOSAGE AND ADMINISTRATION\n"
        "The recommended starting dosage is 2.5 mg injected subcutaneously once weekly.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-segmented",
        message_id="msg-segmented",
        url="https://example.org/mounjaro",
        title="Mounjaro Label",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-segmented",
        message_id="msg-segmented",
        query="According to the label, what is the boxed warning, and what is the starting dose?",
        artifact_ids=[stored["artifact_id"]],
        top_k=1,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
    )

    assert queried["status"] == "ok"
    assert queried["retrieval_mode_effective"] == web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED
    assert queried["structured_index_used"] is True
    assert queried["focus_retrieval_used"] is True
    assert queried["coverage_after_merge"] >= queried["coverage_before_merge"]
    assert any("boxed warning" in snippet["text"].lower() for snippet in queried["snippets"])
    assert any("recommended starting dosage is 2.5 mg" in snippet["text"].lower() for snippet in queried["snippets"])


def test_query_web_evidence_store_segmented_mode_falls_back_to_chunk_for_unstructured_large_document(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Chunk Fallback Test",
    )

    filler_a = "loose narrative filler " * 600
    filler_b = "more loose narrative filler " * 600
    content = (
        f"{filler_a}\n\n"
        "tirzepatide causes thyroid c-cell tumors in rats.\n\n"
        f"{filler_b}\n\n"
        "The recommended starting dosage is 2.5 mg injected subcutaneously once weekly.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-fallback",
        message_id="msg-fallback",
        url="https://example.org/fallback",
        title="Fallback Example",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-fallback",
        message_id="msg-fallback",
        query="thyroid tumors recommended starting dosage",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
    )

    assert queried["status"] == "ok"
    assert queried["retrieval_mode_effective"] == web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED
    assert queried["structured_index_used"] is False
    assert queried["fallback_chunk_mode"] is True
    assert any(snippet.get("chunked") for snippet in queried["snippets"])


def test_segment_dedupe_preserves_focus_coverage_metadata():
    overlapping = [
        {
            "artifact_id": "wp_focus",
            "segment_id": "seg_warning",
            "focus_clause": "boxed warning",
            "start": 0,
            "end": 180,
            "score": 0.61,
            "text": "WARNING: RISK OF THYROID C-CELL TUMORS",
            "path": "",
        },
        {
            "artifact_id": "wp_focus",
            "segment_id": "seg_contra",
            "focus_clause": "who should not use Mounjaro",
            "start": 120,
            "end": 280,
            "score": 0.74,
            "text": "Mounjaro is contraindicated in patients with medullary thyroid carcinoma.",
            "path": "",
        },
        {
            "artifact_id": "wp_focus",
            "segment_id": "seg_dose",
            "focus_clause": "starting dose and dose escalation schedule",
            "start": 500,
            "end": 700,
            "score": 0.68,
            "text": "The recommended starting dosage is 2.5 mg once weekly.",
            "path": "",
        },
    ]

    deduped, _meta = web_store._dedupe_segment_snippets(overlapping)
    focus_targets = {
        "boxed warning": {"seg_warning"},
        "who should not use Mounjaro": {"seg_contra"},
        "starting dose and dose escalation schedule": {"seg_dose"},
    }
    selected = web_store._select_final_segment_snippets(
        deduped,
        focus_targets=focus_targets,
        limit=3,
    )

    assert len(deduped) == 2
    assert any(set(snippet.get("covered_segment_ids") or []) >= {"seg_warning", "seg_contra"} for snippet in deduped)
    assert web_store._count_focus_coverage(deduped, focus_targets=focus_targets) == 3
    assert web_store._count_focus_coverage(selected, focus_targets=focus_targets) == 3


@pytest.mark.asyncio
async def test_fetch_url_store_mode_returns_pointer_metadata(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "A" * 1024,
            None,
            {"status": "ok", "resource_kind": "html", "content_source": "primary_loader"},
        ),
    )
    monkeypatch.setattr(
        builtin_tools,
        "store_web_page",
        lambda **kwargs: {
            "status": "stored",
            "artifact_id": "wp_abc",
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "url": kwargs["url"],
            "domain": "example.org",
            "title": kwargs.get("title", ""),
            "path": "/tmp/fake.txt",
            "fetched_at": 1,
            "content_chars": 1024,
            "sha256": "deadbeef",
            "fts_indexed": True,
        },
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "stored"
    assert payload["mode"] == "store"
    assert payload["artifact_id"] == "wp_abc"
    assert payload["content_source"] == "primary_loader"
    assert payload["resource_kind"] == "html"
    assert payload["retrieval_mode_effective"] == "legacy_store_retrieval"
    assert payload["retrieval_mode_source"] == "global_default"
    assert payload["available_to"] == "query_web_evidence"
    assert payload["evidence_query_scope"]["chat_id"] == "chat-1"
    assert payload["evidence_query_scope"]["message_id"] == "msg-1"
    assert "A" * 100 not in output


@pytest.mark.asyncio
async def test_fetch_url_store_mode_requires_chat_id(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "content",
            None,
            {"status": "ok", "resource_kind": "html", "content_source": "primary_loader"},
        ),
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=_request_with_retrieval_mode(),
        __metadata__={},
    )
    payload = json.loads(output)

    assert "error" in payload
    assert payload["mode"] == "store"


@pytest.mark.asyncio
async def test_fetch_url_returns_typed_result_for_unsupported_binary(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "",
            [],
            {
                "status": "unsupported_binary",
                "resource_kind": "xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content_source": "document_extractor",
                "binary_handling": "unsupported_binary",
                "retry_recommended": False,
                "next_action": "choose_another_source",
                "message": "Direct fetch/extraction is not supported for .xlsx resources. Choose another source.",
            },
        ),
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/file.xlsx",
        mode="store",
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "unsupported_binary"
    assert payload["mode"] == "store"
    assert payload["retry_recommended"] is False


@pytest.mark.asyncio
async def test_fetch_url_store_mode_honors_chat_retrieval_override(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "content",
            None,
            {"status": "ok", "resource_kind": "html", "content_source": "primary_loader"},
        ),
    )

    def fake_store_web_page(**kwargs):
        captured.update(kwargs)
        return {
            "status": "stored",
            "artifact_id": "wp_override",
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "url": kwargs["url"],
            "domain": "example.org",
            "title": kwargs.get("title", ""),
            "path": "/tmp/fake.txt",
            "fetched_at": 1,
            "content_chars": 7,
            "sha256": "deadbeef",
            "fts_indexed": True,
        }

    monkeypatch.setattr(builtin_tools, "store_web_page", fake_store_web_page)

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=_request_with_retrieval_mode("legacy_store_retrieval"),
        __metadata__={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {
                "custom_params": {
                    "web_evidence_retrieval_mode": "segmented_confidence_gated"
                }
            },
        },
    )
    payload = json.loads(output)

    assert captured["retrieval_mode"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_effective"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_source"] == "chat_override"


@pytest.mark.asyncio
async def test_query_web_evidence_tool_uses_store(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: captured.update(kwargs) or {
            "status": "ok",
            "query": kwargs["query"],
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 1,
            "searched_artifact_ids": ["wp_1"],
            "searched_domains": ["example.org"],
            "missing_artifact_ids": [],
            "evidence_strength": "adequate",
            "suggested_next_action": "answer_with_current_evidence",
            "snippets": [
                {
                    "artifact_id": "wp_1",
                    "url": "https://example.org/page",
                    "domain": "example.org",
                    "title": "Example",
                    "start": 0,
                    "end": 42,
                    "score": 0.9,
                    "text": "evidence window",
                }
            ],
            "narrow_count": 1,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": True,
            "retrieval_mode_effective": kwargs.get("retrieval_mode"),
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "ok"
    assert payload["chat_id"] == "chat-1"
    assert payload["message_id"] == "msg-1"
    assert payload["scope_mode"] == "implicit_current_message"
    assert len(payload["snippets"]) == 1
    assert captured["retrieval_mode"] == "legacy_store_retrieval"
    assert captured["concept_alignment_enabled"] is False
    assert payload["retrieval_mode_source"] == "global_default"


@pytest.mark.asyncio
async def test_query_web_evidence_tool_honors_chat_override(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: captured.update(kwargs) or {
            "status": "ok",
            "query": kwargs["query"],
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 0,
            "searched_artifact_ids": [],
            "searched_domains": [],
            "missing_artifact_ids": [],
            "evidence_strength": "weak",
            "suggested_next_action": "fetch_more",
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": True,
            "retrieval_mode_effective": kwargs.get("retrieval_mode"),
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=_request_with_retrieval_mode(),
        __metadata__={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"custom_params": {"web_evidence_retrieval_mode": "segmented_confidence_gated"}},
        },
    )
    payload = json.loads(output)

    assert captured["retrieval_mode"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_effective"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_source"] == "chat_override"


@pytest.mark.asyncio
async def test_query_web_evidence_tool_passes_concept_alignment_flag(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: captured.update(kwargs) or {
            "status": "ok",
            "query": kwargs["query"],
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 1,
            "searched_artifact_ids": ["wp_1"],
            "searched_domains": ["example.org"],
            "missing_artifact_ids": [],
            "evidence_strength": "adequate",
            "suggested_next_action": "answer_with_current_evidence",
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": True,
            "retrieval_mode_effective": kwargs.get("retrieval_mode"),
            "concept_alignment_enabled": kwargs.get("concept_alignment_enabled"),
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="sleep onset latency",
        __request__=_request_with_retrieval_mode(
            "segmented_confidence_gated",
            concept_alignment_enabled=True,
        ),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert captured["concept_alignment_enabled"] is True
    assert payload["concept_alignment_enabled"] is True


@pytest.mark.asyncio
async def test_query_web_evidence_tool_adds_local_section_notice(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: {
            "status": "ok",
            "query": kwargs["query"],
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 1,
            "searched_artifact_ids": ["wp_1"],
            "searched_domains": ["example.org"],
            "missing_artifact_ids": [],
            "evidence_strength": "adequate",
            "suggested_next_action": "refine_within_same_source",
            "snippets": [
                {
                    "artifact_id": "wp_1",
                    "url": "https://example.org/page",
                    "domain": "example.org",
                    "title": "Example",
                    "start": 100,
                    "end": 5900,
                    "score": 0.9,
                    "text": "evidence window",
                }
            ],
            "narrow_count": 1,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": True,
            "retrieval_mode_effective": kwargs.get("retrieval_mode"),
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="sleep latency",
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["returned_context_kind"] == "hit_centered_local_section"
    assert "hit-centered local section" in payload["agent_context_notice"]


@pytest.mark.asyncio
async def test_query_web_evidence_tool_requires_chat_id():
    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=SimpleNamespace(),
        __metadata__={},
    )
    payload = json.loads(output)

    assert "error" in payload
    assert payload["snippets"] == []
