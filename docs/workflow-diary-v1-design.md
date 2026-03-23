# Workflow Diary V1 Design

This document defines the first general-purpose workflow retrospective layer for Ariadne.

The goal is not to create another chat summary feature.

The goal is to create a bounded, inspectable "garage notebook" for recurring workflows so Ariadne can later:

- aggregate repeated wins and failures
- promote operator-approved playbooks
- build a real workflow pack from observed tasks
- compare prompt and routing variants against real repeated work

This sits directly under [Ariadne Roadmap](./ariadne-roadmap.md) and is intended to be the first implementation-grade step toward the paper lessons worth borrowing.

Consumer-facing follow-up:

- [Workflow Lessons Serving V0](./workflow-lessons-serving-v0.md)

## Current Status

`Workflow Diary V1` is no longer design-only.

`Phase 1A: Persisted Operational Snapshot` is implemented in:

- [middleware.py](../backend/open_webui/utils/middleware.py)
- [test_chat_response_middleware.py](../backend/open_webui/test/util/test_chat_response_middleware.py)

What Phase 1A now does:

- writes one bounded JSON packet per eligible assistant turn
- builds the packet only from the exact persisted assistant payload plus minimal request context
- runs immediately after assistant-message persistence in both streaming and non-streaming finalize paths
- keeps packet identity chat-scoped by writing under `<chat_artifacts_dir>/workflow_diary/packets/<message_id>.json`

What is still pending:

- background diary materialization
- specialist enrichment
- weekly aggregation and digesting
- playbook extraction and promotion

Important direction change:

- remaining phases should target a compact model-facing lessons serving layer
- raw diary packets and internal materialization rows are not meant to be injected into live model context directly
- the builder-only serving contract now exists in [Workflow Lessons Serving V0](./workflow-lessons-serving-v0.md)
- that serving contract was implemented and smoke-validated on production on 2026-03-23, so Step 2 now has a real downstream target instead of a hypothetical one

Production validation on 2026-03-23 confirmed:

- negative-control plain turns do not produce packets
- native streaming tool turns do produce packets with `tool_calls_in_output`
- the primary path uses persisted `output`, not `turn_recap` fallback, in the validated positive case
- packet path isolation works for the same `message_id` reused in two different chats

Known caveat:

- for `GLM-4.7-Flash-UD-Q8_K_XL`, native non-streaming tool execution was not a reliable positive validation path on production at the time of validation
- this was treated as a separate runtime/tool-continuation issue, not as a `Workflow Diary` regression

## Why This Exists

Ariadne already has strong runtime substrate:

- explicit lanes and working modes
- bounded context maintenance
- exact recall and ledger continuity
- tool, memory, and prompt telemetry
- research-only `source_diary`
- bounded specialist routing for narrow offline tasks

What Ariadne still does not have is a cross-lane retrospective artifact that says:

- what kind of workflow this was
- what runtime path it took
- what clearly helped
- what was wasteful
- what failure mode showed up
- what heuristic might be worth remembering later

Without that artifact, Ariadne can accumulate many local improvements without learning from them systematically.

## Scope

`Workflow Diary V1` should:

- produce one bounded retrospective artifact for each eligible assistant turn
- stay lane-aware instead of collapsing everything into generic chat summaries
- capture enough structured data for later weekly aggregation and playbook promotion
- preserve low daytime latency by separating cheap turn capture from slower retrospective materialization
- remain file-backed and inspectable

`Workflow Diary V1` should not:

- change runtime behavior directly
- auto-promote heuristics into production
- replace `source_diary`
- require a database migration
- require a new frontend surface in V1
- act as a complete analytics platform

## Product Intent

The product intent is simple:

- during the day, Ariadne does work
- after the work is done, Ariadne records how the work went
- later, Ariadne can review repeated patterns instead of re-learning the same lesson manually

This is the operational equivalent of the maintainer's garage notebook:

- not just "what was broken"
- also "what order of checks tends to pay off first"

## Existing Substrate

`Workflow Diary V1` is realistic because Ariadne already has the core pieces it needs.

### 1. Per-turn artifact directories

Chat-scoped artifact directories already exist and are cleaned up with the chat lifecycle. This makes file-backed retrospective artifacts cheap to add without introducing new persistence systems.

Relevant code:

