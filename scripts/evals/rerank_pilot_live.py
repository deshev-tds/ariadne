#!/usr/bin/env python3

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://192.168.1.117"
DEFAULT_MODEL = "Qwen3.5-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled.Q6_K"
DEFAULT_TOOL_ID = "rerank_eval_harness"


CASES: dict[str, dict[str, Any]] = {
    "sildenafil_natural": {
        "kind": "local_corpus",
        "prompt": "What medicines not to take with sildenafil.",
        "query": "what medicines not to take with sildenafil",
        "book_ids": ["a87339f6c294"],
        "top_k": 10,
        "expected_positive_terms": [
            "sildenafil",
            "erectile dysfunction",
            "pde5",
            "nitrate",
            "nitroglycerin",
        ],
        "expected_negative_terms": [
            "asthma",
            "sedative-hypnotics",
            "case study",
        ],
        "notes": "Known bad baseline in prior replay: relevant ED section was below unrelated Katzung chunks.",
    },
    "sildenafil_contraindications": {
        "kind": "local_corpus",
        "prompt": "Find the key contraindication signal for sildenafil with nitrates and hypotension.",
        "query": "Sildenafil nitrates nitroglycerin contraindicated hypotension phosphodiesterase PDE5 inhibitor",
        "book_ids": ["a87339f6c294"],
        "top_k": 10,
        "expected_positive_terms": [
            "sildenafil",
            "nitrate",
            "nitroglycerin",
            "hypotension",
            "erectile dysfunction",
        ],
        "expected_negative_terms": [
            "asthma",
            "sedative-hypnotics",
        ],
        "notes": "Control case using the more specific reformulation from the earlier tool trace.",
    },
    "sildenafil_metabolism": {
        "kind": "local_corpus",
        "prompt": "Find the CYP3A4 interaction evidence for sildenafil.",
        "query": "sildenafil metabolism CYP3A4 ketoconazole ritonavir clarithromycin grapefruit",
        "book_ids": ["a87339f6c294"],
        "top_k": 10,
        "expected_positive_terms": [
            "sildenafil",
            "cyp3a4",
            "ketoconazole",
            "ritonavir",
            "clarithromycin",
            "grapefruit",
        ],
        "expected_negative_terms": [
            "asthma",
            "sedative-hypnotics",
        ],
        "notes": "Control case for pharmacology/drug-interaction retrieval inside the same book.",
    },
    "pneumonia_imaging": {
        "kind": "local_corpus",
        "prompt": "Find the best radiology chunk for lobar pneumonia consolidation imaging findings.",
        "query": "pneumonia chest X-ray consolidation lobar pneumonia imaging findings",
        "book_ids": ["17ac590d304f", "068ce8937fc7"],
        "top_k": 5,
        "expected_positive_terms": [
            "lobar pneumonia",
            "consolidation",
            "air bronchogram",
            "bronchopneumonia",
        ],
        "expected_negative_terms": [
            "fungi",
            "blastomycosis",
            "coccidioidomycosis",
        ],
        "notes": "Known bad baseline in prior replay: endemic fungal imaging chunk outranked the directly relevant pneumonia chunk.",
    },
    "psoriasis_morphology": {
        "kind": "local_corpus",
        "prompt": "Find the best dermatology chunk for plaque psoriasis morphology and clinical features.",
        "query": "psoriasis plaque morphology clinical features diagnosis",
        "book_ids": ["3d2d1a7499b0"],
        "top_k": 5,
        "expected_positive_terms": [
            "psoriasis",
            "plaque",
            "erythematosquamous",
            "silvery-white",
            "clinical morphology",
        ],
        "expected_negative_terms": [
            "basal cell carcinoma",
            "bowen",
        ],
        "notes": "Known bad baseline in prior replay: an off-topic Clinical Features chunk outranked the core psoriasis passage.",
    },
    "sepsis_management": {
        "kind": "local_corpus",
        "prompt": "Retrieve strong local evidence for sepsis diagnosis and management.",
        "query": "sepsis diagnosis criteria management antibiotics",
        "book_ids": ["e730ac1af401", "6c2ee5b41224", "02cfb40a0b42"],
        "top_k": 8,
        "expected_positive_terms": [
            "sepsis",
            "management",
            "antibiotics",
        ],
        "expected_negative_terms": [],
        "notes": "Control case that already looked reasonable in the prior replay.",
    },
    "stroke_anticoagulation": {
        "kind": "local_corpus",
        "prompt": "Retrieve strong local evidence for acute stroke anticoagulation and thrombolysis.",
        "query": "stroke management anticoagulation thrombolysis",
        "book_ids": ["2a1bbfeddfa5", "aaa1e221ed78", "95d611cc7c04"],
        "top_k": 8,
        "expected_positive_terms": [
            "stroke",
            "anticoagulation",
            "thrombolysis",
        ],
        "expected_negative_terms": [],
        "notes": "Control case that already looked reasonable in the prior replay.",
    },
    "offsec_macos_malware": {
        "kind": "offsec",
        "prompt": "Retrieve focused offsec evidence for macOS malware detection and code-signing telemetry.",
        "query": "macOS malware detection process binary code signing endpoint security monitoring",
        "book_ids": ["art-of-mac-malware-vol2"],
        "max_snippets": 10,
        "expected_positive_terms": [
            "macos",
            "code signing",
            "malware",
            "endpoint",
        ],
        "expected_negative_terms": [],
        "notes": "Offsec control case taken from the prior tool replay.",
    },
    "offsec_recon_mapping": {
        "kind": "offsec",
        "prompt": "Retrieve focused offsec evidence for recon, subdomain enumeration, endpoint discovery, and service mapping.",
        "query": "reconnaissance techniques subdomain enumeration endpoint discovery service mapping nmap burp",
        "book_ids": [
            "web-application-pentesting",
            "bug-bounty-from-scratch",
            "ultimate-kali-linux-book-3e",
        ],
        "max_snippets": 12,
        "expected_positive_terms": [
            "recon",
            "subdomain",
            "endpoint",
            "nmap",
            "burp",
        ],
        "expected_negative_terms": [],
        "notes": "Offsec control case taken from the prior tool replay.",
    },
}


