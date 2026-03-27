# Open WebUI Fork for Local Context, Recall, Voice, and Token Inspection

This repository is a fork of Open WebUI, built for a different priority set than the upstream project.

That priority set is:

- local-first behavior over backend-agnostic product smoothing
- long-chat survivability over naive full-history replay
- memory with evidence and recall, not summary-only compression
- fast-path UX by default, with bounded slow paths only when needed
- lightweight local voice that still sounds good enough to use
- token-level inspection and deliberate response branching for real debugging
- honest control surfaces for `llama.cpp`-style local stacks instead of universal-magic toggles

Upstream Open WebUI is a strong base platform and UI for self-hosted LLM workflows. This fork keeps that base, but diverges where local-first power-user workflows need tighter control: long-running chats, context overflow, exact recovery of old facts, practical local TTS quality, and generation inspection that is useful during real debugging rather than only during demos.

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
- The live prompt budget is now derived from the active `llama.cpp` runtime instead of being treated as a static design assumption.
- Recall is now explicitly layered as `FTS preferred, raw fallback guaranteed` for cases where lexical retrieval is not ready or not good enough.
- Request-scoped memory telemetry can be turned on per turn for debugging without leaving noisy long-lived logging enabled in production.
- Ledger continuity now uses explicit chat-scoped mode selection: `vibe` by default, `agentic` only when enabled in the UI toggle.
- Web retrieval was pushed toward planned, bounded evidence gathering instead of naive query-and-dump behavior.
- A native focused-web tool (`web_research_strong`, with `search_strong_sources` kept as an alias) was added for local-first strong-source search, with broader fallback only when evidence from locally-listed strong domains is weak.
- A domain-first local corpus path was added for proprietary or bought literature, with selection-layer markdown, per-book evidence retrieval, and table-aware follow-up tools instead of flattening everything into one generic knowledge pile.
- The `v2` local-corpus reasoning lane now uses deterministic retrieval projection so user-facing framing text does not quietly pollute axis queries and slow or misroute retrieval.
- The `default` function-calling selector now has its own runtime discipline layer, so local-corpus preference, retrieval term hygiene, and prior-work fallback do not depend entirely on native tool-calling behavior.
- In `default` function calling, `local_corpus_mode=auto` now deterministically performs a single local shelf-inspection step first (`local_corpus_list_domains`) before any web or model-only fallback, and only continues locally when the returned usable domains show a real thematic fit.
- Source routing now supports explicit `planner_hints.is_local`, so local/trusted domains can be prioritized deterministically.
- Focused search now emits visible chat status phases (targeted run, fallback escalation, completed) using the same status UI path as regular web search.
- Kokoro TTS paths were added because they sound better than many lightweight local options without dragging in a huge stack.
- Persona V1 was added so the daily-driver interaction unit can be a persona rather than only a model preset.
- Persona chats now pin requested runtime defaults per chat while keeping persona identity live across the UI.
- The app now has a dedicated `Workspace -> Personas` surface with persona-first chat selection, voice preview, and direct-model fallback.
- Automatic pre-migration SQLite backups were added before Alembic upgrades so local schema changes do not run without a fresh snapshot.
- Token explorer support and manual response branching from token alternatives were added so local generation is less of a black box.
- On-demand tool journey telemetry was added so agent/tool execution paths can be inspected per request without permanent log bloat.

The rest of this README explains the rationale behind those changes.

## Persona Runtime in This Fork

This fork now has a first usable persona layer on top of the older model-preset substrate.

The important distinction is that persona is no longer treated as "just another model with a different prompt". The runtime shape is:

`persona identity + binding + requested defaults + chat attachment`

Current Persona V1 behavior:

- personas are first-class private objects with their own workspace screen
- a chat can attach to one `persona_id`
- persona identity is live across attached chats
- persona runtime defaults are pinned per chat at creation time
- direct model use still exists as a separate fallback path
- voice belongs to the persona definition and can be previewed inline

That split matters because it keeps older chats stable when a persona's behavior settings change, while still letting the persona's visible identity update across the interface.

