import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import open_webui.retrieval.offsec_corpus as offsec_corpus
import open_webui.tools.builtin as builtin_tools
import open_webui.utils.tools as tool_utils


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _mini_offsec_corpus(root: Path) -> Path:
    _write(
        root / "_serving" / "tasks" / "index.md",
        """# Tasks

- `website_assessment`: Initial web assessment workflow.
""",
    )
    _write(
        root / "_serving" / "tools" / "index.md",
        """# Tools

- `burp_suite`: Interactive proxying and request replay.
""",
    )
    _write(
        root / "_serving" / "domains" / "index.md",
        """# Domains

- `web_security`: Website assessment and validation work.
""",
    )
    _write(
        root / "_serving" / "tasks" / "website_assessment.md",
        """# Website Assessment

Domain: Web Security

Initial pass on a website to map attack surface and form an early test plan.

Books to open first:
- Web Application PenTesting: Web testing methodology and validation flow.
- Hacking and Security: Broad fallback reference for practical examples.

Useful tools or frameworks:
- Burp Suite: Interactive web proxying and replay support.
""",
    )
    _write(
        root / "_serving" / "tools" / "burp_suite.md",
        """# Burp Suite

Interactive web proxying, request inspection, replay, and validation support.

Start with these books:
- Web Application PenTesting: Methodology and validation patterns.
- Hacking and Security: Broad fallback examples.
""",
    )
    _write(
        root / "_serving" / "domains" / "web_security.md",
        """# Web Security

Website assessment, mapping, and validation work.

Tasks:
- `website_assessment`: Initial pass on a website.

Books:
- Web Application PenTesting: Methodology-heavy web testing source.
""",
    )
    _write(
        root / "_serving" / "books" / "web-application-pentesting.md",
        """# Web Application PenTesting

Primary domain: Web Security

What this book is:
- A methodology-oriented web testing book.

Useful tools or frameworks:
- Burp Suite: Interactive web proxying and replay.
""",
    )
    _write(
        root / "_serving" / "books" / "hacking-and-security-comprehensive-guide.md",
        """# Hacking and Security

Primary domain: General Security Reference

What this book is:
- A broad security reference with many practical examples.
""",
    )

    taxonomy = {
        "domains": [
            {
                "id": "web_security",
                "title": "Web Security",
                "summary": "Website assessment and validation work.",
            }
        ],
        "tasks": [
            {
                "id": "website_assessment",
                "title": "Website Assessment",
                "domain": "web_security",
                "summary": "Initial pass on a website to map attack surface.",
                "start_here": "Use this when methodology comes first.",
                "books": [
                    "web-application-pentesting",
                    "hacking-and-security-comprehensive-guide",
                ],
                "tools": ["burp_suite"],
                "avoid": ["Do not jump to automation first."],
            }
        ],
        "tools": [
            {
                "id": "burp_suite",
                "title": "Burp Suite",
                "summary": "Interactive proxying and request replay.",
                "books": [
                    "web-application-pentesting",
                    "hacking-and-security-comprehensive-guide",
                ],
                "tasks": ["website_assessment"],
                "caveats": ["Not a substitute for methodology."],
            }
        ],
        "books": [
            {
                "id": "web-application-pentesting",
                "title": "Web Application PenTesting",
                "source_pdf": "Maleh/Web Application PenTesting.pdf",
                "primary_domain": "web_security",
                "task_tags": ["website_assessment"],
                "tool_tags": ["burp_suite"],
                "platform_tags": ["web", "http"],
                "artifact_style": ["methodology-heavy"],
                "one_liner": "Web testing methodology and validation patterns.",
                "what_it_is": "A methodology-oriented web testing source.",
                "use_when": ["You need a web assessment plan."],
                "strong_for": ["assessment methodology", "validation workflow"],
                "weak_for": ["deep bug bounty process"],
                "notes": ["Use first for method."],
            },
            {
                "id": "hacking-and-security-comprehensive-guide",
                "title": "Hacking and Security",
                "source_pdf": "Kofler/Hacking and Security.pdf",
                "primary_domain": "general_security_reference",
                "task_tags": ["website_assessment"],
                "tool_tags": [],
                "platform_tags": ["web", "general"],
                "artifact_style": ["example-heavy"],
                "one_liner": "Broad security examples and fallback reference.",
                "what_it_is": "A broad fallback security reference.",
                "use_when": ["You need examples."],
                "strong_for": ["examples", "broad survey"],
                "weak_for": ["tight methodology"],
                "notes": ["Use after focused web sources."],
            },
        ],
        "quarantine": [],
        "lookups": {
            "domain_ids": ["web_security"],
            "task_ids": ["website_assessment"],
            "tool_ids": ["burp_suite"],
            "book_ids": [
                "web-application-pentesting",
                "hacking-and-security-comprehensive-guide",
            ],
        },
    }
    _write(
        root / "_serving" / "internal" / "offsec-taxonomy-catalog.json",
        json.dumps(taxonomy, ensure_ascii=False, indent=2),
    )

    web_doc_dir = (
        root
        / "_compiled_docling_review"
        / "maleh-web-application-pentesting"
        / "maleh-web-application-pentesting--1234abcd"
    )
    broad_doc_dir = (
        root
        / "_compiled_docling_review"
        / "kofler-hacking-and-security"
        / "kofler-hacking-and-security--5678efgh"
    )
    _write(
        web_doc_dir / "selected" / "retrieval.md",
        """# Document Metadata

## Page 14
Section path: Introduction to Penetration Testing and Methodologies

## Penetration testing methodologies

A structured website assessment should map attack surface before narrowing into validation.

## Page 42
Section path: Mastering Web Application Penetration Testing with Burp Suite

## Burp Suite workflow

Burp Suite helps with intercepting, replaying, and validating suspected web issues.
""",
    )
    _write(web_doc_dir / "selected" / "raw.md", "raw web book")
    _write(web_doc_dir / "selected" / "catalog.json", json.dumps({"pages": []}))
    _write(web_doc_dir / "manifest.json", "{}")
    _write(
        broad_doc_dir / "selected" / "retrieval.md",
        """# Document Metadata

## Page 229
Section path: 4.1.2 Examples

## ffuf examples

ffuf can be used for endpoint discovery, vhost fuzzing, and targeted content enumeration.
""",
    )
    _write(broad_doc_dir / "selected" / "raw.md", "raw broad book")
    _write(broad_doc_dir / "selected" / "catalog.json", json.dumps({"pages": []}))
    _write(broad_doc_dir / "manifest.json", "{}")

    review = {
        "review_queue": [
            {
                "source_path": str(root / "Maleh" / "Web Application PenTesting.pdf"),
                "retrieval_markdown_path": str(web_doc_dir / "selected" / "retrieval.md"),
                "raw_markdown_path": str(web_doc_dir / "selected" / "raw.md"),
                "catalog_path": str(web_doc_dir / "selected" / "catalog.json"),
                "manifest_path": str(web_doc_dir / "manifest.json"),
                "success": True,
            },
            {
                "source_path": str(root / "Kofler" / "Hacking and Security.pdf"),
                "retrieval_markdown_path": str(broad_doc_dir / "selected" / "retrieval.md"),
                "raw_markdown_path": str(broad_doc_dir / "selected" / "raw.md"),
                "catalog_path": str(broad_doc_dir / "selected" / "catalog.json"),
                "manifest_path": str(broad_doc_dir / "manifest.json"),
                "success": True,
            },
        ]
    }
    _write(
        root / "_compiled_docling_review" / "compiled-offsec-review.json",
        json.dumps(review, ensure_ascii=False, indent=2),
    )
    return root