def build_eval_tool_content() -> str:
    cases_json = json.dumps(CASES, ensure_ascii=False, indent=4)
    return f'''import json
from typing import Any

from open_webui.retrieval.local_corpus import retrieve_local_corpus_evidence
from open_webui.retrieval.offsec_corpus import retrieve_offsec_evidence

CASES = {cases_json}


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _haystack(item: dict[str, Any]) -> str:
    return " ".join(
        [
            _normalize_text(item.get("title")),
            _normalize_text(item.get("section_path")),
            _normalize_text(item.get("content")),
        ]
    ).lower()


def _keyword_summary(items: list[dict[str, Any]], positives: list[str], negatives: list[str]) -> dict[str, Any]:
    top1_blob = _haystack(items[0]) if items else ""
    top3_blob = " ".join(_haystack(item) for item in items[:3]).strip()

    top1_positive_hits = [term for term in positives if term.lower() in top1_blob]
    top3_positive_hits = [term for term in positives if term.lower() in top3_blob]
    top1_negative_hits = [term for term in negatives if term.lower() in top1_blob]
    top3_negative_hits = [term for term in negatives if term.lower() in top3_blob]

    return {{
        "top1_positive_hits": top1_positive_hits,
        "top3_positive_hits": top3_positive_hits,
        "top1_negative_hits": top1_negative_hits,
        "top3_negative_hits": top3_negative_hits,
        "top1_positive_hit_count": len(top1_positive_hits),
        "top3_positive_hit_count": len(top3_positive_hits),
        "top1_negative_hit_count": len(top1_negative_hits),
        "top3_negative_hit_count": len(top3_negative_hits),
    }}


def _top_digest(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    digest = []
    for index, item in enumerate(items[:limit], start=1):
        digest.append(
            {{
                "rank": index,
                "title": item.get("title"),
                "section_path": item.get("section_path"),
                "page_no": item.get("page_no"),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score"),
                "rationale": item.get("rationale"),
                "citation_label": item.get("citation_label"),
                "content_preview": _normalize_text(item.get("content"))[:500],
            }}
        )
    return digest


class Tools:
    def run_case(self, case_id: str, __request__=None) -> str:
        """
        Run one fixed retrieval evaluation case against the server's current corpus/retrieval stack.

        :param case_id: One of: {", ".join(sorted(CASES.keys()))}
        """
        case = CASES.get(case_id)
        if case is None:
            return json.dumps({{"status": "error", "error": f"Unknown case_id: {{case_id}}"}} , ensure_ascii=False)

        if case["kind"] == "local_corpus":
            payload = retrieve_local_corpus_evidence(
                query=case["query"],
                book_ids=case["book_ids"],
                top_k=case["top_k"],
                include_related_tables=True,
                include_related_figures=False,
                config_or_path=(__request__.app.state.config if __request__ is not None else None),
            )
        elif case["kind"] == "offsec":
            payload = retrieve_offsec_evidence(
                query=case["query"],
                book_ids=case["book_ids"],
                max_snippets=case["max_snippets"],
                config_or_path=(__request__.app.state.config if __request__ is not None else None),
            )
        else:
            return json.dumps({{"status": "error", "error": f"Unsupported case kind: {{case['kind']}}"}} , ensure_ascii=False)

        items = list(payload.get("items") or [])
        summary = _keyword_summary(
            items,
            case.get("expected_positive_terms") or [],
            case.get("expected_negative_terms") or [],
        )

        config_snapshot = {{}}
        if __request__ is not None:
            cfg = __request__.app.state.config
            config_snapshot = {{
                "ENABLE_CORPUS_EVIDENCE_RERANKING": getattr(cfg, "ENABLE_CORPUS_EVIDENCE_RERANKING", False),
                "CORPUS_EVIDENCE_RERANKING_MODEL": getattr(cfg, "CORPUS_EVIDENCE_RERANKING_MODEL", ""),
                "ENABLE_RAG_HYBRID_SEARCH": getattr(cfg, "ENABLE_RAG_HYBRID_SEARCH", False),
                "RAG_RERANKING_MODEL": getattr(cfg, "RAG_RERANKING_MODEL", ""),
                "TOP_K": getattr(cfg, "TOP_K", None),
                "TOP_K_RERANKER": getattr(cfg, "TOP_K_RERANKER", None),
            }}

        result = {{
            "status": "ok",
            "case": case,
            "payload": payload,
            "summary": {{
                **summary,
                "top_digest": _top_digest(items),
                "item_count": len(items),
                "top1_title": items[0].get("title") if items else None,
                "top1_section_path": items[0].get("section_path") if items else None,
                "top1_score": items[0].get("score") if items else None,
                "top1_rerank_score": items[0].get("rerank_score") if items else None,
            }},
            "config_snapshot": config_snapshot,
        }}
        return json.dumps(result, ensure_ascii=False)
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("OWUI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("OWUI_API_KEY"))
    parser.add_argument("--model", default=os.environ.get("OWUI_EVAL_MODEL", DEFAULT_MODEL))
    parser.add_argument("--tool-id", default=DEFAULT_TOOL_ID)
    parser.add_argument(
        "--output",
        default="agentic_artifacts/rerank_eval/live-baseline.json",
    )
    parser.add_argument(
        "--case-ids",
        nargs="*",
        default=list(CASES.keys()),
    )
    return parser.parse_args()


class OwuiClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ssl_context = ssl._create_unverified_context()

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        data = None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(request, context=self.ssl_context, timeout=180) as response:
            return json.load(response)


def ensure_tool(client: OwuiClient, tool_id: str) -> dict[str, Any]:
    payload = {
        "id": tool_id,
        "name": "Rerank Eval Harness",
        "meta": {
            "description": "Temporary live evaluation harness for corpus evidence reranking."
        },
        "content": build_eval_tool_content(),
    }

    try:
        existing = client.request("GET", f"/api/v1/tools/id/{tool_id}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        existing = None

    if existing:
        return client.request("POST", f"/api/v1/tools/id/{tool_id}/update", payload)
    return client.request("POST", "/api/v1/tools/create", payload)


def extract_tool_result(chat_response: dict[str, Any], tool_id: str) -> dict[str, Any]:
    sources = chat_response.get("sources") or []
    for source in sources:
        source_name = ((source.get("source") or {}).get("name") or "").strip()
        if source_name != f"{tool_id}/run_case":
            continue
        documents = source.get("document") or []
        for raw in documents:
            if not isinstance(raw, str):
                continue
            candidate = raw.strip()
            if not candidate.startswith("{"):
                return {
                    "status": "error",
                    "raw_tool_output": candidate,
                }
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "raw_tool_output": candidate,
                }
    raise RuntimeError(f"Could not find {tool_id}/run_case result in chat response")


def run_case(client: OwuiClient, model: str, tool_id: str, case_id: str) -> dict[str, Any]:
    prompt = (
        "Use the available tool and call run_case with "
        f'case_id="{case_id}". After the tool call, keep the assistant reply to one short line.'
    )
    payload = {
        "model": model,
        "stream": False,
        "tool_ids": [tool_id],
        "messages": [{"role": "user", "content": prompt}],
        "params": {"temperature": 0},
    }

    started = time.time()
    response = client.request("POST", "/api/chat/completions", payload)
    duration_s = round(time.time() - started, 3)
    try:
        tool_result = extract_tool_result(response, tool_id)
        error = None
    except Exception as exc:
        tool_result = None
        error = str(exc)
    return {
        "case_id": case_id,
        "duration_s": duration_s,
        "prompt": prompt,
        "chat_response": response,
        "tool_result": tool_result,
        "error": error,
    }


def main() -> int:
    args = parse_args()
    if not args.api_key:
        print("Missing API key. Set --api-key or OWUI_API_KEY.", file=sys.stderr)
        return 2

    requested_case_ids = []
    for case_id in args.case_ids:
        if case_id not in CASES:
            print(f"Unknown case_id: {case_id}", file=sys.stderr)
            return 2
        requested_case_ids.append(case_id)

    client = OwuiClient(args.base_url, args.api_key)
    tool = ensure_tool(client, args.tool_id)
    retrieval_config = client.request("GET", "/api/v1/retrieval/config")
    models = client.request("GET", "/api/models")

    run_results = []
    for case_id in requested_case_ids:
        run_results.append(run_case(client, args.model, args.tool_id, case_id))

    artifact = {
        "suite_name": "rerank_pilot_live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "model": args.model,
        "tool_id": args.tool_id,
        "tool_meta": tool,
        "requested_case_ids": requested_case_ids,
        "cases": {case_id: CASES[case_id] for case_id in requested_case_ids},
        "retrieval_config_snapshot": retrieval_config,
        "models_snapshot_count": len(models.get("data", models) if isinstance(models, dict) else models),
        "results": run_results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
