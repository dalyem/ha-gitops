# Architecture

HA-GitOps is a single Python (FastAPI) container that runs three cooperating concerns
in one asyncio process:

1. **Web/API** — FastAPI served through HA **Ingress**; HTML UI + JSON API.
2. **Scheduler** — background poll loop using conditional GitHub requests (ETag → 304).
3. **Engine** — the deterministic core (git, GitHub, validation, deploy, sync, state).

## Filesystem topology

| Path (in container) | Mapping | Role |
|---|---|---|
| `/homeassistant` | `homeassistant_config:rw` | Live HA config (the deploy target). **Git never touches it.** |
| `/data/repo` | add-on volume | Full git clone (the git work-tree lives here). |
| `/data/staging` | add-on volume | Ephemeral worktree of an *incoming* commit, for Layer-1 validation. |
| `/data/snapshots/<id>` | add-on volume | Per-deploy file snapshot enabling precise revert. |
| `/data/state.db` | add-on volume | SQLite: pointers, history, conflicts, events. |
| `/data/.credentials` | add-on volume (0600) | The GitHub token (never in `options.json`). |

**Design refinement vs. the original plan:** rather than making `/homeassistant` a git
work-tree, the engine keeps the clone in `/data/repo` and applies changes via a
snapshot-backed *file sync* (`core/filesync.py`). This supports a `config_path`
subdirectory inside the repo and removes any risk of git deleting untracked files
(secrets, `.storage`, the database) in the live config dir.

## Deploy pipeline (`core/deployer.py`)

```
fetch → gate (conflict / up-to-date) → readiness (zero blockers)
      → Layer-1 validate (staging worktree, isolated)
      → pre-deploy partial backup
      → snapshot + apply to /homeassistant
      → Layer-2 check_config (HA authoritative)
      → valid? restart Core : restore snapshot (no restart)
      → record + notify + update pointers/manifest
```

## State model (`core/state.py`)

- `last_deployed_sha` — what's running.
- `last_remote_sha` — latest observed branch head (from polling).
- `sync_base_sha` — common base for divergence math.
- `manifest` — `{config-relative path: sha256}` of the last synced tree; drives
  local-drift detection.

Conflict = remote moved **and** local moved from `sync_base_sha`
(`core/conflicts.py`). MVP detects + blocks; resolution actions are future work.

## Module map

| Module | Responsibility |
|---|---|
| `core/engine.py` | AppContext: wiring, mutex, high-level ops, status aggregation. |
| `core/git_engine.py` | git CLI (clone/fetch/ls-tree/worktree/commit/push) via `GIT_ASKPASS`. |
| `core/github_client.py` | REST (repos, branches, branch head w/ ETag, issues). |
| `core/supervisor.py` | Supervisor + Core API (check_config, restart, backup, notify, versions). |
| `core/filesync.py` | hashing, change set, snapshot apply/restore, drift detection. |
| `core/readiness.py` | repo readiness analysis + scoring. |
| `core/validator.py` | Layer-1 YAML / `!include` / `!secret` / forbidden-file checks. |
| `core/deployer.py` | GitHub → HA pipeline. |
| `core/sync.py` | HA → GitHub push + empty-repo initialize. |
| `core/scheduler.py` | poll loop + auto-deploy. |
| `core/notifier.py` | persistent + mobile notifications. |
| `core/issues.py` | (future) GitHub issue reporting + dedup. |

## Security highlights

- Fine-grained PAT, single repo, least privilege. Token on-device only (`0600`),
  redacted everywhere, injected to git via `GIT_ASKPASS` (never in URLs/argv/config).
- No inbound ports (polling only); UI only via authenticated Ingress.
- Forbidden-file guards on init/push; readiness blockers on committed secrets/DBs.
