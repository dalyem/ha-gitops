"""Runtime paths and environment.

These are *not* user options (those live in ``core/options.py``). Everything here
can be overridden via environment variables so the engine can run outside a real
Home Assistant install for tests and local development.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import __version__


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default))


# Add-on private volume (persists across restarts/updates; in add-on backups).
DATA_DIR: Path = _path("HA_GITOPS_DATA_DIR", "/data")

# The live Home Assistant config dir, mounted via `homeassistant_config:rw`.
HA_CONFIG_DIR: Path = _path("HA_GITOPS_HA_CONFIG", "/homeassistant")

# Derived paths under DATA_DIR.
REPO_DIR: Path = DATA_DIR / "repo"          # full clone (git work-tree lives here)
STAGING_DIR: Path = DATA_DIR / "staging"    # ephemeral worktree for pre-validation
SNAPSHOT_DIR: Path = DATA_DIR / "snapshots"  # per-deploy file snapshots for revert
STATE_DB: Path = DATA_DIR / "state.db"
CREDENTIALS_FILE: Path = DATA_DIR / ".credentials"   # mode 0600
CONNECTION_FILE: Path = DATA_DIR / "connection.json"  # non-secret repo selection
OPTIONS_FILE: Path = DATA_DIR / "options.json"        # Supervisor-written options
ASKPASS_FILE: Path = DATA_DIR / ".git-askpass.sh"     # GIT_ASKPASS helper

# Supervisor API.
SUPERVISOR_URL: str = os.environ.get("SUPERVISOR_URL", "http://supervisor")
SUPERVISOR_TOKEN: str = os.environ.get("SUPERVISOR_TOKEN", "")

# GitHub API.
GITHUB_API_URL: str = os.environ.get("GITHUB_API_URL", "https://api.github.com")
GITHUB_BASE_URL: str = os.environ.get("GITHUB_BASE_URL", "https://github.com")

ADDON_VERSION: str = os.environ.get("HA_GITOPS_VERSION", __version__)
INGRESS_PORT: int = 8099

# A label applied to issues the add-on opens, used for de-duplication.
ISSUE_LABEL: str = "ha-gitops"
