# Morning News PoC

## Why This Exists

`Morning News` is not meant to be a generic "summarize today's web" assistant.

It is a persona-scoped evidence lane with a very specific job:

- collect a curated local news set
- analyze and thread it before the user wakes up
- synthesize a long-form daily briefing from grounded local artifacts
- emit a ready-to-play `wav` file so the morning experience is instant

The point is not to look concise. The point is to be prepared.

If the model gives a briefing built from 12 or 18 grounded facts, each with setup, exposition, and conclusion, that is not prompt bloat or accidental verbosity. That is the product. A hostile read of the output should land on the same conclusion every time: these are facts, not hallucinated filler. The harness carries that burden on purpose.

## Product Shape

This PoC combines three ideas that are stronger together than separately:

- persona as runtime scaffold, not prompt skin
- evidence lane as a bounded domain workflow, not generic retrieval
- precomputation as a UX choice, not a performance hack

In practice the user does not ask the model to go discover the world from scratch at 7 a.m. The system already did the expensive work:

1. ETL over a curated source registry
2. article analysis and story threading
3. local briefing synthesis
4. TTS rendering to a morning `wav`

By the time the user asks "what is my brief today?", the answer should already exist as an artifact.

## Token Budget Is a Feature

The daily briefing regularly costs on the order of `60,000` tokens.

That is not treated here as an optimization failure. It is a product preference:

- the user wants a full morning dossier, not an executive-summary toy
- the stories should carry enough structure to be speakable and memorable
- the expensive path should run before the user needs it

The system therefore chooses to spend tokens offline so the interactive path stays fast and predictable.

## Daily Ritual UX

The target experience is intentionally cinematic:

- one hour before wake-up, the pipeline runs automatically
- curtains can be synced to sunrise through Home Assistant
- the news ETL, aggregation, synthesis, and TTS finish before the operator is at the desk
- a short intro plays
- a familiar voice reads the prepared briefing

The result is not "voice mode for news." It is a local-first morning ritual built out of precomputed evidence artifacts and persona packaging.

That product direction matters because it clarifies why the system is shaped the way it is:

- local corpus first
- explicit routing
- scheduled artifact creation
- long-form grounded outputs
- TTS as a first-class output, not an afterthought

## Why Persona Matters Here

`Morning News` works better as a persona than as a raw model preset because the workflow is opinionated:

- broad briefing asks should prefer `latest_briefing`
- if no compiled artifact exists, the runtime should build from the latest closed snapshot
- if no snapshot exists, the assistant should say that plainly
- the attached voice, working mode, and runtime defaults should already be right

That is persona territory. The user is not selecting a model. The user is selecting a prepared morning function.

## Why Evidence Lane Matters Here

The evidence lane framing is equally important.

This is not a free-form "read feeds and improvise" feature. The lane exists to constrain and stage the work:

- sources are curated
- recency is enforced before fetch
- stories are deduped and threaded
- analysis outputs become explicit artifacts
- briefing synthesis happens over that local artifact layer

That staging is what lets the final output be both long and trustworthy.

## Product Direction

If this line keeps proving itself, it should be treated as a real fork direction rather than an incidental subfeature:

- persona-packaged workflows with explicit runtime contracts
- evidence-first lanes for high-value bounded domains
- precomputed daily-driver artifacts instead of always-live inference
- voice outputs that are ready before the user enters the loop

In other words: not "an LLM app that also has personas," but a local-first system where a persona can own a serious workflow end to end.
