# Ariadne Roadmap

This document tracks Ariadne's path from a strong local-first runtime with bounded lanes into a system that can learn from repeated workflows and gradually improve its own agent behavior.

Primary inspiration:

- `Hyperagents` (`arXiv:2603.19461v1`)

The target is not a self-rewriting production runtime. The target is a practical local workbench that gets better at:

- choosing the right lane
- preserving task invariants through long workflows
- separating verified facts from reasonable inference
- remembering which operational heuristics save time on repeated tasks
- testing prompt/routing/playbook variants offline before promotion

This is the bounded, high-ROI path toward the lessons worth borrowing from the `Hyperagents` paper.

## Why This Exists

This roadmap exists so Ariadne does not lose the thread between:

- tactical fixes
- medium-term workflow learning
- longer-term bounded "evolution" of agent policy

Without a written sequence, the fork risks accumulating clever local improvements that do not compound.

With a written sequence, Ariadne can move toward a system that does not merely solve tasks, but gradually improves how it solves recurring classes of tasks.

## Strategic Frame

The important lesson from `Hyperagents` is not "let the system rewrite its own code in production."

The useful lessons are narrower:

- structured improvement beats vague "be smarter" prompting
- persistent memory about what worked and what failed matters
- evaluation substrate matters more than vibes
- reusable meta-level heuristics are more valuable than one-off prompt wins
- transfer matters: if an improvement helps only one narrow case, it is a patch, not progress

For Ariadne, that translates to:

- evolve agent policy before even thinking about evolving core code
- prefer offline selection and promotion over live autonomous mutation
- optimize for one maintainer and one daily power-user, not generic mass adoption
- keep all new behavior inspectable and reversible

## What Ariadne Already Has

These are the current building blocks that make the roadmap realistic rather than aspirational.

### 1. Deliberate Lane Split

Ariadne already has explicit lanes instead of one flat "chat with tools" surface:

- chat lane
- web retrieval lane
- local corpus lane
- deep research lane

Why this matters:

- different workflows already have different operational contracts
- future workflow learning can attach to lane choice instead of guessing from free-form history
- this is already closer to a task/runtime graph than to a naive chat UI

### 2. Bounded Context and Exact Recall

Ariadne already has:

- bounded hot context
- structured state snapshot
- exact recall
- ledger continuity

Why this matters:

- workflow learning needs durable state and bounded re-entry
- long-running chats already have a server-owned memory model instead of naive replay
- future playbooks can use this substrate instead of inventing a second memory system

### 3. Inspectable Telemetry

Ariadne already exposes:

- `memoryTelemetry`
- `toolJourneyTelemetry`
- `promptTelemetry`
- runtime telemetry tap

Why this matters:

- no workflow diary should be built from vibes alone
- the system already emits the raw operational signals needed for later evaluation and retrospectives

### 4. Background Source Diary

Ariadne already writes a bounded `source_diary` for completed research turns.

Why this matters:

- this is the first real example of post-turn reflective artifact generation
- it can be generalized into a wider workflow diary instead of being replaced

### 5. Guided Offsec Skeleton

Ariadne already has an Offsec guided lane with:

- consultation
- structured plan registration
- step result registration
- bounded execution budget per step
- explicit confirmation boundaries

Why this matters:

- Offsec is already closer to a stateful agent loop than the rest of the runtime
- this is the right place to learn operational heuristics before trying broader agent-policy evolution

### 6. Bounded Specialist Slot

Ariadne already has a narrow specialist path for bounded transforms.

Why this matters:

- background retrospective jobs can use a cheaper/smaller model
- future variant evaluation and diary generation do not need to block the main chat model

## What Ariadne Still Does Not Have

These are the important gaps between today's runtime and the workflow-learning system we actually want.

### Missing 1. Workflow Diary Across Lanes

What is missing:

- a unified retrospective artifact for completed workflows
- lane-aware summaries of what worked, what failed, and what was wasteful
- a place to store candidate operational heuristics

Why it matters:

- right now Ariadne has telemetry and a research-only source diary, but not a general "garage notebook"

### Missing 2. Weekly Background Digest

What is missing:

- a scheduled background job that aggregates diary entries
- recurring pattern detection across chats
- a bounded weekly artifact for operator review

Why it matters:

- raw diary entries alone do not compound
- the system needs a slower, lower-frequency aggregation pass that does not interfere with daytime use

### Missing 3. Workflow Pack V1

What is missing:

- a fixed set of real Ariadne workflows used for regression and comparison
- repeated research cases
- repeated artifact-export cases
- repeated offsec cases

Why it matters:

- without a task distribution, "improvement" becomes impossible to measure honestly
- this is Ariadne's bounded equivalent of the paper's evaluation substrate

### Missing 4. Evidence / Inference Contract

What is missing:

- a stable way to distinguish:
  - verified facts
  - reasonable inference from evidence
  - background knowledge
  - unknown / unverified
- a stable access-state label for sources:
  - full text
  - stored snippets only
  - landing page only
  - news/reporting only
  - metadata only

Why it matters:

- Ariadne should not suppress inference
- Ariadne should suppress false attribution and fake confidence

### Missing 5. Playbook Promotion Layer

What is missing:

- a registry of recurring heuristics
- promotion states such as:
  - observed
  - repeated
  - promoted
- a review surface where operator judgment can decide promotion

Why it matters:

- not every useful note should immediately change runtime behavior
- Ariadne needs a boundary between observation and promoted policy

### Missing 6. Offline Variant Engine

What is missing:

- bounded offline variants of:
  - planning prompts
  - synthesis prompts
  - retrieval sufficiency rubrics
  - playbook fragments
  - routing hints
- a way to compare them on a fixed workflow pack

Why it matters:

- this is the practical place where Ariadne can start to "evolve" agent behavior
- it is far safer and cheaper than autonomous production mutation

### Missing 7. Transfer Tests

What is missing:

- explicit testing of whether a useful heuristic from one workflow class helps another

Why it matters:

- the paper's most interesting lesson is not local improvement, but improvement in the ability to improve
- transfer is how Ariadne will tell the difference between a patch and a meta-level gain

### Missing 8. Bounded Meta-Agent Suggestions

What is missing:

- a retrospective assistant that reads diaries, digests, and pack results
- proposes one bounded change
- does not auto-promote it

Why it matters:

- this is the later, safer version of "agent self-improvement" that still keeps the maintainer in control

## Roadmap Principles

These are the rules for deciding what gets built first.

### Principle 1. Capture Before Mutation

Before Ariadne changes behavior based on recurring workflows, it should first capture what happened and why.

### Principle 2. Aggregate Before Promotion

Single-chat lessons are noisy. Promotion should require repetition or operator review.

### Principle 3. Evaluate Before Evolving

No variant engine should exist before a fixed workflow pack exists.

### Principle 4. Preserve Inference, Tighten Attribution

The goal is not citation-only answering. The goal is truthful synthesis.

### Principle 5. Keep Runtime Changes Reversible

Anything promoted into runtime behavior should be removable without surgery.

### Principle 6. Optimize for ROI, Not for Grandiosity

If a step does not obviously help a one-maintainer, one-user local system, it should wait.

## Ordered Roadmap

This is the intended build order.

### Milestone 1. Workflow Diary V1

Goal:

- create the first real "garage notebook" for Ariadne

Current status:

- `Phase 1A: Persisted Operational Snapshot` is implemented
- production validation was completed on 2026-03-23 for the real native streaming tool path
- `Workflow Lessons Serving V0` is implemented, deployed, and validated as the builder-only consumer-facing layer
- `Phase 1B: Deterministic Materializer` is implemented as a manual/admin path
- production validation was completed on 2026-03-23 for one real `research` turn and one real `offsec` turn through the materializer path
- `Workflow Lesson Taxonomy Registry V1` is now implemented locally as the canonical identity layer for runtime lessons
- local validation now covers registry-backed materialization, repeated-candidate review, and one-by-one export into the curated catalog
- the remaining work for this milestone is production validation of the review/export path, then later enrichment and aggregation

Design doc:

- [Workflow Diary V1 Design](./workflow-diary-v1-design.md)
- [Workflow Lessons Serving V0](./workflow-lessons-serving-v0.md)

What it should do:

- run after completed chats or completed assistant turns
- classify workflow type
- record lane usage and major tools
- record operator-visible failure modes
- capture candidate heuristics in bounded language

Minimum output:

- one markdown or JSON artifact per analyzed workflow

Why first:

- everything else in this roadmap depends on not forgetting what happened

Depends on:

- existing telemetry
- existing chat artifacts
- existing `source_diary` machinery as a starting point

What Phase 1A already covers:

- bounded per-turn packet capture
- strict eligibility for operationally meaningful turns
- chat-scoped packet identity
- fail-open packet writing that does not change runtime behavior

What still remains inside Milestone 1:

- production validation of the registry-backed review/export path
- accumulation of enough real runtime rows to exercise the first `repeated` candidate on-host
- specialist enrichment fallback policy
- optional maintenance/admin ergonomics beyond the current manual CLI

Related implemented substrate:

- `Workflow Lessons Serving V0` now exists as the builder-only consumer-facing layer for promoted lessons
- production smoke confirmed the generated `_serving` layer exists on-host and does not change runtime behavior by itself
- `Workflow Diary` now materializes deterministic `observed` lesson rows into a runtime catalog under `AGENTIC_ARTIFACTS_DIR/_workflow_lessons_runtime`
- production smoke confirmed the runtime catalog rebuilds cleanly from real diary entries while keeping `_serving` empty for non-promoted rows
- runtime lesson rows are now registry-backed through `workflow_lessons/internal/taxonomy-registry.json`
- repeated clustering and curated export now work on canonical registry identity rather than on surface wording

### Milestone 2. Weekly Background Digest V1

Goal:

- aggregate diary entries into a slower, more strategic summary

What it should do:

- run off-hours
- cluster repeated failures
- cluster repeated wins
- emit candidate playbook notes
- surface unresolved questions worth explicit review

