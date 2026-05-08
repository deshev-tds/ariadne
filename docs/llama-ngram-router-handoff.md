# Handoff: ngram Speculation Findings And Ariadne Router Ideas

Date: 2026-05-09

Repo: `/Users/damyandeshev/projects/ariadne`

Remote box: `deshev@192.168.1.117`

Related report:

- `/Users/damyandeshev/projects/ariadne/docs/llama-qwen35-ngram-routing-benchmark.md`
- Remote raw benchmark files under `/home/deshev/.local/state/ariadne-llama-bench/results/`
- Harness: `/Users/damyandeshev/projects/ariadne/scripts/llama_patch/ngram-routing-benchmark.py`

## Current Production Shape

The local llama backend currently runs as one router server on port `1234` with
the `dual` profile:

- `Qwen3.6-27B-Dense-MTP-Q6_K`: fast text-only dense lane with MTP
- `Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL`: daily 35B MoE lane, multimodal with mmproj,
  no MTP by default
- `MODELS_MAX=2`
- `CTX=131072`
- KV cache: `q8_0/q8_0`
- promoted backend:
  `/home/deshev/.local/opt/ariadne-llama/current/bin/llama-server`

Important invariant: do not route vision to the 27B lane. 35B is the
multimodal resident lane.

## Core Finding

`ngram` speculation is not a general structured-output accelerator. It is a
copy/continuation accelerator.

The useful predictor is not:

```text
output_format == json
```

The useful predictor is:

```text
expected_output_copies_or_transforms_prompt_context == true
```

Our realistic 35B MoE benchmark showed that `--spec-type ngram-map-k` is
basically flat for analysis-style router workloads:

| Variant | Mean wall time | Mean prompt tok/s | Mean decode tok/s | Contract OK | Mean copy overlap | Mean draft acceptance |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| no-spec | 27.11s | 621.05 | 40.37 | 6/11 | 0.060 | n/a |
| ngram-map-k | 27.16s | 618.41 | 40.54 | 6/11 | 0.062 | 31.6% |

Interpretation:

- No meaningful wall-time win.
- Prompt processing got slightly worse.
- Decode improved only ~0.4% on mean.
- Contract validity did not improve.
- Copy/paste risk did not meaningfully change.
- Deterministic failures were mostly identical between no-spec and ngram.

This does not contradict earlier short greedy tests where ngram reached very
high decode rates. Those tests were output-heavy, low-entropy, and repetitive.
The realistic benchmark was prompt-heavy and analysis-oriented.

## External Signals Checked

These sources treat ngram/prompt-lookup speculation as real, but specialized:

- vLLM official n-gram speculation:
  <https://docs.vllm.ai/en/stable/features/speculative_decoding/n_gram/>
- Hugging Face assisted decoding / prompt lookup:
  <https://huggingface.co/docs/transformers/v5.0.0/assisted_decoding>
- TensorRT-LLM n-gram performance analysis:
  <https://nvidia.github.io/TensorRT-LLM/blogs/tech_blog/blog7_NGram_performance_Analysis_And_Auto_Enablement.html>
- TensorRT-LLM speculative decoding docs:
  <https://nvidia.github.io/TensorRT-LLM/legacy/advanced/speculative-decoding.html>
- llama.cpp speculative decoding docs:
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md>
- Prompt Lookup Decoding reference implementation:
  <https://github.com/apoorvumang/prompt-lookup-decoding>
- SAM-Decoding / suffix automaton prompt-overlap work:
  <https://github.com/hyx1999/sam-decoding>
- SuffixDecoding paper:
  <https://arxiv.org/abs/2411.04975>

External consensus:

- Prompt lookup / ngram helps when output overlaps heavily with prompt,
  generated history, or a retrieval corpus.
- Common positive examples: summarization with quotation, document QA, code
  editing, multi-turn repeated workflows, translation/localization, structured
  transforms.
- Generic chat and high-entropy reasoning are weak targets.
- Multi-turn workflows can improve because repeated patterns accumulate.
- Serving systems that expose this feature still rely on heuristics or targeted
  enablement, not blind always-on routing.

## Ariadne Router Policy Sketch

The router should produce a score, not a binary magical answer:

```text
ngram_score = 0.0 .. 1.0
```

Recommended routing:

```text
score >= 0.65  -> use ngram candidate lane
0.45..0.65     -> use ngram only under explicit structured/transform mode or low load
score < 0.45   -> no ngram
```

Never use ngram for:

- vision requests
- active 27B MTP lane unless explicitly tested
- high-temperature creative chat
- open-ended analysis/reasoning where output is mostly new prose
- model-switching inside an existing chat unless the conversation policy allows it

Strong positive task intents:

- `code_patch`
- `code_refactor`
- `apply_diff`
- `quote_extract`
- `evidence_jsonl`
- `log_normalize`
- `config_convert`
- `schema_migration`
- `tool_trace_generation`
- `templated_runbook`
- `translation_or_localization_with_placeholders`

Weak or negative task intents:

- `open_chat`
- `creative_writing`
- `brainstorm`
- `critique`
- `reasoning`
- `explain`
- `summarize_in_own_words`
- `vision`

