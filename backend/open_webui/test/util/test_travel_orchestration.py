from open_webui.utils.travel_orchestration import (
    TravelBrief,
    TravelCandidate,
    TravelClassifierResult,
    TravelDateRange,
    TravelFinalPlace,
    TravelRefinementPlan,
    TravelWeatherTarget,
    _cap_candidates,
    _get_previous_assistant_answer,
    _normalize_brief,
    _normalize_refinement_plan,
    _summarize_weather_findings,
    classify_source,
    derive_map_targets,
    should_activate_travel_orchestration,
)


def test_should_activate_travel_orchestration_requires_persona_capability_and_native_fc():
    model = {"info": {"meta": {"capabilities": {"travel_orchestration": True}}}}
    metadata = {"params": {"function_calling": "native"}}
    assert should_activate_travel_orchestration(model, metadata) is True
    assert should_activate_travel_orchestration(model, {"params": {"function_calling": "default"}}) is False
    assert should_activate_travel_orchestration({"info": {"meta": {"capabilities": {}}}}, metadata) is False


def test_should_activate_travel_orchestration_uses_persona_effective_capabilities_first():
    model = {"info": {"meta": {"capabilities": {}}}}
    metadata = {
        "params": {"function_calling": "native"},
        "persona_effective_capabilities": {"travel_orchestration": True},
    }

    assert should_activate_travel_orchestration(model, metadata) is True


def test_should_activate_travel_orchestration_can_fall_back_to_persona_requested_defaults():
    model = {"info": {"meta": {"capabilities": {}}}}
    metadata = {
        "params": {"function_calling": "native"},
        "persona_requested_defaults": {"capabilities": {"travel_orchestration": True}},
    }

    assert should_activate_travel_orchestration(model, metadata) is True


def test_persona_capability_false_overrides_model_capability():
    model = {"info": {"meta": {"capabilities": {"travel_orchestration": True}}}}
    metadata = {
        "params": {"function_calling": "native"},
        "persona_effective_capabilities": {"travel_orchestration": False},
    }

    assert should_activate_travel_orchestration(model, metadata) is False


def test_brief_and_classifier_confidence_are_separate_fields():
    classifier = TravelClassifierResult(
        classification="broad_trip",
        orchestration_confidence=0.82,
        reasons=["multi-day itinerary ask"],
    )
    brief = TravelBrief(
        brief_confidence=0.61,
        destinations=["Bari", "Lecce"],
        weather_targets=[
            TravelWeatherTarget(
                place_name="Bari",
                start_date="2026-04-01",
                end_date="2026-04-03",
            )
        ],
    )

    assert classifier.orchestration_confidence == 0.82
    assert brief.brief_confidence == 0.61


def test_normalize_brief_caps_assumed_defaults_and_allowed_buckets():
    brief = TravelBrief(
        brief_confidence=0.7,
        assumed_defaults=[
            "Use the first afternoon lightly because arrival sounds constrained.",
            "Prefer walkable evenings.",
            "Prefer walkable evenings.",
            "Keep one weather-flex slot.",
            "Avoid overpacking day trips.",
            "This should be trimmed away.",
        ],
        research_buckets=["food_drink", "nightlife_events", "not_allowed", "food_drink"],
    )

    normalized = _normalize_brief(brief)

    assert len(normalized.assumed_defaults) == 4
    assert normalized.research_buckets == ["food_drink", "nightlife_events"]


def test_classify_source_uses_deterministic_precedence_for_hybrids():
    source_class, matched_traits = classify_source(
        url="https://www.eventbrite.com/e/local-guide-night-market",
        title="Local Guide: Night Market Festival",
        snippet="Official calendar for tickets and city guide notes",
    )

    assert source_class == "official_events"
    assert matched_traits


def test_local_cc_tld_can_beat_generic_travel_list_when_not_directory():
    source_class, matched_traits = classify_source(
        url="https://bari.buzz.it/best-things-to-do",
        title="Best Things To Do In Bari",
        snippet="A local guide from Bari",
    )

    assert source_class == "local_editorial"
    assert matched_traits


