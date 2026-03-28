# Persona Scene Note Spec

Status: draft

Owner: local fork

Last updated: 2026-03-27

Companion to:

- `docs/roadmap/persona-runtime-and-continuity.md`
- `docs/roadmap/persona-runtime-ux-and-implementation-plan.md`

## Goal

Add a first-class `Scene Note` layer for persona-attached chats so storytelling and roleplay can be steered without bloating the core persona prompt.

This is the thin equivalent of:

- SillyTavern `author's note` / `post-history instructions`
- Kobold `Author's Note`
- a chat-local atmospheric steering layer

The design target is not a giant scene manager.

The design target is:

- fast scene setup
- strong atmospheric steering
- clear separation from persona identity
- no confusion with continuity or lore

## Why This Exists

For practical assistant personas, the current V1 substrate is already useful.

For personas like `SUNFALL`, it is not enough.

The missing layer is not more memory.
The missing layer is a chat-local steering surface for:

- current setting
- current mood
- current pacing
- current relational frame

without having to rewrite the persona itself.

## Design Principles

### 1. Scene Is Not Identity

Persona defines:

- who the assistant is
- how it generally behaves
- what runtime it binds to

Scene note defines:

- where this chat currently is
- what atmosphere should dominate
- how the present interaction should feel

The scene must be editable without mutating persona identity.

### 2. Scene Is Not Continuity

Scene note is:

- current
- local
- intentional

Continuity is:

- cross-chat
- learned
- compact

They must not be merged.

### 3. Presets Are Authoring Shortcuts, Not Runtime Magic

A preset should:

- prefill a strong scene seed
- reduce friction
- stay fully editable

A preset should not:

- become a hidden behavior engine
- lock the user into a taxonomy
- create a second persona abstraction

## Proposed Data Model

Minimal V1 shape:

```ts
type SceneNote = {
	enabled: boolean;
	preset_id: string | null;
	title: string | null;
	note: string;
	resolved_note: string;
	updated_at: number | null;
};
```

Recommended persistence location for first implementation:

- `chat.meta.scene_note`

Not a new table in V1.

Reason:

- scene is chat-local
- scene lifetime should match chat lifetime
- no need for a separate relational subsystem yet

## Preset Model

Minimal preset registry:

```ts
type ScenePreset = {
	id: string;
	label: string;
	seed: string;
};
```

Recommended preset set for first pass:

1. `foggy-place`
   Label: `A Foggy Place Where Anything Can Become Real`
   Seed: `The scene feels unpinned from ordinary reality: dim, soft-edged, and slightly dreamlike. Distance and closeness can shift without warning. Let atmosphere, uncertainty, and invitation lead before action does.`

2. `tavern-after-midnight`
   Label: `The Tavern After Midnight`
   Seed: `A late hour, low light, slow voices, worn wood, and a sense that the room is holding onto old stories. Keep the pacing unhurried and let tension build through glances, pauses, and physical proximity.`

3. `smoky-room`
   Label: `A Smoky Room With Heavy Curtains`
   Seed: `The air is dense, intimate, and slightly dangerous. Light is filtered, sound is muted, and every movement feels deliberate. Favor restraint, texture, and pressure over speed.`

4. `lights-left-low`
   Label: `The Apartment With The Lights Left Low`
   Seed: `Private, enclosed, and close. The scene should feel domestic but charged, with attention on body language, silence, and the small shifts that make closeness feel earned.`

5. `hotel-room`
   Label: `A Hotel Room In A City That Hardly Sleeps`
   Seed: `Temporary space, late hour, thin walls, and a sense of being suspended outside ordinary consequence. Keep the emotional and atmospheric focus sharper than the logistics.`

6. `backstage`
   Label: `Backstage After The Show`
   Seed: `Residual adrenaline, heat, noise fading into distance, and the feeling that something is still vibrating under the skin. Let exhaustion, electricity, and afterglow shape the rhythm.`

7. `office-after-hours`
   Label: `The Office After Hours`
   Seed: `An almost-empty place that still remembers order and routine, now shifted into private ambiguity. Keep the tone controlled, charged, and slightly transgressive without rushing.`

