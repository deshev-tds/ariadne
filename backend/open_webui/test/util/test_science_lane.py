from open_webui.utils.science_lane import (
    build_science_lane_skill_sets,
    normalize_configured_skill_ids,
    resolve_science_lane_default_skill_ids,
)


def test_normalize_configured_skill_ids_accepts_csv_json_and_sequences():
    assert normalize_configured_skill_ids("alpha, beta ,, gamma") == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert normalize_configured_skill_ids('["alpha", "beta", "alpha"]') == [
        "alpha",
        "beta",
    ]
    assert normalize_configured_skill_ids(["alpha", " ", "beta", "alpha"]) == [
        "alpha",
        "beta",
    ]


def test_resolve_science_lane_default_skill_ids_only_activates_for_science():
    assert resolve_science_lane_default_skill_ids("science", ["alpha", "beta"]) == {
        "alpha",
        "beta",
    }
    assert resolve_science_lane_default_skill_ids("general", ["alpha", "beta"]) == set()


def test_build_science_lane_skill_sets_promotes_lane_defaults_to_explicit_skills():
    lane_ids, explicit_ids, all_ids = build_science_lane_skill_sets(
        working_mode="science",
        configured_skill_ids='["lane-a", "lane-b"]',
        user_skill_ids=["user-a"],
        model_skill_ids=["model-a", "lane-b"],
    )

    assert lane_ids == {"lane-a", "lane-b"}
    assert explicit_ids == {"lane-a", "lane-b", "user-a"}
    assert all_ids == {"lane-a", "lane-b", "user-a", "model-a"}