@pytest.fixture
def offsec_corpus_fixture(tmp_path):
    corpus_root = _mini_offsec_corpus(tmp_path / "offsec")
    offsec_corpus.clear_offsec_corpus_caches()
    yield corpus_root
    offsec_corpus.clear_offsec_corpus_caches()


def _request_for_offsec(corpus_root: Path) -> SimpleNamespace:
    config = SimpleNamespace(
        ENABLE_LOCAL_CORPUS_TOOLS=True,
        OFFSEC_CORPUS_ROOT=str(corpus_root),
        LOCAL_CORPUS_ROOT="",
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def test_load_offsec_registry_resolves_books_to_retrieval_payloads(offsec_corpus_fixture):
    registry = offsec_corpus.load_offsec_registry(str(offsec_corpus_fixture))

    assert sorted(registry.books_by_id) == [
        "hacking-and-security-comprehensive-guide",
        "web-application-pentesting",
    ]
    assert registry.books_by_id["web-application-pentesting"].retrieval_path.exists()
    assert registry.books_by_id[
        "hacking-and-security-comprehensive-guide"
    ].retrieval_path.exists()


def test_load_offsec_registry_fails_clearly_when_book_mapping_is_missing(tmp_path):
    corpus_root = _mini_offsec_corpus(tmp_path / "broken-offsec")
    review_path = corpus_root / "_compiled_docling_review" / "compiled-offsec-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["review_queue"] = review["review_queue"][:1]
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    offsec_corpus.clear_offsec_corpus_caches()

    with pytest.raises(ValueError, match="hacking-and-security-comprehensive-guide"):
        offsec_corpus.load_offsec_registry(str(corpus_root))


def test_load_offsec_registry_rebases_foreign_review_paths(tmp_path):
    corpus_root = _mini_offsec_corpus(tmp_path / "relocated-offsec")
    review_path = corpus_root / "_compiled_docling_review" / "compiled-offsec-review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))

    for item in review["review_queue"]:
        source_name = Path(item["source_path"]).name
        retrieval_suffix = item["retrieval_markdown_path"].split("/_compiled_docling_review/", 1)[1]
        raw_suffix = item["raw_markdown_path"].split("/_compiled_docling_review/", 1)[1]
        catalog_suffix = item["catalog_path"].split("/_compiled_docling_review/", 1)[1]
        manifest_suffix = item["manifest_path"].split("/_compiled_docling_review/", 1)[1]

        item["source_name"] = source_name
        item["source_path"] = f"/opt/offsec/foreign-source/{source_name}"
        item["retrieval_markdown_path"] = (
            f"/opt/offsec/_compiled_docling_review/{retrieval_suffix}"
        )
        item["raw_markdown_path"] = (
            f"/opt/offsec/_compiled_docling_review/{raw_suffix}"
        )
        item["catalog_path"] = (
            f"/opt/offsec/_compiled_docling_review/{catalog_suffix}"
        )
        item["manifest_path"] = (
            f"/opt/offsec/_compiled_docling_review/{manifest_suffix}"
        )

    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    offsec_corpus.clear_offsec_corpus_caches()

    registry = offsec_corpus.load_offsec_registry(str(corpus_root))

    web_book = registry.books_by_id["web-application-pentesting"]
    broad_book = registry.books_by_id["hacking-and-security-comprehensive-guide"]

    assert str(web_book.retrieval_path).startswith(str(corpus_root))
    assert str(broad_book.retrieval_path).startswith(str(corpus_root))
    assert web_book.retrieval_path.exists()
    assert broad_book.retrieval_path.exists()


def test_consult_offsec_corpus_routes_website_assessment_to_task(offsec_corpus_fixture):
    payload = offsec_corpus.consult_offsec_corpus(
        objective="Assess a website and map the attack surface",
        phase="start",
        config_or_path=str(offsec_corpus_fixture),
    )

    assert payload["route"] == "task"
    assert payload["matched_id"] == "website_assessment"
    assert payload["recommended_book_ids"][0] == "web-application-pentesting"
    assert "Website Assessment" in payload["selection_markdown"]


def test_consult_offsec_corpus_routes_burp_to_tool(offsec_corpus_fixture):
    payload = offsec_corpus.consult_offsec_corpus(
        objective="Need a Burp workflow for validation",
        phase="mid_run",
        named_entity="Burp Suite",
        config_or_path=str(offsec_corpus_fixture),
    )

    assert payload["route"] == "tool"
    assert payload["matched_id"] == "burp_suite"
    assert payload["docs_fallback_suggested"] is False


def test_consult_offsec_corpus_uses_direct_lookup_for_ffuf(offsec_corpus_fixture):
    payload = offsec_corpus.consult_offsec_corpus(
        objective="Give me creative uses of ffuf",
        phase="start",
        named_entity="ffuf",
        config_or_path=str(offsec_corpus_fixture),
    )

    assert payload["route"] == "direct_lookup"
    assert payload["recommended_book_ids"] == ["hacking-and-security-comprehensive-guide"]
    assert payload["docs_fallback_suggested"] is True
    assert payload["preview_items"][0]["book_id"] == "hacking-and-security-comprehensive-guide"


def test_retrieve_offsec_evidence_returns_retrieval_snippets_only(offsec_corpus_fixture):
    payload = offsec_corpus.retrieve_offsec_evidence(
        query="ffuf endpoint discovery examples",
        max_snippets=3,
        config_or_path=str(offsec_corpus_fixture),
    )

    assert payload["status"] == "ok"
    assert payload["items"]
    assert payload["items"][0]["book_id"] == "hacking-and-security-comprehensive-guide"
    assert "raw broad book" not in json.dumps(payload)


def test_builtin_tools_expose_offsec_tools_only_in_offsec_mode(offsec_corpus_fixture):
    request = _request_for_offsec(offsec_corpus_fixture)
    model = {
        "info": {
            "meta": {
                "capabilities": {},
                "builtinTools": {
                    "local_corpus": True,
                    "knowledge": True,
                },
            }
        }
    }

    tools = tool_utils.get_builtin_tools(
        request,
        {
            "__metadata__": {
                "params": {
                    "working_mode": "offsec",
                    "local_corpus_mode": "prefer",
                }
            }
        },
        features={},
        model=model,
    )

    assert "offsec_consult" in tools
    assert "offsec_retrieve_evidence" in tools
    assert "local_corpus_shortlist_books" not in tools
    assert "list_knowledge_bases" not in tools
    assert "search_knowledge_files" not in tools
    assert "query_knowledge_files" not in tools


@pytest.mark.asyncio
async def test_builtin_offsec_consult_uses_request_config(offsec_corpus_fixture):
    request = _request_for_offsec(offsec_corpus_fixture)

    payload = json.loads(
        await builtin_tools.offsec_consult(
            objective="Assess a website and map the attack surface",
            phase="start",
            __request__=request,
        )
    )

    assert payload["route"] == "task"
    assert payload["matched_id"] == "website_assessment"
