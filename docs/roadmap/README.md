# Roadmap

This directory is the repo-local roadmap source of truth for fork-specific work.

Use it for:

- long-lived product and architecture intent
- epics, stories, and tasks that may span multiple chats
- context packs for future agent sessions
- references to external papers, upstream repos, and relevant local git history

Do not use it for:

- minute-by-minute execution status
- ad-hoc scratch notes
- full postmortems or long research transcripts

Recommended workflow:

1. Keep roadmap roots and epic documents here.
2. Track day-to-day execution in a board tool if needed.
3. Link board items back to these docs instead of duplicating the architecture context there.

Structure:

- `root -> story -> task`
- one markdown file per active root/epic
- each epic should include:
  - goal
  - why it exists
  - non-goals
  - references
  - known history / prior attempts
  - story/task breakdown
  - next sensible starting point for a fresh chat

Active epics:

- [Verification-Native Agent Improvement Platform](./verification-native-agent-improvement-platform.md)
- [Persona Runtime And Continuity Layer](./persona-runtime-and-continuity.md)

Context packs:

- [Google Maps Integration Handover](./google-maps-integration-handover.md)
