#!/usr/bin/env python3
"""Benchmark routing-shaped workloads against llama-server chat endpoints.

The suite is intentionally workload-oriented instead of prompt-only. It mixes
public log samples, structured extraction, runbook/table/code-patch tasks, and
sampled open chat. The goal is to evaluate whether n-gram speculative decoding
is a specialized structured-output lane or a safe general chat default.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


LOGHUB_SOURCES = {
    "apache": "https://raw.githubusercontent.com/logpai/loghub/master/Apache/Apache_2k.log",
    "hdfs": "https://raw.githubusercontent.com/logpai/loghub/master/HDFS/HDFS_2k.log",
    "openssh": "https://raw.githubusercontent.com/logpai/loghub/master/OpenSSH/OpenSSH_2k.log",
}


@dataclass(frozen=True)
class Workload:
    name: str
    category: str
    output_contract: str
    temperature: float
    max_tokens: int
    prompt: str


def request_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, path: pathlib.Path, timeout: float) -> str:
    if path.exists() and path.stat().st_size > 0:
        return path.read_text(errors="replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "ariadne-ngram-routing-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", "replace")
    path.write_text(text)
    return text


def fallback_logs(kind: str) -> list[str]:
    if kind == "apache":
        return [
            "[Fri Dec 01 10:00:01.100000 2026] [proxy:error] [pid 1201] AH01114: HTTP: failed to make connection to backend: api.local",
            "[Fri Dec 01 10:00:02.130000 2026] [authz_core:error] [pid 1205] AH01630: client denied by server configuration: /srv/app/admin",
            "[Fri Dec 01 10:00:03.270000 2026] [ssl:warn] [pid 1207] AH01909: RSA certificate configured for api.local does NOT include an ID which matches the server name",
        ] * 500
    if kind == "openssh":
        return [
            "Dec  1 10:00:01 edge sshd[2210]: Failed password for invalid user deploy from 203.0.113.7 port 48301 ssh2",
            "Dec  1 10:00:02 edge sshd[2212]: Accepted publickey for deshev from 192.168.1.12 port 51422 ssh2",
            "Dec  1 10:00:03 edge sshd[2215]: Connection closed by authenticating user root 198.51.100.8 port 39210 [preauth]",
        ] * 500
    return [
        "081109 203615 148 INFO dfs.DataNode$DataXceiver: Receiving block blk_38865049064139660 src: /10.251.123.1:3456 dest: /10.251.123.2:50010",
        "081109 203616 149 WARN dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: Redundant addStoredBlock request received for blk_38865049064139660",
        "081109 203617 150 INFO dfs.DataNode: PacketResponder 1 for block blk_38865049064139660 terminating",
    ] * 500


def load_logs(cache_dir: pathlib.Path, timeout: float) -> dict[str, list[str]]:
    logs: dict[str, list[str]] = {}
    for name, url in LOGHUB_SOURCES.items():
        try:
            text = fetch_text(url, cache_dir / f"{name}.log", timeout)
            lines = [line.rstrip() for line in text.splitlines() if line.strip()]
            logs[name] = lines or fallback_logs(name)
        except (OSError, urllib.error.URLError, TimeoutError):
            logs[name] = fallback_logs(name)
    return logs


def sample_lines(lines: list[str], *, start: int, count: int) -> str:
    if not lines:
        return ""
    if len(lines) < count:
        repeated = (lines * ((count // len(lines)) + 1))[:count]
        return "\n".join(repeated)
    start = min(start, max(0, len(lines) - count))
    return "\n".join(lines[start : start + count])


def interleave(groups: list[list[str]], count_each: int) -> str:
    rng = random.Random(20260509)
    selected: list[str] = []
    for idx, group in enumerate(groups):
        offset = (idx * 137) % max(1, len(group))
        selected.extend(sample_lines(group, start=offset, count=count_each).splitlines())
    rng.shuffle(selected)
    return "\n".join(selected)


def build_workloads(logs: dict[str, list[str]]) -> list[Workload]:
    apache = logs["apache"]
    hdfs = logs["hdfs"]
    openssh = logs["openssh"]

    schema = """Return only valid JSON matching:
{
  "summary": string,
  "severity": "low" | "medium" | "high",
  "top_patterns": [{"pattern": string, "count_estimate": integer, "example": string}],
  "recommended_actions": [string]
}
"""

    yaml_contract = """Return only YAML with keys:
incident:
  title: string
  suspected_root_cause: string
  evidence: list
