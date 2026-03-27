# Persona Runtime And Continuity Layer

Status: proposed

Owner: local fork

Last updated: 2026-03-27

## Executive Summary

This epic is about turning Ariadne's existing model-preset machinery into a first-class persona runtime.

The fork already has part of the substrate:

- model presets over base models
- per-preset system prompts
- per-preset tools and skills
- model avatars
- chat recall
- context compaction and ledger capture

What it does not yet have is a clean product layer that lets one local operator treat those pieces as durable digital assistants rather than as renamed model variants.

The goal is not to invent synthetic personalities for their own sake.

The goal is to let the maintainer switch between stable assistants with:

- distinct identity
- distinct capability policy
- distinct visual marker
- distinct model bindings
- scoped continuity across chats

without giving up Ariadne's existing agentic capabilities or contaminating one persona's continuity with another's.

## Goal

Build a local-first persona runtime for Ariadne that supports:

- persona selection as a first-class interaction surface
- composition of `identity + bindings + capability policy + continuity`
- soft switching between personas that share a loaded runtime
- hard switching when a persona requires a different runtime or LoRA stack
- persona-scoped continuity across chats
- shared system learning across personas without shared interpersonal residue

## Problem Statement

Today, the fork can already simulate personas in a primitive but useful way:

- create multiple model presets
- point them at the same base model
- give each a different system prompt
- enable or disable tools per preset
- assign a profile image

That works, but it is still model-centric rather than persona-centric.

In practice, this creates several problems:

- the same assistant identity is tied too tightly to one model preset
- there is no clean distinction between persona identity and capability policy
- continuity across chats is either global, weak, or manual
- switching feels like changing model configuration, not changing who you are talking to
- uncensored or LoRA-bound personas need hard runtime switching, but the product model does not name that explicitly
- if continuity grows later, there is a serious risk of one persona inheriting another persona's residue

This epic exists to fix that product and architecture gap without replacing Ariadne's stronger runtime core.

## Why This Exists

The maintainer already uses multiple local models and model presets as if they were different assistants.

That means the core need is already real.

What is missing is not prompt quality.

What is missing is a durable runtime container around those prompts so the system can support:

- stable assistant identity
- stable visual markers
- stable capability defaults
- stable binding rules
- stable continuity rules

The fork should make that mode of use explicit instead of pretending everything is still just "a model."

## Why Now

This is worth doing now for four reasons:

1. The fork already has the right low-level substrate, so this is mostly a composition problem rather than a greenfield system.
2. The maintainer already has high-quality live prompt designs, so the value is in productizing them rather than inventing canned characters.
3. HomePilot showed that there is real user-facing value in persona packaging, even if its memory story is weaker than its product story.
4. The current roadmap already separates global self-improvement from generic memory, which makes this the right moment to define persona continuity cleanly instead of bolting it on later.

## Why This Matters In This Fork

This is not a generic assistant feature.

It matters specifically here because this fork is used as:

- a local model orchestrator
- a wrapper over multiple llama.cpp-served runtimes
- an environment with both censored and uncensored model lines
- a system where tools and domain routing matter
- a daily-driver interface for sharply different interaction modes

In that environment, the real product unit is often not the raw model.

It is:

`persona = identity + runtime binding + capability defaults + continuity policy`

## Inspired By

This roadmap is informed by three lines of prior work.

### External inspiration

HomePilot contributed the useful product insight:

- personas should be first-class user-facing objects
- personas benefit from their own assets and continuity
- persona switching should feel like switching assistants, not editing a prompt

But this epic does not treat HomePilot as an architecture to port directly.

Its memory system is not the target shape for Ariadne.

### Ecosystem inspiration

Character-card ecosystems such as TavernAI and related formats proved that users like portable identity packs with:

- name
- description
- persona text
- scenario
- greeting
- image

That portability matters, even if Ariadne's runtime layer should be stricter and more structured.

### Internal inspiration

This fork already has the primitives that make a better implementation possible:

- model presets and base-model indirection
- tool and skill binding
- model avatars
- chat recall
- context maintenance
- ledger capture

The epic is about composing those pieces into a clean persona layer, not bypassing them.

