# Upstream v0.9.2 Integration Shortlist

Approved on 2026-04-25 for Ariadne follow-up integration work.

## Take First

- Brotli security bump to `Brotli==1.2.0`.
- `fetch_url` null-content guard in `backend/open_webui/tools/builtin.py`.
- Proxy header cleanup for `backend/open_webui/routers/openai.py` and `backend/open_webui/routers/ollama.py`.
  Remove stale upstream headers after aiohttp auto-decompression:
  `Content-Encoding`, `Content-Length`, `Transfer-Encoding`.
- Direct API error response fixes and cancelled-stream cleanup in OpenAI/Ollama proxy flow.
- `create_automation` current-model detection fix if Ariadne automation flow still depends on upstream `create_automation`.

## Take If Relevant

- Firecrawl v2 API migration with retry, exponential backoff, and timeout handling.
- MCP cancellation stability fixes.
- MCP `resource.text` handling fix.
- OAuth protected-resource discovery fallback for MCP auth flows.
- `CUSTOM_API_KEY_HEADER` support for reverse-proxy / forward-auth compatibility.
- RAG template warning UI for duplicate `[context]` / `{{CONTEXT}}` placeholders.
- Rich text extension conflict fix for lists and code blocks.

## Streaming Perf Investigation

- Candidate: browser-native message virtualization via `content-visibility: auto` on chat message list items.
- Important: do not evaluate this in isolation.
- Pair it with upstream `Markdown.svelte` cleanup fix:
  remove reactive-label `onDestroy` registration during streaming updates.
- Reason:
  Ariadne already has requestAnimationFrame throttling in `Messages.svelte` and `Markdown.svelte`, plus a fast-path clone check in `ResponseMessage.svelte`.
  The remaining slowdown during long streaming sessions is likely a mix of:
  long message-list rebuilds, markdown parse cost on the live message, and browser render/layout pressure from large chat DOM.
- Expected value of `content-visibility`:
  likely helps off-screen render/layout cost for long chats.
  unlikely to fully solve live-token lag by itself.
- Decision rule:
  if we take the streaming perf pass, take both:
  1. `Markdown.svelte` cleanup fix
  2. `Message.svelte` `content-visibility` / `contain-intrinsic-size` patch

## Fork-Aware Reminder

- Ariadne is a single-user / single-maintainer fork.
- Prefer boring stability backports over feature-surface expansion.
- Avoid heavy upstream migrations unless there is an active operational need:
  `psycopg` async migration, PaddleOCR-vl, calendar feature expansion, telemetry-only changes.
