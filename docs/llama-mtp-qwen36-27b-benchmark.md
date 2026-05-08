# Qwen3.6 27B MTP benchmark on Strix Halo llama.cpp stack

Date: 2026-05-08
Remote host: `192.168.1.117`
Production toolbox: `llama-rocm-7.2.2`
Production endpoint: `0.0.0.0:1234`
Production status after tests: healthy, no test server left on `127.0.0.1:1241`

## Scope

Goal: evaluate whether speculative decoding via MTP is practical in the current llama.cpp stack before considering a vLLM migration.

The production `/usr/local/bin/llama-server` build is still `9025 (eff06702b)` and does not expose `--spec-type mtp`. MTP tests used a separate llama.cpp PR build:

- Source: `/home/deshev/.local/src/llama.cpp-pr22673-ariadne`
- Build: `build-mtp/bin/llama-server`
- Version: `9032 (5d5f1b46e)`
- Branch: PR 22673, "Adding support for Multi-Token Prediction (MTP)"
- Ariadne server patch applied cleanly:
  - `/home/deshev/open-webui/scripts/llama_patch/patches/0001-server-allow-streamed-tool-calls-with-content-logprobs.patch`
- Runtime wrapper required for this local build:
  - `LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm-7.2.2/lib:/home/deshev/.local/src/llama.cpp-pr22673-ariadne/build-mtp/bin`

## Downloaded test models

From `froggeric/Qwen3.6-27B-MTP-GGUF`:

- `/home/deshev/models/Qwen3.6-27B-Q8_0-mtp.gguf`
  - Size: `29,047,086,752` bytes
  - Disk: `28G`
- `/home/deshev/models/Qwen3.6-27B-Q6_K-mtp.gguf`
  - Size: `22,533,852,832` bytes
  - Disk: `21G`

Both downloads completed cleanly with no `.aria2` sidecar left.

The abandoned 35B MTP partial download was stopped and removed.

## Test server flags

The test server used port `127.0.0.1:1241` and production-like BEAST settings:

```bash
--no-mmap -ngl 999 -fa on -c 131072 -b 2048 --ubatch-size 512 \
--threads 32 --threads-batch 32 \
--cache-type-k q8_0 --cache-type-v q8_0 \
--host 127.0.0.1 --port 1241 \
--no-cache-prompt --cache-reuse 256 --slot-prompt-similarity 0.10 \
--parallel 1 --metrics
```

MTP candidate flags:

```bash
--spec-type mtp --spec-draft-n-max 2 --spec-draft-n-min 1
```

Q8 tuning showed `nmax=4` best for a short greedy prompt; Q6 tuning showed `nmax=2` best.

## Bench prompts

All runs used `/completion`, `stream=false`, `cache_prompt=false`, `ignore_eos=true`, `top_k=40`, `top_p=0.95`, `min_p=0.05`.

1. `story_greedy_512`: short story prompt, `temperature=0.0`, `n_predict=512`
2. `story_sampled_512`: incident-report prompt, `temperature=0.7`, `n_predict=512`
3. `long_context_greedy_384`: 32,308-token synthetic operational context, `temperature=0.0`, `n_predict=384`

## Results

Decode throughput is `timings.predicted_per_second` from llama-server responses.

| Quant | Spec | Test | Prompt tok/s | Decode tok/s | Wall |
| --- | --- | --- | ---: | ---: | ---: |
| Q8_0 | none | story_greedy_512 | 61.87 | 7.63 | 67.58s |
| Q8_0 | none | story_sampled_512 | 65.39 | 7.61 | 67.73s |
| Q8_0 | none | long_context_greedy_384 | 181.52 | 6.71 | 235.31s |
| Q8_0 | MTP nmax4 | story_greedy_512 | 56.65 | 15.04 | 34.48s |
| Q8_0 | MTP nmax4 | story_sampled_512 | 59.96 | 15.26 | 34.05s |
| Q8_0 | MTP nmax4 | long_context_greedy_384 | 164.07 | 14.73 | 223.08s |
| Q6_K | none | story_greedy_512 | 27.06 | 9.34 | 55.73s |
| Q6_K | none | story_sampled_512 | 28.54 | 9.33 | 55.82s |
| Q6_K | none | long_context_greedy_384 | 248.02 | 7.98 | 178.50s |
| Q6_K | MTP nmax2 | story_greedy_512 | 25.17 | 17.52 | 30.22s |
| Q6_K | MTP nmax2 | story_sampled_512 | 26.41 | 15.40 | 34.29s |
| Q6_K | MTP nmax2 | long_context_greedy_384 | 222.04 | 14.85 | 171.47s |

## Tuning sweeps

Short greedy prompt, 256 generated tokens.

Q8_0 MTP:

| nmax | Decode tok/s | Acceptance |
| ---: | ---: | ---: |
| 1 | 12.34 | 83.45% |
| 2 | 14.71 | 66.97% |
| 4 | 15.19 | 45.06% |
| 8 | 12.42 | 26.37% |
| 16 default | 6.99 | 13.34% |

