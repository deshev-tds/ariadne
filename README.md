# Ariadne

Ariadne is a local-first AI workbench for long chats, evidence recall, personas, TTS, and `llama.cpp` debugging.

Ariadne began as a fork of Open WebUI, but it is now built around a different priority set than the upstream project.

That priority set is:

- local-first behavior over backend-agnostic product smoothing
- long-chat survivability over naive full-history replay
- memory with evidence and recall, not summary-only compression
- fast-path UX by default, with bounded slow paths only when needed
- lightweight local voice that still sounds good enough to use
- token-level inspection and deliberate response branching for real debugging
- honest control surfaces for `llama.cpp`-style local stacks instead of universal-magic toggles

Upstream Open WebUI remains a strong base platform and UI for self-hosted LLM workflows. Ariadne keeps that base where it is useful, but diverges where local-first power-user workflows need tighter control: long-running chats, context overflow, exact recovery of old facts, practical local TTS quality, and generation inspection that is useful during real debugging rather than only during demos.

In practice, the local stack behind this fork is centered on `llama.cpp`, OpenAI-compatible local serving, and AMD Strix Halo hardware. That matters because a lot of the design decisions here are not abstract product ideas; they are responses to the behavior and limits of a real local runtime.

If you care about local models, long technical chats, prompt-budget hygiene, and being able to inspect or deliberately fork a response path, this fork is trying to make those workflows less opaque and less fragile.

## Why This Fork Exists

The upstream project is broad. This fork is narrow on purpose.

The main idea is that local LLM UX falls apart in a few predictable places:

- context windows fill up long before the conversation is actually "done"
- summary alone is not memory
- local TTS often sounds either too synthetic or too heavyweight
- token-level generation is usually hidden, even when you need to inspect or steer it

So this fork biases toward:

- server-side context hygiene
- bounded recall instead of blind replay
- lightweight but good local TTS
- generation observability and deliberate branching

It is not trying to replace upstream Open WebUI. It is trying to make a specific local workflow sharper.

## What Changed from Upstream

The important divergences are not cosmetic.

- Server-side context maintenance was added so long chats do not simply degrade or fail once the prompt gets too large.
- Summary generation was changed from a loose recap into a structured state snapshot.
- Exact recall was added so older raw facts can be recovered when they fall out of the live prompt window.
- That recall path is now gated behind a secondary UI flag under the Web Search integrations menu and defaults to off, after real-chat testing showed that broader automatic triggering could add random latency and status noise in otherwise normal conversation.
- The live prompt budget is now derived from the active `llama.cpp` runtime instead of being treated as a static design assumption.
- When recall is enabled, it is explicitly layered as `FTS preferred, raw fallback guaranteed` for cases where lexical retrieval is not ready or not good enough.
- Request-scoped memory telemetry can be turned on per turn for debugging without leaving noisy long-lived logging enabled in production.
- Ledger continuity now uses explicit chat-scoped mode selection: `vibe` by default, `agentic` only when enabled in the UI toggle.
- Web retrieval was pushed toward planned, bounded evidence gathering instead of naive query-and-dump behavior.
- A native focused-web tool (`web_research_strong`, with `search_strong_sources` kept as an alias) was added for bounded evidence-first search, with optional domain constraints and broader fallback only when the first pass is weak.
- A domain-first local corpus path was added for proprietary or bought literature, with selection-layer markdown, per-book evidence retrieval, and table-aware follow-up tools instead of flattening everything into one generic knowledge pile.
- The `v2` local-corpus reasoning lane now uses deterministic retrieval projection so user-facing framing text does not quietly pollute axis queries and slow or misroute retrieval.
- The `default` function-calling selector now has its own runtime discipline layer, so local-corpus preference, retrieval term hygiene, and prior-work fallback do not depend entirely on native tool-calling behavior.
- In `default` function calling, `local_corpus_mode=auto` now deterministically performs a single local shelf-inspection step first (`local_corpus_list_domains`) before any web or model-only fallback, and only continues locally when the returned usable domains show a real thematic fit.
- Source routing now supports explicit `planner_hints.is_local`, so local/trusted domains can be prioritized deterministically.
- Focused search now emits visible chat status phases (targeted run, fallback escalation, completed) using the same status UI path as regular web search.
- Kokoro TTS paths were added because they sound better than many lightweight local options without dragging in a huge stack.
- Persona V1 was added so the daily-driver interaction unit can be a persona rather than only a model preset.
- Persona chats now pin requested runtime defaults per chat while keeping persona identity live across the UI.
- Persona authoring now includes a first-class `partner_profile` layer for always-on relational or operator framing that stays separate from the persona core prompt.
- The app now has a dedicated `Workspace -> Personas` surface with persona-first chat selection, voice preview, and direct-model fallback.
- Persona rebinding on an existing chat now refreshes the pinned runtime snapshot instead of silently carrying stale defaults from the previous persona.
- Persona-attached chats now have a first-class `scene_note` layer for chat-local atmospheric or situational steering without mutating persona identity.
- `scene_note` authoring now supports optional thumbnails, including one-click generation through the existing image generation backend when that feature is enabled.
- Persona-scoped workflow hooks can now carry narrow domain logic on top of a bound local model, so a persona can do more than restyle a chat without turning the whole app into a generic always-agentic shell.
- Automatic pre-migration SQLite backups were added before Alembic upgrades so local schema changes do not run without a fresh snapshot.
- Token explorer support and manual response branching from token alternatives were added so local generation is less of a black box.
- On 2026-04-25, downstream `llama.cpp` patch tooling was added for the local Strix Halo toolbox workflow, so Ariadne can track kyuz0's prebuilt containers while testing a narrow `stream + native tools + logprobs` server patch without turning the whole deployment into a long-lived runtime fork.
- On-demand tool journey telemetry was added so agent/tool execution paths can be inspected per request without permanent log bloat.
- A news lane was added for local-first morning briefings: configurable source registry, RSS fetch and article analysis pipeline, story threading with stability scoring, daily briefing synthesis, and Kokoro TTS playback.
- Model timeouts in the news pipeline are now configurable and batch-safe, with separate connect, article analysis, and briefing synthesis timeout floors to prevent long batch runs from dying on a single slow call.
- A `Morning News` system persona is now seeded automatically for admin accounts on startup, pre-bound to the briefing model and TTS voice from config.
- Persona `capabilities` now support a `preferred_working_mode` field that routes the chat runtime into a specialized workflow path when a compatible persona is active, without relying on chat-text inference.
- The news lane now has an explicit local-state contract for broad briefing asks: read `latest_briefing` when present, build on demand from the latest closed snapshot when no saved briefing exists yet, and return a plain empty-state when there is still no closed snapshot.
- Feed discovery in the news lane now gates candidates to a rolling `24h` recency window before article fetch, so weekly or stale feeds do not quietly recycle old stories into a "today" briefing.
- Morning briefings now run as full briefings by default rather than a tight executive-summary funnel: the target item count was widened, the editorial keep pool was expanded, and paragraph-level detail is preserved more aggressively.

