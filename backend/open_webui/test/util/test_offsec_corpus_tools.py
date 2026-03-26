import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import open_webui.retrieval.corpus_runtime as corpus_runtime
import open_webui.retrieval.local_corpus as local_corpus
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
""",
    )
    _write(
        root / "_serving" / "books" / "web-application-pentesting.md",
        """# Web Application PenTesting

Primary domain: Web Security

What this book is:
- A methodology-oriented web testing book.
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
                "books": [
                    "web-application-pentesting",
                    "hacking-and-security-comprehensive-guide",
                ],
                "tools": ["burp_suite"],
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
    _write(
        broad_doc_dir / "selected" / "retrieval.md",
        """# Document Metadata

## Page 229
Section path: 4.1.2 Examples

## ffuf examples

ffuf can be used for endpoint discovery, vhost fuzzing, and targeted content enumeration.
""",
    )
    _write(web_doc_dir / "selected" / "raw.md", "raw web book")
    _write(web_doc_dir / "selected" / "catalog.json", json.dumps({"pages": []}))
    _write(web_doc_dir / "manifest.json", "{}")
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


def test_load_offsec_registry_rebases_stale_absolute_review_paths(offsec_corpus_fixture):
    review_path = (
        offsec_corpus_fixture / "_compiled_docling_review" / "compiled-offsec-review.json"
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    stale_root = Path("/Volumes/External/Books/Offsec")
    for item in review["review_queue"]:
        item["source_path"] = str(
            stale_root / Path(str(item["source_path"])).relative_to(offsec_corpus_fixture)
        )
        for key in (
            "retrieval_markdown_path",
            "raw_markdown_path",
            "catalog_path",
            "manifest_path",
        ):
            item[key] = str(
                stale_root / Path(str(item[key])).relative_to(offsec_corpus_fixture)
            )
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    offsec_corpus.clear_offsec_corpus_caches()

    registry = offsec_corpus.load_offsec_registry(str(offsec_corpus_fixture))

    assert sorted(registry.books_by_id) == [
        "hacking-and-security-comprehensive-guide",
        "web-application-pentesting",
    ]
    assert registry.books_by_id["web-application-pentesting"].retrieval_path.exists()
    assert registry.books_by_id[
        "hacking-and-security-comprehensive-guide"
    ].retrieval_path.exists()


def test_resolve_offsec_corpus_root_anchors_relative_paths_to_repo_root(tmp_path, monkeypatch):
    repo_root = tmp_path / "portable-repo"
    corpus_root = _mini_offsec_corpus(repo_root / "offsec_corpus")
    monkeypatch.setattr(local_corpus, "BASE_DIR", repo_root)
    offsec_corpus.clear_offsec_corpus_caches()

    resolved = corpus_runtime.resolve_offsec_corpus_root("offsec_corpus")

    assert resolved == corpus_root.resolve()


def test_resolve_offsec_corpus_root_falls_back_from_stale_absolute_repo_path(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "portable-repo"
    corpus_root = _mini_offsec_corpus(repo_root / "offsec_corpus")
    monkeypatch.setattr(local_corpus, "BASE_DIR", repo_root)
    offsec_corpus.clear_offsec_corpus_caches()

    resolved = corpus_runtime.resolve_offsec_corpus_root(
        "/old/location/open-webui/offsec_corpus"
    )

    assert resolved == corpus_root.resolve()


def test_builtin_tools_expose_offsec_tools_only_in_offsec_mode(offsec_corpus_fixture):
    request = _request_for_offsec(offsec_corpus_fixture)
    model = {"info": {"meta": {"capabilities": {}, "builtinTools": {"local_corpus": True}}}}

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
