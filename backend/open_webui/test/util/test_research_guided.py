from open_webui.utils import research_guided


def test_research_guided_eligibility_accepts_science_turn_and_bypasses_bali():
    eligible, reason = research_guided.is_research_guided_turn_eligible(
        latest_user_text="What evidence exists for whether evening blue light changes circadian phase?",
        working_mode="science",
        research_guided_mode=True,
        web_evidence_enabled=True,
    )
    assert eligible is True
    assert reason is None

    eligible, reason = research_guided.is_research_guided_turn_eligible(
        latest_user_text="Where should I drink in Bali tonight?",
        working_mode="science",
        research_guided_mode=True,
        web_evidence_enabled=True,
    )
    assert eligible is False
    assert reason == "non_science_query"


def test_research_guided_entry_prompt_prefers_search_then_read():
    state = research_guided.build_initial_state(
        "Based on recent human studies and reviews, is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?"
    )

    prompt = research_guided.build_entry_prompt(state)

    assert "read_web_page(url=...)" in prompt
    assert "whole_document_returned=true" in prompt
    assert "next_cursor" in prompt
    assert "fetch_url(url, mode=\"store\") before" not in prompt


def test_research_guided_marks_strict_goals_explicitly():
    strict_goals = research_guided.build_goal_plan(
        "Based on recent human studies and reviews, is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?"
    )

    assert strict_goals[0]["is_strict"] is True


def test_research_guided_page_quality_and_title_helpers():
    resolved_title, source = research_guided.resolve_stored_title(
        explicit_title="pmc.ncbi.nlm.nih.gov",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
        content="# Blue-light blocking glasses systematic review\n\nAbstract text",
        metadata_title_candidates=["Blue-light blocking glasses systematic review"],
    )
    page_quality = research_guided.classify_page_quality(
        url="https://example.org/article",
        resolved_title=resolved_title,
        content="Just a moment... verify you are human before continuing",
        status="stored",
        content_chars=72,
    )

    assert resolved_title == "Blue-light blocking glasses systematic review"
    assert source == "extracted_metadata"
    assert page_quality == research_guided.PAGE_QUALITY_CHALLENGE


def test_research_guided_fetch_and_read_collapses_same_family_and_resolves_goal():
    state = research_guided.build_initial_state(
        "Based on recent human studies and reviews, is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={
            "url": "https://www.frontiersin.org/journals/neurology/articles/10.3389/fneur.2025.1699303/full",
            "mode": "store",
        },
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-frontiers",
            "url": "https://www.frontiersin.org/journals/neurology/articles/10.3389/fneur.2025.1699303/full",
            "domain": "frontiersin.org",
            "title": "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes",
            "content_chars": 40000,
            "identifier_hints": {"doi": "10.3389/fneur.2025.1699303"},
            "counts_as_strong_source": True,
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
            "mode": "store",
        },
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-pmc",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
            "domain": "pmc.ncbi.nlm.nih.gov",
            "title": "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes",
            "content_chars": 42000,
            "identifier_hints": {
                "doi": "10.3389/fneur.2025.1699303",
                "pmcid": "PMC12668929",
            },
            "counts_as_strong_source": True,
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="read_web_page",
        tool_params={"artifact_id": "art-pmc"},
        tool_result={
            "status": "ok",
            "artifact_id": "art-pmc",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
            "domain": "pmc.ncbi.nlm.nih.gov",
            "title": "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes",
            "text": (
                "The pooled effect on sleep onset latency was not statistically significant. "
                "Current randomized-trial evidence does not support significant effects "
                "of blue-light-blocking glasses on actigraphic sleep outcomes in adults."
            ),
            "whole_document_returned": True,
            "done": True,
        },
    )

    stored_artifacts = state["stored_artifacts"]
    assert stored_artifacts[0]["canonical_family_id"] == stored_artifacts[1]["canonical_family_id"]
    assert state["family_alias_count"] >= 1
    assert state["ready_to_answer"] is True
    assert state["candidate_claims"]
    assert state["goals"][0]["status"] in {
        research_guided.GOAL_STATUS_NOT_SUPPORTED,
        research_guided.GOAL_STATUS_MIXED,
    }


def test_research_guided_snapshot_omits_query_loop_fields():
    state = research_guided.build_initial_state(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )
    snapshot = research_guided.build_research_snapshot(state)

    assert "query_rewrite_count" not in snapshot
    assert "low_novelty_query_count" not in snapshot
