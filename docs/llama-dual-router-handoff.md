# Llama Dual Router Handoff

Date: 2026-05-09

Remote host: `deshev@192.168.1.117`

SSH key: `~/.ssh/ariadne_192_168_1_117_ed25519`

## Current Production State

`/home/deshev/models/run_llama.sh` now defaults to the promoted backend symlink:

```bash
/home/deshev/.local/opt/ariadne-llama/current/bin/llama-server
```

with:

```bash
LD_LIBRARY_PATH=/home/deshev/.local/opt/ariadne-llama/current/lib64:/home/deshev/.local/opt/ariadne-llama/current/lib:/home/deshev/.local/opt/ariadne-llama/current/bin:/opt/rocm/lib:/opt/rocm-7.2.2/lib
```

The promoted backend store is:

```text
/home/deshev/.local/opt/ariadne-llama
  current  -> builds/20260509T010638-pinned-5d5f1b46e4f5-llama-rocm-7.2.2-imported
  previous -> builds/20260509T010512-pinned-5d5f1b46e4f5-llama-rocm-7.2.2-imported
```

The promoted backend is the same known-good PR 22673 build that was already
running before the symlink migration:

```text
llama-server version = 9032 (5d5f1b46e)
lane                 = pinned
toolbox              = llama-rocm-7.2.2
patch sha256         = 29175306dbf17e2f0d495a2759a0292b992db7a570de7a5b97abb74c9f7eed72
```

Important: the live PID was not restarted during the backend promotion work.
At the time of the migration, the running process was still the same old-path
binary, but it is byte-equivalent to the imported promoted backend. The next
`./run_llama.sh restart-profile dual` starts through `current`.

The active service is a single router on `0.0.0.0:1234`, profile `dual`, with
two resident model instances:

```text
Qwen3.6-27B-Dense-MTP-Q6_K
Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL
```

Both are loaded with:

```text
ctx = 131072
parallel = 1
cache-type-k = q8_0
cache-type-v = q8_0
build = b9032-5d5f1b46e
```

Their roles differ:

```text
Qwen3.6-27B-Dense-MTP-Q6_K
  text-only
  spec-type = mtp
  no mmproj
  vision = false

Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL
  multimodal
  no runtime speculative decoding
  mmproj = /home/deshev/models/mmproj-Qwen3.6-35B-A3B-Q6_K.gguf
  vision = true
```

Important: router `MODELS_MAX=2` does not split `CTX` across models. Each child `llama-server` receives `--ctx-size 131072` because router spawns separate model instances.

## Files Changed Remotely

`/home/deshev/models/run_llama.sh`

- Default backend is now the promoted symlink, not stock `/usr/local/bin/llama-server`.
- `dual`, `beast`, and manual starts inherit the same backend unless explicitly overridden by env vars.
- `dual` preloads:
  - `Qwen3.6-27B-Dense-MTP-Q6_K`
  - `Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL`
- Startup waits for `/models/load` to reach `loaded`.
- Status JSON includes `llama_server_bin`, `llama_server_ld_library_path`,
  `preload_models`, and a `backend` object with the promoted manifest path,
  build id, lane, llama ref, patch hash, and smoke status.

`/home/deshev/models/ariadne-llama-backend.sh`

- Wrapper symlink to `/home/deshev/open-webui/scripts/llama_patch/ariadne-llama-backend.sh`.
- Manages `status`, `list`, `build`, `import-current`, `smoke`, `promote`, and `rollback`.
- Promotion and rollback are locked with `flock` and use atomic symlink replacement.
- Canary smoke tests run on port `1235` and do not stop production on `1234`.
- The current promoted backend passed `27b-text-mtp` smoke:
  - MTP activity present in logs
  - Ariadne streamed tool/logprob smoke passed
  - `SMOKE_PROMPT_TOK_S=20.84`
  - `SMOKE_DECODE_TOK_S=22.02`
  - `SMOKE_TOTAL_EVAL_MS=1648.45`

`/home/deshev/models/models.ini`

Added:

