# Workflow Learning Loop

This document explains Ariadne's workflow-learning loop in product terms rather than code terms.

It is meant for someone who wants to understand:

- what the workflow-learning system is
- why it is structured this way
- how lesson candidates are generated
- how a repeated workflow becomes a promoted lesson
- what the current admin UI does and does not do

## What This Is

Ariadne now has a bounded way to learn from repeated work without changing live runtime behavior automatically.

At a high level, the system does this:

1. capture a trustworthy per-turn operational snapshot
2. turn that snapshot into a deterministic lesson observation
3. cluster repeated observations across distinct chats
4. let the operator promote a repeated pattern into the curated lesson catalog
5. generate a compact serving card from the curated lesson

That means Ariadne can now move from:

- "a lot of telemetry and some intuition"

to:

- "an inspectable repeated lesson with provenance and a deliberate promotion boundary"

This is the first real workflow-learning loop in the project.

## Why It Works

The loop works because it is intentionally conservative.

It avoids the two most common failure modes of "agent learning" features:

- promoting vibes instead of evidence
- letting free-form wording drift decide whether two lessons match

The design stays stable by enforcing these rules:

- capture uses the exact persisted assistant turn, not reconstructed chat memory
- candidate generation is deterministic and allowlisted
- lesson identity is registry-backed, not based on surface wording
- repeated status requires distinct chats, not just repeated turns in one chat
- promotion is manual and one-by-one
- model-facing lesson cards are generated only from curated promoted lessons

So the system does not "discover wisdom" in a vague sense.

It records operational evidence, normalizes it into a stable lesson shape, and only then lets the operator promote it.

## The End-To-End Flow

### 1. Runtime work happens normally

The user runs a normal chat, research, local-corpus, or Offsec workflow.

Nothing about the live answer path changes just because workflow learning exists.

This is an important constraint:

- no runtime injection
- no auto-promotion
- no hidden behavior changes in the chat path

### 2. Ariadne captures a per-turn packet

After a meaningful assistant turn is persisted, Ariadne writes a bounded packet into the chat's artifact directory.

That packet is the raw operational evidence for the turn.

It includes only stable, persisted fields such as:

- assistant content and output
- turn recap
- termination cause
- guided Offsec state
- memory, tool, and prompt telemetry presence
- small request context such as working mode and function-calling mode

The packet is intentionally narrow. It is not a full chat replay.

### 3. The materializer turns packets into diary entries and observed lessons

An explicit materializer command reads those packets and produces two things:

- a per-turn diary entry
- a runtime lessons catalog with `observed` lesson rows

The diary entry is the human-inspectable retrospective artifact.

The observed lesson row is the machine-facing normalized record that can later be clustered.

These runtime artifacts live under:

- `AGENTIC_ARTIFACTS_DIR/.../workflow_diary/entries/`
- `AGENTIC_ARTIFACTS_DIR/_workflow_lessons_runtime/internal/lessons-catalog.jsonl`

This is still staging data, not curated project policy.

### 4. Candidate lessons are generated deterministically

Observed lesson rows are not written as free-form prose guesses.

They are generated from a small allowlisted taxonomy registry in:

- `workflow_lessons/internal/taxonomy-registry.json`

That registry defines:

- allowed lesson patterns
- allowed condition codes
- allowed prefer codes
- allowed avoid codes
- allowed signal codes
- the canonical human-facing rendering of each pattern

In other words, the system does not invent new lesson types on the fly.

It can only emit rows that match a known pattern.

Current pattern families are deliberately small:

- `research_local_corpus_grounded_turn`
- `research_web_evidence_grounded_turn`
- `offsec_guided_bounded_turn`

This keeps the early system narrow enough to trust.

### 5. Review turns observed rows into repeated candidates

The review step reads the runtime observed catalog and clusters rows by canonical lesson identity.

It does not cluster by free-form text.

It clusters by registry-backed fields such as:

- `registry_version`
- `pattern_key`
- canonical condition codes
- canonical prefer codes
- canonical avoid codes
- canonical signal codes

This matters because two real repeated workflows may be described with slightly different wording, but should still count as the same lesson if their canonical identity matches.

A lesson becomes `repeated` only when it appears in at least two distinct chats.

That prevents one noisy chat from manufacturing a fake repeated pattern.

The review step writes:

- `internal/repeated-candidates.jsonl`
- `review/latest.md`

The markdown digest is the operator-facing summary.
The JSONL file is the machine-facing review artifact.

### 6. The operator promotes one repeated candidate

Promotion is an explicit operator action.

It exports one repeated candidate into the curated repo-root catalog:

- `workflow_lessons/internal/lessons-catalog.jsonl`

The promoted row keeps:

- canonical registry identity
- rendered human-facing lesson text
- source turn provenance

Promotion is intentionally one-by-one.

That is the boundary between:

- runtime observation
- curated project policy

### 7. The builder generates the serving layer

The serving builder reads the curated catalog and writes compact markdown lesson cards into:

- `workflow_lessons/_serving/`

Only `promoted` lessons are materialized into this serving layer.

Observed and repeated runtime artifacts never become live serving cards automatically.

This is what keeps the model-facing layer bounded and reviewable.

## How Candidate Lessons Are Generated

This is the key question for anyone trying to judge whether the loop is trustworthy.

Candidate lessons are generated by combining:

- the persisted packet
- deterministic materializer rules
- the taxonomy registry

They are not generated by asking a model to write a clever retrospective paragraph.

The current candidate-generation logic is pattern-based.

Examples:

- if a research turn used bounded web-evidence tools, it can become `research_web_evidence_grounded_turn`
- if a science turn used local-corpus tools, it can become `research_local_corpus_grounded_turn`
- if an Offsec turn used the guided bounded path, it can become `offsec_guided_bounded_turn`

Each observed row then receives:

- a `pattern_key`
- canonical code lists
- canonical rendered lesson text
- source turn ids
- provenance fields such as origin and update timestamp

That is why the loop is explainable:

- the operator can see the source turns
- the candidate pattern is explicit
- the canonical codes are explicit
- the promotion boundary is explicit

## Why The Registry Matters

Without the taxonomy registry, the system would still be vulnerable to hidden semantic drift.

For example:

- a slight wording change in a lesson title
- a renamed "prefer" phrase
- a split or merged lesson type

could cause the system to undercount repeated workflows even if the real operational pattern had not changed.

The registry prevents that by making canonical lesson identity explicit and versioned.

That means:

- vocabulary changes are repo changes
- incompatible changes require a registry version bump
- review never clusters across versions by accident

So "canonical identity" is not just a nice-sounding hash.
It is a governed vocabulary plus a deterministic signature.

## What The Admin UI Does

The thin admin UI at `/admin/workflow-lessons` is an operator surface over the existing file-backed workflow-learning core.

It currently supports:

- viewing runtime `observed` rows
- viewing runtime `repeated` candidates
- viewing the review digest
- viewing curated `promoted` lessons
- running the review step
- promoting a repeated candidate

It does not currently support:

- triggering materialization
- editing the taxonomy registry
- editing lesson semantics free-form
- automatic promotion
- rollback or unpromote
- replace/overwrite promotion paths

This is deliberate.

The UI is meant to be a thin management layer over a stable core, not a new source of semantic drift.

## Why The Model Does Not Read Raw JSON

The model is not meant to consume raw diary packets or raw runtime JSON catalogs directly.

Those artifacts are for:

- storage
- validation
- clustering
- review
- provenance

The model-facing layer is the compact markdown serving card generated from curated promoted lessons.

This keeps token cost and KV-cache pressure low while preserving a clean promotion boundary.

## What Has Been Proven So Far

As of `2026-03-23`, Ariadne has already validated the whole bounded loop:

- packet capture from real turns
- deterministic materialization into observed rows
- repeated clustering across distinct chats
- one-by-one promotion into the curated catalog
- generated promoted serving card
- thin admin UI for review and promotion

That does not mean the system is "finished."

It means the core workflow-learning loop now exists and works end to end.

## What Still Does Not Exist

Important things are still intentionally deferred:

- runtime lesson injection into the live chat path
- automatic policy mutation
- automatic promotion
- UI rollback or unpromote
- broad taxonomy editing from the UI
- model-written lesson semantics

Those are later-stage features, and they only make sense because the conservative core loop now exists.

## Related Docs

- [Ariadne Roadmap](./ariadne-roadmap.md)
- [Workflow Diary V1 Design](./workflow-diary-v1-design.md)
- [Workflow Lessons Serving V0](./workflow-lessons-serving-v0.md)
