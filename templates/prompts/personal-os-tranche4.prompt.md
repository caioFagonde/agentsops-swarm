# Personal OS Tranche 4: mobile continuity and deployment reliability

Goal: make Personal OS usable across PC and phone with robust offline sync, Tailscale/QR pairing, ntfy status, backup/update confidence, and safe agent orchestration.

Suggested tasks:
1. mobile-offline-sync: offline capture queue, retry UI, sync state, conflict banner.
2. tailscale-phone-pairing: QR pairing page, device trust, private URL discovery, ntfy subscription instructions.
3. portable-capsule: USB/portable mode docs and scripts; no secrets on host PC by default.
4. agentops-ui: DAG/worktree/report viewer inside Personal OS.
5. qa-mobile-continuity-repair: test all mobile/continuity routes and repair concrete failures.

Acceptance:
- phone can open web UI over Tailscale/private LAN
- capture/task/zettel flows work offline-first or show clear degraded state
- pairing and notification setup are guided
- update/backup status is visible
- tests/certification scripts exist