- `backend/open_webui/models/chats.py`

### 2. Research-side background diary generation

`source_diary` already proves that Ariadne can:

- build a bounded context packet for a completed turn
- route a narrow offline task through the bounded specialist slot
- write a stable artifact into the chat's artifact directory

Relevant code:

- `backend/open_webui/utils/middleware.py`
- `backend/open_webui/utils/task.py`

### 3. Inspectable runtime telemetry

Ariadne already emits the raw signals this diary should use:

- `memoryTelemetry`
- `toolJourneyTelemetry`
- `promptTelemetry`
- research turn state
- source metadata
- guided Offsec state

This matters because the diary should be built from real runtime evidence, not just from retrospective prose.

## Problem Statement

Today, Ariadne has the pieces of operational memory, but not the memory object itself.

What exists today:

- request-scoped telemetry
- research-only source diary
- turn recap and exact replay hygiene
- guided Offsec step state

What is still missing:

- a common retrospective schema across research, artifact, and Offsec workflows
- a durable place to record candidate heuristics
- a file layout built for later nightly or weekly aggregation
- a single artifact that can connect telemetry to operational lessons

## Design Goals

`Workflow Diary V1` should optimize for these properties:

### 1. Bounded

The artifact should stay narrow and easy to inspect. It should never try to reconstruct the entire chat.

### 2. Structured First

The canonical output should be machine-friendly enough for later aggregation. Free-form prose can exist, but should not be the only thing stored.

### 3. Deterministic At The Edge

Anything that is easy to compute directly from runtime state should be written deterministically by the backend instead of delegated to a model.

### 4. Off-path Materialization

The slower reflective step should not block the live chat path.

### 5. Useful Without Full Automation

Even before any weekly digest, playbook registry, or variant engine exists, the diary should already help the maintainer inspect repeated workflow failures and wins.

## Unit Of Analysis

The V1 unit of analysis should be the eligible assistant turn, keyed by:

- `chat_id`
- `message_id`

Why this unit is the right starting point:

- it aligns with the existing `source_diary` model
- it aligns with server-owned message metadata
- it avoids waiting for the entire chat to "finish"
- it keeps capture deterministic and idempotent
- later weekly digestion can cluster multiple turns into larger workflow patterns

V1 should therefore think in terms of:

- exact turn capture now
- broader workflow aggregation later

Not:

- full-chat retrospective first

## Processing Model

`Workflow Diary V1` should be a two-stage pipeline.

### Stage 1. Turn Capture

When an eligible assistant turn completes, Ariadne should synchronously write a small deterministic capture packet.

This stage should be cheap and should not call a model.

It exists because some runtime signals only exist reliably at turn completion time:

- in-memory telemetry
- tool journey state
- research turn state
- prompt telemetry
- server-owned message metadata before it gets harder to reconstruct later

### Stage 2. Diary Materialization

A background job should later read the capture packet and generate the actual diary entry.

This stage may:

- classify the workflow more cleanly
- normalize the goal and constraints
- identify success and failure signals
- propose candidate heuristics in bounded language
- attach references to sibling artifacts such as `source_diary`

This stage should be allowed to use the bounded specialist slot, but it should also degrade gracefully if no specialist model is configured.

## Scheduling Model

The recommended V1 scheduling model is:

- `turn time`: write the deterministic capture packet
- `off-hours`: materialize pending diary entries for turns old enough to be stable

Recommended stability rule:

- only materialize entries for turns whose chat has been idle for a minimum threshold
- a six-hour default is a reasonable starting point

Why this helps:

- keeps daytime work fast
- reduces churn from active chats still in motion
- makes it more likely that the immediate next user correction already exists if one was made

The weekly digest is not part of this document, but this scheduling model is intentionally chosen so it can feed a later nightly or weekly aggregation pass cleanly.

## Eligibility Rules

V1 should not try to diary every trivial chat turn.

An assistant turn should be eligible only when all of the following are true:

- the chat is persisted and not `local:`
- the turn has a stable `message_id`
- the assistant turn completed normally enough to persist artifacts

And at least one of the following is true:

- tools were used
- research turn state exists
- local corpus retrieval was used
- guided Offsec state exists
- a sibling `source_diary` would be eligible
- stored artifacts or export-like outputs were produced
- the working mode is explicitly specialized rather than plain general chat

