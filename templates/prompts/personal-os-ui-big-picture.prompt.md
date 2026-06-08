# Personal OS Big Picture UI redesign prompt

Use the reference images under `.agentops/examples/images/` or `agentops-swarm/examples/images/` as visual inspiration only.

Design direction:
- Steam Big Picture-style command cockpit.
- Large horizontal carousel cards with clear focus state.
- Floating glass panels over a cinematic blurred/gradient background.
- Clean, readable text; no white-on-white panels.
- Profile/device selector and status row.
- Left rail that can collapse into a focus mode.
- Big action cards: Capture, Tasks, Study, Zettel, Research, Maps, Coding Agent, Intelligence.
- Social/communication metaphor for task delegation and secretary flows: floating message cards, online status, pending action chips.
- Controller/keyboard-friendly navigation.
- Mobile-first simplification: bottom nav + horizontal carousels.

Functional constraints:
- Do not copy third-party copyrighted assets; generate local SVG/gradient illustrations.
- Preserve route contracts and API calls.
- Every page must have loading, empty, success, and error states.
- Use shared Nexus components rather than per-page hacks.
- Add visual regression/contract tests for no unreadable cards, no icon text artifacts, and no horizontal overflow.

Acceptance:
- Command Center feels like the main hub.
- /big-picture exists and is useful.
- /capture, /tasks, /study, /zettelkasten, /research, /geospatial, /connectors render cleanly at 1366x768 and mobile width.
- pnpm build passes.
