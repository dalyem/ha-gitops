# HA-GitOps

A Home Assistant Supervisor **add-on** that provides GitOps-style, validation-gated,
bidirectional configuration management between a GitHub repository and your Home
Assistant instance.

> **Core promise:** a bad commit can never take Home Assistant down. Every change is
> validated *before* it is applied, and Home Assistant is only restarted once the new
> configuration is known-good.

## What it does

- 🔌 Connect a GitHub repo with a **fine-grained Personal Access Token** (least-privilege, no hosted infrastructure).
- 🔍 **Readiness analysis** — refuses to deploy from a repo that has committed secrets, databases, logs or `.storage`.
- 🌱 **Initialize** an empty repo from your current Home Assistant config (with a safe `.gitignore` and `secrets.yaml.example`).
- ⏱ **Continuously polls** GitHub (configurable interval) using cheap conditional requests.
- ✅ **Two-layer validation** (in-staging pre-screen + Home Assistant's authoritative `check_config`) before any deploy.
- 🚀 **Deploy Now** for on-demand syncs; **Core-only restart** (never reboots HAOS).
- ↩️ **Local change detection** + manual **push back** to GitHub.
- ⚠️ **Conflict detection** when both sides diverge (warns and blocks, never silently clobbers).
- 📜 Full **deployment & sync history** and **notifications** (persistent + mobile).

## Status

Early MVP — see [`docs/`](docs/) and the in-repo design plan. Built with Python + FastAPI.

## Repository layout

```
ha-gitops/
├─ repository.yaml          # Home Assistant add-on store descriptor
├─ ha_gitops/               # the add-on (Docker build context)
│  ├─ config.yaml           # add-on manifest
│  ├─ Dockerfile / build.yaml / run.sh / apparmor.txt
│  ├─ requirements.txt
│  └─ engine/               # the Python deployment engine + web UI
├─ tests/                   # pytest unit/integration tests
└─ docs/                    # architecture & usage docs
```

## Installing (local/dev)

1. Add this repository to the Home Assistant add-on store (**Settings → Add-ons → ⋮ → Repositories**),
   or place the `ha_gitops/` folder under `/addons/` on a Supervised/HAOS install.
2. Install **HA-GitOps**, start it, and open the **Web UI** (Ingress).
3. Paste a fine-grained PAT, pick your repo/branch/path, review readiness, and deploy.

See [`ha_gitops/DOCS.md`](ha_gitops/DOCS.md) for full setup and the token permissions required.

## License

See [LICENSE](LICENSE).
