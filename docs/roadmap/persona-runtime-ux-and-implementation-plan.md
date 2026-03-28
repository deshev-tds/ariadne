# Persona Runtime UX And Implementation Plan

Status: active, partially implemented

Owner: local fork

Last updated: 2026-03-28

Companion to:

- `docs/roadmap/persona-runtime-and-continuity.md`
- `docs/roadmap/persona-partner-profile-spec.md`
- `docs/roadmap/persona-scene-note-spec.md`

## Goal

Turn persona into the primary daily-driver interaction unit without making the app feel like a new product.

The UX should feel like a clean evolution of the current model-preset workflow:

- personas become the primary thing the maintainer chooses
- models remain visible, but secondary
- persona creation stays fast
- roleplay/storytelling quality improves through better prompt layering, not through giant editor surfaces

## Current Implementation Snapshot

Already shipped:

- `Workspace -> Personas`
- persona list, create, edit, duplicate, enable/disable
- persona-first navbar selection
- direct-model fallback lane
- per-persona bound model, partner profile, and voice controls
- inline voice preview
- per-chat pinned runtime defaults
- chat-local `Scene Note`

Still pending from this plan:

- richer starter archetype defaults
- opening / greeting as a true runtime directive layer
- lorebook
- continuity and persona-scoped recall

## Product Position

For this fork, persona should be:

- more important than model choice in normal use
- lighter than a "character studio"
- stronger than "just a different system prompt"
- usable for both:
  - practical assistants
  - storyteller / companion / coach modes

The app should still preserve a direct "no persona / raw model" path for advanced or debugging use.

## UX Principles

### 1. Persona First, Model Second

In normal chat UX, the user should feel like they are choosing **who** they are talking to.

Model selection still matters, but it should become:

- a bound runtime detail
- a debug/advanced control
- a fallback path when no persona is attached

### 2. One Persona Per Chat

Persona identity should remain singular at the chat level.

This means:

- one chat belongs to one `persona_id`
- persona switching on an existing chat should be explicit
- multi-model mode should stay available only for direct model workflows, not persona-attached chats

That keeps the mental model clean and prevents identity drift.

### 3. Persona Creation Must Be Fast

A persona should be creatable in a few minutes, not an afternoon.

V1 persona creation should optimize for:

- archetype first
- identity second
- runtime binding third
- voice preview in the same screen

Not for:

- giant advanced settings walls
- cloud sharing
- wardrobe systems
- fully programmable prompt graphs

### 4. Storytelling Value Comes From Layering

Roleplay/storytelling quality will not come mainly from continuity.

It will come from keeping these layers separate:

- identity anchor
- partner profile
- scene note
- lorebook
- learned continuity

The UI should expose them as different things.

## Core User Journeys

### Journey A: Start A New Persona Chat

Desired flow:

1. Click `New Chat`.
2. The app starts with the last-used persona by default.
3. If needed, switch persona from the navbar before the first message.
4. The chat immediately feels persona-specific via:
   - avatar
   - name
   - greeting / description
   - bound voice

### Journey B: Create A New Persona

Desired flow:

1. Open `Workspace -> Personas`.
2. Click `New Persona`.
3. Pick archetype:
   - assistant
   - storyteller
   - companion
   - coach
4. Fill in:
   - name
   - emoji
   - avatar
   - description
   - preferred model binding
   - voice
   - voice speed
5. Preview the voice inline on the same screen.
6. Save.
7. Start a new chat with that persona.

### Journey C: Nudge A Chat Into Story Mode

Desired flow:

1. Open a persona-attached chat.
2. Add or edit a small `Scene Note`.
3. Optionally attach lorebook entries later.
4. Continue chatting without editing the core persona itself.

This is the high-ROI bridge between assistant UX and actual storytelling UX.

## Surface Plan

### 1. Navbar

Current state:

- the navbar is model-first via `src/lib/components/chat/Navbar.svelte`
- model choice is driven by `src/lib/components/chat/ModelSelector.svelte`

V1 plan:

- replace the primary selector in persona-attached chats with a `PersonaSelector`
- show the bound model as a secondary chip or inline detail
- if the binding implies a hard switch, show that clearly before switching
- preserve the current model selector only for:
  - direct model chats
  - advanced fallback/debug mode

UX rule:

- if a chat has a persona, the navbar should lead with persona identity
- if a chat has no persona, keep the current model-first navbar

### 2. Chat Placeholder / Empty State

Current state:

- the empty state is model-first via `src/lib/components/chat/Placeholder.svelte`

V1 plan:

- show persona avatar, name, and short description instead of the bound model name
- optionally show the bound model in smaller secondary text
- support persona greeting seed or opening copy in the empty state

This gives instant "who am I talking to?" value before the first message.

