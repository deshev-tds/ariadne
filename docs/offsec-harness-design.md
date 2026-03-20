# Offsec Harness Design

## Scope

This design is for OWUI-side inference and harness behavior for the existing Offsec corpus.

It is not a plan to rebuild the corpus.

The harness should adapt to the corpus as it exists today:

- Selection layer: `_serving/`
- PDF evidence layer: `_compiled_docling_review/`
- Future code sidecars: `_support/extracted_archives/`

The target is a capable agent doing live work alongside Open Terminal, not a library chatbot.

This document also assumes a broader OWUI product direction:

- OWUI should expose first-class working modes
- those modes should map to a smaller number of backend harness families
- Offsec should be one such mode, not just "another local corpus"

## Product Model

The product-facing abstraction should be `working mode`, not raw corpus selection.

Recommended user-facing modes:

- `General Mode`
- `Science Mode`
- `Offsec Mode`

Recommended backend harness families:

- `evidence_first`
- `workflow_first`

Mapping:

- `Science Mode` -> `evidence_first`
- `Offsec Mode` -> `workflow_first`

This distinction matters.

`Science Mode` is the product term.
`evidence_first` is the backend family that currently includes medicine and can later include biology, chemistry, physics, quantum mechanics, mathematics, and similar disciplines.

`Offsec Mode` is the product term.
`workflow_first` is the backend family that uses the Offsec corpus, terminal context, and targeted docs fallback to support live operator work as well as corpus-backed questions.

## Harness Families

### `evidence_first`

This is the current family that OWUI already approximates today.

It should remain the home for:

- medicine
- biology
- chemistry
- physics
- quantum mechanics
- mathematics
- similar science or reference-heavy domains

Its common traits are:

- frame the question
- identify the domain and task shape
- retrieve bounded evidence
- answer conservatively from that evidence
- treat web material as evidence to be disciplined, not just as generic search results

This family should not be named as if it were permanently the medical lane.

### `workflow_first`

This is the family Offsec belongs to.

Its common traits are:

- route quickly by task, tool, domain, or direct lookup
- use terminal context when relevant
- allow priors to do real work
- use the local corpus to sharpen and constrain
- fall back to official docs or GitHub docs for exactness
- support both live agent work and corpus-backed Q&A

The key point is that Offsec is not only an agent loop.
It still needs to answer reference-style questions from the corpus, but it should do so under a different harness policy than science/reference domains.

## What Exists Now

The Offsec corpus already has a strong markdown-first selection layer:

- task-first entry
- tool-first entry
- domain-first entry
- book cards behind those entrypoints

It also already has a usable compiled evidence layer:

- `selected/retrieval.md` is the primary serving payload
- `selected/raw.md` is the audit payload
- image-dominant pages are intentionally omitted from default retrieval context

This means OWUI should not design around "search all books first".
It should design around "route cheaply, then retrieve narrowly".

## Why The Current OWUI Local-Corpus Path Does Not Fit Offsec Yet

The current local-corpus stack is best described as `evidence_first` and science-leaning, but its concrete file adapter is still medicine-shaped.

Concrete mismatches:

- `backend/open_webui/retrieval/local_corpus.py` expects `_serving/serving-catalog.jsonl`, but Offsec uses `_serving/internal/offsec-taxonomy-catalog.json`.
- The current loader expects book cards at `_serving/domains/<domain>/books/<book_id>.md`, but Offsec uses flat book cards under `_serving/books/`.
- The current selector guidance in `backend/open_webui/utils/middleware.py` forces `local_corpus_list_domains` in `auto` mode. That is wrong for Offsec because task-first and tool-first are first-class entry modes.
- The current reasoning path is `frame_problem -> plan_axes -> collect_axis_evidence -> assess_evidence`. That is reasonable for medicine, but it is too indirect for Offsec live work.
- `backend/open_webui/config.py` currently supports a single `LOCAL_CORPUS_ROOT`. That is not enough if OWUI must support medicine mode and Offsec mode cleanly in the same deployment.

The current `backend/open_webui/retrieval/local_corpus_packs/offensive_security.json` should not drive primary Offsec behavior.
It is generic and axis-oriented, while the new Offsec corpus is explicitly built around task, tool, domain, and book routing.

## Design Principles For Offsec Mode

- Markdown-first for model-facing context.
- Tool-first is normal, not exceptional.
- Priors are allowed to do real work.
- Local corpus should sharpen approach, tool choice, examples, and platform context.
- Local corpus should not rigidly gate every answer.
- `retrieval.md` stays close to source.
- Image-dominant pages stay out of default context.
- Official docs and GitHub docs are valid fallbacks for exact syntax, version-specific behavior, and configuration details.
- Broad web search should come after targeted docs fallback, not before it.