The rest of this README explains the rationale behind those changes.

## Persona Runtime in This Fork

This fork now has a first usable persona layer on top of the older model-preset substrate.

The important distinction is that persona is no longer treated as "just another model with a different prompt". The core runtime shape is:

`persona identity + binding + partner profile + requested defaults + chat attachment + scene note`

That core shape is still the right mental model for most persona-attached chats. What has changed more recently is that a persona can also carry optional persona-scoped workflow hooks on top of that base, when a narrow domain path benefits from explicit runtime discipline instead of hoping a long prompt will hold. A persona can now also declare a `preferred_working_mode` in its `capabilities`, which routes the chat backend into a specialized runtime path without requiring the user to flip a chat-level toggle or inferring intent from message text.

Current Persona V1 behavior:

- personas are first-class private objects with their own workspace screen
- a chat can attach to one `persona_id`
- persona identity is live across attached chats
- persona `partner_profile` is a separate always-on layer for relational framing and user/operator-specific stance
- persona runtime defaults are pinned per chat at creation time
- changing persona on an existing chat replaces that chat's pinned persona snapshot instead of reusing stale defaults
- `scene_note` is chat-local, mutable, and injected as forward-only current-scene framing
- `scene_note` can also carry an optional UI thumbnail without changing prompt/runtime behavior
- direct model use still exists as a separate fallback path
- voice belongs to the persona definition and can be previewed inline
- persona editing now exposes:
  - bound model
  - partner profile
  - per-persona voice override
  - inherited voice fallback from bound model and app audio defaults
- active persona chats now have a direct `Edit Persona` path from the navbar

That split matters because it keeps older chats stable when a persona's behavior settings change, while still letting the persona's visible identity update across the interface.

The current V1 implementation deliberately stops before:

- lorebooks
- persona-scoped continuity synthesis
- persona-scoped recall

Those remain roadmap items rather than pretending they already exist.

### A Narrow But Real Persona Workflow Proof

One useful proof has now shown up on top of that persona runtime: a persona can carry a narrow domain workflow that is materially more useful than "same chat, different prompt".

There are now two concrete examples of that in this fork.

The first is a travel-planning persona bound to a local model and a bounded tool surface. In the successful path, it can:

- turn a broad trip brief into layered research passes instead of one blind query-and-dump turn
- preserve persona-specific runtime defaults across follow-up refinement turns
- selectively enrich an accepted plan with map links instead of always restarting research
- use the local terminal/tool path to emit practical artifacts such as mobile-friendly HTML and PDF guides

The second is the `Morning News` persona, which declares `preferred_working_mode: "news"` in its capabilities. When a chat is attached to that persona, the backend routes into the news lane runtime instead of the default chat path. The persona is pre-bound to the configured briefing model and TTS voice, and is seeded automatically for admin accounts on startup so it is ready without manual setup.

That is not presented here as a claim that personas are now full applets. It is a narrower point:

- persona can be a runtime scaffold, not just a prompt skin
- business logic can stay persona-scoped instead of contaminating every chat path
- a local `llama.cpp` stack on real hardware can be pushed far enough to produce outputs that are worth actually using

The point is not that "travel" or "morning news" is special. The point is that this fork now has concrete examples where persona identity, runtime defaults, tool routing, and specialized backend paths combine into workflows that would have been weaker if persona were only cosmetic.

## News Lane

This fork adds a local-first news pipeline for structured morning briefings over a private news corpus.

For the product framing behind this lane, including why the token budget, scheduled precomputation, persona packaging, and TTS ritual are intentional rather than incidental, see [docs/morning-news-poc.md](./docs/morning-news-poc.md).

The design premise is the same one that shows up in the local corpus and context work: do not flatten everything into one generic retrieval step, and do not make quality depend on an opaque inference chain you cannot inspect.

A news briefing has a different job than a general web search result:

- it should follow a consistent source set over time, not rediscover the same sources on each run
- articles about the same story from different sources should be threaded, not treated as independent items
- the briefing should surface genuine conflicts between sources rather than smoothing them into a confident-sounding synthesis
- the output should be speakable, not just readable

The news lane was built around those requirements.

### Pipeline Architecture

The news pipeline now runs as a single scheduled morning prep pass.

**Morning prep pass:**

1. Discover and fetch only feed entries published within the last rolling `24h` from the configured source registry
2. Prefetch related pages for any articles that need additional context
3. Analyze articles: extract claims, identify supported claim kinds (`count`, `status`, `decision`, `date_time`, `official_position`), and score source alignment
4. Build or update a story snapshot: thread articles that cover the same event, score thread stability, and close threads that have converged
5. Immediately synthesize a full morning briefing from the kept snapshot pool, with a runtime target floor of `18` items and an admin-configurable ceiling of `24`
6. Render the briefing as speakable audio using Kokoro TTS with the configured voice when playback is requested

By default the scheduler runs this once per day at `05:08`. The morning prep pass and the briefing-only rebuild can also be triggered manually through the admin API, so the pipeline does not depend on the scheduler to be useful during development or first-time setup.

### Briefing Runtime Contract

Broad briefing requests in `Morning News` no longer rely on a single happy path.

- If a saved compiled briefing exists, `news_consult` returns `route=latest_briefing`.
- If no compiled briefing exists yet but a closed snapshot does, `news_consult` returns `route=build_from_snapshot` and synthesizes an ephemeral briefing from the snapshot on demand.
- If there is still no closed snapshot, `news_consult` returns `route=empty_state` with an explicit local-state message instead of pretending it is still "looking" or pivoting to generic web search.

This matters because the user-facing chat contract is now aligned with the actual local corpus state: no briefing artifact means "build from snapshot now", while no snapshot means "say that plainly".

