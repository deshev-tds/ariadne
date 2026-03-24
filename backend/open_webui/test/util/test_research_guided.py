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


def test_research_guided_builds_bounded_goal_plan():
    goals = research_guided.build_goal_plan(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )

    assert 1 <= len(goals) <= research_guided.RESEARCH_GUIDED_MAX_GOALS
    assert all(goal["question"].endswith("?") for goal in goals)
    assert all(goal["disconfirmation_targets"] for goal in goals)


def test_research_guided_register_tool_event_collapses_duplicate_fetches_and_finalizes_claims():
    state = research_guided.build_initial_state(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="search_web",
        tool_params={"query": "evening blue light circadian phase study"},
        tool_result=[{"link": "https://example.org/paper"}],
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="web_research_strong",
        tool_params={"query": "evening blue light circadian phase trial"},
        tool_result={
            "citation_items": [
                {
                    "title": "Randomized human trial on evening light and circadian phase",
                    "link": "https://doi.org/10.1000/example-doi",
                    "domain": "doi.org",
                }
            ]
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://doi.org/10.1000/example-doi", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-1",
            "url": "https://doi.org/10.1000/example-doi",
            "domain": "doi.org",
            "title": "Randomized human trial on evening light and circadian phase",
            "content_chars": 1200,
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://doi.org/10.1000/example-doi", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-1b",
            "url": "https://doi.org/10.1000/example-doi",
            "domain": "doi.org",
            "title": "Randomized human trial on evening light and circadian phase",
            "content_chars": 1200,
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://doi.org/10.1000/example-doi-2", "mode": "content"},
        tool_result={
            "status": "stored",
            "mode": "content",
            "artifact_id": "art-2",
            "url": "https://doi.org/10.1000/example-doi-2",
            "domain": "doi.org",
            "title": "Independent adult trial on evening light and circadian phase",
            "content_chars": 2200,
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={"query": "evening blue light circadian phase trial"},
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["doi.org"],
            "snippets": [
                {
                    "artifact_id": "art-1",
                    "domain": "doi.org",
                    "text": "Randomized human trial data suggest evening blue light can shift circadian phase markers.",
                }
            ],
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={"query": "evening blue light circadian phase trial broader corroboration"},
        tool_result={
            "status": "ok",
            "searched_artifact_count": 2,
            "searched_domains": ["doi.org", "example.org"],
            "snippets": [
                {
                    "artifact_id": "art-2",
                    "domain": "doi.org",
                    "text": "An independent adult trial found evening short-wavelength light shifted circadian phase markers.",
                }
            ],
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="search_web",
        tool_params={"query": "counterevidence no meaningful circadian phase shift evening blue light"},
        tool_result=[{"link": "https://example.org/null"}],
    )

    assert state["duplicate_fetch_count"] == 1
    assert state["candidate_claims"]
    assert all("basis_summary" in claim for claim in state["candidate_claims"])


def test_research_guided_strict_goal_rejects_single_snippet_meta_source():
    state = research_guided.build_initial_state(
        "Based on recent human studies and reviews, is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?"
    )
    goal = state["goals"][0]

    assert goal["coverage_requirement"] == "strict"

    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://example.org/meta", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-meta",
            "url": "https://example.org/meta",
            "domain": "example.org",
            "title": "Systematic review and meta-analysis of blue-light blocking glasses",
            "content_chars": 1800,
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={
            "query": "evening blue light blocking glasses improve sleep latency adults strong evidence",
            "artifact_ids": ["art-meta"],
        },
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["example.org"],
            "snippets": [
                {
                    "artifact_id": "art-meta",
                    "domain": "example.org",
                    "text": "The pooled effect on sleep onset latency was directionally favorable but not statistically significant.",
                }
            ],
        },
    )

    goal = state["goals"][0]
    assert goal["status"] == research_guided.GOAL_STATUS_OPEN
    assert goal["coverage_pending_reason"]
    assert state["ready_to_answer"] is False
    assert state["candidate_claims"] == []


def test_research_guided_strict_goal_conflict_before_broader_fallback_stays_open():
    state = research_guided.build_initial_state(
        "Based on recent human studies and reviews, is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-meta",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
            "domain": "pmc.ncbi.nlm.nih.gov",
            "title": "Systematic review and meta-analysis of blue-light blocking glasses",
            "content_chars": 1800,
            "identifier_hints": {
                "pmcid": "PMC12668929",
                "doi": "10.3389/fneur.2025.1699303",
            },
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={"query": "blue-light blocking glasses sleep latency adults meta analysis significance"},
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["pmc.ncbi.nlm.nih.gov"],
            "snippets": [
                {
                    "artifact_id": "art-meta",
                    "domain": "pmc.ncbi.nlm.nih.gov",
                    "text": "The pooled effect on sleep onset latency was directionally favorable.",
                }
            ],
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={
            "query": "blue-light blocking glasses sleep latency adults meta analysis not statistically significant"
        },
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["pmc.ncbi.nlm.nih.gov"],
            "snippets": [
                {
                    "artifact_id": "art-meta",
                    "domain": "pmc.ncbi.nlm.nih.gov",
                    "text": "The pooled effect on sleep onset latency was not statistically significant.",
                }
            ],
        },
    )

    goal = state["goals"][0]
    proposition = state["working_propositions"][0]
    assert goal["status"] == research_guided.GOAL_STATUS_OPEN
    assert "broader fallback" in goal["coverage_pending_reason"]
    assert proposition["state"] == "leaning_mixed"
    assert state["same_family_conflict_count"] == 1
    assert state["ready_to_answer"] is False
    assert state["candidate_claims"] == []