## Recommended Runtime Model

The runtime should distinguish:

- working mode
- corpus or domain profile
- coarse retrieval preference

Recommended working mode selector:

- `general`
- `science`
- `offsec`

Recommended family mapping:

- `science` -> `evidence_first`
- `offsec` -> `workflow_first`

Recommended science profile selector:

- `medicine`
- `biology`
- `chemistry`
- `physics`
- `quantum_mechanics`
- `mathematics`
- `computer_science`

Keep `local_corpus_mode` as the coarse retrieval-preference switch for now:

- `off`
- `auto`
- `prefer`

Add a separate profile selector behind the chosen mode.

Examples:

- `working_mode=science`, `science_profile=medicine`
- `working_mode=science`, `science_profile=physics`
- `working_mode=offsec`

The profile decides:

- corpus root
- loader or adapter
- selector guidance
- fallback policy
- answer posture

This separation matters because:

- "which mode am I in?"
- "which corpus or discipline profile is active?"
- "how strongly should corpus retrieval be preferred?"

are different decisions.

## Science Mode Direction

`Science Mode` should become the product home for the current evidence-led path.

That means:

- the current local-corpus implementation should be treated as the beginning of `Science Mode`, not as a permanently medical subsystem
- medicine remains one science profile, likely the most mature one
- other disciplines can join the same family if they follow the same evidence-first retrieval logic

This is not only about local corpus behavior.
Over time, `Science Mode` should also shape how OWUI handles web information:

- stronger source discipline
- better distinction between primary and secondary sources
- clearer handling of recency versus evergreen reference material
- better evidence saturation and contradiction handling

In other words, `Science Mode` should eventually govern the broader evidence posture, not merely which local files get searched.

## Recommended OWUI Shape

### 1. Add Corpus Profiles

Introduce a small profile registry instead of baking more special cases into the current local-corpus loader.

Each profile should declare:

- `id`
- `working_mode`
- `harness_family`
- `root`
- `adapter_kind`
- `selection_policy`
- `evidence_policy`
- `docs_fallback_policy`

Examples:

- `science:medicine`
- `science:physics`
- `science:chemistry`
- `offsec`

For Offsec, the profile should point at:

- corpus root
- `_serving/internal/offsec-taxonomy-catalog.json`
- `_compiled_docling_review/compiled-offsec-review.json`

### 2. Add An Offsec Adapter

OWUI should adapt to the existing Offsec layout instead of requiring Offsec to emit medicine-style `serving-catalog.jsonl`.

The Offsec adapter should:

- load domains, tasks, tools, books, and flat markdown card paths from `offsec-taxonomy-catalog.json`
- join each book to compiled evidence using the book's `source_pdf` and the compiled review metadata
- expose normalized records for:
  - domain
  - task
  - tool
  - book
  - retrieval path
  - raw path
  - catalog path

This adapter can feed the existing evidence-indexing ideas, but it should not inherit the medicine selection assumptions.

### 3. Route Before Retrieving

Offsec should begin with a small routing step, not with domain listing and not with full evidence retrieval for every query.

The router should classify the current need into:

- `task`
- `tool`
- `domain`
- `mixed`
- `book`
- `direct_lookup`

The router should also reserve a `target` slot internally for future use, even though `targets/` does not exist yet.

Today, target-like signals should influence task or domain selection without creating a fake `targets/` dependency.

## Offsec Harness Flow

### Step 0. Enter Offsec Profile

An OWUI chat should explicitly enter the Offsec profile.

Recommended behavior:

- `working_mode=offsec`
- `local_corpus_mode=prefer` for dedicated Offsec chats
- `local_corpus_mode=auto` for general chats that may or may not be Offsec

### Step 1. Route The Query

The first Offsec-specific tool call should be a routing call, not `local_corpus_list_domains`.

Routing rules:

- Tool-first when the user already names a tool or framework.
- Task-first when the user states an objective but not an instrument.
- Domain-first when platform or body-of-knowledge is obvious but task shape is still vague.
- Mixed when both task and tool signals are strong.
- Book-first only when the user names a book directly.
- Direct lookup when the user asks about a named tool, concept, or tactic that is not represented in the curated Offsec taxonomy but may still exist in the compiled evidence.

Examples:

- "Assess this website" -> primary task `website_assessment`, supporting domain `web_security`
- "Use Burp to validate reflected XSS" -> primary tool `burp_suite`, supporting task `web_vulnerability_validation`
- "Why is this Windows token check failing?" -> primary domain `windows_security`, supporting task `windows_auth_and_access_reasoning`
- "Creative uses of ffuf" -> `direct_lookup` first, then infer supporting books or domain from evidence hits because `ffuf` is not a curated first-class Offsec selection node today

