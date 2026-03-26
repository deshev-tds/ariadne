# Verification-Native Agent Improvement Platform

Status: proposed

Owner: local fork

Last updated: 2026-03-27

## Executive Summary

This epic is about rebuilding the fork's agent-improvement line on verification-first foundations.

The earlier implementation line around `workflow diary / workflow lessons` was directionally good, but it optimized for observability and review before it optimized for instance-level verification of the current run.

The new plan keeps the good parts of that work:

- rich run capture
- structured materialization
- human review surfaces

but moves them behind a stronger center:

- verifier-facing trace
- claim/evidence representation
- targeted verification
- bounded retry feedback

## Goal

Build a local-first verification substrate for agentic runs that supports:

- test-time verification of the current run
- bounded retry loops with actionable feedback
- downstream workflow learning from verification artifacts

The design is inspired by DeepVerifier, but adapted for this fork's interactive Open WebUI environment rather than benchmark-only pipelines.

## Problem Statement

Today, agentic runs can fail in ways that are expensive and annoying for a local-first power user:

- a run reaches a plausible but weak conclusion
- the agent used tools, but the final answer is only loosely grounded in what those tools actually showed
- the run made one or two crucial mistakes, but the system cannot name them precisely
- retries, when they exist, are too generic and mostly amount to "try again"
- useful lessons from repeated failures are hard to separate from noisy one-off incidents

In practice, this means the user often has to do the hard verification step manually:

- inspect the trajectory
- infer where the run drifted
- decide whether to retry
- figure out what the retry should change

That is exactly the burden this epic is meant to reduce.

## Why This Exists

The previous `workflow diary / workflow lessons` line was useful, but it was stronger at offline observability than online verification.

It helped answer:

- what patterns repeat across chats?
- what workflow choices seem good or bad?

It did not fully answer:

- what is likely wrong in this specific run right now?
- what exactly should the agent re-check?
- should the agent retry, stop, or narrow scope?

This roadmap exists to rebuild that area on better foundations.

## Why Now

This is worth doing now for three reasons:

1. The fork already has better context hygiene, telemetry, local corpus routing, and admin surfaces than before, so a verifier now has cleaner substrate to stand on.
2. DeepVerifier gives a credible reference shape for what the loop should look like, instead of inventing the next attempt from scratch.
3. The current local-first usage pattern is increasingly agentic and research-heavy, which makes "good enough answer generation" less valuable than "detect, localize, and correct likely failure points."

## Why This Matters In This Fork

This is not a generic product feature request. It matters specifically here because this fork is optimized for:

- local models
- long technical chats
- agentic workflows
- context-budget discipline
- practical debugging of model and tool behavior

In that environment, bad verification has a high cost:

- you waste limited prompt budget on low-value retries
- you keep too much trajectory in context because the system cannot distill what matters
- you spend human attention re-checking runs the system should have triaged better

## Inspired By

This roadmap is explicitly inspired by two lines of prior work.

### External inspiration

DeepVerifier contributes the most important conceptual move:

- verification should exploit asymmetry
- the verifier should decompose the problem into small, checkable questions
- feedback should be structured and actionable
- retries should be bounded

### Internal inspiration

The older local line around:

- `workflow diary`
- `workflow lessons`
- admin review surfaces

proved that this fork benefits from:

- durable run artifacts
- structured post-hoc review
- repeated-pattern mining

The new plan keeps that spirit but relocates it behind a stronger online verification core.

## Non-Goals

- not a generic "memory system"
- not full raw reasoning capture and replay
- not a prompt-only reflexion loop
- not a direct port of Cognitive Kernel-Pro or DeepVerifier's harness
- not replacing human review for promoted lessons

## Core Design Principle

The center of gravity is:

`trace -> claims/evidence -> verifier packet -> verdict/retry`

Not:

`workflow diary -> lessons -> maybe useful heuristics`

`workflow lessons` should become a downstream human-reviewed product of verification artifacts, not the primary mechanism.

## What Success Looks Like

Success is not "we have a verifier UI" or "we store more logs."

Success looks like this:

- a weak agent run can be inspected and challenged without replaying the whole run mentally
- the system can point to the specific claims or steps that are most likely wrong
- retries are shorter, more surgical, and more useful
- repeated patterns across runs can be promoted into curated lessons only after they already proved useful in verification artifacts
- a fresh agent session can resume work from repo docs alone without re-reading papers and git archaeology

## What Failed Before

The previous line did not fail because the idea was bad.

It failed because the center of the design was off.