Minimum output:

- one weekly digest artifact

Why second:

- diary entries are raw memory
- the digest turns memory into usable operator review material

Depends on:

- Workflow Diary V1

### Milestone 3. Workflow Pack V1

Goal:

- create the fixed task substrate Ariadne currently lacks

What it should include:

- research workflows with local corpus use
- research workflows with web evidence and synthesis
- long-form artifact/export workflows
- offsec guided operational workflows

Minimum output:

- a versioned pack of recurring tasks with success notes and failure traps

Why third:

- once diary and digest exist, Ariadne can choose real repeated workflows rather than synthetic benchmarks

Depends on:

- Workflow Diary V1
- Weekly Background Digest V1

### Milestone 4. Evidence / Inference Contract

Goal:

- preserve synthesis quality while tightening truthfulness

What it should add:

- claim classes
- source access-state classes
- output expectations for evidence-backed synthesis

Minimum output:

- stable runtime contract for research answers

Why fourth:

- once real workflows are captured, Ariadne can formalize the line between grounded fact and inference without guessing

Depends on:

- Workflow Pack V1

### Milestone 5. Playbook Registry and Promotion

Goal:

- let Ariadne remember repeated operational heuristics without turning every diary note into runtime law

What it should add:

- playbook registry
- promotion states
- review workflow
- runtime consumption only for promoted entries

Minimum output:

- a bounded set of reusable operator-approved playbooks

Why fifth:

- this is the first point where repeated observations become stable runtime guidance

Depends on:

- Workflow Diary V1
- Weekly Background Digest V1
- Workflow Pack V1
- Evidence / Inference Contract

### Milestone 6. Offline Variant Engine

Goal:

- begin bounded "evolution" of agent policy offline

What it should vary:

- prompts
- routing instructions
- synthesis contracts
- sufficiency rubrics
- playbook variants

What it should not vary:

- production code blindly
- core runtime architecture

Minimum output:

- a scored archive of bounded policy variants

Why sixth:

- this is where Ariadne starts to borrow the paper's evolutionary flavor in a practical form

Depends on:

- Workflow Pack V1
- Evidence / Inference Contract
- Playbook Registry and Promotion

### Milestone 7. Transfer Testing

Goal:

- measure whether useful policy improvements transfer across workflow classes

Minimum output:

- explicit notes about which improvements are local and which are meta-level

Why seventh:

- transfer is the first honest test of "improving the ability to improve"

Depends on:

- Offline Variant Engine

### Milestone 8. Bounded Meta-Agent Proposal Loop

Goal:

- let Ariadne propose candidate improvements without granting it production authority

What it should do:

- read diaries
- read weekly digests
- read pack results
- propose one bounded change at a time
- require human review before promotion

Why eighth:

- only after Ariadne has memory, aggregation, evaluation, and transfer data does a meta-agent proposal loop become grounded rather than theatrical

Depends on:

- Workflow Diary V1
- Weekly Background Digest V1
- Workflow Pack V1
- Playbook Registry and Promotion
- Offline Variant Engine
- Transfer Testing

## Dependency Summary

Short form:

- `Workflow Diary V1` -> `Weekly Background Digest V1`
- `Workflow Diary V1` + `Weekly Background Digest V1` -> `Workflow Pack V1`
- `Workflow Pack V1` -> `Evidence / Inference Contract`
- `Workflow Diary V1` + `Weekly Background Digest V1` + `Workflow Pack V1` + `Evidence / Inference Contract` -> `Playbook Registry and Promotion`
- `Workflow Pack V1` + `Evidence / Inference Contract` + `Playbook Registry and Promotion` -> `Offline Variant Engine`
- `Offline Variant Engine` -> `Transfer Testing`
- everything above -> `Bounded Meta-Agent Proposal Loop`

## What We Are Explicitly Not Doing Yet

Not now:

- self-modifying production code
- auto-promotion of runtime behavior without review
- optimization against one narrow hidden metric
- unconstrained multi-agent swarms
- full archive search over whole codebase rewrites
- "improvement" based only on anecdotal good vibes from one chat

These may be revisited later, but they are intentionally out of scope for the current roadmap.

## Immediate Next Step

The next implementation target should be:

### `Workflow Diary V1` lesson-row review and promotion policy

Reason:

- deterministic capture exists
- the lessons serving contract now exists too
- the builder-only serving layer has already been deployed and smoke-validated on production
- deterministic materialization now exists too
- registry-backed review/export now exists locally
- the next ROI move is to validate that real runtime rows can accumulate into honest `repeated` candidates and then be exported safely into the curated policy layer

Tactics:

- keep deterministic materialization and registry-backed identity as the always-available baseline
- validate the review/export CLI path on real host artifacts before adding more automation
- only after that, add optional enrichment on top of canonical rows rather than on free-form lesson text
- continue to defer runtime injection until the diary-fed lesson corpus proves useful

If diary-fed lesson materialization is not useful in practice, the later digest/playbook phases should be reconsidered before more complexity is added.
