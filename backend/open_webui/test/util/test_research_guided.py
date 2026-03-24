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
        tool_params={"query": "strong evidence blue-light blocking glasses sleep latency adults"},
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
    assert goal["status"] != research_guided.GOAL_STATUS_SUPPORTED
    assert state["candidate_claims"]
    assert (
        state["candidate_claims"][0]["label"]
        != research_guided.CLAIM_LABEL_VERIFIED
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
