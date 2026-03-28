# Google Maps Integration Handover

Status: ready for implementation planning

Owner: local fork

Last updated: 2026-03-28

## Why This Exists

This is a context pack for the next clean chat about Google Maps integration.

The goal is to let the next session start from implementation reality instead of re-discovering:

- what persona substrate already exists
- what the `TRAVEL PLANNER` persona is supposed to do
- what map-related capability is currently missing
- where the likely code touchpoints are

## Executive Summary

The fork now has a usable persona runtime:

- first-class private personas
- per-persona `bound_model_id`
- per-persona `partner_profile`
- per-persona voice overrides
- chat-local `scene_note`
- per-persona tools / features / capabilities defaults

What it does not have yet is a dedicated Google Maps / Places integration.

That missing capability matters most for `TRAVEL PLANNER`, whose job is not just to recommend places, but to produce usable artifacts such as:

- phone-ready map lists
- shortlist clusters by neighborhood
- stay-area recommendations
- walking / evening flows

The immediate value of the Google Maps work is not "travel fluff."

It is a place-resolution layer that can turn researched recommendations into clean, map-usable outputs with stable links.

## Current Product State

### Personas already shipped

The fork already ships:

- `SUNFALL`
- `ADVISOR`
- `TRAVEL PLANNER`

`TRAVEL PLANNER` was seeded live through the personas API and currently exists on the local live box as:

- name: `TRAVEL PLANNER`
- emoji: `🧭`
- bound model: `assistant-step-35-flash-ablitiratedi1-iq4xs`
- default feature: `web_search`

The local draft sources used to seed it are git-ignored:

- `/.local/persona-drafts/travel-planner-core-prompt.md`
- `/.local/persona-drafts/travel-planner-partner-profile.json`
- `/.local/persona-drafts/travel-planner-seed.json`
- `/.local/persona-drafts/travel-planner-notes.md`

### Persona substrate already available

Relevant persona capabilities that already exist:

- persona editor with bound model selection
- persona editor with `partner_profile`
- persona-level default tools and features
- persona-attached chats with pinned runtime defaults
- `scene_note` for chat-local steering

This means the Google Maps work should not start by inventing a new persona system.

It should attach a reusable place-resolution capability to the existing runtime.

## What Does Not Exist Yet

There is currently no Google Maps / Places integration in this fork.

There is no:

- built-in Google Maps tool
- Places autocomplete adapter
- place details resolver
- map-link enrichment step
- dedicated admin or workspace config for Google Maps API credentials

There are generic autocomplete surfaces in the codebase, but they are unrelated to place resolution.

There is also existing `web_search`, but that is not enough for clean place linking.

## Product Goal For The Maps Work

The first implementation should make it easy for personas, especially `TRAVEL PLANNER`, to do this:

1. Research places normally.
2. Filter / rank them.
3. Resolve chosen places into stable, map-usable references.
4. Output artifacts that are easy to use on a phone or turn into a guide.

The system does not need a giant travel platform on day one.

The highest-ROI MVP is:

- resolve place names into canonical map-ready results
- return stable Google Maps links
- optionally return structured metadata useful for grouping or validation

## Recommended MVP Shape

Prefer a reusable tool, not a prompt-only trick.

Recommended initial tool contract:

- input:
  - place name
  - city / neighborhood / region / country context
  - optional query intent or hint text
- output:
  - canonical place name
  - Google Maps URL
  - formatted address
  - place id if available
  - coordinates if cheap to obtain
  - lightweight confidence / ambiguity note

This should be usable by:

- `TRAVEL PLANNER`
- future local-city or logistics personas
- any other persona that wants clean map links

## Why A Tool Is Better Than Prompting

Prompt-only map generation will fail in predictable ways:

- wrong or unstable links
- fuzzy naming
- hallucinated addresses
- inconsistent formatting
- too much reliance on generic search results

The right abstraction is:

`research and filtering via normal model + search -> place resolution via dedicated tool`

## Recommended Integration Direction

### Preferred architecture

Implement this as a server-side tool or built-in capability that can be exposed to personas through the existing tool/function pipeline.

That gives:

- secret handling on the backend
- reusable structured outputs
- attachability to personas via existing `tool_ids`
- future extension to other personas without product reshaping

### Not recommended for V1