### Thread Stability and Scoring

Articles about the same story are grouped into threads. Each thread carries a stability score based on how much the source coverage has settled.

Two thresholds shape story candidate selection:

- `NEWS_THREAD_UNSTABLE_THRESHOLD` (0.45): threads below this score are penalized in briefing candidate ranking because they are still in flux
- `NEWS_THREAD_PENDING_SPLIT_THRESHOLD` (0.90): threads near a split point get a separate penalty because they are likely covering two stories that have not yet diverged into distinct threads

The result is that briefing candidates tend toward stories where coverage has stabilized and sources are no longer contradicting each other on basic facts, without requiring the pipeline to wait until every thread is fully resolved.

### Source Registry and Category Config

Sources are defined in a `NEWS_SOURCE_REGISTRY`, a structured list of feeds with associated metadata. Categories are defined separately in `NEWS_CATEGORY_CONFIG`, which maps source groups to topic areas.

Both registry and category config are versioned and hash-tracked so the system can detect when configuration has changed between runs. Normalization and validation run on update to catch structural problems before they reach the fetch or analysis step.

The source registry is per-instance and intended to be a curated set, not a directory of every available feed. The point is that the sources you track are a deliberate choice, not a discovery problem to outsource to a search engine.

This became more important once the ingestion layer started enforcing a rolling `24h` freshness gate. A source that only publishes weekly can still be a good source in the abstract, but it is a bad fit for a daily morning-news lane unless it gets a source-specific age window. In practice that means the registry should prefer durable, machine-readable feeds with daily or near-daily cadence.

Recent additions that fit those constraints and expand weaker coverage areas:

- `ars_ai` for a real `tech_ai` lane
- `smithsonian_smartnews` for lighter science and `weird` coverage
- `sciencedaily_strange` for a dedicated `weird / anomaly` lane

### Model Configuration and Timeout Safety

The news pipeline uses two separate model roles:

- `NEWS_ARTICLE_MODEL`: used for per-article claim extraction and analysis; can be a smaller or faster model
- `NEWS_BRIEF_MODEL`: used for final briefing synthesis; benefits from stronger instruction-following

Both roles use a configurable OpenAI-compatible endpoint (`NEWS_ARTICLE_MODEL_ENDPOINT`) so the news pipeline can target a different local server than the main chat path if needed.

Model timeouts are also configurable and batch-safe:

- `NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS` (default 300s): applies per article analysis call
- `NEWS_BRIEF_MODEL_TIMEOUT_SECONDS` (default 300s): applies to the briefing synthesis call
- connect timeout is separate (10s fixed) so a slow article does not close the underlying connection pool

The separation matters because a batch of 10+ articles runs sequentially. A single timeout value covering both connect and read would either time out on slow but valid analysis calls or leave hung connections open across the batch.

### Morning News Persona

On startup, the backend automatically seeds a `Morning News` system persona for each admin account.

The persona is pre-configured with:

- bound to `NEWS_BRIEF_MODEL` from config
- voice set from `NEWS_TTS_VOICE_ID` from config
- system prompt biased toward local news corpus preference and source-grounded output
- `preferred_working_mode: "news"` in capabilities, which routes attached chats into the news lane runtime instead of the default chat path
- full-briefing behavior for broad asks: preserve paragraph-level detail, prefer the compiled briefing when present, and build from the latest closed snapshot when it is not
- explicit empty-state behavior when there is still no closed local snapshot, instead of falling through to a fake-progress response or generic web search

The persona ID is deterministic per user (`system-morning-news-{user_id}`), so re-running startup is idempotent: existing personas are updated in place rather than duplicated.

### Configuration Reference

The important config keys for the news lane:

- `NEWS_ENABLED`: toggles the entire lane on or off
- `NEWS_ARTICLE_STORE_ROOT`: where fetched article data is stored on disk
- `NEWS_CORPUS_ROOT`: the local news corpus directory used for retrieval
- `NEWS_BRIEFINGS_ROOT`: where synthesized briefings are written
- `NEWS_ARTICLE_MODEL_ENDPOINT`: OpenAI-compatible endpoint for article analysis and briefing
- `NEWS_ARTICLE_MODEL`: model ID for article analysis
- `NEWS_BRIEF_MODEL`: model ID for briefing synthesis
- `NEWS_BRIEF_TARGET_ITEM_COUNT`: admin-set target item count for briefing selection, runtime-capped to `24` and floored to `18` for full briefings
- `NEWS_ARTICLE_MODEL_TIMEOUT_SECONDS`: per-call timeout for article analysis
- `NEWS_BRIEF_MODEL_TIMEOUT_SECONDS`: timeout for briefing synthesis
- `NEWS_TTS_VOICE_ID`: Kokoro voice used for spoken briefings
- `NEWS_WAKE_TIME`: daily morning prep time in `HH:MM`, defaulting to `05:08`
- `NEWS_PLAYBACK_DEVICE`: audio output device for TTS playback

All config is managed through the admin Settings → News panel, which also exposes the source registry editor, category config, latest snapshot view, and manual worker triggers.

### Regression Notes and Lessons

The current shape of the news lane came out of a few concrete failures rather than abstract cleanup:

- `no_closed_snapshot` needed an explicit UX contract. Returning a bare empty payload let the model fake progress instead of explaining the local state.
- A compiled briefing is not guaranteed to exist at request time. The right fallback is "build from latest closed snapshot", not "act empty".
- Weekly or stale feeds are dangerous in a daily lane if ingestion only looks at "most recent available". A rolling `24h` gate fixed that class of regression.
- Richer sources were not enough on their own. The editorial keep limit, per-category caps, and briefing target count were all compressing too early, so the funnel had to be widened to get a genuinely fuller briefing.
- Paragraph quality mattered more than expected. Tight `4-6 sentence` summaries were still too telegraphic for the intended product; `7-9 sentence` source-grounded paragraphs gave the model enough substrate to stop collapsing everything into short thematic bullets.

## Migration Safety

This fork now creates an automatic pre-migration backup for local SQLite databases before Alembic upgrades run on launch.

That is intentionally local-first behavior:

- schema migrations still run automatically
- but SQLite gets a timestamped snapshot first
- if backup creation fails, the migration does not proceed

The goal is not enterprise migration orchestration. The goal is to avoid dumb local footguns on a single-maintainer system.