Q6_K MTP:

| nmax | Decode tok/s | Acceptance |
| ---: | ---: | ---: |
| 1 | 15.07 | 86.77% |
| 2 | 18.03 | 71.43% |
| 4 | 16.81 | 47.99% |
| 8 | 15.19 | 28.90% |

## Interpretation

Q8 is not useless, but it is probably overkill for this daily local inference path. The llama.cpp docs say quantization reduces model size and can speed inference while possibly introducing accuracy loss, and their quantization table shows Q6_K substantially smaller than Q8_0. The old llama.cpp perplexity scoreboard reports Q6_K only `0.16%` delta from fp16 on Llama 2 70B, while being much smaller than fp16.

For this Strix Halo box and this model:

- Q6_K + MTP nmax2 is the best practical candidate.
- It gives about `1.88x` over Q6_K no-spec on greedy short generation.
- It gives about `2.30x` over Q8_0 no-spec on greedy short generation.
- Long-context total wall time improves mostly from Q6 prompt processing and MTP decode combined: Q8 no-spec `235.31s`, Q8 MTP `223.08s`, Q6 no-spec `178.50s`, Q6 MTP `171.47s`.
- MTP helps decode strongly, but does not remove prompt processing cost. For large prompts, quant choice matters a lot.

## Caveats

- This is a PR branch, not production upstream llama.cpp main.
- Test server shutdown sometimes emitted `Aborted (core dumped)` after the benchmark completed and the process was killed. Production `1234` was not affected, but this is a reason not to blindly replace `/usr/local/bin/llama-server`.
- MTP default `--spec-draft-n-max 16` is actively bad here.
- The MTP GGUF is not the user's existing Hauhau uncensored 27B model; it is the available Qwen3.6 27B MTP GGUF from Hugging Face.

## Recommended next step

Do not switch production directly. Add a separate `run_llama.sh` profile for Q6_K MTP on the PR build, with a separate model alias and a fast rollback to the current BEAST profile.

Candidate production-ish flags:

```bash
LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm-7.2.2/lib:/home/deshev/.local/src/llama.cpp-pr22673-ariadne/build-mtp/bin \
/home/deshev/.local/src/llama.cpp-pr22673-ariadne/build-mtp/bin/llama-server \
  --no-mmap -ngl 999 -fa on -c 131072 -b 2048 --ubatch-size 512 \
  --threads 32 --threads-batch 32 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --host 0.0.0.0 --port 1234 \
  --model /home/deshev/models/Qwen3.6-27B-Q6_K-mtp.gguf \
  --alias Qwen3.6-27B-Q6_K-mtp \
  --no-cache-prompt --cache-reuse 256 --slot-prompt-similarity 0.10 \
  --parallel 1 --metrics \
  --spec-type mtp --spec-draft-n-max 2 --spec-draft-n-min 1
```

## 35B A3B MoE follow-up

The user's daily driver is:

- `/home/deshev/models/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf`

An MTP-capable clean GGUF was downloaded from `havenoammo/Qwen3.6-35B-A3B-MTP-GGUF`:

- `/home/deshev/models/Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf`
- Size: `39,348,646,304` bytes
- Disk: `37G`

The clean MTP GGUF was also tested with `--spec-type none`, to separate model/quant differences from actual speculative decoding.

### 35B baseline comparison

| Model | Spec | Test | Prompt time | Decode tok/s | Wall |
| --- | --- | --- | ---: | ---: | ---: |
| Hauhau Q8_K_P | none | story_greedy_512 | 0.158s | 40.90 | 12.70s |
| Hauhau Q8_K_P | none | story_sampled_512 | 0.161s | 40.90 | 12.70s |
| Hauhau Q8_K_P | none | long_context_greedy_384 | 58.28s | 31.62 | 70.48s |
| Clean MTP UD Q8_K_XL | none | story_greedy_512 | 0.142s | 44.60 | 11.65s |
| Clean MTP UD Q8_K_XL | none | story_sampled_512 | 0.146s | 44.62 | 11.64s |
| Clean MTP UD Q8_K_XL | none | long_context_greedy_384 | 54.80s | 33.95 | 66.16s |

Interpretation: the clean Qwen3.6 35B A3B quant is about 9% faster than the Hauhau Q8_K_P daily file in these tests, even without speculative decoding. Hauhau remains useful as an uncensored/special-purpose lane, but the clean model is a plausible better default daily lane if quality is acceptable.

### 35B MTP sweep

Short greedy prompt, 256 generated tokens, clean MTP UD Q8_K_XL.

| nmax | Decode tok/s | Acceptance |
| ---: | ---: | ---: |
| none | 44.03-44.60 | n/a |
| 1 | 37.89 | 30.26% |
| 2 | 28.59 | 9.91% |
| 4 | 23.25 | 8.42% |
| 8 | 12.84 | 2.84% |
| 16 | 7.79 | 1.85% |