V1 should intentionally skip:

- trivial no-tool chat replies
- tiny acknowledgements
- local temporary chats

## Storage Layout

The diary should live inside the existing chat artifact directory.

Recommended layout:

```text
<chat_artifacts>/
  source_diary/
    <message_id>.md
  workflow_diary/
    packets/
      <message_id>.json
    entries/
      <message_id>.json
```

Rationale:

- `packets/` holds deterministic turn-time captures
- `entries/` holds materialized retrospective artifacts
- keeping both under `workflow_diary/` makes later aggregation straightforward
- chat deletion already removes the whole artifact tree

V1 should not require database persistence for diary content.

## Capture Packet

The capture packet is the raw, deterministic substrate for later retrospective analysis.

It should be written at turn completion and should avoid model-generated language except where the runtime already produced it.

### Capture Packet Requirements

- file-backed JSON
- idempotent rewrite by `message_id`
- bounded field sizes
- references to large artifacts instead of copying them
- enough information to materialize a diary entry later without replaying the whole chat

### Recommended Capture Packet Shape

```json
{
  "version": 1,
  "kind": "workflow_capture",
  "chat_id": "chat-123",
  "message_id": "msg-456",
  "captured_at": "2026-03-23T10:22:31Z",
  "active_model": "model-id",
  "working_mode": "offsec",
  "user_prompt": "...",
  "assistant_text": "...",
  "turn_recap": "...",
  "termination_cause": "completed",
  "runtime_path": {
    "tools": ["run_command", "offsec_register_plan"],
    "tool_counts": {
      "run_command": 4
    },
    "research_discovery_lane": null,
    "local_corpus_mode": "prefer"
  },
  "telemetry": {
    "memory": {},
    "tool_journey": {},
    "prompt": {}
  },
  "research": {
    "state": null,
    "source_diary_path": null
  },
  "offsec": {
    "guided_state": {}
  },
  "artifacts": {
    "stored_artifact_ids": [],
    "stored_artifact_paths": []
  }
}
```

### Deterministic Fields To Prefer

These should come from the backend directly rather than from a retrospective model pass:

- identifiers and timestamps
- active model and selected bounded specialist metadata
- working mode and retrieval preference
- tool names and counts
- lane markers
- termination cause
- presence of source artifacts
- presence of guided Offsec state
- truncated telemetry previews

### Boundedness Rules

The capture packet should not include:

- full tool stdout or stderr
- full transcripts
- large fetched documents
- large token telemetry

Instead it should include:

- previews
- counts
- references
- artifact paths

## Diary Entry

The materialized diary entry is the operator-facing retrospective artifact.

Unlike the capture packet, it may include limited normalized language, but it should still be predominantly structured.

### Diary Entry Requirements

- canonical JSON artifact
- stable enough for later aggregation
- readable enough for manual inspection
- resilient when the specialist pass is unavailable

### Recommended Diary Entry Shape

```json
{
  "version": 1,
  "kind": "workflow_diary_entry",
  "chat_id": "chat-123",
  "message_id": "msg-456",
  "status": "complete",
  "captured_at": "2026-03-23T10:22:31Z",
  "materialized_at": "2026-03-23T23:41:02Z",
  "workflow_family": "offsec",
  "workflow_tags": ["guided", "terminal", "bounded_execution"],
  "classifier": {
    "kind": "heuristic_v1",
    "confidence": 0.94,
    "reasons": ["offsec_guided_state_present", "offsec_tools_used"]
  },
  "goal": {
    "user_request": "...",
    "normalized_goal": "..."
  },
  "constraints": {
    "hard": [],
    "soft": []
  },
  "runtime_path": {
    "working_mode": "offsec",
    "active_model": "model-id",
    "major_tools": ["offsec_register_plan", "run_command"],
    "lane_markers": []
  },
  "outcome": {
    "success_signals": [],
    "failure_signals": [],
    "wasteful_actions": [],
    "invariant_violations": [],
    "evidence_inference_issues": [],
    "operator_correction_signals": []
  },
  "candidate_playbook_notes": [
    {
      "note": "...",
      "confidence": "low"
    }
  ],
  "references": {
    "capture_packet": "workflow_diary/packets/msg-456.json",
    "source_diary": null
  },
  "generator": {
    "mode": "deterministic_plus_specialist",
    "model_id": "task-model-id"
  }
}
```

