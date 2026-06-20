# HA-GitOps

GitOps-style configuration management for Home Assistant. GitHub is the source of
truth; this add-on keeps your live configuration in sync with a repository while
**guaranteeing invalid configuration is never deployed**.

## 1. Create a GitHub token (fine-grained PAT)

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Resource owner:** your user, or the organization that owns the repo (the org must allow fine-grained tokens).
3. **Repository access:** *Only select repositories* → choose the single repo you want to manage.
4. **Repository permissions:**
   - **Contents:** Read and write *(required — read commits, push syncs)*
   - **Metadata:** Read-only *(required, auto-selected)*
   - **Issues:** Read and write *(only if you enable `create_github_issues_on_failure`)*
5. Generate and copy the token.

The token is stored on-device only (in the add-on's private `/data` volume, mode `0600`),
never written to `config.yaml`/`options.json`, never placed in a git URL or logged.

## 2. Configure & connect

Open the add-on's **Web UI** (Ingress) and:

1. Paste the token.
2. Pick the **repository**, **branch**, and **config path** (the folder inside the repo
   that maps to your Home Assistant config root — usually the repo root, `""`).
3. Review the **Readiness** report and resolve any blockers.
4. If the repo is empty, use **Initialize Repository From Current Home Assistant Configuration**.

## 3. Add-on options

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | Engine log verbosity. |
| `check_interval` | `5m` | Poll cadence: `1m`, `5m`, `15m`, `30m`, `1h`, or custom (`90s`, `10m`, `2h`). |
| `auto_deploy` | `true` | Auto-deploy new commits when there is **no** conflict/local drift. |
| `notify_on_success` | `false` | Persistent/mobile notification on successful deploy. |
| `notify_on_failure` | `true` | Notify on validation/deploy failure. |
| `notify_service` | `""` | A `notify.*` service for mobile push, e.g. `notify.mobile_app_pixel`. |
| `create_github_issues_on_failure` | `false` | (Future) open a GitHub issue on failure. |
| `backup_before_deploy` | `true` | Take a Supervisor partial backup before each deploy. |

## 4. How a deploy works

1. Poll detects a new commit (or you press **Deploy Now**).
2. The incoming commit is checked out to an isolated staging area in `/data` (never your live config).
3. **Layer-1 validation** runs there: YAML parse, `!include`/`!secret` resolution, forbidden-file check.
4. A Supervisor **partial backup** is taken (if enabled).
5. Files are applied to `/homeassistant`, **snapshotting** every file first.
6. **Layer-2 validation** runs Home Assistant's authoritative `check_config`.
7. **Valid →** Home Assistant Core is restarted (the host is *not* rebooted).
   **Invalid →** the snapshot is restored, Core is **not** restarted, and you are notified.

## 5. What is and isn't synced

**Synced (YAML config):** `configuration.yaml`, `automations.yaml`, `scripts.yaml`,
`scenes.yaml`, `customize.yaml`, `themes/`, `packages/`, `blueprints/`, etc.

**Never synced (runtime/instance state):** `secrets.yaml`, `.storage/` (UI helpers,
dashboards in storage mode, integrations, users), `home-assistant_v2.db`, logs, backups,
`.cloud/`, `deps/`, `tts/`. These are excluded by the recommended `.gitignore` and the
add-on actively refuses to commit them.

> Edits you make in the UI that land in **`.storage`** (e.g. helpers, storage-mode
> dashboards, integration setup) are **not** version-controlled by design — `.storage`
> also holds auth tokens, login credentials, integration API keys and the device/entity
> registries, so committing it would leak secrets and corrupt other instances.

## 5a. Making dashboards versionable (for AI-driven editing)

By default Home Assistant keeps **dashboards in "storage mode"** (in `.storage`), so they
can't be committed or edited as files. To version them — and let an AI build/edit them via
files that HA-GitOps then deploys — switch the dashboard(s) to **YAML mode**:

- **A whole dashboard in YAML:** Settings → Dashboards → open a dashboard → ⋮ → **Edit in
  YAML** won't persist to a file by itself. Instead add a YAML-mode dashboard in
  `configuration.yaml`:
  ```yaml
  lovelace:
    mode: yaml          # the default/overview dashboard now reads ui-lovelace.yaml
    dashboards:
      laundry:
        mode: yaml
        title: Laundry
        filename: dashboards/laundry.yaml
        show_in_sidebar: true
  ```
  Create `ui-lovelace.yaml` (and `dashboards/laundry.yaml`) — these are plain YAML the AI
  can write. HA-GitOps versions and deploys them; a Core restart (or a Lovelace reload)
  applies them.
- **Automations / scripts / scenes** are *already* YAML (`automations.yaml`, `scripts.yaml`,
  `scenes.yaml`) even when created in the UI, so they're versioned and AI-editable as-is.
- **Not versionable as files:** UI-created helpers, integration/config entries, devices,
  areas and users live in `.storage` and stay instance-local. (Many helpers *can* be
  declared in YAML via `input_*:` / `template:` if you want them versioned.)

The Readiness page flags when dashboards are still in storage mode.

## 6. Known limitations

- `check_config` validates YAML/schema, not runtime behaviour; a config can validate yet
  fail at startup (e.g. a missing custom integration). The post-restart health check and
  the pre-deploy backup mitigate this.
- Architectures: **aarch64** and **amd64** (the 32-bit arches were deprecated in HA 2025.12).
- One repo ↔ one Home Assistant instance. Pointing several instances at one repo will diverge.

## 7. Security

- Least-privilege token (single repo). 
- No inbound ports — polling only (NAT-friendly).
- Web UI is reachable only through authenticated Home Assistant Ingress.
- Tokens are redacted from logs, diffs, notifications and issues.

## 8. Recovery — what the repo is (and isn't) a backup of

HA-GitOps versions your **editable YAML configuration**, not your whole instance. The
repo alone **cannot** restore a lost instance, because it deliberately excludes
`.storage`, the database and `secrets.yaml`.

- **In the repo:** `configuration.yaml`, `automations/scripts/scenes.yaml`, `themes/`,
  `packages/`, `blueprints/`, YAML-mode dashboards, `www/`.
- **NOT in the repo (lives in `.storage` / DB / secrets):** your integrations and their
  settings (config entries), devices, areas, entity customisations, UI-created helpers,
  storage-mode dashboards, users & auth tokens, and all history — plus `secrets.yaml`.

**To recover a lost instance, restore a Home Assistant *full backup*, not this repo.** A
full backup contains `.storage`, the database, secrets and your add-ons. Keep full backups
**off the instance** (e.g. the Google Drive Backup or Samba Backup add-on) — the partial
backups HA-GitOps takes before each deploy live *on* the instance and only help you roll
back a bad deploy, not recover lost hardware. After restoring (or on a fresh HA), reinstall
HA-GitOps, reconnect the repo, and **Deploy Now** to bring the YAML layer back.

**If all you have is the git repo:** you can rebuild the config *files*, but you'll still
re-add every integration, re-create storage-mode dashboards/helpers, re-enter
`secrets.yaml` (use the generated `secrets.yaml.example`), and re-pair devices — that state
lives in `.storage`. Also make sure referenced dirs like `themes/` exist (keep a
`themes/.gitkeep`) and `secrets.yaml` is present, or validation will block the deploy.

**What HA-GitOps *does* protect you from:** bad commits / config mistakes — it validates
before deploying, never restarts on invalid config, reverts a failed apply, takes a
pre-deploy backup, and keeps a deployment history you can redeploy from.
