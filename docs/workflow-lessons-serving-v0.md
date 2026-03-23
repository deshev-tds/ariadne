# Workflow Lessons Serving V0

This document defines the first consumer-facing product of the workflow-learning epic.

It sits between raw `Workflow Diary` artifacts and any later weekly digest, playbook promotion, or variant engine.

Primary inspiration:

- `Hyperagents` (`arXiv:2603.19461v1`)

The relevant lesson from that paper for Ariadne is not autonomous self-rewriting.

It is that a system only improves if it can turn experience into reusable, inspectable lessons that later steps can actually consume.

## Current Status

`Workflow Lessons Serving V0` is now implemented as a builder-only layer.

Implemented pieces:

- `workflow_lessons/internal/lessons-catalog.jsonl`
- generated `workflow_lessons/_serving/`
- `scripts/build_workflow_lessons_serving.py`
- builder and validation logic in
  [workflow_lessons.py](../backend/open_webui/utils/workflow_lessons.py)
- regression coverage in
  [test_workflow_lessons_serving.py](../backend/open_webui/test/util/test_workflow_lessons_serving.py)
- three hand-authored `promoted` seed lessons spanning `research` and `offsec`

Validation completed on 2026-03-23:

- local builder and regression tests passed before deploy
- production filesystem smoke confirmed `_serving/` was generated with family pages and lesson cards
- production UI/API smoke confirmed no runtime regression on plain turns, native tool turns, or a realistic research/tool turn
- direct HTTP streaming native tool execution remains a separate API-path caveat for `GLM-4.7-Flash-UD-Q8_K_XL`, but this does not affect the builder-only serving milestone itself

Current constraints:

- there is still no runtime consumer
- only hand-authored `promoted` seed lessons are surfaced from the repo-root curated workspace
- diary-fed runtime lessons are still `observed` only and stay under `AGENTIC_ARTIFACTS_DIR/_workflow_lessons_runtime/`
- `Workflow Diary` still does not promote anything into the repo-root catalog automatically

## Why This Exists

`Workflow Diary Phase 1A` already captures trustworthy per-turn operational snapshots.

That is necessary, but it is not yet the product Ariadne or the model should consume.

If Ariadne consumes raw diary packets directly:

- token cost grows too quickly
- prompt clutter increases
- KV-cache pressure gets worse
- the model spends attention on storage fields instead of decisions

So the next layer must separate:

- internal structured memory
- model-facing lesson serving

## Design Rule

The lesson substrate must be consumer-first.

That means every artifact shape should be judged by three consumers:

- the maintainer reviewing and promoting lessons
- Ariadne aggregating and selecting lessons
- the model consulting a small number of lessons during live work

If one of those consumers would need a different representation, we should generate that representation instead of forcing one format to do every job.

## Canonical Split

### 1. Internal lessons catalog

This is the machine-facing source of truth.

Use it for:

- aggregation
- dedupe
- promotion state
- provenance
- filtering
- builder inputs

Recommended format:

- `JSONL` or similarly compact structured rows

Important rule:

- this is **not** injected into the model prompt directly

### 2. Model-facing serving cards

This is the inference-facing layer.

Use it for:

- cheap lesson selection
- bounded live consultation
- compact runtime guidance

Recommended format:

- strict markdown cards

Important rule:

- these cards should be generated from the internal catalog
- markdown is acceptable here because it is for model consumption, not for canonical storage

## Why Markdown Cards Are Preferred At Runtime

The existing medicine and Offsec corpora already show the right pattern:

- keep an internal normalized source of truth
- generate a thin markdown-first serving layer for the model

Relevant references:

- [Medical Corpus README](/Volumes/External/Books/Medicine/_compiled_docling_review/README.md)
- [Offsec Corpus README](/Volumes/External/Books/Offsec/README.md)
- [build_markdown_serving_layer.py](/Volumes/External/Books/Medicine/_compiled_docling_review/tools/build_markdown_serving_layer.py)
- [build_offsec_serving_taxonomy.py](/Volumes/External/Books/Offsec/tools/build_offsec_serving_taxonomy.py)

Markdown is preferred over raw JSON at runtime because:

- it is cheaper in tokens
- it is easier for the model to scan
- it aligns with the current corpus-serving pattern Ariadne already uses
- it keeps structured sections without forcing the model to read storage-oriented keys

The risk is not "markdown".

The risk is loose, narrative markdown.

So the cards must stay tightly templated.

## Serving Card Contract

Each lesson card should be short, operational, and scoped.

Recommended sections:

- title
- applies when
- prefer
- avoid
- signal
- confidence
- last updated

Optional section:

- do not apply when

Each section should stay short and list-shaped.

Do not include:

- long provenance dumps
- raw telemetry
- full diary prose
- verbose JSON blobs

## Runtime Injection Policy

The serving layer should be consulted sparingly.

Recommended policy:

- shortlist lesson ids first
- open at most one or two lesson cards per turn
- inject them ephemerally for the current turn only
- do not keep full lesson cards hot across the whole chat

If cross-turn continuity is needed, preserve only:

- active lesson ids
- or one distilled server-owned note

Do not preserve the full serving-card body in hot context by default.

## Reuse Strategy

What to reuse:

- the corpus pattern of `internal source of truth -> generated serving layer`
- the markdown-first serving posture from medicine and Offsec
- the compact consultation style already present in [offsec_corpus.py](/Volumes/External/projects/open-webui/backend/open_webui/retrieval/offsec_corpus.py)

What not to treat as canonical:

- generic notes
- prior-work fallback tools
- ad hoc free-form markdown files

Those are useful as leads, but they are not the durable improvement substrate.

## Relationship To Workflow Diary

`Workflow Diary` remains the capture and retrospective substrate.

`Workflow Lessons Serving V0` is the first consumer-facing layer built on top of it.

That means:

- diary packets are raw evidence
- materialized diary entries become lesson candidates
- the lessons catalog stores normalized candidate lessons
- serving cards are the only thing the model should normally see

## Immediate Next Steps

1. Review real runtime `observed` lesson rows before adding enrichment or promotion logic.
2. Keep diary-fed runtime rows separate from the curated repo-root catalog.
3. Keep the serving layer builder as the only path that materializes model-facing lesson cards.
4. Delay runtime consultation until diary-fed lesson rows prove useful in practice.

Why this is next:

- packet capture is already trustworthy enough to act as diary substrate
- the serving contract is already real and validated, so new lesson rows now have a stable consumer-facing target
- this is the first step that can turn raw operational memory into reusable improvement memory
