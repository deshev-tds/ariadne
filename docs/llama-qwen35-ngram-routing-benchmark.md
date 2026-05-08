# Qwen3.6 35B ngram-map-k Routing Benchmark

Date: 2026-05-09

Remote box: `deshev@192.168.1.117`

Backend: promoted Ariadne llama.cpp MTP build
`/home/deshev/.local/opt/ariadne-llama/current/bin/llama-server`

Model under test:
`/home/deshev/models/Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf`

Purpose: decide whether `--spec-type ngram-map-k` is useful as a practical
router lane for the daily 35B MoE model, beyond earlier short greedy tests
where ngram decode could reach very high tok/s on predictable output.

## Test Setup

Production was stopped before the clean benchmark because the resident dual
profile was causing swap pressure. Swap was cleared before restarting the test.

Both variants were run as canary servers on port `1235`:

- no-spec baseline: no speculative flags
- ngram: `--spec-type ngram-map-k`

Common server settings:

- `ctx-size`: `32768`
- KV cache: `q8_0/q8_0`
- `cache_prompt=false`
- `cache_reuse=0`
- `parallel=1`
- `batch-size=2048`
- `ubatch-size=512`
- model alias: `qwen35-nospec-bench` or `qwen35-ngram-map-k-bench`

The harness uses public LogHub samples where available:

- Apache: <https://github.com/logpai/loghub/tree/master/Apache>
- HDFS: <https://github.com/logpai/loghub/tree/master/HDFS>
- OpenSSH: <https://github.com/logpai/loghub/tree/master/OpenSSH>

If those sources are unavailable, the harness falls back to synthetic logs. In
this run, the LogHub samples were fetched and cached on the remote box.

## Workload Temperatures

The suite intentionally mixes deterministic and sampled workloads:

| Workload | Temperature | Contract |
| --- | ---: | --- |
| `apache_incident_json` | 0.0 | strict JSON |
| `apache_markdown_table` | 0.0 | markdown table |
| `hdfs_yaml_runbook` | 0.0 | YAML |
| `hdfs_timeline_summary` | 0.1 | numbered timeline |
| `openssh_security_jsonl` | 0.0 | JSONL |
| `mixed_correlation_report` | 0.2 | report |
| `router_policy_json_config` | 0.0 | strict JSON |
| `code_patch_router_bug` | 0.0 | unified diff |
| `tool_trace_compaction_yaml` | 0.0 | YAML |
| `sampled_open_chat` | 0.7 | open chat |
| `sampled_design_critique` | 0.8 | critique |

## Raw Results

Remote raw files:

- `/home/deshev/.local/state/ariadne-llama-bench/results/20260509T015311-qwen35-nospec-clean.jsonl`
- `/home/deshev/.local/state/ariadne-llama-bench/results/20260509T015311-qwen35-nospec-clean-summary.json`
- `/home/deshev/.local/state/ariadne-llama-bench/results/20260509T020242-qwen35-ngram-map-k-clean.jsonl`
- `/home/deshev/.local/state/ariadne-llama-bench/results/20260509T020242-qwen35-ngram-map-k-clean-summary.json`
- `/home/deshev/.local/state/ariadne-llama-bench/results/20260509T021026-audit-nospec-full.jsonl`
- `/home/deshev/.local/state/ariadne-llama-bench/results/20260509T021352-audit-ngram-full.jsonl`

Harness:

- `/home/deshev/open-webui/scripts/llama_patch/ngram-routing-benchmark.py`
- local repo copy: `scripts/llama_patch/ngram-routing-benchmark.py`

## Aggregate Result

| Variant | Mean wall time | Mean prompt tok/s | Mean decode tok/s | Contract OK | Mean copy overlap | Mean draft acceptance |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| no-spec | 27.11s | 621.05 | 40.37 | 6/11 | 0.060 | n/a |
| ngram-map-k | 27.16s | 618.41 | 40.54 | 6/11 | 0.062 | 31.6% |

Interpretation: on this suite, ngram is effectively flat for 35B. It is
slightly slower on wall time, slightly slower on prompt processing, and only
slightly faster on decode. The decode gain is too small to matter because these
realistic tasks are prompt-heavy.

