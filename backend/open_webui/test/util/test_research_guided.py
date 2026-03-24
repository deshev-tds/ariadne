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
        tool_name="search_web",
        tool_params={"query": "counterevidence no meaningful circadian phase shift evening blue light"},
        tool_result=[{"link": "https://example.org/null"}],
    )

    assert state["duplicate_fetch_count"] == 1
    assert state["candidate_claims"]
    assert all("basis_summary" in claim for claim in state["candidate_claims"])
