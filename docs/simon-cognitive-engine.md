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
- `lex_queue_batch_size`
- `lex_queue_poll_ms`

## Operational notes

- Hot cache is non-authoritative; in `auto` mode it disables itself when multiple workers are detected (`WEB_CONCURRENCY`/`UVICORN_WORKERS` > 1).
- Lexical index stores searchable text and message pointers; final context is rehydrated from canonical chat history before injection.
- Lexical queue runs in background at app startup and survives restarts through durable queue rows.

## Rollback

- Deactivate function `simon-cognitive-engine`.
- Deactivate/remove model override `simon-cognitive-engine`.
