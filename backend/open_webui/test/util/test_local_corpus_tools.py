import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import open_webui.retrieval.local_corpus as local_corpus
import open_webui.retrieval.local_corpus_reasoning as local_corpus_reasoning
import open_webui.tools.builtin as builtin_tools
import open_webui.utils.tools as tool_utils


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _mini_corpus(root: Path) -> Path:
    _write(
        root / "_serving" / "domains" / "index.md",
        """# Local Corpus Index

- medicine: 2 usable medical books and guidelines
- chemistry: 1 usable chemistry references
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "index.md",
        """# Medicine Local Index

- cardiology: 2 sources; blood pressure, cardiac function, and bedside management.
""",
    )
    _write(
        root / "_serving" / "domains" / "chemistry" / "index.md",
        """# Chemistry Local Index

- organic_chemistry: 1 source; reaction mechanisms and synthesis references.
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "books" / "med-guide.md",
        """# Hypertension management guideline

What this is:
Guideline for diagnosis and management of hypertension.
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "books" / "med-textbook.md",
        """# Cardiac physiology primer

What this is:
Textbook for cardiac physiology background.
""",
    )
    _write(
        root / "_serving" / "domains" / "chemistry" / "books" / "chem-reference.md",
        """# Organic chemistry reaction reference

What this is:
Reference for reaction mechanisms and synthesis planning.
""",
    )

    serving_rows = [
        {
            "book_id": "med-guide",
            "domain": "medicine",
            "primary_discipline": "cardiology",
            "title": "Hypertension management guideline",
            "resource_type": "guideline",
            "evidence_tier": "guideline",
            "authority_or_publisher": "NICE",
            "year": 2024,
            "document_dir": "med-guide-doc",
            "selected_dir": "med-guide-doc/selected",
            "parse_status": "success",
            "quarantine_reason": None,
            "secondary_tags": ["management"],
            "coverage_phrases": ["hypertension management", "blood pressure thresholds"],
            "negative_scope": [],
            "clean_toc": ["Diagnosis", "Management", "Treatment thresholds"],
            "what_this_is": "Guideline for diagnosis and management of hypertension.",
            "table_count": 1,
            "figure_count": 1,
            "review_flags": [],
        },
        {
            "book_id": "med-textbook",
            "domain": "medicine",
            "primary_discipline": "cardiology",
            "title": "Cardiac physiology primer",
            "resource_type": "textbook",
            "evidence_tier": "textbook",
            "authority_or_publisher": "Elsevier",
            "year": 2021,
            "document_dir": "med-textbook-doc",
            "selected_dir": "med-textbook-doc/selected",
            "parse_status": "success",
            "quarantine_reason": None,
            "secondary_tags": ["physiology"],
            "coverage_phrases": ["cardiac physiology", "hemodynamics"],
            "negative_scope": [],
            "clean_toc": ["Physiology", "Cardiac output"],
            "what_this_is": "Textbook for background cardiac physiology.",
            "table_count": 0,
            "figure_count": 0,
            "review_flags": [],
        },
        {
            "book_id": "chem-reference",
            "domain": "chemistry",
            "primary_discipline": "organic_chemistry",
            "title": "Organic chemistry reaction reference",
            "resource_type": "reference",
            "evidence_tier": "reference",
            "authority_or_publisher": "ACS",
            "year": 2020,
            "document_dir": "chem-reference-doc",
            "selected_dir": "chem-reference-doc/selected",
            "parse_status": "success",
            "quarantine_reason": None,
            "secondary_tags": ["mechanism"],
            "coverage_phrases": ["reaction mechanisms", "organic synthesis"],
            "negative_scope": [],
            "clean_toc": ["Reaction mechanisms", "Synthesis planning"],
            "what_this_is": "Reference for organic chemistry reaction mechanisms.",
            "table_count": 0,
            "figure_count": 0,
            "review_flags": [],
        },
    ]
    _write(
        root / "_serving" / "serving-catalog.jsonl",
        "\n".join(json.dumps(row, ensure_ascii=False) for row in serving_rows) + "\n",
    )

    _write(
        root / "med-guide-doc" / "selected" / "retrieval.md",
        """# Document Metadata

## Page 1
Section path: Hypertension

## Hypertension overview

Hypertension diagnosis depends on repeated blood pressure assessment and cardiovascular risk.

## Page 2
Section path: Management
Tables on this page: 1
Figures on this page: 1
- Figure 1: Treatment pathway

## Management recommendations

Start treatment when blood pressure remains above threshold and escalate according to risk.
""",
    )
    _write(
        root / "med-guide-doc" / "selected" / "catalog.json",
        json.dumps(
            {
                "pages": [
                    {"page_no": 1, "heading_path": ["Hypertension"], "table_count": 0},
                    {"page_no": 2, "heading_path": ["Management"], "table_count": 1},
                ]
            }
        ),
    )
    _write(
        root / "med-guide-doc" / "selected" / "figures.json",
        json.dumps(
            [
                {
                    "index": 1,
                    "page_no": 2,
                    "heading_path": ["Management"],
                    "caption": "Treatment pathway",
                    "bbox": {"l": 1, "t": 2, "r": 3, "b": 4},
                }
            ]
        ),
    )
    _write(
        root / "med-guide-doc" / "selected" / "document.json",
        json.dumps(
            {
                "tables": [
                    {
                        "prov": [{"page_no": 2}],
                        "captions": [],
                        "data": {"table_cells": [], "num_rows": 2, "num_cols": 2},
                    }
                ]
            }
        ),
    )
    _write(
        root / "med-guide-doc" / "selected" / "tables" / "table-001.csv",
        "Drug,Threshold\nACE inhibitor,>=140/90\n",
    )
    _write(
        root / "med-textbook-doc" / "selected" / "retrieval.md",
        """# Document Metadata

## Page 4
Section path: Physiology

## Cardiac physiology

Cardiac output depends on stroke volume, afterload, and preload.
""",
    )
    _write(
        root / "med-textbook-doc" / "selected" / "catalog.json",
        json.dumps({"pages": [{"page_no": 4, "heading_path": ["Physiology"], "table_count": 0}]}),
    )
    _write(root / "med-textbook-doc" / "selected" / "figures.json", "[]")
    _write(root / "med-textbook-doc" / "selected" / "document.json", json.dumps({"tables": []}))

    _write(
        root / "chem-reference-doc" / "selected" / "retrieval.md",
        """# Document Metadata

## Page 7
Section path: Reaction mechanisms

## Organic chemistry reaction mechanisms

Reaction mechanisms explain substitution, elimination, and synthesis strategy.
""",
    )
    _write(
        root / "chem-reference-doc" / "selected" / "catalog.json",
        json.dumps(
            {"pages": [{"page_no": 7, "heading_path": ["Reaction mechanisms"], "table_count": 0}]}
        ),
    )
    _write(root / "chem-reference-doc" / "selected" / "figures.json", "[]")
    _write(root / "chem-reference-doc" / "selected" / "document.json", json.dumps({"tables": []}))

    return root


def _cap_corpus(root: Path) -> Path:
    _write(
        root / "_serving" / "domains" / "index.md",
        """# Local Corpus Index

- medicine: 3 usable medical books and guidelines
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "index.md",
        """# Medicine Local Index

- infectious_disease: 2 sources; syndrome-based antimicrobial therapy and clinical infectious syndromes.
- pulmonology: 1 source; hospital-acquired and ventilator-associated pneumonia.
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "books" / "cap-handbook.md",
        """# Antimicrobial therapy handbook

What this is:
Handbook for syndrome-based antimicrobial therapy and adult empiric regimens.
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "books" / "vap-guide.md",
        """# Hospital pneumonia guide

What this is:
Guide for hospital-acquired and ventilator-associated pneumonia.
""",
    )
    _write(
        root / "_serving" / "domains" / "medicine" / "books" / "lung-abscess.md",
        """# Lung abscess reference

What this is:
Reference for lung abscess diagnosis and treatment.
""",
    )

    serving_rows = [
        {
            "book_id": "cap-handbook",
            "domain": "medicine",
            "primary_discipline": "infectious_disease",
            "title": "Antimicrobial therapy handbook",
            "resource_type": "handbook",
            "evidence_tier": "handbook",
            "authority_or_publisher": "Sanford-like",
            "year": 2025,
            "document_dir": "cap-handbook-doc",
            "selected_dir": "cap-handbook-doc/selected",
            "parse_status": "success",
            "quarantine_reason": None,
            "secondary_tags": ["adult regimens", "antimicrobial therapy"],
            "coverage_phrases": [
                "syndrome-based antimicrobial therapy",
                "community-acquired pneumonia adult empiric therapy",
            ],
            "negative_scope": [],
            "clean_toc": [
                "community-acquired pneumonia",
                "hospital-acquired pneumonia",
                "antimicrobial regimens",
            ],
            "what_this_is": "Handbook for adult empiric antimicrobial regimens.",
            "table_count": 1,
            "figure_count": 0,
            "review_flags": [],
        },
        {
            "book_id": "vap-guide",
            "domain": "medicine",
            "primary_discipline": "pulmonology",
            "title": "Hospital pneumonia guide",
            "resource_type": "guideline",
            "evidence_tier": "guideline",
            "authority_or_publisher": "NICE",
            "year": 2024,
            "document_dir": "vap-guide-doc",
            "selected_dir": "vap-guide-doc/selected",
            "parse_status": "success",
            "quarantine_reason": None,
            "secondary_tags": ["management"],
            "coverage_phrases": [
                "hospital-acquired pneumonia",
                "ventilator-associated pneumonia management",
            ],
            "negative_scope": [],
            "clean_toc": ["HAP", "VAP", "empiric treatment"],
            "what_this_is": "Guideline for hospital-acquired and ventilator-associated pneumonia.",
            "table_count": 0,
            "figure_count": 0,
            "review_flags": [],
        },
        {
            "book_id": "lung-abscess",
            "domain": "medicine",
            "primary_discipline": "infectious_disease",
            "title": "Lung abscess reference",
            "resource_type": "reference",
            "evidence_tier": "reference",
            "authority_or_publisher": "Reference Press",
            "year": 2023,
            "document_dir": "lung-abscess-doc",
            "selected_dir": "lung-abscess-doc/selected",
            "parse_status": "success",
            "quarantine_reason": None,
            "secondary_tags": ["respiratory infection"],
            "coverage_phrases": ["lung abscess treatment", "co-amoxiclav"],
            "negative_scope": [],
            "clean_toc": ["Lung abscess", "Treatment"],
            "what_this_is": "Reference for lung abscess treatment.",
            "table_count": 0,
            "figure_count": 0,
            "review_flags": [],
        },
    ]
    _write(
        root / "_serving" / "serving-catalog.jsonl",
        "\n".join(json.dumps(row, ensure_ascii=False) for row in serving_rows) + "\n",
    )

    _write(
        root / "cap-handbook-doc" / "selected" / "retrieval.md",
        """# Document Metadata

## Page 50
Section path: Community-acquired pneumonia
Tables on this page: 1

## Community-acquired pneumonia

Adults with outpatient CAP and no comorbidity can receive amoxicillin or doxycycline.
Adults with comorbidity can receive amoxicillin-clavulanate plus azithromycin or doxycycline.

## Page 51
Section path: Community-acquired pneumonia > Inpatient treatment

## Treatment

Adults admitted to hospital with CAP can receive ceftriaxone plus azithromycin, or ceftriaxone plus doxycycline.
""",
    )
    _write(
        root / "cap-handbook-doc" / "selected" / "catalog.json",
        json.dumps(
            {
                "pages": [
                    {"page_no": 50, "heading_path": ["Community-acquired pneumonia"], "table_count": 1},
                    {
                        "page_no": 51,
                        "heading_path": ["Community-acquired pneumonia", "Inpatient treatment"],
                        "table_count": 0,
                    },
                ]
            }
        ),
    )
    _write(root / "cap-handbook-doc" / "selected" / "figures.json", "[]")
    _write(
        root / "cap-handbook-doc" / "selected" / "document.json",
        json.dumps(
            {
                "tables": [
                    {
                        "prov": [{"page_no": 50}],
                        "captions": [],
                        "data": {"table_cells": [], "num_rows": 2, "num_cols": 2},
                    }
                ]
            }
        ),
    )
    _write(
        root / "cap-handbook-doc" / "selected" / "tables" / "table-001.csv",
        "Scenario,Regimen\nOutpatient no comorbidity,Amoxicillin or doxycycline\n",
    )

    _write(
        root / "vap-guide-doc" / "selected" / "retrieval.md",
        """# Document Metadata

## Page 10
Section path: Hospital-acquired pneumonia

## Treatment

Hospital-acquired pneumonia can be treated with co-amoxiclav in non-severe cases.

## Page 11
Section path: Ventilator-associated pneumonia

## Treatment

Ventilator-associated pneumonia requires broad-spectrum empiric therapy.
""",
    )
    _write(
        root / "vap-guide-doc" / "selected" / "catalog.json",
        json.dumps(
            {
                "pages": [
                    {"page_no": 10, "heading_path": ["Hospital-acquired pneumonia"], "table_count": 0},
                    {"page_no": 11, "heading_path": ["Ventilator-associated pneumonia"], "table_count": 0},
                ]
            }
        ),
    )
    _write(root / "vap-guide-doc" / "selected" / "figures.json", "[]")
    _write(root / "vap-guide-doc" / "selected" / "document.json", json.dumps({"tables": []}))

    _write(
        root / "lung-abscess-doc" / "selected" / "retrieval.md",
        """# Document Metadata

## Page 85
Section path: Lung abscess

## Treatment

Empirical therapy for community-acquired lung abscess can include co-amoxiclav.
""",
    )
    _write(
        root / "lung-abscess-doc" / "selected" / "catalog.json",
        json.dumps({"pages": [{"page_no": 85, "heading_path": ["Lung abscess"], "table_count": 0}]}),
    )
    _write(root / "lung-abscess-doc" / "selected" / "figures.json", "[]")
    _write(root / "lung-abscess-doc" / "selected" / "document.json", json.dumps({"tables": []}))

    return root