## Context and Memory in This Fork

This fork now treats context as a layered system, not as "replay everything until the model breaks".

### Stage 1: Context Compaction

The first change was server-side context maintenance.

Instead of replaying the entire branch forever, the server keeps:

- the system prompt
- a smart anchor from the start of the chat
- a recent raw tail of messages
- a rolling summary for the older middle

This maintenance can happen inline when the request would overflow, and in the background after a turn when the history is approaching the configured budget.

The goal was straightforward: do not wait for the backend runner to guess what to trim semantically, and do not bluntly drop the oldest turns if the opening contract of the chat still matters.

That compaction layer has since been tightened into a more explicit `hot context` model. Instead of treating the prompt budget as a vague maximum, the fork now derives a live prompt cap from the active `llama.cpp` runtime and budgets working memory against that real ceiling. In other words, the system no longer behaves like "fit as much as possible into whatever the model allows"; it behaves like "maintain a bounded hot working set that is small enough to stay usable on a real local runtime".

### Stage 2: Structured State Snapshot

The second change was not a new memory system. It was a better recap format.

Instead of asking the model for a narrative summary, this fork asks for a structured state snapshot with sections such as:

- User Objectives
- Constraints and Preferences
- Decisions and Conclusions
- Open Questions and Unresolved Work
- Stable Facts and Assumptions

That change matters because models generally behave better when earlier conversation state is presented as explicit structure rather than a prose recap. It reduces drift and makes selective attention less fragile.

### Stage 3: Exact Recall

The third change adds a real recall layer on top of working memory.

Working memory still looks like this:

- system prompt
- anchor
- structured snapshot
- recent raw tail

But if that live state does not provide enough evidence, the server can now recover older raw facts from earlier turns and inject them back into the prompt as evidence.

This recall layer is intentionally bounded and conservative. It is not an always-on retrieval ritual before every response.

That statement became more strict after real usage, not less. The first cut of automatic recall triggering was defensible in theory, but too eager in practice: normal chat phrasing could light up "Checking earlier conversation...", pay latency, and sometimes still return no usable evidence. The right response was not to defend the heuristic set because it sounded clever. The right response was to tighten the product contract.

Recall therefore still exists as a first-class capability, but it now sits behind a user-controlled UI flag and defaults to off. The toggle lives as a secondary option under the Web Search integrations menu rather than as a primary chat setting, because it belongs to the bounded slow-path retrieval family, not to the default fast path. Existing saved opt-ins were also intentionally not grandfathered: the effective UI key changed so stale aggressive behavior does not silently survive just because it was once enabled.

That rationale matters. This fork is not trying to look ideologically consistent with its first draft. If a differentiating feature starts creating more false positives than useful recoveries in normal use, the feature should be re-scoped until it earns its cost again.

It currently has two modes:

- `FTS recall`
  Used for explicit references, missing entities, and continuation cases where there is a useful lexical query.

- `branch_recent recall`
  Used for vague referential phrases like "the other tool" or "the old config", where sending pronoun-heavy text into FTS would likely fail.

There is also an important operational distinction now:

- `FTS` is the preferred evidence path
- but it is no longer a single point of failure

If explicit/entity recall fires and lexical retrieval does not produce usable evidence, the system now falls back to a bounded raw branch scan instead of silently collapsing into a weak generic answer. That is a deliberate maturity step: retrieval is no longer "try FTS and hope". It is now "prefer indexed recall, but still recover raw evidence when the lexical path misses".

Recovered snippets are injected as evidence, not as narrative:

```text
Evidence from earlier conversation:
[turn <id> | <role>]
...
```

That provenance matters. The model is being shown evidence, not a second-hand retelling.

One subtle but important detail here is that the recall path now follows the real OWUI request lifecycle more closely. In the normal frontend flow, the newest user turn may exist in the in-flight request before it has been persisted back into chat history. This fork now reconstructs request history accordingly: if the current user turn is not yet in the database, it loads persisted history up to its parent and appends the current in-flight user turn before running maintenance and recall. That keeps the cold-history path aligned with how the product actually behaves, not just how a simplified synthetic pipeline would behave.

### Simulated User Flows

Here is what that means in practice.

**Flow 1: No recall needed**

User:
`Continue with ffuf for the next step.`

If `ffuf` is still present in the recent raw tail or structured snapshot, the server does nothing special. The request stays on the fast path.

**Flow 2: Explicit factual recall**

User:
`What did we decide earlier about ffuf?`

This triggers bounded FTS recall. The server searches older turns, recovers the relevant excerpt, injects it as evidence, and only then lets the model answer.

**Flow 3: Missing entity, but no explicit "remember" wording**

User:
`Keep the same endpoint, but change the timeout to 5 seconds.`

If the endpoint has already fallen out of the live window, the server can recall the earlier raw mention instead of leaving the model to guess.

**Flow 4: Vague referential phrase**

User:
`What happened with that other tool?`

This is where `branch_recent` recall matters. Instead of issuing a bad FTS query built from vague pronouns, the server inspects a bounded recent window of older branch messages and injects a few evidence snippets for disambiguation.

The design bias is intentional:

- prefer false negatives over false positives
- do not tax every turn with retrieval
- recover old facts when evidence is weak, not whenever retrieval is merely possible

That same bias also explains the current fallback semantics:

- `FTS` should win when it is ready and precise
- `branch_recent` should help when the user is vague
- raw branch fallback should save explicit recall cases when indexing or lexical matching is not enough

The result is not "perfect memory". The result is a layered memory system that is much less likely to fail silently.

### Runtime Semantics and Memory Telemetry

This fork also now exposes the context system in its own runtime terms instead of hiding it behind a pile of internal budget math.

On each turn, the server can reason explicitly about:

- `live_prompt_cap`
- `hot_context_target_tokens`
- anchor size
- snapshot size
- recent tail size
- recall trigger reason
- recall mode
- evidence token cost
- whether fallback retrieval was used

When needed, that telemetry can be requested per turn with a debug flag and returned in the response as `memoryTelemetry`.

That design matters for a local-first system. Without it, the architecture may be doing the right thing but still remain opaque when something goes wrong. With it, the fork becomes inspectable in its own terms:

- was working memory too large?
- did snapshotting happen?
- did recall trigger?
- was lexical retrieval ready?
- did the system fall back to raw branch evidence?