## Non-Goals

- not a generic "character platform"
- not a roleplay-only feature line
- not a replacement for Ariadne's global self-improvement roadmap
- not a new general memory blob
- not a port of HomePilot's memory engines or topology language
- not a wardrobe or creator-studio project
- not a cloud sharing or gallery system in the first implementation phase

## Core Design Principle

The center of gravity is:

`persona identity -> binding -> policy -> continuity -> selective recall`

Not:

`giant prompt blob with vibes and memories`

And not:

`model preset renamed to persona without any state semantics`

## What Success Looks Like

Success is not "we can store another prompt in the database."

Success looks like this:

- the maintainer can switch between assistants the way one switches channels
- some switches are instant because they are only preset swaps
- some switches are explicitly hard because they require a different runtime or LoRA line
- each persona has a stable emoji/avatar marker across all its chats
- tools are constrained by persona policy instead of by ad-hoc per-chat discipline
- a new chat with a persona can inherit the right amount of continuity without becoming bloated or cross-contaminated
- global system learning remains shared across personas
- interpersonal continuity remains scoped to the persona that earned it

## What This Epic Is Not About

This epic is not about making models smarter.

The fork's self-improvement line should remain:

- global
- verifier-informed
- shared across personas where appropriate

Persona work is about:

- product packaging
- runtime composition
- scoped continuity

That separation must remain clear.

## What Failed Before Or Is Easy To Get Wrong

The obvious failure modes here are:

- treating persona as only a system prompt
- treating persona continuity as generic memory sludge
- storing all continuity inline in the prompt
- letting one persona inherit another persona's interpersonal residue
- making every persona equally "deep" even when some only need task continuity
- building a corporate taxonomy instead of a small practical config surface
- overfitting the design to roleplay while neglecting support/coach and technical-assistant use cases

## Key References

Relevant local files and primitives:

- model schema: `backend/open_webui/models/models.py`
- model prompt application: `backend/open_webui/utils/payload.py`
- model preset editor: `src/lib/components/workspace/Models/ModelEditor.svelte`
- model avatars in selector: `src/lib/components/chat/ModelSelector/ModelItem.svelte`
- chat recall middleware: `backend/open_webui/utils/chat_recall.py`
- context maintenance and summaries: `backend/open_webui/utils/context_maintenance.py`
- ledger capture: `backend/open_webui/models/ledger.py`
- character-card parsing utility: `src/lib/utils/characters/index.ts`

External conceptual reference:

- HomePilot, as product inspiration for first-class persona packaging

What matters from that comparison:

- product layer worth borrowing:
  - first-class personas
  - assets
  - packaging
  - continuity framing
- architecture worth not copying:
  - overlapping memory subsystems
  - overclaimed topology abstractions

## Relevant Local History

Current state in this fork:

- the maintainer already uses multiple live prompt-defined assistants
- Ariadne already supports preset models that wrap one base runtime with different prompt and tool configuration
- model-level avatars already exist
- chat recall already exists as a bounded evidence retrieval layer
- context maintenance already exists as structured compaction rather than raw long-history stuffing

Practical reading of that history:

- good substrate already exists
- the missing piece is a dedicated persona object and persona-scoped state model

## What To Reuse Conceptually

Reuse:

- model presets as the first runtime binding substrate
- base-model indirection for soft switching
- recall as evidence retrieval, not as synthetic identity memory
- summary and ledger patterns for compact state artifacts
- model avatars as the first visual identity layer

Do not reuse blindly:

- global chat recall across unrelated personas
- title-generated emoji as the only visual grouping mechanism
- raw chat history as the continuity product
- prompt accumulation as a substitute for scoped continuity state

## What A Zero-Context Agent Should Understand Immediately

If a future coding agent opens this repo cold, the important orientation is:

- this epic is not mainly about prompts
- this epic is not mainly about memory
- the fork already has model presets, avatars, tools, and recall
- the missing abstraction is `persona`
- persona is not the same thing as model
- global system learning should stay separate from persona continuity
- persona continuity should be consolidated across chats, not improvised per turn
- some persona switches should be treated as hard runtime switches, not hidden under the rug