def test_candidate_caps_dedupe_and_limit_to_seven():
    candidates = [
        TravelCandidate(
            place_name=f"Place {idx}",
            city="Bari",
            category="bar",
            why_it_matters="test",
            confidence=1 - (idx * 0.05),
            evidence_notes=["note 1", "note 2", "note 3", "note 4"],
            source_class="local_editorial",
        )
        for idx in range(9)
    ]

    capped = _cap_candidates(candidates)

    assert len(capped) == 7
    assert all(len(candidate.evidence_notes) == 3 for candidate in capped)


def test_map_targets_are_derived_projection_of_final_places():
    final_places = [
        TravelFinalPlace(
            place_name="Enoteca Pinchiorri",
            city="Florence",
            category="wine_bar",
            why_it_matters="iconic splurge",
            source_url="https://example.com/pinchiorri",
            source_snippet="Historic fine-dining wine experience",
            needs_map_resolution=True,
        ),
        TravelFinalPlace(
            place_name="Enoteca Pinchiorri",
            city="Florence",
            category="wine_bar",
            why_it_matters="duplicate mention",
            needs_map_resolution=True,
        ),
        TravelFinalPlace(
            place_name="Mercato Centrale",
            city="Florence",
            category="food_hall",
            why_it_matters="casual lunch",
            needs_map_resolution=False,
        ),
    ]

    targets = derive_map_targets(final_places)

    assert len(targets) == 1
    assert targets[0].place_name == "Enoteca Pinchiorri"
    assert targets[0].city == "Florence"


def test_weather_summary_keeps_mild_conditions_out_of_hard_constraints():
    brief = TravelBrief(
        brief_confidence=0.8,
        interests=["street photography", "walking"],
        explicit_user_asks=["day-by-day itinerary"],
    )
    forecast = {
        "requested_location": {"place_name": "Bari"},
        "forecast_days": [
            {
                "date": "2026-04-01",
                "weather_code": 2,
                "weather_summary": "Partly cloudy",
                "precipitation_probability_max": 20,
                "wind_gusts_10m_max": 15,
                "temperature_2m_max": 22,
            },
            {
                "date": "2026-04-02",
                "weather_code": 95,
                "weather_summary": "Thunderstorm",
                "precipitation_probability_max": 90,
                "wind_gusts_10m_max": 70,
                "temperature_2m_max": 19,
            },
        ],
    }

    findings = _summarize_weather_findings(forecast, brief)

    assert findings[0].finding_type != "hard_constraint"
    assert findings[1].finding_type == "hard_constraint"


def test_dates_within_brief_can_be_normalized_without_affecting_confidence():
    brief = TravelBrief(
        brief_confidence=0.55,
        destinations=["Lecce"],
        date_range=TravelDateRange(start_date="2026-04-01", end_date="2026-04-04", is_exact=True),
        research_buckets=[],
    )

    normalized = _normalize_brief(brief)

    assert normalized.brief_confidence == 0.55
    assert normalized.research_buckets == ["cultural_sites", "food_drink"]


def test_normalize_refinement_plan_filters_unknown_buckets_and_caps_metadata():
    plan = TravelRefinementPlan(
        is_refinement=True,
        preserve_existing_plan=True,
        requested_change_summary=(
            "Update the nightlife picks while keeping the rest of the itinerary intact. "
            "This should stay concise after normalization."
        ),
        target_buckets=["nightlife_events", "food_drink", "not_real", "food_drink"],
        reasons=["keep the current plan", "refresh nightlife only", "keep the current plan"],
    )

    normalized = _normalize_refinement_plan(plan)

    assert normalized.target_buckets == ["nightlife_events", "food_drink"]
    assert normalized.reasons == ["keep the current plan", "refresh nightlife only"]
    assert normalized.requested_change_summary is not None


def test_previous_assistant_answer_ignores_latest_user_and_returns_prior_answer():
    messages = [
        {"role": "user", "content": "Plan my trip."},
        {"role": "assistant", "content": "Original travel plan"},
        {"role": "user", "content": "Change only nightlife."},
    ]

    assert _get_previous_assistant_answer(messages) == "Original travel plan"
