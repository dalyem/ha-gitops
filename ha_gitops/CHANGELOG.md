# Changelog

## 0.1.0 (unreleased)

Initial MVP scaffold.

- Fine-grained PAT authentication (pluggable auth layer).
- Repository browsing, connect (repo/branch/config-path).
- Repository readiness analysis + scoring with deploy gating.
- Empty-repo initialization from the current Home Assistant configuration.
- Polling monitor with conditional (ETag) GitHub requests.
- Two-layer validation (in-staging pre-screen + Core `check_config`).
- GitHub → Home Assistant deploy pipeline with pre-deploy backup, snapshot-backed
  apply, Core-only restart, and revert-on-failure.
- Local change detection and manual push back to GitHub.
- Conflict detection (warn + block).
- SQLite-backed deployment/sync/conflict history.
- Persistent + mobile notifications.