Interpretation: MTP is net negative for this 35B A3B MoE model on this build. The draft head has poor acceptance and adds enough overhead to fall below no-spec even at `nmax=1`.

### 35B n-gram speculative

Clean MTP UD Q8_K_XL, no external draft model.

| Method | Test | Decode tok/s | Acceptance / behavior |
| --- | --- | ---: | --- |
| none | story_greedy_512 | 44.60 | baseline |
| `ngram-map-k n=4 m=16` | story_greedy_512 | 90.65 | 92.94% |
| `ngram-map-k n=4 m=16` | story_sampled_512 | 40.33 | 25.75%, net negative |
| `ngram-map-k n=4 m=16` | long_context_greedy_384 | 34.09 | 43.75%, essentially flat |
| `ngram-mod match=24 min=48 max=64` | story_greedy_512 | 187.06 | 100% on this prompt |
| `ngram-mod match=24 min=48 max=64` | story_sampled_512 | 44.60 | essentially baseline |
| `ngram-mod match=24 min=48 max=64` | long_context_greedy_384 | 33.75 | slightly below baseline |

Interpretation: n-gram speculative is a conditional accelerator, not a default chat accelerator. It is promising for deterministic, low-temperature, repeated, template-like output, but it can be neutral or negative for sampled/open-ended generation. The short greedy story prompt is likely too n-gram-friendly to generalize.

Good candidate workloads:

- structured output
- JSON/YAML
- markdown tables
- runbooks
- logs
- boilerplate code
- patch-like output
- agent/tool traces
- low-temperature completions over repeated operational context

Avoid as a default for:

- creative chat
- high-temperature sampling
- open-ended reasoning
- exploratory answer generation

### 35B external draft model

Tested existing local draft:

- Target: `Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf`
- Draft: `/home/deshev/models/Qwen3-4B-Instruct-2507-Q6_K.gguf`

The server loaded both models, but reported incompatible vocabularies:

- Target tokenizer preset: `qwen35`, `248320` vocab
- Draft tokenizer preset: `qwen2`, `151936` vocab
- llama.cpp translated tokens between them

Short greedy 256-token results:

| nmax | Decode tok/s | Acceptance |
| ---: | ---: | ---: |
| 1 | 17.06 | 1/1 accepted, but only one draft generated |
| 2 | 16.48 | 1/2 |
| 4 | 16.83 | 1/4 |
| 8 | 16.85 | 1/8 |

Interpretation: the existing Qwen3-4B draft is not viable for Qwen3.6 35B A3B. The tokenizer translation path is too expensive and acceptance is effectively unusable. A useful external draft would need the `qwen35` tokenizer family. No small obvious Qwen3.6/Qwen35 draft was found during this pass; using the 27B as a draft would defeat the purpose because it is dense and slower than the 35B A3B target.

## Routing implications

The useful production shape is not one global speculative mode. It should be a router decision.

Candidate policy:

```text
default chat / sampled:
    clean Qwen3.6 35B A3B no-spec

uncensored/special tasks:
    Hauhau 35B A3B lane

dense-quality lane:
    Qwen3.6 27B Q6_K MTP, nmax=2

deterministic structured generation:
    consider ngram-map-k or ngram-mod

creative/high-entropy generation:
    avoid n-gram speculative
```

Future Ariadne `spec_mode = auto` sketch:

```python
if temperature <= 0.2 and output_format in {"json", "yaml", "markdown_table", "runbook", "code_patch", "tool_trace"}:
    spec_mode = "ngram"
elif model == "qwen36_27b_dense":
    spec_mode = "mtp"
else:
    spec_mode = "none"
```

Next steps worth exploring:

- Run n-gram tests on real Ariadne workloads: JSON tool calls, runbooks, markdown tables, patch outputs, citations, and long session-prep blocks.
- Return to the 27B dense model and test n-gram/external-draft methods there; MTP is not the only possible acceleration path.
- Look for a genuinely small `qwen35` tokenizer-compatible draft model. Do not use older Qwen3/Qwen2-tokenizer models as draft for Qwen3.6.
- Add explicit profiles rather than replacing BEAST: `35b-clean-default`, `35b-hauhau-special`, `27b-dense-mtp`, and `35b-ngram-structured`.

## Sources

- Reddit discussion provided by the user: <https://www.reddit.com/r/LocalLLaMA/comments/1t5r4tz/uploaded_unsloth_qwen3635ba3b_ud_xl_models_with/>
- Qwen3.6 27B MTP GGUF files: <https://huggingface.co/froggeric/Qwen3.6-27B-MTP-GGUF>
- llama.cpp PR 22673: <https://github.com/ggml-org/llama.cpp/pull/22673>
- llama.cpp quantize README: <https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md>
- llama.cpp perplexity README: <https://github.com/ggml-org/llama.cpp/blob/master/tools/perplexity/README.md>
- Cohere MoE speculative decoding discussion: <https://cohere.com/blog/mixture-of-experts-models-get-more-from-speculative-decoding>