def test_research_guided_family_aliases_collapse_article_mirrors():
    state = research_guided.build_initial_state(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-pmc",
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
            "domain": "pmc.ncbi.nlm.nih.gov",
            "title": "Blue-light blocking glasses systematic review",
            "content_chars": 1800,
            "identifier_hints": {
                "pmcid": "PMC12668929",
                "doi": "10.3389/fneur.2025.1699303",
            },
        },
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
            "title": "Blue-light blocking glasses systematic review",
            "content_chars": 1800,
            "identifier_hints": {
                "doi": "10.3389/fneur.2025.1699303/full",
            },
        },
    )

    stored = {item["artifact_id"]: item for item in state["stored_artifacts"]}
    assert stored["art-pmc"]["canonical_family_id"] == "doi:10.3389/fneur.2025.1699303"
    assert stored["art-frontiers"]["canonical_family_id"] == "doi:10.3389/fneur.2025.1699303"
    assert state["family_alias_count"] >= 1


def test_research_guided_same_family_conflict_does_not_terminalize_mixed():
    state = research_guided.build_initial_state(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="search_web",
        tool_params={"query": "evening blue light circadian phase trial adults"},
        tool_result=[{"link": "https://example.org/paper"}],
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="search_web",
        tool_params={"query": "evening blue light circadian phase not statistically significant adults"},
        tool_result=[{"link": "https://example.org/null"}],
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://doi.org/10.1000/example-doi", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-1",
            "url": "https://doi.org/10.1000/example-doi",
            "domain": "doi.org",
            "title": "Randomized human trial on evening blue light and circadian phase",
            "content_chars": 2200,
            "identifier_hints": {"doi": "10.1000/example-doi"},
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={"query": "evening blue light circadian phase trial adults"},
        tool_result={
            "status": "ok",
            "searched_artifact_count": 2,
            "searched_domains": ["doi.org", "pmc.ncbi.nlm.nih.gov"],
            "snippets": [
                {
                    "artifact_id": "art-1",
                    "domain": "doi.org",
                    "text": "Randomized human trial data suggest evening blue light shifted circadian phase markers.",
                }
            ],
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={"query": "evening blue light circadian phase not statistically significant adults"},
        tool_result={
            "status": "ok",
            "searched_artifact_count": 2,
            "searched_domains": ["doi.org", "pmc.ncbi.nlm.nih.gov"],
            "snippets": [
                {
                    "artifact_id": "art-1",
                    "domain": "doi.org",
                    "text": "The same trial reported no statistically significant shift in the pooled circadian phase outcome.",
                }
            ],
        },
    )

    goal = state["goals"][0]
    assert goal["status"] == research_guided.GOAL_STATUS_INSUFFICIENT
    assert goal["resolution_basis"] == "budgeted_high_value_search_exhausted"
    assert state["same_family_conflict_count"] == 1


def test_research_guided_low_novelty_queries_trigger_bounded_rewrite_note():
    state = research_guided.build_initial_state(
        "Based on recent human studies and reviews, is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://example.org/meta", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-meta",
            "url": "https://example.org/meta",
            "domain": "example.org",
            "title": "Systematic review and meta-analysis of blue-light blocking glasses",
            "content_chars": 1800,
            "identifier_hints": {"doi": "10.1000/meta-doi"},
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={
            "query": "evening blue light blocking glasses sleep latency adults meta analysis significance",
            "artifact_ids": ["art-meta"],
        },
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["example.org"],
            "snippets": [
                {
                    "artifact_id": "art-meta",
                    "domain": "example.org",
                    "text": "The pooled effect on sleep onset latency was directionally favorable.",
                }
            ],
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={
            "query": "evening blue light blocking glasses sleep latency adults meta analysis conclusion significance",
            "artifact_ids": ["art-meta"],
        },
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["example.org"],
            "snippets": [
                {
                    "artifact_id": "art-meta",
                    "domain": "example.org",
                    "text": "The conclusion remained directionally favorable but limited.",
                }
            ],
        },
    )
    state = research_guided.register_tool_event(
        state,
        tool_name="query_web_evidence",
        tool_params={
            "query": "evening blue light blocking glasses sleep latency adults meta analysis significance conclusion",
            "artifact_ids": ["art-meta"],
        },
        tool_result={
            "status": "ok",
            "searched_artifact_count": 1,
            "searched_domains": ["example.org"],
            "snippets": [
                {
                    "artifact_id": "art-meta",
                    "domain": "example.org",
                    "text": "The same review discussed significance in the conclusion section.",
                }
            ],
        },
    )

    assert state["low_novelty_query_count"] >= 1
    assert state["query_rewrite_count"] == 1
    assert "Rewrite the next query_web_evidence call as a short retrieval phrase." in (
        state["pending_system_note"]
    )