## Proposed Runtime Model

The intended split is:

- `persona`
  - identity, description, emoji, avatar, prompt contract, continuity policy
- `binding`
  - which preset/base runtime this persona prefers or requires
- `capability policy`
  - allowed tools, ask-before-use tools, disabled tools, optional admin defaults
- `continuity`
  - persona-scoped cross-chat interpersonal or task continuity
- `recall`
  - persona-scoped retrieval over archived chats only when needed
- `global learning`
  - shared Ariadne-level verified lessons, never owned by one persona

## Proposed Story Breakdown

### Story 1: Persona Data Model

Goal:

Define the first-class persona object and its minimum durable fields.

Tasks:

- define `PersonaProfile`
- include stable identity fields:
  - id
  - name
  - description
  - emoji
  - avatar reference
- include prompt-facing fields:
  - system contract
  - greeting seed
  - optional style notes
- include policy-facing fields:
  - continuity mode
  - continuity scope
  - recall policy
  - default tool policy
- keep the model small and explicit

### Story 2: Persona Binding Layer

Goal:

Separate persona identity from runtime selection.

Tasks:

- define `PersonaBinding`
- support preferred preset model id
- support fallback preset model ids
- support explicit hard-switch target markers
- distinguish:
  - soft switch
  - hard switch
- expose binding reason in UI so runtime reloads are not surprising

### Story 3: Persona Capability Policy

Goal:

Make persona-level tool behavior explicit rather than accidental.

Tasks:

- define `PersonaCapabilityPolicy`
- support per-tool modes:
  - allowed
  - ask_first
  - disabled
- support admin defaults with optional user override where safe
- make the policy compositional with existing model preset tool settings
- ensure a persona can be more restrictive than its bound preset

### Story 4: Persona Visual Identity

Goal:

Make personas visually legible across the whole UI.

Tasks:

- use persona avatar as the primary chat identity marker
- use persona emoji as a stable cross-chat marker
- keep existing generated chat title behavior, but separate persona marker from title text
- make sidebar, chat header, and selectors render persona identity consistently

### Story 5: Persona-Aware Chat Attachment

Goal:

Attach chats to personas as a first-class relation.

Tasks:

- add `persona_id` to chat metadata or first-class chat state
- ensure a chat always knows which persona it belongs to
- keep model binding and persona attachment distinct
- define migration behavior for old chats with no persona

### Story 6: Persona Continuity Artifact

Goal:

Create a compact continuity artifact for each persona that survives across chats.

Tasks:

- define `PersonaContinuitySnapshot`
- keep it structured rather than freeform
- include fields such as:
  - relationship frame
  - stable preferences
  - recurring dynamics
  - active threads
  - hard boundaries
  - task continuity facts where relevant
- distinguish interpersonal continuity from task continuity
- keep this artifact prompt-sized and durable

### Story 7: Chat Recap And Timeline Consolidation

Goal:

Generate persona continuity from completed chats rather than from noisy individual turns.

Tasks:

- define a per-chat closeout recap artifact
- trigger recap generation asynchronously on chat idle, closeout, or threshold conditions
- order persona chat recaps chronologically outside the prompt path
- run a persona timeline synthesis job that updates the continuity snapshot
- make timeline synthesis incremental rather than full rebuild by default
- keep a repair or rebuild path for maintenance jobs

### Story 8: Persona-Scoped Recall

Goal:

Let a persona retrieve evidence from its own archived chats without inheriting everything by default.

Tasks:

- scope recall to `persona_id` by default
- support persona-specific recall policies:
  - off
  - light
  - on-demand
- inject evidence only when there is a real continuation or reference gap
- keep recall separate from continuity snapshot generation

### Story 9: New Chat Injection Contract

Goal:

Define exactly what a new chat receives when opened under a persona.

Tasks:

- inject persona identity contract
- inject current binding metadata only where useful
- inject compact continuity snapshot according to persona policy
- never dump raw chat ids into prompt context as the main mechanism
- keep the prompt-visible payload minimal and stable

### Story 10: Import And Packaging Surface

Goal:

Prepare a small but useful packaging format for persona portability.