runbook:
  - step: string
    command_or_check: string
    expected_result: string
rollback:
  safe: boolean
  notes: string
"""

    code = r'''
class ModelRouter:
    def __init__(self, lanes):
        self.lanes = lanes
        self.active_chat_model = {}

    async def dispatch(self, chat_id, request):
        if request.image:
            model = "Qwen3.6-27B-Dense-MTP-Q6_K"
        elif request.output_format == "json":
            model = "Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL"
        elif chat_id in self.active_chat_model:
            model = self.active_chat_model[chat_id]
        else:
            model = "Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL"
        self.active_chat_model[chat_id] = model
        return await self.lanes[model].complete(request)
'''

    tool_trace = "\n".join(
        f"{i:03d} tool=search status={'ok' if i % 7 else 'retry'} latency_ms={30 + (i * 17) % 900} "
        f"query='router policy qwen lane {i % 11}' result_count={(i * 3) % 19}"
        for i in range(1, 220)
    )

    config_constraints = "\n".join(
        f"- route_{i:02d}: if signal={signal}, output={fmt}, temperature<={temp}, prefer={lane}"
        for i, (signal, fmt, temp, lane) in enumerate(
            [
                ("image", "any", "1.0", "35b_vision"),
                ("json_schema", "json", "0.2", "35b_ngram"),
                ("yaml_runbook", "yaml", "0.2", "35b_ngram"),
                ("open_chat", "markdown", "0.8", "35b_clean"),
                ("uncensored", "markdown", "0.9", "hauhau"),
                ("long_prefill", "any", "1.0", "prefill_budget"),
            ]
            * 10,
            1,
        )
    )

    return [
        Workload(
            "apache_incident_json",
            "structured_logs",
            "strict_json",
            0.0,
            220,
            f"{schema}\nAnalyze these Apache error logs:\n\n{sample_lines(apache, start=0, count=220)}",
        ),
        Workload(
            "apache_markdown_table",
            "structured_logs",
            "markdown_table",
            0.0,
            240,
            "Create a compact markdown table with columns pattern, severity, evidence, action. "
            "Do not include prose outside the table.\n\n"
            + sample_lines(apache, start=700, count=260),
        ),
        Workload(
            "hdfs_yaml_runbook",
            "structured_logs",
            "yaml",
            0.0,
            260,
            f"{yaml_contract}\nHDFS logs:\n\n{sample_lines(hdfs, start=100, count=260)}",
        ),
        Workload(
            "hdfs_timeline_summary",
            "log_reasoning",
            "numbered_timeline",
            0.1,
            280,
            "Build a numbered incident timeline. Use exactly 8 numbered items and then 3 likely causes.\n\n"
            + sample_lines(hdfs, start=850, count=340),
        ),
        Workload(
            "openssh_security_jsonl",
            "security_logs",
            "jsonl",
            0.0,
            260,
            "Extract suspicious SSH events. Return JSONL only; each line has keys event_type, user, source, reason.\n\n"
            + sample_lines(openssh, start=0, count=360),
        ),
        Workload(
            "mixed_correlation_report",
            "mixed_logs",
            "brief_report",
            0.2,
            320,
            "Correlate these interleaved logs from web, auth, and storage systems. "
            "Return sections: Observed, Hypothesis, Counterevidence, Next checks.\n\n"
            + interleave([apache, hdfs, openssh], 140),
        ),
        Workload(
            "router_policy_json_config",
            "router_policy",
            "strict_json",
            0.0,
            300,
            "Convert the constraints into a router policy JSON object. "
            "Use keys lanes, hard_constraints, soft_preferences, prefill_budget, known_bad.\n\n"
            + config_constraints,
        ),
        Workload(
            "code_patch_router_bug",
            "code_patch",
            "unified_diff",
            0.0,
            340,
            "Return a unified diff only. Fix the routing bug: images must never go to 27B MTP; "
            "sticky chat model must be respected unless capability constraints force a change; "
            "JSON structured output may choose ngram only when temperature <= 0.2.\n\n"
            + code,
        ),
        Workload(
            "tool_trace_compaction_yaml",
            "tool_trace",
            "yaml",
            0.0,
            280,
            "Compress this agent tool trace into YAML with keys repeated_queries, retries, slow_calls, and router_lessons.\n\n"
            + tool_trace,
        ),
        Workload(
            "sampled_open_chat",
            "open_chat",
            "freeform",
            0.7,
            360,
            "You are advising an engineer designing a local LLM router. "
            "Explain the tradeoffs of allowing concurrent 27B and 35B requests. "
            "Use the logs as noisy background evidence, but do not overfit to them.\n\n"
            + interleave([apache, hdfs, openssh], 80),
        ),
        Workload(
            "sampled_design_critique",
            "open_chat",
            "freeform",
            0.8,
            360,
            "Critique this router user story. Keep nuance, mention failure modes, and propose an implementation path.\n\n"
            "User story:\n"
            "- If image, use 35B vision.\n"
            "- If structured JSON/YAML/table, maybe use n-gram.\n"
            "- If chat, avoid n-gram unless deterministic.\n"
            "- If uncensored, use Hauhau.\n"
            "- If long prompt, do not overlap cold prefill.\n"
            "- If same chat, do not switch model casually.\n\n"
            + config_constraints,
        ),
    ]


def extract_text(response: dict[str, Any]) -> str:
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return str(message.get("content") or "")


def contract_score(contract: str, text: str) -> dict[str, Any]:
    stripped = text.strip()
    result: dict[str, Any] = {"contract_ok": None, "contract_note": ""}
    if contract == "strict_json":
        try:
            json.loads(stripped)
            result.update(contract_ok=True, contract_note="valid_json")
        except json.JSONDecodeError as exc:
            result.update(contract_ok=False, contract_note=f"invalid_json:{exc.msg}")
    elif contract == "jsonl":
        lines = [line for line in stripped.splitlines() if line.strip()]
        ok = bool(lines)
        for line in lines:
            try:
                json.loads(line)
            except json.JSONDecodeError:
                ok = False
                break
        result.update(contract_ok=ok, contract_note="jsonl_parse")
    elif contract == "yaml":
        result.update(contract_ok=(":" in stripped and not stripped.startswith("```")), contract_note="yaml_shape_heuristic")
    elif contract == "markdown_table":
        result.update(contract_ok=("|" in stripped and "---" in stripped), contract_note="markdown_table_heuristic")
    elif contract == "unified_diff":
        result.update(contract_ok=("---" in stripped and "+++" in stripped and "@@" in stripped), contract_note="diff_heuristic")
    else:
        result.update(contract_ok=True, contract_note="not_structured")
    return result


def word_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[A-Za-z0-9_./:-]+", text.lower())
    if len(words) < n:
        return set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def copy_overlap_score(prompt: str, output: str) -> dict[str, Any]:
    output_words = re.findall(r"[A-Za-z0-9_./:-]+", output.lower())
    prompt_5 = word_ngrams(prompt, 5)
    output_5 = word_ngrams(output, 5)
    if not output_words or not output_5:
        return {
            "output_words": len(output_words),
            "copy_5gram_overlap": 0.0,
            "copy_5gram_hits": 0,
            "copy_paste_risk": "low",
        }
    hits = len(prompt_5 & output_5)
    overlap = hits / len(output_5)
    if overlap >= 0.40:
        risk = "high"
    elif overlap >= 0.20:
        risk = "medium"
    else:
        risk = "low"
    return {
        "output_words": len(output_words),
        "copy_5gram_overlap": overlap,
        "copy_5gram_hits": hits,
        "copy_paste_risk": risk,
    }


def run_workload(
    endpoint: str,
    model: str,
    variant: str,
    workload: Workload,
    timeout: float,
    repeat: int,
    include_output_text: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    for iteration in range(1, repeat + 1):
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": workload.prompt}],
            "temperature": workload.temperature,
            "max_tokens": workload.max_tokens,
            "stream": False,
            "cache_prompt": False,
        }
        started = time.perf_counter()
        response = request_json(url, payload, timeout)
        wall_s = time.perf_counter() - started
        timings = response.get("timings") or {}
        usage = response.get("usage") or {}
        output = extract_text(response)
        score = contract_score(workload.output_contract, output)
        copy_score = copy_overlap_score(workload.prompt, output)
        row = {
            "variant": variant,
            "model": model,
            "workload": workload.name,
            "category": workload.category,
            "output_contract": workload.output_contract,
            "temperature": workload.temperature,
            "iteration": iteration,
            "wall_s": wall_s,
            "prompt_tokens": timings.get("prompt_n") or usage.get("prompt_tokens"),
            "prompt_ms": timings.get("prompt_ms"),
            "prompt_tok_s": timings.get("prompt_per_second"),
            "decode_tokens": timings.get("predicted_n") or usage.get("completion_tokens"),
            "decode_ms": timings.get("predicted_ms"),
            "decode_tok_s": timings.get("predicted_per_second"),
            "cache_n": timings.get("cache_n"),
            "draft_n": timings.get("draft_n"),
            "draft_n_accepted": timings.get("draft_n_accepted"),
            "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_preview": re.sub(r"\s+", " ", output)[:220],
            **score,
            **copy_score,
        }
        if include_output_text:
            row["output_text"] = output
        rows.append(row)
    return rows


def mean(values: list[float]) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float))]
    if not clean:
        return None
    return statistics.fmean(clean)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_workload: dict[str, list[dict[str, Any]]] = {}
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_workload.setdefault(row["workload"], []).append(row)
        by_category.setdefault(row["category"], []).append(row)

    def summarize_group(group: list[dict[str, Any]]) -> dict[str, Any]:
        contract_values = [row["contract_ok"] for row in group if row["contract_ok"] is not None]
        return {
            "runs": len(group),
            "prompt_tokens_mean": mean([row["prompt_tokens"] for row in group]),
            "wall_s_mean": mean([row["wall_s"] for row in group]),
            "prompt_tok_s_mean": mean([row["prompt_tok_s"] for row in group]),
            "decode_tok_s_mean": mean([row["decode_tok_s"] for row in group]),
            "draft_acceptance_mean": mean(
                [
                    row["draft_n_accepted"] / row["draft_n"]
                    for row in group
                    if isinstance(row.get("draft_n"), int) and row["draft_n"]
                ]
            ),
            "contract_ok_rate": (sum(1 for value in contract_values if value) / len(contract_values)) if contract_values else None,
            "copy_5gram_overlap_mean": mean([row["copy_5gram_overlap"] for row in group]),
            "copy_paste_high_risk_count": sum(1 for row in group if row.get("copy_paste_risk") == "high"),
        }

    return {
        "overall": summarize_group(rows),
        "by_workload": {name: summarize_group(group) for name, group in sorted(by_workload.items())},
        "by_category": {name: summarize_group(group) for name, group in sorted(by_category.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:1234")
    parser.add_argument("--model", required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--cache-dir", default=str(pathlib.Path.home() / ".local/state/ariadne-llama-bench/fixtures"))
    parser.add_argument("--output-dir", default=str(pathlib.Path.home() / ".local/state/ariadne-llama-bench/results"))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--workload", action="append", help="Run only matching workload name(s). Can be passed multiple times.")
    parser.add_argument("--include-output-text", action="store_true", help="Persist full model outputs in JSONL rows for qualitative audits.")
    args = parser.parse_args()

    cache_dir = pathlib.Path(args.cache_dir)
    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logs = load_logs(cache_dir, args.timeout)
    workloads = build_workloads(logs)
    if args.workload:
        wanted = set(args.workload)
        workloads = [workload for workload in workloads if workload.name in wanted]
    if not workloads:
        raise SystemExit("No workloads selected")

    stamp = time.strftime("%Y%m%dT%H%M%S")
    jsonl_path = output_dir / f"{stamp}-{args.variant}.jsonl"
    summary_path = output_dir / f"{stamp}-{args.variant}-summary.json"

    rows: list[dict[str, Any]] = []
    print(json.dumps({"event": "suite_start", "variant": args.variant, "workloads": [w.name for w in workloads]}), flush=True)
    with jsonl_path.open("w", encoding="utf-8") as out:
        for workload in workloads:
            print(json.dumps({"event": "workload_start", "variant": args.variant, "workload": workload.name}), flush=True)
            for row in run_workload(args.endpoint, args.model, args.variant, workload, args.timeout, args.repeat, args.include_output_text):
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                out.flush()
                print(
                    json.dumps(
                        {
                            "event": "workload_done",
                            **{
                                k: row[k]
                                for k in (
                                    "variant",
                                    "workload",
                                    "wall_s",
                                    "prompt_tokens",
                                    "prompt_tok_s",
                                    "decode_tok_s",
                                    "draft_n",
                                    "draft_n_accepted",
                                    "contract_ok",
                                    "copy_5gram_overlap",
                                    "copy_paste_risk",
                                )
                            },
                        }
                    ),
                    flush=True,
                )

    summary = summarize(rows)
    summary["metadata"] = {
        "variant": args.variant,
        "model": args.model,
        "endpoint": args.endpoint,
        "repeat": args.repeat,
        "sources": LOGHUB_SOURCES,
        "jsonl_path": str(jsonl_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    print(f"jsonl={jsonl_path}")
    print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