This debug path is intentionally request-scoped. It is meant to help diagnose a specific turn, not to leave verbose memory logging enabled all the time and quietly fill production disks with telemetry.

### Tool Journey Telemetry (On-Demand)

The same request-scoped discipline now exists for tool execution itself.

When a request is sent with `params.debug_tool_journey=true`, middleware records a bounded per-request tool trace and exposes it as `toolJourneyTelemetry` in the final response/message payload.

That trace captures concrete lifecycle facts such as:

- tool start and completion
- malformed argument parse failures
- per-call duration
- compact result summaries

For strong-source retrieval specifically, the summary includes useful decision signals such as local-phase execution status, Brave fallback usage, fallback reason, and quality/coverage indicators.

This is also emitted in real time as `chat:tool:journey` events, so an in-progress run can be inspected live.

It is off by default, capped, and explicitly opt-in per request.

### Ledger Continuity Modes

This fork now treats ledger mode as an explicit user control instead of a backend heuristic.

The behavior is:

- `vibe` is the default mode for every chat
- `agentic` is enabled only through a chat-scoped toggle in the composer UI
- mode is persisted in chat params, so it survives reloads and continued sessions
- backend mode selection is driven by that explicit state, not by inferring "agentic-looking" language from recent turns

That tradeoff is intentional. It removes hidden mode flips and makes ledger behavior easier to reason about and debug.

Mode switching is also explicit and forward-only:

- existing chat history is left unchanged
- existing ledger entries are not auto-retired
- from the next turn onward, only the selected ledger kind is eligible for capture and injection

On the first turn after a mode switch, the selected mode can force a single ledger injection when active entries already exist for that mode. After that turn, normal selective gating resumes.

### Legacy Simon Pipe Cleanup

The old `simon-cognitive-engine` pipe stack is no longer part of the supported runtime path in this fork.

If an environment previously installed that pipe as a DB-backed function/model override, purge those records after deploy:

```bash
python3 scripts/purge_simon_cognitive_engine.py
python3 scripts/purge_simon_cognitive_engine.py --apply
```

The first command is a dry run. The second command deletes both legacy records:

- function id: `simon-cognitive-engine`
- model id: `simon-cognitive-engine`

After `--apply`, restart backend workers to guarantee no stale DB-loaded function modules remain in memory.

## Web Search and Retrieval Planning

This fork also treats web behavior as a retrieval problem, not just as "run a search and paste the results into context".

The rationale is similar to the context work above: the problem is not merely getting access to more text. The problem is deciding what evidence is worth fetching, how much of it is worth keeping, when to stop, and how to avoid polluting the prompt with redundant or weak material.

In practice, this means the web path here is more structured than a flat search integration:

- multiple planner modes exist instead of one hardcoded query flow
- optional domain constraints can be applied explicitly instead of relying on a giant curated source worldview
- the active model can be used as a query rewriter when that helps, but the system still keeps explicit fallback paths
- planner telemetry tracks retries, fallback usage, executed queries, and stopping conditions instead of treating the whole thing as opaque middleware

The important behavioral shift is this:

- upstream-style web search is often thought of as "query provider -> collect results -> inject results"
- this fork pushes it toward "plan -> target sources -> bound evidence -> stop when enough evidence exists"

That matters more on local setups than it first appears. Prompt budget is finite, retrieval latency is visible, and low-quality web evidence is actively harmful when it crowds out the rest of the conversation. The planner/rewriter/bounded-search work is there to make web retrieval less brute-force and less noisy.

At a high level, the current web path behaves more like this:

1. choose a planning mode
2. optionally rewrite or refine the search queries using the active model
3. target sources with planner hints instead of treating all sources as equivalent
4. stop once the evidence quality or coverage is good enough, instead of continuing mechanically
5. surface planner status and fallback information so the path is inspectable

This is the same overall philosophy as the context work: bounded, inspectable, evidence-oriented behavior beats magical but opaque behavior.

### Strong-Source Search Trigger (Bounded Evidence + Optional Domain Constraints)

The web stack includes a first-class native tool for evidence-critical retrieval: `web_research_strong` (`search_strong_sources` remains as a backward-compatible alias).

This is intentionally not a hard terminal guard. It is a native model-callable path with soft trigger semantics: when confidence is weak, the question is time-sensitive, or provenance quality matters, the model can call the strong-source flow directly.

It is also the second phase of this feature, not the first. The earlier iteration overreached: it tried to teach the model a local worldview of categories, trusted-source classes, and registry-driven domain selection. That looked principled on paper, but it created a brittle behavioral contract, permanent curation debt, and too many ways to miss the obvious next step. This fork did not try to preserve that design out of pride. It was reverted in commit history to get the product back to a simpler working state.

The replacement is intentionally closer to the shape now favored by strong industry signals and a lot of applied retrieval work:

- one bounded evidence-search primitive instead of a multi-step registry ritual
- optional domain constraints when the task already knows the right sites
- server-side policy enforcement for blocked domains
- explicit status, fallback, and evidence surfaces instead of hidden planner magic

The tool contract is intentionally simple:

- call `web_research_strong` with a `query`
- optionally pass `allowed_domains` when the task already knows the domains worth constraining to
- otherwise let the tool run a bounded open-web evidence pass
- if the returned citations look relevant but the snippets are still too thin, use `fetch_url` on the best cited URL rather than restarting broad search

Instance-level blacklists are enforced server-side through `WEB_SEARCH_DOMAIN_FILTER_LIST`, so noisy domains can be blocked without teaching that policy to the model.

In short: focused search is now an inspectable bounded-evidence tool, not a registry-driven wizard pretending to be a source ontology.

### Tool Naming Matters

Model-callable tool names are part of the behavioral interface, not just cosmetics.

In practice, shared generic prefixes encouraged tool-name blending during selection. A notes-only tool and a focused web-research tool were close enough in model space that the model could invent plausible but nonexistent hybrids and keep reaching for the wrong call path.

To reduce that drift, this fork moved toward clearer affordances:

- `search_notes` -> `notes_lookup` (`search_notes` kept as a backward-compatible alias)
- `search_strong_sources` -> `web_research_strong` (`search_strong_sources` kept as a backward-compatible alias)

Tool descriptions were hardened as well:

- `notes_lookup`: **PERSONAL NOTES ONLY**
- `web_research_strong`: **WEB SOURCES ONLY**