### Required Semantic Buckets

Every diary entry should be able to answer these questions:

- What workflow family was this?
- What was the goal?
- What constraints mattered?
- What runtime path did Ariadne take?
- What clearly helped?
- What clearly hurt or wasted effort?
- Was there any drift, false confidence, or invariant break?
- Is there a candidate heuristic worth keeping around?

## Workflow Classification

V1 does not need a perfect taxonomy.

It needs a stable enough one to support later aggregation.

### Recommended `workflow_family`

- `research`
- `artifact`
- `offsec`
- `general`

### Recommended `workflow_tags`

- `web_evidence`
- `local_corpus`
- `mixed_evidence`
- `deep_research`
- `export`
- `guided`
- `exploratory`
- `terminal`
- `multi_tool`
- `user_corrected`

### Initial Heuristic Rules

Use deterministic rules first.

Examples:

- if `offsec_guided_state` exists, classify as `workflow_family=offsec` with tag `guided`
- if Offsec working mode exists without guided state, classify as `workflow_family=offsec` with tag `exploratory`
- if web research tools were used and no local corpus signal exists, classify as `workflow_family=research` with tag `web_evidence`
- if local corpus signals exist and web research tools do not, classify as `workflow_family=research` with tag `local_corpus`
- if both exist, classify as `workflow_family=research` with tag `mixed_evidence`
- if a persisted export or deliverable artifact exists, add tag `export`

The specialist pass may refine the goal and constraints, but it should not be the primary source of basic workflow family classification.

## Relationship To `source_diary`

`Workflow Diary V1` should extend the existing retrospective substrate, not replace it.

Recommended relationship:

- keep `source_diary` exactly as the research-specific exact-turn source artifact
- let workflow diary reference `source_diary` when it exists
- do not duplicate long source lists inside the workflow diary
- use workflow diary for operational lessons, not source-by-source audit

In other words:

- `source_diary` answers "which sources helped in this research turn?"
- `workflow_diary` answers "what operationally happened in this workflow turn, and what might be worth remembering later?"

## Specialist Usage

The bounded specialist slot is useful here, but it should be used carefully.

Recommended usage:

- deterministic capture always runs
- diary materialization may use bounded specialist if available
- if no specialist model is configured, still write a deterministic-only entry

This is important because the diary should be foundational infrastructure, not an optional luxury that disappears whenever the task model is absent.

### Specialist Responsibilities

If a specialist pass is used, it should be limited to:

- goal normalization
- constraint extraction
- bounded success and failure identification
- candidate playbook note extraction
- compact evidence/inference risk hints when relevant

It should not:

- invent sources or tool events
- rewrite runtime history
- reconstruct unseen parts of the chat
- emit large prose summaries

## Operator Corrections

One of the highest-value diary signals is whether the user later corrected the system.

V1 should treat this as a bounded signal, not a full conversation analysis task.

Recommended rule:

- if a diary entry is materialized only after a chat has been idle for a while, the materializer may inspect the immediate next user turn for obvious correction signals

Examples of useful correction signals:

- explicit contradiction
- task invariant restatement
- "that source was not actually accessed"
- "the target changed" or "the target was always X"

This should remain best-effort in V1.

If the signal is not clear, the diary should omit it rather than guess.

## Candidate Playbook Notes

The diary is the first place where Ariadne should start collecting reusable operational heuristics.

These notes must remain tentative in V1.

Recommended note fields:

- `note`
- `scope`
- `confidence`
- `supporting_signals`

Examples:

- "For mixed web-and-local research, preserve hard task invariants before synthesis/export."
- "For guided Offsec work, checkpoint after scope establishment instead of widening execution early."
- "When access state is weaker than full text, avoid paper-specific claims and frame output as inference."

These notes should not affect runtime behavior until a later playbook-promotion layer exists.

## Failure And Risk Categories

The diary should support repeated pattern detection later, so the failure buckets must be reasonably stable.

Recommended categories:

- `false_attribution_risk`
- `evidence_gap`
- `invariant_drift`
- `tool_loop_waste`
- `resume_weakness`
- `over-broad_search`
- `under-specified_export`
- `premature_synthesis`
- `operator_correction_followed`
- `operator_correction_missed`

