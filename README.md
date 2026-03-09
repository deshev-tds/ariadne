# Open WebUI Fork for Local Context, Recall, Voice, and Token Inspection

This repository is a fork of Open WebUI, built for a different priority set than the upstream project README optimizes for.

Upstream Open WebUI is a strong base platform and UI for self-hosted LLM workflows. This fork keeps that base, but diverges where local-first power-user workflows need tighter control: long-running chats, context overflow, exact recovery of old facts, practical local TTS quality, and generation inspection that is useful during real debugging rather than only during demos.

If you care about local models, long technical chats, prompt-budget hygiene, and being able to inspect or deliberately fork a response path, this fork is trying to make those workflows less opaque and less fragile.

## Why This Fork Exists

The upstream project is broad. This fork is narrow on purpose.

The main idea is that local LLM UX falls apart in a few predictable places:

- context windows fill up long before the conversation is actually "done"
- summary alone is not memory
- local TTS often sounds either too synthetic or too heavyweight
- token-level generation is usually hidden, even when you need to inspect or steer it

So this fork biases toward:

- server-side context hygiene
- bounded recall instead of blind replay
- lightweight but good local TTS
- generation observability and deliberate branching

It is not trying to replace upstream Open WebUI. It is trying to make a specific local workflow sharper.

## What Changed from Upstream

The important divergences are not cosmetic.

- Server-side context maintenance was added so long chats do not simply degrade or fail once the prompt gets too large.
- Summary generation was changed from a loose recap into a structured state snapshot.
- Exact recall was added so older raw facts can be recovered when they fall out of the live prompt window.
- Web retrieval was pushed toward planned, bounded evidence gathering instead of naive query-and-dump behavior.
- Kokoro TTS paths were added because they sound better than many lightweight local options without dragging in a huge stack.
- Token explorer support and manual response branching from token alternatives were added so local generation is less of a black box.
- An opt-in Simon Cognitive Engine path was embedded as a separate experimental layer for more aggressive retrieval and cognitive routing ideas.

The rest of this README explains the rationale behind those changes.

## Context and Memory in This Fork

This fork now treats context as a layered system, not as "replay everything until the model breaks".

### Stage 1: Context Compaction

The first change was server-side context maintenance.

Instead of replaying the entire branch forever, the server keeps:

- the system prompt
- a smart anchor from the start of the chat
- a recent raw tail of messages
- a rolling summary for the older middle

This maintenance can happen inline when the request would overflow, and in the background after a turn when the history is approaching the configured budget.

The goal was straightforward: do not wait for the backend runner to guess what to trim semantically, and do not bluntly drop the oldest turns if the opening contract of the chat still matters.

### Stage 2: Structured State Snapshot

The second change was not a new memory system. It was a better recap format.

Instead of asking the model for a narrative summary, this fork asks for a structured state snapshot with sections such as:

- User Objectives
- Constraints and Preferences
- Decisions and Conclusions
- Open Questions and Unresolved Work
- Stable Facts and Assumptions

That change matters because models generally behave better when earlier conversation state is presented as explicit structure rather than a prose recap. It reduces drift and makes selective attention less fragile.

### Stage 3: Exact Recall

The third change adds a real recall layer on top of working memory.

Working memory still looks like this:

- system prompt
- anchor
- structured snapshot
- recent raw tail

But if that live state does not provide enough evidence, the server can now recover older raw facts from earlier turns and inject them back into the prompt as evidence.

This recall layer is intentionally bounded and conservative. It is not an always-on retrieval ritual before every response.

It currently has two modes:

- `FTS recall`
  Used for explicit references, missing entities, and continuation cases where there is a useful lexical query.

- `branch_recent recall`
  Used for vague referential phrases like "the other tool" or "the old config", where sending pronoun-heavy text into FTS would likely fail.

Recovered snippets are injected as evidence, not as narrative:

```text
Evidence from earlier conversation:
[turn <id> | <role>]
...
```

That provenance matters. The model is being shown evidence, not a second-hand retelling.

### Simulated User Flows

Here is what that means in practice.

**Flow 1: No recall needed**

User:
`Continue with ffuf for the next step.`

If `ffuf` is still present in the recent raw tail or structured snapshot, the server does nothing special. The request stays on the fast path.

**Flow 2: Explicit factual recall**

User:
`What did we decide earlier about ffuf?`

This triggers bounded FTS recall. The server searches older turns, recovers the relevant excerpt, injects it as evidence, and only then lets the model answer.

**Flow 3: Missing entity, but no explicit "remember" wording**

User:
`Keep the same endpoint, but change the timeout to 5 seconds.`

If the endpoint has already fallen out of the live window, the server can recall the earlier raw mention instead of leaving the model to guess.

**Flow 4: Vague referential phrase**

User:
`What happened with that other tool?`

This is where `branch_recent` recall matters. Instead of issuing a bad FTS query built from vague pronouns, the server inspects a bounded recent window of older branch messages and injects a few evidence snippets for disambiguation.

The design bias is intentional:

- prefer false negatives over false positives
- do not tax every turn with retrieval
- recover old facts when evidence is weak, not whenever retrieval is merely possible

## Web Search and Retrieval Planning

This fork also treats web behavior as a retrieval problem, not just as "run a search and paste the results into context".