8. `car-in-rain`
   Label: `The Car Pulled Over In The Rain`
   Seed: `A narrow, enclosed space cut off from the world by weather and glass. Use sound, fogged surfaces, breath, and proximity to build atmosphere before escalation.`

9. `quiet-walk`
   Label: `A Late Walk Where The World Feels Further Away`
   Seed: `Movement is slow, the world is dimmer and less crowded, and meaning gathers through what is not said immediately. Let silence and pacing carry part of the scene.`

10. `blank-scene`
    Label: `Start From A Bare Room`
    Seed: `Do not assume a rich setting. Keep the frame minimal and let the scene become concrete only through the user's cues and the immediate exchange.`

## Injection Contract

The current intended order should become:

1. persona core
2. partner profile
3. scene note
4. lorebook hits
5. continuity snapshot
6. normal chat/history/context maintenance stack

For the first implementation, only the `scene note` addition is in scope.

Recommended prompt block shape:

```text
[Scene note - active scene framing]
The user deliberately set or updated the current scene for this chat.
Treat the following as the active scene from this point onward.
Do not rewrite earlier messages.
If the existing chat history implies a transition, make a reasonable attempt to let the shift feel natural.
If no explicit transition is needed, simply assume this is the current setting and behavioral frame now.
Do not overwrite the user's actions, thoughts, choices, or consent.
Active scene:
<resolved scene note text>
```

Scene note should be injected:

- after persona identity/system contract
- before later storytelling layers such as lorebook
- outside the learned continuity path

## Resolved Scene Note

The runtime should not inject preset metadata separately.

Instead, it should resolve:

- preset seed
- plus any manual edits

into one final scene-note text block.

Recommended rule:

- if preset exists and note is empty -> inject preset seed
- if preset exists and note has text -> inject `preset seed + edited note`
- if preset is null and note has text -> inject note only
- if disabled or empty -> inject nothing

## UX Surface

Recommended first UI surface:

- a `Scene` button or menu action in chat chrome
- opens a compact drawer or modal

V1 layout:

- preset picker at the top
- editable title field
- large freeform note textarea
- preview of resolved note
- actions:
  - save
  - clear
  - disable

Important UX rules:

- presets must be editable after selection
- the user must always be able to ignore presets and write freeform
- scene state should be visibly chat-local
- changing scene must never change the persona itself
- changing scene mid-chat is forward-only and does not rewrite earlier turns

## What This Is Not

- not lorebook
- not continuity
- not a giant roleplay taxonomy
- not a hidden mode switch
- not a prompt manager

## Relationship To Future Layers

Future `partner profile` should hold:

- always-on relational/user-facing context
- preferences about how the persona should relate to the maintainer

Future `lorebook` should hold:

- reusable world facts
- recurring places
- recurring side characters
- recurring domain fiction

Future `continuity` should hold:

- compact learned residue across chats
- relationship movement
- unresolved threads that survive session boundaries

Those layers should remain separate from scene note.

## First Behavioral Tests

For `SUNFALL`-style personas, scene note should improve these cases:

1. Atmosphere retention
   The model should keep the active setting legible for several turns without re-stating it mechanically.

2. POV discipline
   The model should not slip into detached narrator mode just because the scene block exists.

3. No user overwrite
   The model should not use the scene note as an excuse to take control of the user's actions.

4. Pacing retention
   Slow-burn scenes should remain slow-burn instead of jumping to outcomes.

5. Persona separation
   Changing scene note should not alter persona identity, voice, or cross-chat defaults.

6. Chat locality
   A scene note created in one chat must not appear in a different chat unless explicitly copied.

## Implementation Recommendation

Do this before:

- continuity synthesis
- persona-scoped recall

Do this after:

- persona core
- partner profile design is at least understood

In practice, this means the next concrete storytelling slice should be:

1. `Scene Note`
2. `Partner Profile`
3. `Lorebook V0`

and only after that:

4. `Continuity`
5. `Persona-Scoped Recall`
