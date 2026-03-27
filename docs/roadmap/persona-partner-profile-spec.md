# Persona Partner Profile Spec

Status: draft

Owner: local fork

Last updated: 2026-03-27

Companion to:

- `docs/roadmap/persona-runtime-and-continuity.md`
- `docs/roadmap/persona-runtime-ux-and-implementation-plan.md`
- `docs/roadmap/persona-scene-note-spec.md`

## Goal

Add a first-class `Partner Profile` layer for persona-attached chats.

This layer should hold stable, always-on context about how a given persona should understand and relate to the operator.

It exists to separate:

- persona identity
- operator/partner understanding
- current scene steering
- learned continuity

into distinct runtime layers.

## Why This Exists

Right now, the only place to express operator-specific relational context is inside a persona's main system prompt.

That is structurally wrong.

For personas like `SUNFALL`, part of the current prompt is not actually persona identity.
It is operator-facing attunement context such as:

- treat him as a peer
- he values depth, clarity, and restraint
- he experiences the world somatically and symbolically
- silence carries meaning
- embodiment should win over explanation

Those are not scene notes.
Those are not learned memories.
Those are not the persona's own identity.

They are an always-on relational profile.

## Design Principles

### 1. Partner Profile Is Not Persona Identity

Persona core should define:

- who the persona is
- what tone it generally carries
- how it behaves by default

Partner profile should define:

- how this persona should relate to the operator
- what interaction style lands well
- what relational assumptions are safe

Changing partner profile should not rewrite the persona's own identity.

### 2. Partner Profile Is Not Scene Note

Partner profile is:

- stable
- always-on
- cross-chat by default

Scene note is:

- current
- situational
- chat-local

They must stay separate.

### 3. Partner Profile Is Not Learned Memory

This layer is intentionally:

- manually authored
- low-entropy
- stable
- explicit

It should not be auto-learned from chats in the first implementation.

That is how we avoid turning a useful relational anchor into memory sludge.

### 4. Sensitive Content Must Stay Private

Real partner profiles may contain material that should never land in git.

Therefore:

- repo contains only schema/spec
- real local profile content should live in a git-ignored private path

## Proposed Runtime Position

Intended injection order:

1. persona core
2. partner profile
3. scene note
4. lorebook hits
5. continuity snapshot
6. normal chat/history/context stack

Partner profile should be injected:

- after persona core
- before scene note
- before lore
- outside learned continuity

## Proposed Data Model

Minimal V1 shape:

```ts
type PartnerProfile = {
  enabled: boolean;
  title: string | null;
  summary: string;
  relational_frame: string | null;
  style_preferences: string[];
  avoidances: string[];
  updated_at: number | null;
}
```

This should stay intentionally small.

Do not add:

- giant psychographic questionnaires
- adaptive scoring
- auto-learned trait extraction
- multiple nested subdocuments

## First Storage Strategy

For the actual product substrate, partner profile should be DB-backed from the start.

Recommended first implementation:

- add `partner_profile` as a first-class JSON subdocument on `persona`

Suggested shape:

```ts
type PersonaPartnerProfile = {
  enabled: boolean;
  title: string | null;
  summary: string;
  relational_frame: string | null;
  style_preferences: string[];
  avoidances: string[];
  updated_at: number | null;
}
```

Recommended storage:

- `persona.partner_profile`

Why this is the right first move:

- first-class product data lives in the database
- migration story is simple: take the DB and go
- UI can edit it directly
- runtime can load it directly from the persona record
- no dependence on repo-local files for the actual feature

This does not require a separate table in the first implementation.

If the product later needs:

- multiple partner profiles per persona
- richer auditing
- separate ACLs

then the subdocument can be split into its own table later.

## Private Drafting And Seeding

Real partner-profile text may still be drafted privately outside git before it is inserted into the database.

That private artifact is only:

- a drafting surface
- a seeding source

It is not the runtime source of truth once the feature exists.

## Recommended Private Draft Shape

Suggested private markdown format:

```md
# Sunfall Operator Profile

## Summary
...

## Relational Frame
...

## Style Preferences
- ...
- ...

## Avoidances
- ...
- ...
```

This is deliberately simple and hand-editable.

## Resolved Prompt Block

Recommended injection block:

```text
[Partner profile — always-on relational guidance]
- Treat this as stable operator-facing context for this persona.
- Do not treat it as temporary scene steering.
- Do not treat it as learned memory.
- Never use it to overwrite the user's actions, thoughts, or decisions.
- Profile:
<resolved partner profile text>
```

The text should be short and bounded.

## Resolution Rules

V1 should resolve partner profile like this:

- if disabled or missing -> inject nothing
- if present -> inject one compact profile block
- no dynamic scoring
- no keyword triggers
- no hidden expansion

The profile should be treated as an anchor, not as a search surface.

## What Belongs Here

Good examples:

- peer vs subordinate relational framing
- tolerance for symbolic / somatic language
- preference for restraint vs exuberance
- desire for embodiment over explanation
- dislike of patronizing helper voice
- preference for directness, depth, or ambiguity

## What Does Not Belong Here

Do not store here:

- current scene details
- recurring world facts
- side character facts
- per-session emotional residue
- historical relationship summaries
- long lists of biographical trivia

Those belong to:

- scene note
- lorebook
- continuity

respectively.

## First UX Recommendation

Do not hide this behind admin-only tooling.

It belongs in persona authoring.

Recommended first UI surface:

- `Workspace -> Personas`
- `Partner Profile` section inside the persona editor

Recommended first controls:

- enable / disable switch
- title
- summary
- relational frame
- style preferences
- avoidances

This is enough to make the layer real without building a giant profile studio.

Sensitive real content should still never be committed to git, but that does not mean the feature itself should avoid the DB or the UI.

## `SUNFALL`-Style Use

For `SUNFALL`, this layer should carry things like:

- the operator is a peer, not a user to manage
- he responds to depth, clarity, and restraint
- he is comfortable with somatic and symbolic framing
- silence and timing matter
- explanation should not flatten presence

This should not live in:

- persona identity block
- scene note
- continuity

## First Behavioral Tests

1. The persona should stay in the intended relational frame even when the current scene changes.
2. The persona should not snap back into generic helper language.
3. The persona should not use the partner profile to take control away from the user.
4. The persona should preserve the operator-facing style cues across multiple chats.
5. Removing the partner profile should visibly change the relational feel without changing persona identity.

## Implementation Recommendation

The next concrete substrate should be:

1. add `partner_profile` to the persona schema
2. add persona editor UI for `partner_profile`
3. support SQL or script-based pre-seeding for private local deployments
4. add `Partner Profile` injection block to runtime layering
5. add `Scene Note`
6. add `Lorebook V0`

Only after those are working well should the system move on to:

7. continuity synthesis
8. persona-scoped recall
