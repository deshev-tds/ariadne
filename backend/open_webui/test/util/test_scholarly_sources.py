from open_webui.utils.scholarly_sources import (
    SCHOLARLY_SOURCE_DEFINITION_MAP,
    build_scholarly_probe_request,
    build_scholarly_source_rows,
    default_scholarly_source_settings,
    merge_scholarly_source_settings,
    normalize_scholarly_source_settings,
)


def test_default_scholarly_source_settings_cover_all_known_sources():
    payload = default_scholarly_source_settings()

    assert sorted(payload["sources"].keys()) == sorted(
        SCHOLARLY_SOURCE_DEFINITION_MAP.keys()
    )
    assert all(source["enabled"] is False for source in payload["sources"].values())


def test_merge_scholarly_source_settings_captures_admin_email_for_contact_sources():
    merged = merge_scholarly_source_settings(
        default_scholarly_source_settings(),
        {
            "crossref": {"enabled": True},
            "pubmed": {"enabled": True, "api_key": "ncbi-key"},
        },
        configured_by_email="deshev.tds@gmail.com",
    )

    assert merged["sources"]["crossref"]["contact_email"] == "deshev.tds@gmail.com"
    assert merged["sources"]["pubmed"]["contact_email"] == "deshev.tds@gmail.com"
    assert merged["sources"]["pubmed"]["api_key"] == "ncbi-key"


def test_build_scholarly_source_rows_reports_planner_coverage():
    rows = build_scholarly_source_rows(
        normalize_scholarly_source_settings(default_scholarly_source_settings()),
        planner_registry={
            "topics": {
                "science_academic": {
                    "primary": [
                        {"domain": "pubmed.ncbi.nlm.nih.gov"},
                        {"domain": "europepmc.org"},
                    ]
                }
            }
        },
    )

    row_map = {row["id"]: row for row in rows}
    assert row_map["pubmed"]["planner_fallback_configured"] is True
    assert row_map["europe-pmc"]["planner_fallback_configured"] is True
    assert row_map["openalex"]["planner_fallback_configured"] is False


def test_pubmed_probe_request_includes_tool_email_and_optional_api_key():
    request_spec = build_scholarly_probe_request(
        "pubmed",
        {"api_key": "ncbi-key", "contact_email": "deshev.tds@gmail.com"},
    )

    assert request_spec["url"].endswith("/esearch.fcgi")
    assert request_spec["params"]["tool"] == "open-webui"
    assert request_spec["params"]["email"] == "deshev.tds@gmail.com"
    assert request_spec["params"]["api_key"] == "ncbi-key"


def test_openalex_probe_request_uses_query_param_api_key():
    request_spec = build_scholarly_probe_request("openalex", {"api_key": "openalex-key"})

    assert request_spec["url"] == "https://api.openalex.org/works"
    assert request_spec["params"]["api_key"] == "openalex-key"
    assert request_spec["params"]["per-page"] == 1


def test_crossref_probe_request_uses_contact_email_without_api_key():
    request_spec = build_scholarly_probe_request(
        "crossref",
        {"contact_email": "deshev.tds@gmail.com"},
    )

    assert request_spec["url"] == "https://api.crossref.org/works"
    assert request_spec["params"]["mailto"] == "deshev.tds@gmail.com"
    assert "api_key" not in request_spec["params"]


def test_doi_probe_request_uses_content_negotiation_header():
    request_spec = build_scholarly_probe_request("doi")

    assert request_spec["url"] == "https://doi.org/10.1126/science.169.3946.635"
    assert (
        request_spec["headers"]["Accept"] == "application/vnd.citationstyles.csl+json"
    )