```ini
[Qwen3.6-27B-Dense-MTP-Q6_K]
model = /home/deshev/models/Qwen3.6-27B-Q6_K-mtp.gguf
spec-type = mtp
spec-draft-n-max = 2
spec-draft-n-min = 1
jinja = true
chat-template-file = /home/deshev/models/templates/qwen36-27b-hauhau-aggressive-think-toggle.jinja
chat-template-kwargs = {"enable_thinking": false}

[Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL]
model = /home/deshev/models/Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL.gguf
mmproj = /home/deshev/models/mmproj-Qwen3.6-35B-A3B-Q6_K.gguf
jinja = true
chat-template-file = /home/deshev/models/templates/qwen36-35b-a3b-hauhau-aggressive-think-toggle.jinja
chat-template-kwargs = {"enable_thinking": false}
```

Downloaded projectors:

```text
/home/deshev/models/mmproj-Qwen3.6-27B-Q6_K.gguf
  sha256 371d139dd5a372e2fe904806ebc84e76188ae5e79a04af57fa449409af7992d1
  note: downloaded and available, but intentionally not used by the 27B MTP resident preset

/home/deshev/models/mmproj-Qwen3.6-35B-A3B-Q6_K.gguf
  sha256 6fb134b841500e3c96918646a6a6fb00580d746b9804086a9a1577c66480c905
```

Recent backups:

```text
/home/deshev/models/run_llama.sh.bak.20260509-002819
/home/deshev/models/run_llama.sh.bak.20260509-010838
/home/deshev/models/models.ini.bak.20260509-003340
```

## Verification Done

Status/properties confirmed:

```text
Qwen3.6-27B-Dense-MTP-Q6_K
  status = loaded
  ctx = 131072
  cache = q8_0 / q8_0
  spec flag = mtp
  mmproj = none
  vision = false

Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL
  status = loaded
  ctx = 131072
  cache = q8_0 / q8_0
  spec flag = none
  mmproj = /home/deshev/models/mmproj-Qwen3.6-35B-A3B-Q6_K.gguf
  vision = true
```

Note: `/props` currently reports `speculative.type = none` for 27B, but the child args include `--spec-type mtp` and the logs confirm `MTP draft head registered`. Trust child args/logs over `/props` for this PR build.

Smoke tests through `/v1/chat/completions`:

```text
Qwen3.6-27B-Dense-MTP-Q6_K text -> ok
Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL text  -> ok
Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL image -> red
```

Useful commands:

```bash
ssh -i ~/.ssh/ariadne_192_168_1_117_ed25519 deshev@192.168.1.117 \
  'toolbox run -c llama-rocm-7.2.2 bash -lc "cd /home/deshev/models && ./run_llama.sh status --json"'

ssh -i ~/.ssh/ariadne_192_168_1_117_ed25519 deshev@192.168.1.117 \
  'toolbox run -c llama-rocm-7.2.2 bash -lc "cd /home/deshev/models && ./run_llama.sh restart-profile dual"'

ssh -i ~/.ssh/ariadne_192_168_1_117_ed25519 deshev@192.168.1.117 \
  'curl -fsS http://127.0.0.1:1234/models?autoload=false | python3 -m json.tool | less'

ssh -i ~/.ssh/ariadne_192_168_1_117_ed25519 deshev@192.168.1.117 \
  '/home/deshev/models/ariadne-llama-backend.sh status'

ssh -i ~/.ssh/ariadne_192_168_1_117_ed25519 deshev@192.168.1.117 \
  '/home/deshev/models/ariadne-llama-backend.sh smoke --candidate 20260509T010638-pinned-5d5f1b46e4f5-llama-rocm-7.2.2-imported --profile 27b-text-mtp --port 1235'
```

## Important Caveat

Do not combine 27B MTP with the 27B `mmproj`.

Tested:

```ini
spec-type = mtp
spec-draft-n-max = 2
spec-draft-n-min = 1
```

Result:

- Text request worked and showed MTP acceptance stats.
- Image request returned HTTP 500 and the 27B child instance exited.

Current default therefore keeps 27B as text-only MTP and uses 35B for multimodal.

## Ariadne Follow-Up

Use the model ids directly:

```text
Qwen3.6-35B-A3B-MTP-UD-Q8_K_XL
Qwen3.6-27B-Dense-MTP-Q6_K
```

Recommended behavior:

- Default daily/general lane: 35B MoE resident model.
- Vision lane: 35B MoE resident model only.
- Fast dense text lane: 27B MTP resident model.
- Do not send image or multimodal requests to `Qwen3.6-27B-Dense-MTP-Q6_K`.
- Do not assume runtime speculative decoding is enabled just because the GGUF filename contains `mtp`; check preset args/logs.