Tasks:

- define a minimal persona export format
- include:
  - manifest
  - profile
  - avatar reference
  - prompt contract
  - policy settings
  - binding hints
- consider later compatibility with character-card style imports where practical
- do not tie V1 to community gallery or cloud distribution

### Story 11: Safety, Hygiene, And Debuggability

Goal:

Keep the system understandable and prevent continuity pollution.

Tasks:

- separate prompt-visible state from runtime-only state
- add telemetry for persona switch type and continuity injection
- test for persona contamination across chats
- test soft vs hard switch correctness
- test that restrictive personas do not unexpectedly inherit broad tool usage
- test that continuity synthesis does not run on every turn

## Suggested Delivery Order

### V0

Build the core persona substrate:

- Story 1
- Story 2
- Story 5
- Story 4

Why:

Without a real persona object, attachment model, and visible identity, everything else is still model presets in disguise.

### V1

Add policy and continuity foundations:

- Story 3
- Story 6
- Story 9

Why:

This is the first version where personas become meaningfully different beyond prompt text.

### V2

Add cross-chat continuity and recall:

- Story 7
- Story 8

Why:

This is where support, coach, and companion personas begin to feel persistent rather than stateless.

### V3

Add packaging and hardening:

- Story 10
- Story 11

Why:

Portability and debuggability matter, but they should come after the runtime shape is right.

## Acceptance Signals For The Epic Direction

Before this epic is considered directionally healthy, the project should be able to answer "yes" to most of these:

- Can one persona identity be rebound to different model presets without redefining the persona itself?
- Can the UI make soft and hard switches legible?
- Can one persona be tool-restricted without losing the agentic surface for another persona?
- Can a new chat inherit compact persona continuity without injecting raw archives?
- Can support or coach personas maintain cross-chat relationship continuity without contaminating technical personas?
- Can global Ariadne learning remain shared while persona continuity remains scoped?
- Can a fresh coding agent read this document and understand why persona is not the same thing as model?

## High-Value Borrowings From Existing Ariadne Substrate

- preset models over a shared base model
- existing avatar support on models
- bounded chat recall
- structured context compaction
- ledger-style durable small artifacts

## Things Not To Copy From HomePilot Or Similar Systems

- multiple overlapping memory engines for one persona concept
- treating product labels as architectural truth
- pretending every mode is a different cognition stack
- building creator-studio scale surface before core persona runtime exists

## Proposed Local-First Data Model

These names are intentionally implementation-facing rather than marketing-facing.

- `PersonaProfile`
- `PersonaBinding`
- `PersonaCapabilityPolicy`
- `PersonaContinuitySnapshot`
- `PersonaChatRecap`
- `PersonaTimelineState`

## What A Fresh Chat Should Read First

If a future agent session starts with zero context, it should read in this order:

1. this document
2. `docs/roadmap/verification-native-agent-improvement-platform.md`
3. `backend/open_webui/models/models.py`
4. `backend/open_webui/utils/payload.py`
5. `backend/open_webui/utils/chat_recall.py`
6. `backend/open_webui/utils/context_maintenance.py`
7. `src/lib/components/workspace/Models/ModelEditor.svelte`

## Minimal Context Pack For The Next Agent

The next agent should not have to rediscover the following:

- Ariadne already has model presets and avatars
- the maintainer already has high-quality live prompts, so prompt writing is not the bottleneck
- the bottleneck is turning those prompts into first-class runtime objects
- continuity should be consolidated across chats, not improvised from every turn
- emotional-support and coaching personas need continuity more urgently than technical personas
- global self-improvement should stay shared across personas
- persona continuity should remain persona-scoped

## If You Only Read One Section Before Coding

Read:

- `Problem Statement`
- `Core Design Principle`
- `Proposed Runtime Model`
- `Suggested Delivery Order`

That is the shortest path to understanding why this epic exists and what shape it should take.

## First Sensible Starting Point

If implementation resumes in a future chat, the first task should be:

`Define the PersonaProfile and PersonaBinding schema, then attach chats to persona_id before touching continuity synthesis.`

That is the highest-leverage place to begin.