V1 does not need every category populated on every entry.

It does need a stable vocabulary so a later digest can cluster entries honestly.

## Downstream Consumers

`Workflow Diary V1` should be designed for these later consumers:

### 1. Weekly Background Digest

The digest will cluster:

- repeated failures
- repeated wins
- repeated candidate heuristics

The diary must therefore be structured enough to aggregate without rereading entire chats.

### 2. Workflow Pack V1

The workflow pack should be built from real recurring workflows, not synthetic examples. The diary provides the raw substrate for choosing those recurring cases.

### 3. Evidence / Inference Contract

The diary can surface repeated evidence or attribution failures before the formal contract exists.

### 4. Playbook Promotion

The diary is where candidate heuristics begin as observations before any promotion state is introduced.

## Likely Implementation Touchpoints

The design should be implementable with targeted changes in a small number of places.

### Primary backend touchpoints

- `backend/open_webui/utils/middleware.py`
  - Phase 1A is implemented here
  - normalize the exact saved assistant payload into a bounded workflow snapshot
  - write the capture packet immediately after assistant-message persistence
  - later phases should add materialization scheduling here only if needed
- `backend/open_webui/utils/task.py`
  - still pending
  - add a bounded specialist task kind for workflow diary generation if needed
- `backend/open_webui/models/chats.py`
  - no schema change was required for Phase 1A

### Testing touchpoints

- `backend/open_webui/test/util/test_chat_response_middleware.py`
- Phase 1A coverage now includes:
  - eligibility rules
  - deterministic capture packet contents
  - streaming/non-streaming normalization symmetry
  - chat-scoped path isolation
  - controlled fallback when `output` is missing
  - fail-open behavior
  - no writes for `local:` chats

## Rollout Plan

The safest V1 rollout is:

### Step 1. Schema And Capture Writer

Implement only:

- capture-packet schema
- eligibility rules
- file writing

No specialist pass yet.

Why first:

- this starts collecting raw substrate immediately
- it keeps the first implementation small and deterministic

Status:

- completed as `Phase 1A: Persisted Operational Snapshot`
- implemented as an observer-only layer with no runtime behavior change
- validated locally by middleware tests and on production through native streaming tool turns

### Step 2. Background Materializer

Add:

- pending-entry discovery
- idle-chat threshold
- diary entry writing

Still safe because runtime behavior remains unchanged.

Important constraint:

- this step should materialize into a structured internal lessons substrate that can later generate compact markdown serving cards
- it should not produce verbose diary prose and expect the model to consume that directly
- the existing target for that materialization is `workflow_lessons/internal/lessons-catalog.jsonl`

Why this is now the right next step:

- `Phase 1A` already captures trustworthy bounded packets
- `Workflow Lessons Serving V0` already proves the consumer-facing card format and builder path
- the missing bridge is the materializer that turns packet evidence into reviewable internal lesson rows

### Step 3. Specialist Enrichment

Only after deterministic capture and materialization are stable:

- add bounded specialist enrichment
- keep deterministic fallback always available

### Step 4. Maintenance Script Or Admin Trigger

Before building a full scheduler, a manual or admin-triggered background run is acceptable.

This is a good ROI move for a one-maintainer, one-user system.

## Guardrails

`Workflow Diary V1` should explicitly keep these guardrails:

- no runtime mutation
- no promotion from one diary entry alone
- no dependency on full transcript replay
- no copying of large artifacts into the diary
- no silent failure that blocks the main chat path
- no requirement for a second model in order to get basic diary coverage

## Deferred Items

These are intentionally out of scope for V1:

- weekly digest generation
- playbook registry and promotion states
- workflow-pack benchmarking
- evidence/inference response contract
- UI review surface
- autonomous prompt or routing mutation
- chat-level stitched workflow views spanning many turns

## Success Criteria

`Workflow Diary V1` is successful if, after implementation, Ariadne can reliably produce bounded retrospective artifacts that let the maintainer answer:

- Which repeated workflow shapes actually dominate my usage?
- Which runtime paths keep paying off?
- Which failures repeat often enough to deserve a playbook or contract change?
- Which diaries are strong enough to seed a real workflow pack?

If the diary cannot answer those questions, it is too vague.

If it answers them while staying cheap, inspectable, and reversible, it is doing the right job.