## Per-Workload Result

| Workload | Temp | Wall no-spec | Wall ngram | Wall delta | Prompt no-spec | Prompt ngram | Decode no-spec | Decode ngram | Acceptance | Contract no/ng | Copy no/ng |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `apache_incident_json` | 0.0 | 18.27s | 18.20s | -0.4% | 681.2 | 677.0 | 41.3 | 42.5 | 47.9% | false/false | 0.148/0.148 |
| `apache_markdown_table` | 0.0 | 21.70s | 21.83s | +0.6% | 657.5 | 652.1 | 40.7 | 40.7 | n/a | true/true | 0.039/0.039 |
| `code_patch_router_bug` | 0.0 | 8.27s | 8.27s | -0.0% | 426.1 | 425.3 | 44.5 | 44.5 | n/a | false/false | 0.212/0.212 |
| `hdfs_timeline_summary` | 0.1 | 55.79s | 55.97s | +0.3% | 577.5 | 575.4 | 35.4 | 35.3 | n/a | true/true | 0.000/0.000 |
| `hdfs_yaml_runbook` | 0.0 | 41.47s | 41.24s | -0.5% | 608.7 | 605.4 | 37.1 | 39.4 | 44.8% | true/true | 0.054/0.054 |
| `mixed_correlation_report` | 0.2 | 48.74s | 48.85s | +0.2% | 589.3 | 587.6 | 36.5 | 36.5 | n/a | true/true | 0.019/0.014 |
| `openssh_security_jsonl` | 0.0 | 37.60s | 37.75s | +0.4% | 610.7 | 607.7 | 37.9 | 37.8 | n/a | false/false | 0.078/0.078 |
| `router_policy_json_config` | 0.0 | 9.52s | 9.53s | +0.1% | 659.3 | 657.5 | 44.4 | 44.4 | n/a | false/false | 0.000/0.000 |
| `sampled_design_critique` | 0.8 | 10.86s | 10.88s | +0.1% | 682.7 | 680.4 | 44.4 | 44.3 | n/a | true/true | 0.000/0.000 |
| `sampled_open_chat` | 0.7 | 30.09s | 30.16s | +0.2% | 634.4 | 632.4 | 39.6 | 39.6 | n/a | true/true | 0.000/0.026 |
| `tool_trace_compaction_yaml` | 0.0 | 15.88s | 16.12s | +1.5% | 704.2 | 701.8 | 42.3 | 41.0 | 2.1% | false/false | 0.111/0.111 |

## Quality Notes

Most deterministic failures were identical between no-spec and ngram. For
example, `apache_incident_json` produced the same output hash in both variants
and failed JSON validation in the same way: the answer hit the token limit while
inside a JSON string. That is a harness/output-budget problem, not an ngram
quality regression.

For sampled workloads, output hashes differed as expected because temperature
was non-zero. Qualitatively, ngram did not create an obvious copy/paste-heavy
failure mode. The measured copy overlap stayed tiny or unchanged:

- no-spec mean copy overlap: `0.060`
- ngram mean copy overlap: `0.062`

The one small increase in `sampled_open_chat` was still low (`0.026`) and did
not look like pathological copying. The answer style differed, but not in a way
that points specifically at ngram.

## Conclusion

Do not make `ngram-map-k` a default 35B production lane from this evidence.

For realistic 35B MoE router-shaped tasks, it is basically a wash:

- wall time: no practical improvement
- prompt processing: slightly worse
- decode: only around +0.4% mean improvement
- structured validity: unchanged
- copy/paste risk: unchanged to tiny increase

The earlier short greedy result remains real, but it appears scoped to highly
predictable, low-entropy, output-heavy completions. This benchmark was dominated
by long prompt processing and analysis-style outputs, where ngram has little
room to help.

Router recommendation:

- daily 35B lane: clean no-spec multimodal model
- 35B ngram lane: keep experimental, not resident by default
- enable ngram only for explicit low-entropy, output-heavy tasks after another
  targeted test with larger `max_tokens`
- improve JSON/YAML validity with schema prompting, constrained decoding, or
  repair/validation, not with ngram speculation

