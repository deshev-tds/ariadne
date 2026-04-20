from open_webui.utils.scholarly_sources import (
    SCHOLARLY_SOURCE_DEFINITION_MAP,
    assess_scholarly_probe_protocol,
    build_scholarly_probe_request,
    build_scholarly_source_runtime_coverage,
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
    assert row_map["pubmed"]["admin_probe_ready"] is True
    assert row_map["pubmed"]["native_tool_adapter_ready"] is False


def test_runtime_coverage_reflects_probe_ready_but_tool_gap():
    coverage = build_scholarly_source_runtime_coverage(
        "crossref",
        planner_registry={
            "topics": {
                "science_academic": {
                    "primary": [
                        {"domain": "crossref.org"},
                    ]
                }
            }
        },
    )

    assert coverage["admin_probe_ready"] is True
    assert coverage["planner_fallback_configured"] is True
    assert coverage["native_tool_adapter_ready"] is False
    assert coverage["skill_support_status"] == "Guidance only"
    assert "kdense-citation-management" in coverage["seeded_skill_ids"]


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


def test_openalex_protocol_fails_when_required_key_is_missing():
    protocol = assess_scholarly_probe_protocol(
        "openalex",
        {
            "status": True,
            "source_id": "openalex",
            "request": {
                "method": "GET",
                "url": "https://api.openalex.org/works?search=CRISPR&per-page=1",
                "headers": {},
            },
            "response": {
                "status": 401,
                "reason": "Unauthorized",
                "url": "https://api.openalex.org/works?search=CRISPR&per-page=1",
                "history": [],
                "headers": {},
                "body_text": '{"error":"unauthorized"}',
                "body_json": {"error": "unauthorized"},
            },
        },
        {"enabled": True, "api_key": ""},
    )

    assert protocol["status"] == "fail"
    assert any(
        check["id"] == "auth-configured" and check["ok"] is False
        for check in protocol["checks"]
    )
    assert any(
        check["id"] == "upstream-status" and check["ok"] is False
        for check in protocol["checks"]
    )


def test_crossref_protocol_warns_when_probe_passes_but_runtime_tooling_is_partial():
    protocol = assess_scholarly_probe_protocol(
        "crossref",
        {
            "status": True,
            "source_id": "crossref",
            "request": {
                "method": "GET",
                "url": "https://api.crossref.org/works?query.title=CRISPR&rows=1&mailto=deshev.tds%40gmail.com",
                "headers": {},
            },
            "response": {
                "status": 200,
                "reason": "OK",
                "url": "https://api.crossref.org/works?query.title=CRISPR&rows=1&mailto=deshev.tds%40gmail.com",
                "history": [],
                "headers": {},
                "body_text": '{"message":{"items":[]}}',
                "body_json": {"message": {"items": []}},
            },
        },
        {"enabled": True, "contact_email": "deshev.tds@gmail.com"},
        planner_registry={
            "topics": {
                "science_academic": {"primary": [{"domain": "crossref.org"}]}
            }
        },
    )

    assert protocol["status"] == "warn"
    assert protocol["coverage"]["planner_fallback_configured"] is True
    assert protocol["coverage"]["native_tool_adapter_ready"] is False
    assert any(
        check["id"] == "payload-shape" and check["ok"] is True
        for check in protocol["checks"]
    )
    assert any(
        check["id"] == "native-tool-adapter" and check["ok"] is False
        for check in protocol["checks"]
    )