@pytest.fixture
def local_corpus_fixture(tmp_path, monkeypatch):
    corpus_root = _mini_corpus(tmp_path / "literature_corpus")
    index_dir = tmp_path / "backend-data" / "local_corpus"
    monkeypatch.setattr(local_corpus, "DEFAULT_LOCAL_CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(local_corpus, "LOCAL_CORPUS_INDEX_DIR", index_dir)
    local_corpus.clear_local_corpus_caches()
    local_corpus_reasoning.clear_local_corpus_reasoning_caches()
    yield corpus_root
    local_corpus.clear_local_corpus_caches()
    local_corpus_reasoning.clear_local_corpus_reasoning_caches()


@pytest.fixture
def cap_corpus_fixture(tmp_path, monkeypatch):
    corpus_root = _cap_corpus(tmp_path / "literature_corpus")
    index_dir = tmp_path / "backend-data" / "local_corpus"
    monkeypatch.setattr(local_corpus, "DEFAULT_LOCAL_CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(local_corpus, "LOCAL_CORPUS_INDEX_DIR", index_dir)
    local_corpus.clear_local_corpus_caches()
    local_corpus_reasoning.clear_local_corpus_reasoning_caches()
    yield corpus_root
    local_corpus.clear_local_corpus_caches()
    local_corpus_reasoning.clear_local_corpus_reasoning_caches()


def _request_for_corpus(corpus_root: Path) -> SimpleNamespace:
    config = SimpleNamespace(
        LOCAL_CORPUS_ROOT=str(corpus_root),
        ENABLE_LOCAL_CORPUS_TOOLS=True,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def test_list_local_corpus_domains_discovers_multiple_domains(local_corpus_fixture):
    payload = local_corpus.list_local_corpus_domains(str(local_corpus_fixture))

    assert payload["status"] == "ok"
    assert {item["domain"] for item in payload["domains"]} == {"medicine", "chemistry"}


def test_shortlist_books_requests_domain_selection_when_ambiguous(local_corpus_fixture):
    payload = local_corpus.shortlist_local_corpus_books(
        query="foundational reference",
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["phase"] == "awaiting_domain_selection"
    assert {item["domain"] for item in payload["domains"]} == {"medicine", "chemistry"}


def test_shortlist_books_prefers_medical_guideline_for_management_query(local_corpus_fixture):
    payload = local_corpus.shortlist_local_corpus_books(
        query="hypertension management treatment threshold",
        domain="medicine",
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["items"][0]["book_id"] == "med-guide"
    assert all(item["domain"] == "medicine" for item in payload["items"])


def test_retrieve_evidence_returns_related_tables_and_freshness_note(local_corpus_fixture):
    payload = local_corpus.retrieve_local_corpus_evidence(
        query="latest hypertension management threshold table",
        book_ids=["med-guide"],
        include_related_tables=True,
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["domain"] == "medicine"
    assert payload["items"][0]["book_id"] == "med-guide"
    assert payload["items"][0]["related_tables"][0]["table_id"] == "table-001"
    assert payload["freshness_note"]


def test_view_table_and_figure_metadata_round_trip(local_corpus_fixture):
    table_payload = local_corpus.view_local_corpus_table(
        book_id="med-guide",
        table_id="table-001",
        config_or_path=str(local_corpus_fixture),
    )
    figure_payload = local_corpus.view_local_corpus_figure_metadata(
        book_id="med-guide",
        figure_id="figure-001",
        config_or_path=str(local_corpus_fixture),
    )

    assert table_payload["status"] == "ok"
    assert table_payload["rows"][0] == ["Drug", "Threshold"]
    assert figure_payload["status"] == "ok"
    assert figure_payload["caption"] == "Treatment pathway"


def test_shortlist_books_prefers_antimicrobial_reference_for_cap_regimen_query(
    cap_corpus_fixture,
):
    payload = local_corpus.shortlist_local_corpus_books(
        query="community-acquired pneumonia initial empiric antibiotics in adults",
        domain="medicine",
        max_books=3,
        config_or_path=str(cap_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["items"][0]["book_id"] == "cap-handbook"
    assert payload["items"][0]["resource_type"] == "handbook"


def test_retrieve_evidence_penalizes_hap_vap_and_lung_abscess_for_cap_query(
    cap_corpus_fixture,
):
    payload = local_corpus.retrieve_local_corpus_evidence(
        query="community-acquired pneumonia initial empiric antibiotics in adults",
        book_ids=["cap-handbook", "vap-guide", "lung-abscess"],
        include_related_tables=True,
        config_or_path=str(cap_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["items"][0]["book_id"] == "cap-handbook"
    assert payload["items"][0]["page_no"] in {50, 51}
    assert "direct_topic" in payload["items"][0]["rationale"]
    assert "community-acquired pneumonia" in payload["items"][0]["section_path"].lower()
    assert payload["evidence_sufficiency"] == "strong"


def test_reasoning_pack_loader_reads_canonical_pack():
    pack = local_corpus_reasoning.load_local_corpus_pack("medicine")

    assert pack["domain"] == "medicine"
    assert pack["maturity_tier"] == 1
    assert "symptom_lab_orientation" in pack["task_types"]


def test_frame_problem_returns_primary_and_secondary_task_types(local_corpus_fixture):
    payload = local_corpus_reasoning.frame_local_corpus_problem(
        query="I have symptoms and lab values. What differential buckets should I think about while waiting?",
        domain_hint="medicine",
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["domain"] == "medicine"
    assert payload["primary_task_type"] in {
        "symptom_lab_orientation",
        "differential_orientation",
    }
    assert isinstance(payload["secondary_task_types"], list)
    assert payload["coverage_is_scaffold_not_exhaustive"] is True


def test_plan_axes_respects_backend_cap(local_corpus_fixture):
    problem_frame = local_corpus_reasoning.frame_local_corpus_problem(
        query="I have symptoms and lab values. What differential buckets should I think about while waiting?",
        domain_hint="medicine",
        config_or_path=str(local_corpus_fixture),
    )

    payload = local_corpus_reasoning.plan_local_corpus_axes(
        problem_frame=problem_frame,
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["axis_budget"] <= 6
    assert payload["coverage_is_scaffold_not_exhaustive"] is True
    assert len(payload["axes"]) == payload["axis_budget"]


def test_collect_axis_evidence_groups_results_by_axis(local_corpus_fixture):
    problem_frame = local_corpus_reasoning.frame_local_corpus_problem(
        query="hypertension management threshold and treatment workup",
        domain_hint="medicine",
        config_or_path=str(local_corpus_fixture),
    )
    plan = local_corpus_reasoning.plan_local_corpus_axes(
        problem_frame=problem_frame,
        config_or_path=str(local_corpus_fixture),
    )

    payload = local_corpus_reasoning.collect_local_corpus_axis_evidence(
        problem_frame=problem_frame,
        axes=plan["axes"],
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["axis_count"] >= 1
    assert payload["axis_results"][0]["axis_id"]
    assert isinstance(payload["axis_results"][0]["shortlisted_books"], list)


def test_assess_evidence_returns_cautious_state_for_missing_required_axes(local_corpus_fixture):
    problem_frame = local_corpus_reasoning.frame_local_corpus_problem(
        query="hypertension management threshold and treatment workup",
        domain_hint="medicine",
        config_or_path=str(local_corpus_fixture),
    )
    evidence_bundle = {
        "status": "ok",
        "axis_results": [
            {
                "axis_id": "management_guidance",
                "evidence_items": [],
                "directness": "none",
            }
        ],
    }

    payload = local_corpus_reasoning.assess_local_corpus_evidence(
        problem_frame=problem_frame,
        evidence_bundle=evidence_bundle,
        config_or_path=str(local_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["evidence_sufficiency"] in {"weak", "partial"}
    assert payload["coverage_is_scaffold_not_exhaustive"] is True


def test_builtin_local_corpus_tools_respect_off_chat_mode(local_corpus_fixture):
    request = _request_for_corpus(local_corpus_fixture)
    model = {"info": {"meta": {"capabilities": {}, "builtinTools": {"local_corpus": True}}}}

    tools = tool_utils.get_builtin_tools(
        request,
        {"__metadata__": {"params": {"local_corpus_mode": "off"}}},
        features={},
        model=model,
    )

    assert "local_corpus_shortlist_books" not in tools


@pytest.mark.asyncio
async def test_builtin_local_corpus_tools_use_request_config(local_corpus_fixture):
    request = _request_for_corpus(local_corpus_fixture)

    shortlist_output = await builtin_tools.local_corpus_shortlist_books(
        query="organic chemistry reaction mechanism",
        __request__=request,
    )
    evidence_output = await builtin_tools.local_corpus_retrieve_evidence(
        query="blood pressure threshold",
        book_ids=["med-guide"],
        __request__=request,
    )

    shortlist_payload = json.loads(shortlist_output)
    evidence_payload = json.loads(evidence_output)

    assert shortlist_payload["phase"] == "completed"
    assert shortlist_payload["items"][0]["domain"] == "chemistry"
    assert evidence_payload["status"] == "ok"
    assert evidence_payload["items"][0]["domain"] == "medicine"


@pytest.mark.asyncio
async def test_builtin_reasoning_tools_use_request_config(local_corpus_fixture):
    request = _request_for_corpus(local_corpus_fixture)

    frame_output = await builtin_tools.local_corpus_frame_problem(
        query="What differential buckets should I think about for symptoms and lab values?",
        domain_hint="medicine",
        __request__=request,
    )
    frame_payload = json.loads(frame_output)

    plan_output = await builtin_tools.local_corpus_plan_axes(
        problem_frame=frame_payload,
        __request__=request,
    )
    plan_payload = json.loads(plan_output)

    collect_output = await builtin_tools.local_corpus_collect_axis_evidence(
        problem_frame=frame_payload,
        axes=plan_payload["axes"],
        __request__=request,
    )
    collect_payload = json.loads(collect_output)

    assess_output = await builtin_tools.local_corpus_assess_evidence(
        problem_frame=frame_payload,
        evidence_bundle=collect_payload,
        __request__=request,
    )
    assess_payload = json.loads(assess_output)

    assert frame_payload["status"] == "ok"
    assert plan_payload["status"] == "ok"
    assert collect_payload["status"] == "ok"
    assert assess_payload["status"] == "ok"