def test_research_guided_fetch_url_counts_as_strong_source_probe():
    state = research_guided.build_initial_state(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )

    state = research_guided.register_tool_event(
        state,
        tool_name="fetch_url",
        tool_params={"url": "https://doi.org/10.1000/example-doi", "mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-1",
            "url": "https://doi.org/10.1000/example-doi",
            "domain": "doi.org",
            "title": "Randomized human trial on evening blue light and circadian phase",
            "content_chars": 1200,
        },
    )

    goal = state["goals"][0]
    assert goal["probe_budget"]["observed"]["strong_source"] == 1


def test_research_guided_completed_turn_finalization_downgrades_supported_goal_without_disconfirmation():
    state = research_guided.build_initial_state(
        "What evidence exists for whether evening blue light changes circadian phase?"
    )
    goal = state["goals"][0]
    goal["status"] = research_guided.GOAL_STATUS_SUPPORTED
    goal["resolution_basis"] = "contract_satisfied"

    evidence_id = "ev-1"
    state["evidence_ledger"] = [
        {
            "evidence_id": evidence_id,
            "goal_ids": [goal["goal_id"]],
            "source_role": "primary_study",
            "source_ref": {
                "title": "Randomized human trial on evening blue light",
                "url": "https://doi.org/10.1000/example-doi",
                "domain": "doi.org",
            },
            "canonical_url": "https://doi.org/10.1000/example-doi",
            "evidence_family_id": "doi:10.1000/example-doi",
            "evidence_class": "systematic_synthesis",
            "stance": "supports",
            "directness": "direct",
            "method_strength": "high",
            "context_fit": "strong",
            "value_bucket": "high",
            "limitations": [],
            "blocked": False,
            "text_preview": "Trial data support an evening circadian phase shift.",
        }
    ]

    finalized = research_guided.finalize_state_for_completed_turn(
        state,
        visible_answer_present=True,
    )

    assert finalized["phase"] == "final_response"
    assert finalized["ready_to_answer"] is True
    assert finalized["goals"][0]["disconfirmation_outcome"] == "not_meaningfully_tested"
    assert finalized["candidate_claims"][0]["label"] == research_guided.CLAIM_LABEL_INFERENCE
