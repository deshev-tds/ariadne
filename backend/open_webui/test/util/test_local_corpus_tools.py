import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import open_webui.retrieval.local_corpus as local_corpus
import open_webui.tools.builtin as builtin_tools


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


@pytest.fixture
def local_corpus_fixture(tmp_path, monkeypatch):
    corpus_root = _mini_corpus(tmp_path / "literature_corpus")
    index_dir = tmp_path / "backend-data" / "local_corpus"
    monkeypatch.setattr(local_corpus, "DEFAULT_LOCAL_CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(local_corpus, "LOCAL_CORPUS_INDEX_DIR", index_dir)
    local_corpus.clear_local_corpus_caches()
    yield corpus_root
    local_corpus.clear_local_corpus_caches()


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