The current V1 implementation deliberately stops before:

- scene notes
- lorebooks
- persona-scoped continuity synthesis
- persona-scoped recall

Those remain roadmap items rather than pretending they already exist.

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
- a source registry provides machine-readable hints about where different kinds of queries should go
- the active model can be used as a query rewriter when that helps, but the system still keeps explicit fallback paths
- planner telemetry tracks retries, fallback usage, executed queries, and stopping conditions instead of treating the whole thing as opaque middleware

The important behavioral shift is this:

- upstream-style web search is often thought of as "query provider -> collect results -> inject results"
- this fork pushes it toward "plan -> target sources -> bound evidence -> stop when enough evidence exists"

That matters more on local setups than it first appears. Prompt budget is finite, retrieval latency is visible, and low-quality web evidence is actively harmful when it crowds out the rest of the conversation. The planner/rewriter/source-registry work is there to make web retrieval less brute-force and less noisy.

At a high level, the current web path behaves more like this:

1. choose a planning mode
2. optionally rewrite or refine the search queries using the active model
3. target sources with planner hints instead of treating all sources as equivalent
4. stop once the evidence quality or coverage is good enough, instead of continuing mechanically
5. surface planner status and fallback information so the path is inspectable

This is the same overall philosophy as the context work: bounded, inspectable, evidence-oriented behavior beats magical but opaque behavior.

### Strong-Source Search Trigger (Hybrid Local-First + Broader Fallback)

The web stack includes a first-class native tool for evidence-critical retrieval: `web_research_strong` (`search_strong_sources` remains as a backward-compatible alias).

This is intentionally not a hard terminal guard. It is a native model-callable path with soft trigger semantics: when confidence is weak, the question is time-sensitive, or provenance quality matters, the model can call the strong-source flow directly.

The tool now supports a stateful contract:

1. `mode=list_categories`
2. `mode=list_domains` (for selected categories)
3. `mode=search` (for selected domains)

`mode=search` remains backward-compatible for single-call usage. But when the model needs explicit routing support, the stateful path is available and bounded.

Domain selection is explicit and constrained:

- model picks `1..4` domains
- domains are validated against the registry-derived allowlist
- invalid/empty selections return a correction payload, and search is not executed

In short: focused search is now an inspectable interaction protocol, not a one-shot black box.

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

### Hybrid Routing: Coarse Gate + Model Disambiguation

Classifier bloat was avoided on purpose.

This fork keeps a lightweight coarse gate with obvious buckets:

- `software`, `medicine`, `legal`, `science`, `news`, `shopping`, `general`

Routing behavior is hybrid:

- high-confidence coarse route: skip category pass and jump directly to domain shortlist
- low-confidence / ambiguous route: model first selects category, then domains, then runs focused search

This preserves speed on easy queries and flexibility on hard queries, without drifting into endless keyword creep.

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

### Operational Caveat: `is_local` Is a Routing Primitive

`local-first` depends on `planner_hints.is_local=true` entries inside the source registry. If a selected category has no such entries, there are no local candidates for Phase A, so focused search naturally behaves fallback-heavy (fast escalation to broader/non-local search).

This is expected behavior, not a planner bug: the routing contract is "prefer local-marked domains when they exist".

### Strong Domains in Practice

This fork does not ship a local evidence corpus. It ships a locally maintained strong-source domain registry used for routing and query constraints.

Current examples (from the registry) include:

- `science_academic`: `pubmed.ncbi.nlm.nih.gov`, `ncbi.nlm.nih.gov`
- `medicine_health`: `who.int`, `cdc.gov`
- `software_apis_devops`: `docs.python.org`, `kubernetes.io`
- `legal_compliance`: `eur-lex.europa.eu`, `dv.parliament.bg`

Where this lives:

- static registry file: `backend/open_webui/retrieval/web/source_registry.json`
- admin API (read/update): `/api/v1/retrieval/web/search/planner/source-registry`

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
- recall is bounded, evidence-first, and not always-on
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
