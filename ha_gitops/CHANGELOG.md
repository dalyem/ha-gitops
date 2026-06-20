# Changelog

## 0.2.0

- New: one-click **dashboard converter** (Readiness page). Converts your UI
  (storage-mode) Lovelace dashboards to YAML mode automatically — reads `.storage`
  **read-only** (your dashboards stay intact and it's reversible), writes
  `ui-lovelace.yaml` + `dashboards/*.yaml`, pulls in your custom-card resources,
  and appends a `lovelace: mode: yaml` block to `configuration.yaml` (append-only,
  with a pre-convert backup). Then review on Changes → Push → restart HA.

## 0.1.6

- Fix: deploy no longer fails validation when the config references an empty or
  optional directory that git can't store — e.g. the default
  `frontend: themes: !include_dir_merge_named themes`. The validator now accepts
  an `!include`/`!include_dir` target that exists on the live instance, and a
  missing `!include_dir` is a warning rather than a blocker (Home Assistant's
  `check_config` stays the authoritative gate).
- Initialize writes a `.gitkeep` into non-ignored empty directories so folders
  like `themes/` and `packages/` are preserved in the repo.

## 0.1.5

- Ignore Home Assistant's `.cache/` directory (downloaded brand icons / runtime
  cache) — it was being committed (hundreds of files, several MB of churn).
  Added to the recommended `.gitignore`, the always-ignore set, and readiness.

## 0.1.4

- Readiness now detects when Lovelace dashboards are in UI/"storage" mode (in
  `.storage`, so not versionable) and recommends switching to YAML mode, which
  makes dashboards editable files an AI can build and HA-GitOps can deploy.
- DOCS: added "Making dashboards versionable" with the YAML-mode setup, and
  clarified why `.storage` must never be committed (it holds auth tokens,
  credentials, integration API keys and the registries).

## 0.1.3

- Fix: Initialize/push no longer try to commit rotated logs (`home-assistant.log.1`
  etc.) — the `*.log` pattern didn't match them. Added `*.log.*` everywhere.
- Add: a hard 50 MB per-file size cap on commits (well under GitHub's 100 MB
  limit); oversized files are skipped and reported, never pushed.
- Fix: Initialize now re-clones from scratch, so a previous failed init (which
  may have left an oversized commit locally) can't poison the retry.

## 0.1.2

- Fix: the Web UI rendered unstyled under HA Ingress because the external
  `/static/app.css` and `app.js` links were mis-routed by the per-session
  ingress path prefix. CSS/JS are now inlined into the page shell, and the
  ingress prefix is read from the `X-Ingress-Path` header for nav/API URLs.

## 0.1.1

- Fix: remove the custom AppArmor profile that blocked the s6 init system and
  caused the add-on to crash-loop at startup ("can't open '/init':
  Permission denied"). A tested profile will return in a later hardening pass.

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