Terminal context should influence routing when available:

- current working directory
- last command
- last stderr or help text
- obvious OS or platform clues

That allows tool-first re-entry after a live terminal failure.

### Step 2. Return Small Selection Context

The router should return a compact, inspectable payload.

Suggested shape:

```json
{
  "profile": "offsec",
  "entry_mode": "task",
  "primary_entry": {"kind": "task", "id": "website_assessment"},
  "supporting_entries": [
    {"kind": "domain", "id": "web_security"},
    {"kind": "tool", "id": "burp_suite"}
  ],
  "candidate_books": [
    "web-application-pentesting",
    "bug-bounty-from-scratch"
  ],
  "next_action": "retrieve_evidence"
}
```

The returned payload should include short markdown snippets or one-line summaries from the selected cards.

Do not force a second "open card" step when the route is already obvious.

Use full card reads only when:

- routing confidence is low
- two or more books are close
- the model needs the "use when" and "avoid" details before acting

### Step 3. Narrow To Books

Book narrowing should come from the routed task, tool, and domain cards.

For `direct_lookup`, book narrowing should come from the first evidence hits.

Policy:

- prefer books that appear in the overlap of the selected task and tool
- prefer specialized books over `general_security_reference`
- keep the first pass to 1 to 3 books
- only pull in the broad reference when the focused books are weak or clearly incomplete

This keeps token cost low and avoids broad-book inertia.

### Step 4. Retrieve Evidence From Compiled Payloads

Evidence retrieval should usually run only against the selected books.

For `direct_lookup`, allow a cheap first pass across the full Offsec evidence shelf, then narrow to the books that produced hits.

Initial retrieval defaults should stay tight:

- `top_k`: 4 to 6
- `max_books`: 1 to 3
- figures: off by default
- tables: off by default unless the query looks structured

Query construction should preserve the user's substantive terms and add the routed selection context:

- original user terms
- primary task or tool title
- supporting task or tool titles when helpful
- platform tags from the selected book or selection node

For `direct_lookup`, preserve the named entity exactly and do not force it through a vague task-style rewrite.

### Step 5. Score Evidence For Offsec Work

Offsec evidence scoring should differ from medicine scoring.

It should boost:

- exact tool-name hits
- exact platform hits such as `windows`, `macos`, `android`, `web`
- methodology sections for task-first questions
- example-heavy sections for tool-first questions
- book tags coming from the routed task or tool

It should downrank:

- table of contents pages
- prefaces, forewords, "dear reader", and similar front matter that survived conservative suppression
- broad reference books when a specialized book has direct hits
- image-only or near-empty pages

`raw.md` should not be part of normal retrieval.
It is a rescue and audit lane only.

### Step 6. Let The Model Work

Offsec mode should not require a hard evidence gate before the model can answer.

The right posture is:

- use priors for the backbone
- use local corpus to sharpen and constrain
- cite local evidence where it materially improved the plan
- admit when the corpus gave methodology but not exact syntax

This is intentionally lighter than medicine mode.
It still supports corpus-backed answers, but without inheriting the full evidence-first posture of Science Mode.

## Official Docs And GitHub Fallback

The fallback ladder for Offsec should be:

1. local Offsec selection layer
2. local Offsec compiled evidence
3. official docs or GitHub docs
4. broader web search only if still needed

Official-doc fallback should be allowed when:

- the local corpus explains the method but not the exact current flags
- the task depends on exact CLI syntax
- the task depends on version-specific config keys
- the tool behavior has likely changed since the book
- the agent hits repeated syntax or usage failures
- the terminal returns `unknown option`, usage text, deprecated flags, or install mismatches
- the model needs current API or integration details not appropriate to expect from a static book

GitHub docs are valid when the tool is open source and the canonical usage or examples live in the repo.

Broad web search should not be the first fallback for Offsec.
The first fallback should be targeted docs.

By contrast, `Science Mode` should eventually treat web material as another disciplined evidence lane and apply stronger source controls there too.

## Smart Trigger Points During Live Work

The Offsec harness should encourage corpus use at specific phases, not on every turn.

High-value trigger points:

- task start, when the agent needs methodology or task framing
- first tool choice, when multiple tools are plausible
- branch points, when the agent must choose between validation paths
- after two failed syntax attempts
- after an obvious environment mismatch
- before drafting findings or report text
- when switching from recon to validation
- when switching from methodology to exact command construction

This keeps the corpus in the loop without turning the agent into a compulsive retriever.

## Context Assembly Rules

Default Offsec context should be small and layered.