## Cheap Router Signals

The scorer must be cheap enough to run synchronously before backend selection.
Use lexical/regex features, not another LLM call.

Suggested feature extraction:

```python
features = {
    "temperature": request.temperature,
    "max_tokens": request.max_tokens,
    "has_image": bool(request.images),
    "output_format": request.output_format,
    "code_block_ratio": code_block_chars / prompt_chars,
    "structured_text_ratio": structured_chars / prompt_chars,
    "repeated_line_ratio": repeated_lines / total_lines,
    "repeated_shingle_ratio": repeated_5grams / total_5grams,
    "path_identifier_density": count_paths_identifiers(prompt) / prompt_tokens,
    "log_line_density": count_log_like_lines(prompt) / total_lines,
    "quote_or_extract_intent": keyword_or_intent_match(prompt),
    "transform_intent": keyword_or_intent_match(prompt),
    "analysis_intent": keyword_or_intent_match(prompt),
    "conversation_copy_profile": rolling_chat_profile.copy_score,
}
```

A first-pass scoring function can be rule-based:

```python
score = 0.0

if has_image:
    return 0.0

if task in {"code_patch", "code_refactor", "quote_extract", "log_normalize", "config_convert"}:
    score += 0.35

if output_format in {"jsonl", "diff", "table", "yaml", "json"}:
    score += 0.10

if temperature <= 0.2:
    score += 0.15
elif temperature > 0.5:
    score -= 0.25

if max_tokens >= 512:
    score += 0.10
if max_tokens >= 1000:
    score += 0.10

if repeated_shingle_ratio > 0.08:
    score += 0.15

if repeated_line_ratio > 0.10:
    score += 0.10

if code_block_ratio > 0.25 or structured_text_ratio > 0.35:
    score += 0.10

if task in {"open_chat", "creative_writing", "reasoning", "analysis", "critique"}:
    score -= 0.35

score = max(0.0, min(1.0, score))
```

Important nuance: `output_format=json` should be only a small positive signal.
The benchmark showed that JSON analysis can be ngram-flat. JSONL extraction
that preserves fields from the input is much more promising.

## Better Workloads To Test Next

The benchmark suite should be extended with output-heavy copy/transform tasks,
because the first realistic suite was too analysis-heavy to validate the
positive side of ngram.

Candidate test workloads:

1. Code edit:
   - prompt: full source file + small requested change
   - output: complete patch or complete rewritten function/file
   - metrics: wall, prompt tok/s, decode tok/s, acceptance, patch validity,
     unchanged-context copy ratio

2. Exact quote extraction:
   - prompt: long document chunks
   - output: JSONL evidence records with exact quotes
   - metrics: JSONL validity, quote exactness, acceptance, copy overlap

3. Log normalization:
   - prompt: raw Apache/HDFS/OpenSSH logs
   - output: JSONL preserving `raw_line`, timestamp, service, severity, message
   - metrics: JSONL validity, raw-line preservation, acceptance

4. Config migration:
   - prompt: large INI/TOML/YAML config
   - output: target schema preserving paths, IDs, ports, model names
   - metrics: parse validity, preserved key/value count, acceptance

5. Tool trace reconstruction:
   - prompt: repeated agent trace blocks
   - output: normalized timeline or compact trace records
   - metrics: repeated field preservation, acceptance, copy overlap

6. Localization:
   - prompt: UI string catalog with placeholders and product names
   - output: translated catalog preserving placeholders
   - metrics: placeholder preservation, parse validity, acceptance

## Implementation Plan For Ariadne

1. Add an offline scorer first.
   - It should run over benchmark workloads and write `predicted_ngram_score`
     into each result row.
   - Compare score against actual wall delta, decode delta, acceptance, and
     validity.

2. Tune thresholds on Ariadne-shaped data.
   - Do not tune from Reddit anecdotes or one synthetic prompt.
   - Use local benchmark rows and real Ariadne request samples where safe.

3. Add router metadata to request handling.
   - Record `route_reason`, `ngram_score`, `model_id`, `spec_mode`, and
     `expected_copy_transform`.
   - Store these with telemetry so bad routing decisions can be inspected.

4. Keep ngram non-resident until proven useful.
   - Current daily 35B lane should remain clean no-spec.
   - Add an explicit `35b-ngram-copy-transform` profile only after targeted
     workloads show wall-time wins.

5. Do not let ngram override conversation continuity.
   - If a chat already has an active model/lane, prefer staying on it unless
     the user explicitly requests a structured transform or vision/capability
     forces a lane change.

## Current Recommendation

For production/default Ariadne routing:

```text
vision -> 35B multimodal no-spec
fast text, no vision -> 27B dense MTP
general 35B chat/analysis -> 35B no-spec
uncensored/special -> Hauhau lane if explicitly selected
copy/transform long low-temp task -> consider ngram candidate, after scorer test
```

Do not make ngram globally enabled just because a request asks for JSON,
markdown tables, or YAML. Enable it only when the expected output is likely to
reuse long spans from the prompt or conversation context.