This significantly improves real-world tool selection behavior, especially in long agentic loops where a model can otherwise get stuck in a misleading callable pattern.

### Domain Constraints Over Curated Taxonomy

The strong-search lane no longer expects the model to drive a category-to-domain selection ritual.

That simplification was voluntary. The old design tried to prevent bad source choices by maintaining more local structure than the product could realistically justify. In practice, the maintenance burden was real, the planner assumptions were fragile, and mainstream search engines were often already returning better source mixes than the local registry logic deserved credit for.

Instead:

- `allowed_domains` is an optional hard constraint when the task already has trustworthy domains
- admin/operator blacklists stay server-side
- open discovery still works when no domain constraint is provided

What was removed on purpose:

- category-to-domain selection as a first-class model ritual
- giant curated domain worldview as the primary routing mechanism
- the assumption that focused search must begin with taxonomy before it can begin with evidence

What stayed:

- bounded search
- explicit domain constraints when they are genuinely useful
- server-side domain policy
- inspectable fallback behavior

That keeps the behavioral contract closer to what models naturally expect from a focused web-research tool, while still preserving the operational controls that actually matter.

### Evidence Surface Honesty

`web_research_strong` output is now intentionally layered:

- `items` = candidate pool (exploratory)
- `evidence_items` = evidence used for quality/coverage decisions
- `citation_items` = public citation surface

Default citation policy is stricter:

- `trust >= 0.72`
- non-community only by default
- canonical URL dedupe

Middleware citation extraction for this tool now prefers `citation_items` and falls back to `items` only if needed.

Numeric score remains available for diagnostics, but is no longer a default public confidence ornament.

### Engine and Fallback Policy

Focused broader fallback is engine-agnostic and admin-driven:

- fallback uses configured `WEB_SEARCH_ENGINE` (not hardcoded Brave)
- if engine is Brave, fallback remains paced and query-capped for free-tier constraints
- existing `search_web` path remains backward-compatible
- discovery does not depend on sitemap/seed hygiene from legacy sites

### Focused Search UX Contract

When focused search runs, chat now surfaces explicit progress using the existing web-search status UI (same expandable result block style as regular web search):

- `Focused search: running targeted queries`
- visible targeted phrases (model-rewritten / planner-executed)
- visible targeted websites/domains
- visible layer counts: candidate pool, evidence used, citations shown

If local-first evidence is insufficient, chat explicitly shows escalation:

- `Focused search did not return enough evidence, trying broader search now`
- updated phrases/sites for the broader pass

When search ends, the final focused-search status event is emitted with `done=true`, so the progress shimmer stops and the phase is visibly closed. Planner score is shown only in explicit debug mode.

The intended operator reading is important here: the UI is no longer pretending that focused search is an infallible oracle. It shows where the bounded pass worked, where it degraded, and when the system broadened the search rather than quietly fabricating confidence.

### Operational Caveat: Domain Policy Belongs to the Server

`web_research_strong` may expose `allowed_domains` to the model as an explicit task constraint, but blocking bad domains is not part of the model contract.

The infrastructure blacklist lives in `WEB_SEARCH_DOMAIN_FILTER_LIST`, which already supports block syntax such as `!quora.com`. That filter is enforced server-side on search results and can also narrow focused-search runs without teaching the model phantom capabilities.

## Optional Local Corpus Lane

This fork now also supports a separate local-corpus path for cases where the strongest evidence is not on the web at all, but on disk.

That matters for a very specific class of workflows:

- proprietary literature
- bought handbooks and textbooks
- internal review corpora
- local material that should not be flattened into a generic OWUI knowledge blob

The important design choice here is the same one that shows up elsewhere in this fork:

do not collapse unlike jobs into one vague retrieval step.

For a real literature corpus, there are at least two different jobs:

- shortlist which local sources are worth searching
- retrieve evidence inside those selected sources

This fork therefore does not treat a local corpus as "just another text pile to ingest".

It supports a domain-first lane with a split architecture:

- a lightweight serving layer for source selection
- per-document compiled artifacts for actual evidence retrieval

There are now two local-corpus operating paths:

- `v1`: direct lookup, shortlist, book card narrowing, evidence retrieval, table follow-up
- `v2`: a bounded reasoning layer for abstract questions, with problem framing, axis planning, grouped evidence, and conservative sufficiency assessment

### Why This Exists

The immediate practical reason was medical literature.

If you take a shelf of guidelines, manuals, and textbooks and dump all of it into a generic flattened RAG collection, you lose exactly the distinctions that matter:

- which domain a source belongs to
- which discipline it belongs to
- whether it is a guideline, handbook, textbook, atlas, or reference
- where the relevant evidence actually lives
- whether the useful answer is in a nearby table rather than in a prose paragraph

So the local-corpus path here was built around a stricter idea:

first narrow the source set, then retrieve evidence inside that narrowed set.

That gives the model a better chance of behaving like a careful reader instead of a stochastic blender.

### Engineering Path

The path that led here was intentionally incremental.

1. Keep the literature outside OWUI's generic ingest path.
2. Compile each source into a per-document artifact set.
3. Build a tiny markdown serving layer in front of those artifacts.
4. Add a domain-first router instead of a medicine-only one, so future domains can be added without redesign.
5. Add native tools that preserve `domain`, `discipline`, `book_id`, `resource_type`, page metadata, and nearby tables/figures.
6. Use lexical-first retrieval with metadata-aware reranking instead of jumping straight to embedding-heavy complexity.
7. Validate the whole flow against a live OWUI instance with tool telemetry turned on.

There is now also a field note for the corpus-evidence reranking pilot on the Strix Halo box, including what was tested, what failed, what actually helped, and the exact host setup commands used to get ROCm-backed reranking working without moving OWUI into a toolbox container:

- `docs/rocm-rerank-pilot.md`

That last step matters.

The point was not just to make the tools exist. The point was to prove that a single loaded model could actually:

- choose the right local domain
- narrow to the right books
- retrieve evidence from those books
- open a table when the evidence was table-shaped
- answer with book/page/section grounding instead of free-floating synthesis

### How The Local Corpus Path Behaves

At a high level, the local-corpus lane behaves like this:

1. user asks a question
2. OWUI routes to a local domain when that is appropriate, or asks for domain narrowing if the query is ambiguous
3. the model shortlists books from the serving layer
4. the model opens book cards only for the shortlisted books
5. retrieval searches only those selected books
6. results return page, section, and nearby table/figure pointers
7. the model can open a table explicitly when the answer depends on it