### 3. Sidebar And Chat List

Current state:

- chats are title-first in `src/lib/components/layout/Sidebar.svelte`
- search results are title-first in `src/lib/components/layout/SearchModal.svelte`

V1 plan:

- show persona avatar or emoji marker for each chat
- keep the title as the primary text line
- make persona markers consistent in:
  - sidebar
  - search modal
  - archived/shared chat lists later

This is cheap and high-value because it makes the whole corpus legible at a glance.

### 4. Persona Workspace

Proposed new surface:

- `Workspace -> Personas`

This should be a proper screen, not only a modal.

Reason:

- voice preview
- avatar picking
- model binding
- archetype selection
- future lorebook / continuity controls

all want more room than a dropdown can comfortably provide.

Suggested shape:

- left: persona list
- right: editor / details
- action buttons:
  - create
  - duplicate
  - archive/disable
  - start chat

### 5. Persona Editor

V1 editor sections:

- Identity
  - name
  - emoji
  - avatar
  - description
- Archetype
  - assistant
  - storyteller
  - companion
  - coach
- Runtime
  - preferred model
  - fallback model
  - hard-switch marker
- Voice
  - voice dropdown
  - speed slider
  - preview button
- Behavior
  - system contract
  - greeting seeds
  - response style
- Capabilities
  - tool policy summary

V2 editor sections:

- Partner profile
- Scene note defaults
- Lorebook
- Continuity policy

### 6. Voice UX

This should stay intentionally thin.

The system already has what we need:

- discoverable voices via `backend/open_webui/routers/audio.py`
- frontend voice loading via `src/lib/apis/audio/index.ts`
- existing TTS settings UI in `src/lib/components/chat/Settings/Audio.svelte`
- current model-level TTS metadata in `src/lib/components/workspace/Models/ModelEditor.svelte`

V1 voice plan:

- persona stores:
  - `voice_id`
  - `voice_speed`
- persona editor loads the available voices from the existing audio API
- preview button synthesizes a short sample using the currently selected voice and speed
- do not build a new audio subsystem

Preview behavior:

- one neutral preview sentence
- optional archetype-flavored preview sentence later
- preview should work without saving the persona first if practical

### 7. Scene Note UX

This should not live in persona creation.

Detailed companion spec:

- `docs/roadmap/persona-scene-note-spec.md`

It belongs to the active chat.

Suggested V1.5/V2 surface:

- a small persona menu in chat chrome
- action: `Edit Scene Note`
- opens a compact drawer or modal

Fields:

- current scene / setup
- temporary style steer
- behavioral note

This makes storytelling useful without contaminating long-term persona identity.

## Decisions To Lock Now

These should be treated as design constraints unless a strong reason appears to change them:

- persona is the primary selector in persona-attached chats
- one chat has one persona
- multi-model mode is not a persona feature
- persona authoring gets a dedicated workspace screen
- voice belongs to persona definition
- voice preview happens in the persona editor
- scene note is chat-local, not persona-global
- lorebook is separate from continuity
- direct raw-model chat remains available

## Phased Implementation

### Phase 1: Persona V1 Daily Driver

Scope:

- persona schema
- persona binding
- archetypes
- chat attachment
- navbar persona selector
- sidebar/search persona markers
- persona workspace
- persona editor
- voice picker + speed + preview

Success signal:

- the maintainer can use personas all day without needing the model workspace for normal use

Status:

- implemented

### Phase 2: Storytelling Lift

Scope:

- new-chat injection contract
- scene note
- persona capability policy

Success signal:

- storyteller and companion personas feel materially different in-chat, not only cosmetically different

Status:

- partially implemented
- `Scene Note` is shipped
- richer capability policy and deeper storytelling layering remain

### Phase 3: Persistent Persona Value

Scope:

- lorebook
- compact continuity snapshot
- chat recap -> continuity update

Success signal:

- fictional and relationship-heavy personas feel persistent without prompt sludge

Status:

- not started

### Phase 4: Hardening

Scope:

- persona-scoped recall
- import surface
- safety/telemetry/tests

Success signal:

- persona UX remains understandable as the system gains memory and portability

Status:

- not started

## First Concrete Slice

If implementation starts immediately, the first UX slice should be:

1. Create `Workspace -> Personas`.
2. Add a minimal persona list + editor.
3. Support:
   - name
   - emoji
   - avatar
   - archetype
   - preferred model
   - voice
   - voice speed
4. Add inline voice preview.
5. Attach new chats to `persona_id`.
6. Replace the navbar selector with persona-first UX for persona chats.
7. Show persona markers in sidebar and search.

That slice is enough to make persona real in the product before continuity or lorebook work begins.

Current note:

- this initial slice is delivered and is now the baseline substrate for the next layer of work