Selection context:

- one primary selection node
- up to two supporting nodes
- prefer markdown card excerpts over synthetic summaries

Book context:

- zero to two full book cards on the first pass
- otherwise one-line book summaries only

Evidence context:

- three to six retrieval chunks total
- no more than two chunks from one book on the first pass
- dedupe by page and section
- prefer sections that map directly to the selected task or tool

Tables and figures:

- tables only when the question obviously needs structured content
- figures off by default
- figure metadata only on demand

Raw audit:

- `raw.md` only when retrieval looks incomplete or a suppression decision may matter

This keeps the harness token-cheap and suitable for live terminal work.

## How Offsec Should Differ From Science Mode

`Science Mode` should stay conservative and evidence-led.

Offsec mode should instead be:

- route-first instead of axis-first
- tool-first when appropriate
- lighter on evidence gating
- more willing to let priors do the backbone work
- more willing to use official docs for exactness
- less dependent on full coverage before answering

In practice, that means the Offsec profile should not default to:

- `frame_problem`
- `plan_axes`
- `collect_axis_evidence`
- `assess_evidence`

Those tools can remain available for niche reasoning-heavy Offsec questions, but they should not be the default lane.

It also means that the current evidence-led path should no longer be described as if it were synonymous with medicine.
Medicine is one profile under `Science Mode`, not the permanent name of the family.

## Future Code-Sidecar Fit

The next evidence layer will include bounded code-sidecar records.

This design should leave a clean join point for them now.

The Offsec router and retrieval layer should already think in terms of evidence kinds:

- `pdf_retrieval`
- `code_record`
- `project_record`

When code-sidecar records exist, the routing and book-selection stages should stay the same.
Only the evidence stage should expand to search both:

- selected PDF evidence
- selected code or project records tied to the same books or tools

Do not require that layer for the first Offsec harness implementation.

## Likely Code Touchpoints

Backend:

- `backend/open_webui/config.py`
- `backend/open_webui/main.py`
- `backend/open_webui/utils/tools.py`
- `backend/open_webui/utils/middleware.py`
- `backend/open_webui/retrieval/local_corpus.py`
- `backend/open_webui/retrieval/local_corpus_reasoning.py`
- `backend/open_webui/tools/builtin.py`

Recommended new backend modules:

- `backend/open_webui/retrieval/local_corpus_profiles.py`
- `backend/open_webui/retrieval/offsec_corpus.py`
- `backend/open_webui/retrieval/offsec_router.py`

Likely future naming cleanup:

- current local-corpus backend family -> `evidence_first`
- current product-facing medical lane -> folded into `Science Mode`

Frontend:

- `src/lib/components/chat/Chat.svelte`
- `src/lib/components/workspace/Models/BuiltinTools.svelte`

Tests:

- new Offsec corpus tool tests
- middleware selector tests for Offsec routing
- regression tests to ensure medicine behavior does not change

## Realistic Incremental Plan

### Phase 1. Add Profile Plumbing

- add `working_mode`
- add profile plumbing under the selected mode
- keep current medicine behavior, but relabel it conceptually as the first `Science Mode` profile
- make selector guidance family-aware and mode-aware
- stop forcing `local_corpus_list_domains` for Offsec chats

### Phase 2. Add Offsec Adapter And Router

- load Offsec taxonomy and compiled review metadata
- join books to evidence paths
- add Offsec routing, selection, and direct-lookup tools
- reuse or extract the generic page-chunk indexing logic for `retrieval.md`

### Phase 3. Add Offsec Retrieval Policy

- add Offsec-specific evidence scoring and front-matter penalties
- keep figures off by default
- keep `raw.md` as an audit lane only

### Phase 4. Add Docs Fallback Ladder

- add profile-aware selector guidance for official docs and GitHub docs
- use targeted docs before broad search
- add terminal-failure triggers for exact syntax lookup

### Phase 4.5. Grow Science Mode

- treat the current evidence-led local-corpus path as `Science Mode`
- let science profiles share one harness family
- improve how strong-source web evidence is handled under that mode over time

### Phase 5. Add Future Code-Sidecar Join

- keep the routing layer unchanged
- add code-record retrieval as a second evidence backend

## Recommended First Implementation Bias

Do not begin by trying to fully genericize the entire medicine stack.

The pragmatic first move is:

- add mode and profile plumbing
- add an Offsec adapter
- add an Offsec router
- add Offsec-specific selector guidance
- keep the current science/evidence-first behavior intact while renaming it conceptually away from a permanently medical identity

If that proves stable, the shared evidence-indexing pieces can be extracted later.

That yields the right behavior sooner and avoids destabilizing the existing medicine path.