That means the serving layer is not the evidence base.

It is the selector that sits in front of the evidence base.

The evidence base is still the per-document compiled output.

### Native Tool Surface

The model-facing tool family is intentionally narrow and explicit:

- `local_corpus_list_domains`
- `local_corpus_list_disciplines`
- `local_corpus_frame_problem`
- `local_corpus_plan_axes`
- `local_corpus_collect_axis_evidence`
- `local_corpus_assess_evidence`
- `local_corpus_shortlist_books`
- `local_corpus_view_book_cards`
- `local_corpus_retrieve_evidence`
- `local_corpus_view_table`
- `local_corpus_view_figure_metadata`

That tool split is not cosmetic.

It is there to stop the system from pretending that "find a likely book" and "quote the evidence inside that book" are the same operation.

The important constraint is that `v2` is still bounded and inspectable.

- axis count is backend-capped
- axes are scaffolds, not claims of completeness
- the backend assessor is intentionally conservative and narrow
- packs exist at different maturity tiers and are not presented as equally battle-tested

### What A Corpus Should Look Like

If you want to use this path in your own fork, the expected shape is simple and strict.

At the corpus root:

- `_serving/domains/index.md`
- `_serving/serving-catalog.jsonl`

Then, per domain:

- `_serving/domains/<domain>/index.md`
- `_serving/domains/<domain>/disciplines/*.md`
- `_serving/domains/<domain>/books/*.md`

Then, per document:

- `<slug>--<book_id>/selected/retrieval.md`
- `<slug>--<book_id>/selected/catalog.json`
- `<slug>--<book_id>/selected/figures.json`
- `<slug>--<book_id>/selected/tables/`

That split is the contract.

The `_serving` tree is the canonical model-facing selection layer.
The per-document `selected/` tree is the canonical evidence layer.

If you preserve that distinction, the router and tool chain stay useful even as the corpus grows across domains.

If you flatten everything into one undifferentiated collection, you are throwing away the main value of the design.

### Configuration and Runtime Expectations

The local-corpus lane is optional and local-first.

The important toggles are:

- `ENABLE_LOCAL_CORPUS_TOOLS`
- `LOCAL_CORPUS_ROOT`

There is also a chat-scoped operating control:

- `local_corpus_mode = off | auto | prefer`

That control exists because users do not always want the same thing.

- `off`: answer from weights and other enabled lanes; do not inject local corpus tools
- `auto`: let routing decide, but in `default` function calling first perform one deterministic inspection of the currently usable local domains before abandoning the local lane
- `prefer`: bias toward local corpus when the question is compatible

By default, the fork will auto-enable the tool family when a compatible `literature_corpus/` directory exists at the repo root.

Derived lexical indexes are built as disposable local cache artifacts under `backend/data/local_corpus/`. The corpus itself remains the source of truth.

### What Was Actually Proven

The useful claim here is not "medical RAG is solved".

The useful claim is narrower and more honest:

- the domain-first architecture works
- the tool flow is stable enough for real local use
- table-aware retrieval is viable
- page/section-grounded answers are achievable without flattening the corpus

What still determines answer quality after that is ordinary retrieval work:

- shortlist quality
- intra-book ranking quality
- evidence sufficiency discipline in the final answer

That is a good place to be.

Architecture problems are expensive.
Ranking problems are irritating, but fixable.

## Voice / TTS

This fork adds Kokoro because local TTS quality matters, and a lot of local stacks are either too robotic or too heavy for the quality they provide.

Kokoro was added as a practical compromise:

- more natural sounding voices than many lightweight alternatives
- lightweight enough to remain useful in local deployments
- broad enough voice selection to make experimentation worthwhile

This fork supports Kokoro in the places where it is actually useful:

- backend/local Kokoro ONNX
- browser-side Kokoro.js where that path makes sense

The point is not to chase the largest TTS stack. The point is to improve voice quality per watt, latency, and setup complexity.

There is also an important dependency split in this fork:

- the normal backend runtime lives in `backend/.venv`, and `pull_and_run.sh` restores that environment with `python -m pip install -r backend/requirements.txt`
- Kokoro ONNX belongs to that base backend dependency set and is expected to work from the regular backend install path
- OmniVoice is intentionally optional and is not part of the base requirements; the code lazy-imports it and expects `omnivoice` plus a compatible `torch` / `torchaudio` runtime to be installed separately into `backend/.venv`

On the Strix Halo / ROCm box, the right mental model is:

- base backend install first
- then host ROCm runtime
- then ROCm `torch` / `torchvision` / `torchaudio` wheels into `backend/.venv`
- then optional extras such as OmniVoice on top of that same backend virtualenv

That layering matters because breaking or replacing a root-level repo `.venv` should not be treated as a backend TTS/runtime migration. The app itself is wired around `backend/.venv`.

## Token Exploration and Response Branching

Most chat UIs hide generation internals completely. That is convenient until you need to inspect why a response happened, or you want to deliberately fork from a different token path.

This fork adds token explorer support so you can inspect token/logit alternatives when the backend exposes the necessary telemetry. In the author's local stack, that means `llama.cpp` speaking an OpenAI-compatible API and returning usable logprob/top-logprob style data.

The explorer itself is fully implemented in this fork. The constraint is not the UI path; the constraint is backend capability.

It also supports manually creating a new response branch from a selected token alternative instead of treating the sampled continuation as sacred.

That matters for local workflows because it gives you:

- a way to inspect why a continuation happened
- a way to compare plausible alternatives
- a way to fork a response branch deliberately without rewriting the whole prompt

This is not presented here as "full interpretability". It is a practical debugging and exploration tool for model behavior.

It is also intentionally not implemented as "keep the whole live generation tree resident forever". In this fork, manual branching is driven by bounded token telemetry and a fallback prefix-forcing strategy, which is far more practical on local hardware than pretending every backend will give you infinite branching state for free.

As of 2026-04-25, the repo also carries a small downstream `llama.cpp` patch workflow under `scripts/llama_patch/`. It is not a new model runner and it does not patch compiled binaries in place. It builds local derivative images from kyuz0's Strix Halo toolboxes, applies a narrow upstream-server patch, and keeps logprob telemetry attached only to visible assistant content chunks rather than pretending tool-call deltas have clean OpenAI-compatible token probabilities.