- not a giant "travel planner module"
- not a full custom itinerary engine
- not a full map UI
- not a client-side-only API key flow

## Existing Code Touchpoints

These are the most relevant places for the next chat to inspect first.

### Persona runtime and editor

- `backend/open_webui/models/personas.py`
- `backend/open_webui/utils/personas.py`
- `backend/open_webui/routers/personas.py`
- `src/lib/apis/personas/index.ts`
- `src/lib/components/workspace/Personas/PersonaEditor.svelte`

Why:

- personas already support bound models, tools, features, and `partner_profile`
- this is where `TRAVEL PLANNER` can be given the new map tool by default later

### Tool and function infrastructure

- `backend/open_webui/main.py`
- `backend/open_webui/functions.py`
- `backend/open_webui/models/functions.py`
- `backend/open_webui/models/tools.py`
- `backend/open_webui/routers/functions.py`
- `backend/open_webui/routers/tools.py`
- `backend/open_webui/utils/middleware.py`

Why:

- this is where tools/functions are registered, resolved, and surfaced to chat runtime
- native and non-native function-calling flows already exist
- the maps integration should probably plug in here, not invent a parallel execution path

### Existing feature and settings patterns

- `backend/open_webui/config.py`
- `src/lib/components/admin/Settings/Models/ModelSettingsModal.svelte`
- `src/lib/components/workspace/Models/BuiltinTools.svelte`
- `src/lib/components/workspace/Models/DefaultFeatures.svelte`

Why:

- useful reference for how existing features and per-model defaults are exposed
- likely useful when deciding how to configure the new maps capability or attach it to a persona

### Existing web search pipeline

- `backend/open_webui/utils/middleware.py`
- `src/lib/components/chat/MessageInput.svelte`

Why:

- `TRAVEL PLANNER` already defaults to `web_search`
- the future maps tool will probably complement web search, not replace it

## Travel Planner-Specific Context

The user explicitly wants `TRAVEL PLANNER` to be:

- anti-generic-tourism
- local-language-first when useful
- good at filtering tourist noise
- good at neighborhood logic
- capable of turning research into phone-ready and printable artifacts

For this persona, Google Maps integration is not "extra convenience."

It is part of making the output operational.

The most valuable artifact forms are:

- shortlist with map links
- grouped-by-area lists
- hotel-area recommendations with spatial logic
- day/evening clusters

## Security And Secrets

Do not store the Google Maps API key in git.

The next implementation should assume:

- credentials live in local config, env vars, or admin settings
- no key should be written into repo docs, fixtures, or tracked files

If a new config field is added, it should follow existing secret-handling patterns in the backend config layer.

## Open Design Questions For The Next Chat

The next implementation chat should decide:

1. Which exact Google API surface to use first.
2. Whether V1 needs only autocomplete-style resolution or also place details.
3. Whether the integration should be:
   - a built-in tool
   - a function
   - or another backend capability surface
4. Where the API key should be configured:
   - env var
   - admin settings
   - or another secret store pattern already used in the fork
5. Whether the MVP returns only a single best match or a ranked shortlist of candidate matches.

Do not over-design this before the first working tool exists.

## Suggested MVP Milestones

1. Pick the integration surface.
2. Add backend config for the API key without leaking secrets.
3. Implement one minimal place-resolution call.
4. Return structured result + stable Maps URL.
5. Expose it as a reusable tool.
6. Attach it to `TRAVEL PLANNER` and smoke-test on a real city query.
7. Only then decide whether richer details or batch workflows are worth adding.

## Strong Recommendation

Do not start with a giant travel feature.

Start with:

- one reusable maps resolution tool
- one clean config path
- one persona consumer (`TRAVEL PLANNER`)

If that works well, the rest can grow around it.

## Next Sensible Starting Point For A Fresh Chat

Open these files first:

1. `docs/roadmap/google-maps-integration-handover.md`
2. `backend/open_webui/routers/tools.py`
3. `backend/open_webui/utils/middleware.py`
4. `src/lib/components/workspace/Personas/PersonaEditor.svelte`
5. `/.local/persona-drafts/travel-planner-core-prompt.md`
6. `/.local/persona-drafts/travel-planner-partner-profile.json`

Then decide the thinnest possible Google Maps MVP that can be attached to `TRAVEL PLANNER` without distorting the current persona runtime.