The main failure modes were:

- too much emphasis on diary capture before verifier-facing data contracts
- lessons becoming the visible product before verification became the runtime core
- not enough separation between:
  - online correction of a current run
  - offline curation of repeated patterns across runs

That earlier work should be treated as a prototype and substrate, not as wasted effort.

## Key References

Primary paper:

- Wan et al., "Inference-Time Scaling of Verification: Self-Evolving Deep Research Agents via Test-Time Rubric-Guided Verification"
- arXiv: [2601.15808](https://arxiv.org/abs/2601.15808)
- HTML: [arXiv HTML](https://arxiv.org/html/2601.15808v1)

Reference implementation:

- GitHub: [yxwan123/DeepVerifier](https://github.com/yxwan123/DeepVerifier)
- most relevant files:
  - `System/ckv3/DeepVerifier/verifier.py`
  - `System/ckv3/DeepVerifier/template.py`
  - `README.md`

Paper takeaways that matter here:

- verification gains come from targeted decomposition, not re-solving the whole task
- verifier output should be structured and actionable
- retries should be bounded
- performance often peaks early rather than monotonically improving forever

## Relevant Local History

Previous implementation line in local git history:

- `b7dfdc96c` Add workflow diary Phase 1A capture
- `2eba9110f` Add workflow diary materializer
- `456684107` Add workflow lessons serving layer
- `3787c1202` Add workflow lesson taxonomy registry and review flow
- `b2bfed389` Add workflow lessons admin UI
- `d503b04b5` Add workflow lesson unpromote flow

Related historical design docs in git history:

- `docs/ariadne-roadmap.md`
- `docs/workflow-diary-v1-design.md`
- `docs/workflow-lessons-serving-v0.md`
- `docs/offsec-harness-design.md`

Practical reading of that older line:

- good instincts:
  - rich capture
  - materialization instead of raw logs only
  - curated lesson serving
  - repeated-pattern review UI
- weak point:
  - the loop was observability-first, not verification-first

## What To Reuse Conceptually

Reuse:

- the idea of canonical run capture
- materialization into structured artifacts
- reviewable admin surfaces
- repeated-pattern mining as a downstream process

Do not reuse blindly:

- prompt-and-regex glue as the main protocol
- loose lesson cards as a substitute for verifier logic
- text-only feedback injected as an unstructured prompt appendix

## What A Zero-Context Agent Should Understand Immediately

If a future coding agent opens this repo cold, the important orientation is:

- this epic is not about memory
- this epic is not about a generic judge
- this epic is not about restoring old `workflow-lessons` as-is
- this epic is about making current agent runs verifiable and correctable
- the old lessons/diary line matters mainly as historical substrate and UI inspiration
- the first serious implementation target is verifier-facing data shape, not front-end polish

## Proposed Story Breakdown

### Story 1: Canonical Verification Trace

Goal:

Create a stable, addressable artifact for each agent run that can support both online verification and offline review.

Tasks:

- define a canonical trace schema
- include goal, plan revisions, tool calls, sanitized tool outputs, evidence refs, final answer, termination reason
- assign stable step ids
- separate verifier-facing fields from forensic/debug-only fields
- keep heavy artifacts out of the prompt path

### Story 2: Claim And Evidence Ledger

Goal:

Represent what the agent is actually asserting, what supports it, and what remains unresolved.

Tasks:

- define claim objects
- define evidence references and provenance fields
- define unresolved gap and assumption objects
- extract claims from final answers, key intermediate decisions, and important plan revisions
- define hygiene rules for what never enters this layer

### Story 3: Failure Taxonomy And Rubric Registry

Goal:

Maintain a versioned machine-usable verifier taxonomy instead of ad-hoc lessons.

Tasks:

- define failure families and sub-failures
- define rubric questions and scoring criteria
- define retryability classes
- support generic and domain-specific packs
- version the registry explicitly

### Story 4: Verification Packet Contract

Goal:

Create the first-class input/output protocol for verification.

Tasks:

- define `VerifierPacket`
- define `VerificationResult`
- define `RetryInstructionSet`
- define packet creation rules and cost gates

### Story 5: Decomposition-Driven Verification Engine

Goal:

Check the riskiest parts of the current run without re-solving the entire task.

Tasks:

- choose the top risky claims or failure points
- generate 3-7 targeted verification questions
- check against concrete evidence or source refs
- keep verification bounded by budget

### Story 6: Judge And Scoring Layer

Goal:

Return structured verdicts rather than vague reviewer prose.

Tasks:

- score correctness, adequacy, and evidence sufficiency
- emit failure tags and confidence
- emit retryability and stop conditions
- define thresholds for retry vs stop

### Story 7: Bounded Retry Loop

Goal:

Turn verifier findings into controlled self-improvement instead of open-ended reflexion.

Tasks:

- define retry budgets
- define how retry instructions are injected back into the run
- define protections against repeating the same mistake
- stop on diminishing returns or high verifier uncertainty

### Story 8: Offline Lesson Mining From Verification Artifacts

Goal:

Keep the good part of the old workflow-lessons line, but downstream from the verifier.

Tasks:

- materialize repeated failure patterns
- materialize repeated successful retry patterns
- define lesson candidate schema
- distinguish reusable lessons from one-off findings

### Story 9: Admin Review Surface

Goal:

Expose verifier runs, repeated patterns, and curated lessons in admin UI.

Tasks:

- add views for verification runs
- add views for repeated clusters
- add promote/unpromote flows for curated lessons
- add filters by family, domain, model, and tool family

### Story 10: Safety, Hygiene, And Debuggability

Goal:

Avoid prompt bloat, replay regressions, and opaque verifier behavior.

Tasks:

- keep reasoning/tool-output replay gated and minimal
- separate prompt-visible and runtime-only artifacts
- add request-scoped verifier telemetry
- test for verifier drift, retry loop pathologies, and context hygiene failures

## Suggested Delivery Order

### V0

Build the substrate only:

- Story 1
- Story 2
- Story 4

Why:

Without high-quality trace, claims, and packet structure, all higher layers become prompt glue.

### V1

Add minimal online verification:

- Story 3
- Story 5
- Story 6
- Story 7

Why:

This is the first version that can actually do test-time verification and bounded retries.

### V2

Add offline learning and admin review:

- Story 8
- Story 9
- Story 10

Why:

This is where old `workflow-lessons` ideas come back in a better role.

## Acceptance Signals For The Epic Direction

Before this epic is considered directionally healthy, the project should be able to answer "yes" to most of these:

- Can we represent a run in a verifier-facing format without dumping full raw artifacts into prompt context?
- Can we name the riskiest claims in a run instead of only showing the whole trajectory?
- Can a verifier emit bounded retry instructions that are more useful than "try again"?
- Can repeated lessons be derived from verification artifacts rather than hand-curated from vague impressions?
- Can a fresh coding agent start from this document and know where to begin without doing paper archaeology first?

## High-Value Borrowings From DeepVerifier

- stage separation: context/decomposition, additional checks, judging, feedback
- targeted verification questions instead of whole-task re-solving
- bounded retries
- separate verifier modes for baseline vs stronger verifier variants

## Things Not To Copy From DeepVerifier

- hard dependency on Cognitive Kernel-Pro
- prompt marker parsing as the primary transport protocol
- purely text-based feedback injection into the next task prompt
- benchmark-shaped assumptions as if they were product architecture

## Proposed Local-First Data Model

These names are intentionally product-facing rather than benchmark-facing.

- `VerificationTrace`
- `ClaimRecord`
- `EvidenceRef`
- `VerifierPacket`
- `VerificationResult`
- `RetryInstructionSet`
- `LessonCandidate`

## What A Fresh Chat Should Read First

If a future agent session starts with zero context, it should read in this order:

1. this document
2. the paper abstract and Sections 3, 4, 5, and 7
3. DeepVerifier `verifier.py` and `template.py`
4. local history around:
   - `b7dfdc96c`
   - `2eba9110f`
   - `456684107`
   - `3787c1202`
   - `b2bfed389`
   - `d503b04b5`

## Minimal Context Pack For The Next Agent

The next agent should not have to rediscover the following:

- previous `workflow diary / workflow lessons` work existed and was not pointless
- its main weakness was being observability-first instead of verification-first
- the new design should separate online verification from offline lesson mining
- DeepVerifier is the conceptual reference, but not a codebase to port directly
- the first real milestone is not UI; it is a strong verifier-facing trace and packet contract

## If You Only Read One Section Before Coding

Read:

- `Problem Statement`
- `Inspired By`
- `What Failed Before`
- `Suggested Delivery Order`

That is the shortest path to understanding why this epic exists and why the work is sequenced this way.

## First Sensible Starting Point

If implementation resumes in a future chat, the first task should be:

`Design the canonical verifier-facing run artifact and packet schema before touching verifier prompts or admin UI.`

That is the highest-leverage place to begin.
