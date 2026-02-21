# Simon Cognitive Engine (OWUI-Embedded V1)

This fork embeds a Simon-style cognitive layer directly into OpenWebUI as an opt-in `pipe` model.

## What it does

- Deterministic pre-LLM intent routing (`recall` / `save`) with regex heuristics.
- Gatekeeper routing based on context debt + retrieval quality.
- Surgical context injection with a small fixed token budget (`4096` target budget).
- Branch-aware recall using `chat_id + parent_message_id` lineage.
- SQLite lexical retrieval with recursive FTS search.
- Async post-flight persistence (chat diagnostics + optional memory save intent).

## What it does not do (V1)

- Full agentic deep tool loop (deep route uses deterministic enhanced retrieval only).
- File/image reasoning in Simon path.
- PostgreSQL lexical parity (SQLite lexical path only in V1).

## Install / Update

Run from project root:

```bash
python3 scripts/install_simon_cognitive_engine.py
```

This script upserts:

- Function: `simon-cognitive-engine` (type `pipe`)
- Model override: `simon-cognitive-engine` with locked-down capabilities and `params.function_calling="native"`

## Required valve setup

After install, open function valves and set:

- `simon_default_model`: a non-pipe base model id (required)

Optional valves:

- `enable_deep_mode` (default `false`)
- `emit_trace_status`
- `max_status_events_per_turn`
- `hot_cache_mode` (`auto|on|off`)
- `freeze_memory_per_session` (default `true`)
- `frozen_memory_k` (default `3`)
- `frozen_memory_ttl_sec` (default `21600`)
- `enable_on_demand_retrieval` (default `true`)
- `lex_queue_batch_size`
- `lex_queue_poll_ms`

## Operational notes

- Hot cache is non-authoritative; in `auto` mode it disables itself when multiple workers are detected (`WEB_CONCURRENCY`/`UVICORN_WORKERS` > 1).
- Session bootstrap memories are frozen per `chat_id:session_id` scope to keep prompt prefixes stable for cache reuse.
- On-demand retrieval augmentation is triggered for explicit/soft recall intents (and deep trigger when enabled).
- Lexical index stores searchable text and message pointers; final context is rehydrated from canonical chat history before injection.
- Lexical queue runs in background at app startup and survives restarts through durable queue rows.

## Injection telemetry

- Every turn stores deterministic injection trace under `chat.meta.simon.last.injection`.
- Rolling history is stored under `chat.meta.simon.injection_history` (last 40 turns).
- Inspect telemetry from shell:

```bash
PYTHONPATH=backend backend/.venv/bin/python scripts/simon_injection_dashboard.py --limit 10
PYTHONPATH=backend backend/.venv/bin/python scripts/simon_injection_dashboard.py --chat-id <chat_uuid> --history 20
```

## Rollback

- Deactivate function `simon-cognitive-engine`.
- Deactivate/remove model override `simon-cognitive-engine`.