The rationale is similar to the context work above: the problem is not merely getting access to more text. The problem is deciding what evidence is worth fetching, how much of it is worth keeping, when to stop, and how to avoid polluting the prompt with redundant or weak material.

In practice, this means the web path here is more structured than a flat search integration:

- multiple planner modes exist instead of one hardcoded query flow
- a source registry provides machine-readable hints about where different kinds of queries should go
- the active model can be used as a query rewriter when that helps, but the system still keeps explicit fallback paths
- planner telemetry tracks retries, fallback usage, executed queries, and stopping conditions instead of treating the whole thing as opaque middleware

The important behavioral shift is this:

- upstream-style web search is often thought of as "query provider -> collect results -> inject results"
- this fork pushes it toward "plan -> target sources -> bound evidence -> stop when enough evidence exists"

That matters more on local setups than it first appears. Prompt budget is finite, retrieval latency is visible, and low-quality web evidence is actively harmful when it crowds out the rest of the conversation. The planner/rewriter/source-registry work is there to make web retrieval less brute-force and less noisy.

At a high level, the current web path behaves more like this:

1. choose a planning mode
2. optionally rewrite or refine the search queries using the active model
3. target sources with planner hints instead of treating all sources as equivalent
4. stop once the evidence quality or coverage is good enough, instead of continuing mechanically
5. surface planner status and fallback information so the path is inspectable

This is the same overall philosophy as the context work: bounded, inspectable, evidence-oriented behavior beats magical but opaque behavior.

## Simon Cognitive Engine

This fork also embeds an opt-in Simon Cognitive Engine path as a separate pipe model.

Simon is a separate public project by the same author:

- https://github.com/deshev-tds/simon

The relationship between the two is deliberate:

- Open WebUI is the more mature product substrate
- Simon is where more aggressive memory, retrieval, and cognitive-routing ideas are explored as a more comprehensive experimental system
- this fork borrows architectural and behavioral ideas from Simon where they can improve Open WebUI without turning the whole product into an experimental research scaffold

So the Simon integration here should be read as a bridge, not as a replacement. It lets the fork expose a more opinionated cognitive path without making the standard Open WebUI chat path depend on it.

## Voice / TTS

This fork adds Kokoro because local TTS quality matters, and a lot of local stacks are either too robotic or too heavy for the quality they provide.

Kokoro was added as a practical compromise:

- more natural sounding voices than many lightweight alternatives
- lightweight enough to remain useful in local deployments
- broad enough voice selection to make experimentation worthwhile

This fork supports Kokoro in the places where it is actually useful:

- backend/local Kokoro ONNX
- browser-side Kokoro.js where that path makes sense

The point is not to chase the largest TTS stack. The point is to improve voice quality per watt, latency, and setup complexity.

## Token Exploration and Response Branching

Most chat UIs hide generation internals completely. That is convenient until you need to inspect why a response happened, or you want to deliberately fork from a different token path.

This fork adds token explorer support so you can inspect token/logit alternatives when the backend supports the necessary telemetry. It also supports manually creating a new response branch from a selected token alternative instead of treating the sampled continuation as sacred.

That matters for local workflows because it gives you:

- a way to inspect why a continuation happened
- a way to compare plausible alternatives
- a way to fork a response branch deliberately without rewriting the whole prompt

This is not presented here as "full interpretability". It is a practical debugging and exploration tool for model behavior.

## Other Notable Divergences

The most important differences in this fork are the ones above, but the operating theme is consistent:

- working memory is managed server-side instead of being left to overflow
- recap is structured state, not a loose narrative
- recall is bounded, evidence-first, and not always-on
- web retrieval is planned and bounded rather than treated as a blind append-to-prompt step
- local TTS is treated as a quality problem worth solving
- token-level generation is inspectable when the backend exposes enough data
- Simon exists as an opt-in cognitive path for ideas that are more aggressive than the default chat path should be

There is also early scaffolding for runtime MoE experts probing/control on compatible OpenAI-style backends. That work is real, but it is not yet central enough to this fork's identity to present as a finished headline capability.

Also, as a matter of principle, chat does not try to steal `Cmd+R` from the browser. Reload still means reload. Some boundaries deserve respect.

The bias throughout is the same: better local behavior, better debuggability, fewer "just trust the model" assumptions.

## Compatibility / Install

This is still Open WebUI under the hood, and the basic deployment model remains broadly compatible with upstream expectations. If you already know how to run Open WebUI, that knowledge still transfers.

This README intentionally does not duplicate the upstream install matrix. The fork is primarily aimed at local deployments and local model runners, and the most practical path is usually to keep using the deployment method you already use for Open WebUI while applying this fork's code and settings.

For generic deployment guidance, refer to the upstream Open WebUI documentation:

- https://docs.openwebui.com/
- https://github.com/open-webui/open-webui

## Upstream Attribution

This fork is built on Open WebUI, and upstream remains the base platform.

The goal here is not to replace upstream, but to diverge where local-first usage benefits from different tradeoffs: tighter context management, bounded recall, better lightweight voice options, and more transparent generation tooling.

Licensing and attribution remain those of the underlying project and this fork's codebase. See [LICENSE](./LICENSE) and [LICENSE_HISTORY](./LICENSE_HISTORY).