That tooling exists because the practical target stack currently has a real backend boundary: upstream `llama.cpp` accepts streaming token logprobs and streaming native tool calls separately, but blocks the combined `stream + tools + logprobs` shape. Ariadne's local UX needs the token explorer to stay honest while native tools remain usable, so the patch workflow is deliberately fail-closed and operational rather than presented as a universal backend abstraction.

## Thinking / Reasoning Controls

Open WebUI already has a fair amount of upstream reasoning/thinking plumbing. What matters in this fork is not inventing a separate "reasoning mode", but documenting how that plumbing actually behaves in a `llama.cpp`-centric local stack.

For the `llama.cpp`-centric stack used here, the important path is template-aware thinking control. The chat UI can set `custom_params.chat_template_kwargs.enable_thinking`, which is useful when the loaded model's Jinja chat template actually checks that flag and switches behavior accordingly.

That distinction matters:

- if the template honors `enable_thinking`, the toggle is real
- if the template ignores it, the toggle is just a no-op parameter

So this is not advertised here as a fork-specific universal "reasoning mode". It is better understood as a practical control surface for backends and model templates that already expose a thinking/not-thinking branch.

That also means `enable_thinking` is not a universal cross-model convention. In this stack, `llama.cpp` passes Jinja template kwargs through to the active chat template; the template itself decides what those kwargs mean. Some templates use `enable_thinking`, some use different flags, and some ignore the concept entirely.

A representative `llama.cpp` preset for a model that honors an `enable_thinking` kwarg looks like this:

```ini
[Qwen3.5-27B-Q6_K]
model = /path/to/models/Qwen3.5-27B-Q6_K.gguf
jinja = true
chat-template-file = /path/to/models/templates/qwen35-27b-think-toggle.jinja
chat-template-kwargs = {"enable_thinking": false}
```

In that kind of setup, the fork's UI toggle is not inventing a new reasoning protocol. It is sending a real signal back to a template that was explicitly authored to switch between thinking and non-thinking prompt shapes.

The important distinction for this README is attribution: most of the generic reasoning-tag handling, thought-block rendering, and provider-specific reasoning params come from Open WebUI itself. The fork-specific point is that this repo treats template-aware thinking control as operationally important for local `llama.cpp` deployments and describes it accordingly, without pretending that one toggle can force every model/backend pair into a coherent thinking mode.

## Recent Lessons

One recent lesson is worth stating plainly.

The failure mode in the web stack was not just "retrieval bug" or "bad model".

The stack gradually drifted from:

- find source
- fetch source
- answer from fetched text

to something closer to:

- find source
- store source artifact
- query snippets from the stored artifact
- infer whether more slabs are needed
- carry extra selector and evidence-routing semantics in middleware

That design was attractive because it promised:

- better source discipline
- less prompt bloat
- more explicit evidence control

But in practice the models repeatedly showed the opposite behavior:

- they treated full fetches as if they were only previews
- they kept searching after already receiving the relevant answer span
- they drifted into storage or note-lookup mental models
- native function-calling became easier to derail because the tool contract stopped resembling a normal `open page / read page` pattern

The practical lesson is not "never plan retrieval".

The practical lesson is:

- keep retrieval bounded
- keep evidence contracts inspectable
- but preserve a model-usable mental model that still feels like reading sources, not operating a storage choreography engine

## Other Notable Divergences

The most important differences in this fork are the ones above, but the operating theme is consistent:

- working memory is managed server-side instead of being left to overflow
- recap is structured state, not a loose narrative
- recall is bounded, evidence-first, and now explicit opt-in instead of a silent background habit
- hot context is treated as a first-class runtime layer instead of an accidental byproduct of reserve math
- lexical recall is preferred, but raw evidence fallback is now part of the contract
- memory telemetry can be enabled on demand for a single turn when debugging continuity vs exactness failures
- web retrieval is planned and bounded rather than treated as a blind append-to-prompt step
- local TTS is treated as a quality problem worth solving
- token-level generation is inspectable when the backend exposes enough data

There is also early scaffolding for runtime MoE experts probing/control on compatible OpenAI-style backends. That work is real, but it is still backend-dependent and not yet central enough to this fork's identity to present as a finished headline capability.

Also, as a matter of principle, chat does not try to steal `Cmd+R` from the browser. Reload still means reload. Some boundaries deserve respect.

The bias throughout is the same: better local behavior, better debuggability, fewer "just trust the model" assumptions.

## Compatibility / Install

This is still Open WebUI under the hood, and the basic deployment model remains broadly compatible with upstream expectations. If you already know how to run Open WebUI, that knowledge still transfers.

This README intentionally does not duplicate the upstream install matrix. The fork is primarily aimed at local deployments and local model runners, and the most practical path is usually to keep using the deployment method you already use for Open WebUI while applying this fork's code and settings.

The author's practical target stack is:

- `llama.cpp` as the serving engine
- AMD Strix Halo as the local hardware platform
- the prebuilt Strix Halo toolboxes maintained here:
  - https://github.com/kyuz0/amd-strix-halo-toolboxes/tree/main

Those toolboxes are worth calling out because they make `llama.cpp` on Strix Halo much less painful to operate, and this fork has been developed with that runtime reality in mind rather than against an abstract "supports every backend equally" ideal.

That also explains some of the fork's behavior:

- context maintenance exists because `llama.cpp` will not semantically manage long chat history for you
- recall is bounded because local prompt budget and latency are both visible costs
- thinking mode is useful only when the active model template actually honors the flag being sent
- token exploration is built around telemetry that an OpenAI-compatible local server can actually expose
- some advanced controls, such as MoE probing, remain conditional on backend support rather than being treated as guaranteed product invariants

For generic deployment guidance, refer to the upstream Open WebUI documentation:

- https://docs.openwebui.com/
- https://github.com/open-webui/open-webui

## Upstream Attribution

This fork is built on Open WebUI, and upstream remains the base platform.

The goal here is not to replace upstream, but to diverge where local-first usage benefits from different tradeoffs: tighter context management, bounded recall, better lightweight voice options, and more transparent generation tooling.

Licensing and attribution remain those of the underlying project and this fork's codebase. See [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY).

---

Maintained by [Damyan Deshev](https://github.com/damyan-deshev) - local-first software, deterministic data paths, retrieval, evaluation, and practical product systems.
