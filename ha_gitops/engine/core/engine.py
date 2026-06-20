"""The AppContext: constructs and wires every component, owns the engine mutex,
and exposes the high-level operations used by the API and the scheduler.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from .. import settings
from ..models import (
    Connection,
    DeployResult,
    LocalChanges,
    ReadinessReport,
    SyncState,
)
from . import conflicts, connection_store, readiness, secrets_store
from .deployer import Deployer
from .git_engine import GitEngine
from .github_client import GitHubClient
from .notifier import Notifier
from .options import Options
from .scheduler import Scheduler
from .state import State
from .supervisor import SupervisorClient
from .sync import SyncEngine

log = logging.getLogger("ha_gitops.engine")


def _join(config_path: str, name: str) -> str:
    cp = config_path.strip("/")
    return f"{cp}/{name}" if cp else name


class Engine:
    def __init__(self) -> None:
        self.state = State()
        self.options = Options.load()
        self.supervisor = SupervisorClient()
        self.notifier = Notifier(self.supervisor, self.options)
        self.git = GitEngine()
        self.connection: Connection | None = connection_store.load_connection()
        self._token: str | None = secrets_store.load_token()
        self.github: GitHubClient | None = (
            GitHubClient(self._token) if self._token else None
        )

        self.lock = asyncio.Lock()
        self.busy: str | None = None
        self.last_poll_ts: str | None = None
        self.last_error: str | None = None
        self._versions: dict | None = None

        self.deployer = Deployer(self)
        self.sync = SyncEngine(self)
        self.scheduler = Scheduler(self)

    # ---- lifecycle ----------------------------------------------------------
    async def aclose(self) -> None:
        await self.scheduler.stop()
        await self.supervisor.aclose()
        if self.github:
            await self.github.aclose()
        self.state.close()

    def reload_options(self) -> None:
        self.options = Options.load()
        self.notifier.options = self.options

    # ---- credentials / connection ------------------------------------------
    @property
    def connected(self) -> bool:
        return self.connection is not None and self._token is not None

    def require_token(self) -> str:
        if not self._token:
            raise RuntimeError("No GitHub token configured.")
        return self._token

    def require_connection(self) -> Connection:
        if not self.connection:
            raise RuntimeError("No repository connected.")
        return self.connection

    def require_github(self) -> GitHubClient:
        if not self.github:
            raise RuntimeError("No GitHub token configured.")
        return self.github

    async def set_token(self, token: str) -> None:
        secrets_store.save_token(token)
        if self.github is not None:
            await self.github.aclose()
        self._token = token
        self.github = GitHubClient(token)

    async def authenticate(self, token: str) -> dict:
        """Verify a token *before* persisting it, then store and swap clients."""
        probe = GitHubClient(token)
        try:
            user = await probe.verify_token()
        finally:
            await probe.aclose()
        await self.set_token(token)
        return user

    @staticmethod
    def _etag_key(conn: Connection) -> str:
        # Scope by repo + branch so switching repos can't reuse a stale ETag.
        return f"etag:{conn.full_name}:{conn.branch}"

    async def list_repos(self) -> list[dict]:
        return await self.require_github().list_repos()

    async def list_branches(self, owner: str, repo: str) -> list[str]:
        return await self.require_github().list_branches(owner, repo)

    @contextlib.asynccontextmanager
    async def _operation(self, label: str):
        if self.lock.locked():
            raise RuntimeError(f"Busy: {self.busy or 'another operation in progress'}")
        async with self.lock:
            self.busy = label
            try:
                yield
            finally:
                self.busy = None

    async def connect(
        self, owner: str, repo: str, branch: str, config_path: str, token: str | None
    ) -> ReadinessReport:
        async with self._operation("Connecting repository"):
            if token:
                await self.authenticate(token)  # validates before persisting
            gh = self.require_github()
            await gh.get_repo(owner, repo)  # raises if no access
            conn = Connection(owner=owner, repo=repo, branch=branch, config_path=config_path)
            tok = self.require_token()
            # Bootstrap the clone BEFORE persisting the connection/state, so a
            # failure here leaves the previous connection intact.
            await self.git.ensure_repo(conn.clone_url, tok, branch)
            await self.git.fetch(branch, tok)
            connection_store.save_connection(conn)
            self.connection = conn
            self.state.reset_sync_state()
            self.state.set(self._etag_key(conn), None)
            self.state.last_remote_sha = await self.git.remote_head(branch)
            return await self._run_readiness_locked()

    # ---- readiness ----------------------------------------------------------
    @staticmethod
    def _has_storage_dashboards() -> bool:
        """True if the live instance has UI/storage-mode Lovelace dashboards."""
        storage = settings.HA_CONFIG_DIR / ".storage"
        try:
            return storage.is_dir() and any(storage.glob("lovelace*"))
        except OSError:
            return False

    async def readiness_for(self, sha: str) -> tuple[ReadinessReport, dict[str, str]]:
        conn = self.require_connection()
        tracked = await self.git.ls_tree(sha, conn.config_path)
        gi_bytes = await self.git.show_bytes(sha, _join(conn.config_path, ".gitignore"))
        gi_text = gi_bytes.decode("utf-8", "replace") if gi_bytes else None
        report = readiness.analyze(
            list(tracked.keys()), gi_text, is_empty=False,
            storage_dashboards=self._has_storage_dashboards(),
        )
        return report, tracked

    async def _run_readiness_locked(self) -> ReadinessReport:
        conn = self.require_connection()
        remote = await self.git.remote_head(conn.branch)
        if remote is None:
            report = readiness.analyze([], None, is_empty=True)
        else:
            report, _ = await self.readiness_for(remote)
        self.state.record_readiness(report.to_dict())
        if report.has_blockers:
            await self.notifier.readiness_failure(report)
        return report

    async def run_readiness(self) -> ReadinessReport:
        async with self._operation("Analyzing repository"):
            tok = self.require_token()
            conn = self.require_connection()
            await self.git.fetch(conn.branch, tok)
            self.state.last_remote_sha = await self.git.remote_head(conn.branch)
            return await self._run_readiness_locked()

    # ---- changes / state ----------------------------------------------------
    async def detect_changes(self) -> LocalChanges:
        manifest = self.state.get_manifest()
        if not manifest:
            return LocalChanges()
        gi_path = settings.HA_CONFIG_DIR / ".gitignore"
        gi_text = gi_path.read_text(encoding="utf-8") if gi_path.is_file() else None
        from . import filesync

        return await asyncio.to_thread(
            filesync.detect_local_changes, manifest, settings.HA_CONFIG_DIR, gi_text
        )

    async def compute_sync_state(self) -> tuple[SyncState, LocalChanges]:
        if not self.connected:
            return SyncState.NOT_CONNECTED, LocalChanges()
        changes = await self.detect_changes()
        remote_empty = self.state.last_remote_sha is None
        state = conflicts.evaluate(
            base_sha=self.state.sync_base_sha,
            remote_sha=self.state.last_remote_sha,
            local_dirty=changes.dirty,
            repo_empty=remote_empty,
        )
        return state, changes

    async def check_remote(self) -> tuple[str | None, bool]:
        """Cheap poll of the branch head using a stored ETag."""
        conn = self.require_connection()
        gh = self.require_github()
        etag = self.state.get(self._etag_key(conn))
        head = await gh.get_branch_head(conn.owner, conn.repo, conn.branch, etag)
        if head.not_modified:
            return self.state.last_remote_sha, False
        if head.etag:
            self.state.set(self._etag_key(conn), head.etag)
        changed = head.sha != self.state.last_remote_sha
        self.state.last_remote_sha = head.sha
        return head.sha, changed

    # ---- high-level operations (lock-wrapped) -------------------------------
    async def deploy_now(self, target_sha: str | None = None) -> DeployResult:
        async with self._operation("Deploying"):
            return await self.deployer.run(target_sha=target_sha, reason="manual")

    async def push_local(self, message: str) -> DeployResult:
        async with self._operation("Pushing local changes"):
            return await self.sync.push(message)

    async def initialize_repo(self) -> DeployResult:
        async with self._operation("Initializing repository"):
            return await self.sync.initialize()

    async def preview_dashboard_conversion(self) -> dict:
        from . import lovelace
        conv = await asyncio.to_thread(lovelace.build, settings.HA_CONFIG_DIR)
        return conv.to_dict()

    async def convert_dashboards(self) -> dict:
        async with self._operation("Converting dashboards"):
            from . import lovelace
            conv = await asyncio.to_thread(lovelace.build, settings.HA_CONFIG_DIR)
            if conv.empty:
                return {
                    "converted": 0, "files": [], "warnings": conv.warnings,
                    "message": "No storage-mode dashboards found to convert.",
                }
            backup = None
            if self.options.backup_before_deploy and self.supervisor.available:
                try:
                    backup = await self.supervisor.create_partial_backup(
                        "HA-GitOps pre-dashboard-convert"
                    )
                except Exception as exc:  # noqa: BLE001
                    self.state.log_event("warning", "convert", f"pre-convert backup failed: {exc}")
            written = await asyncio.to_thread(lovelace.apply, conv, settings.HA_CONFIG_DIR)
            n = len(conv.dashboards) + (1 if conv.has_default else 0)
            msg = (
                f"Converted {n} dashboard(s) to YAML ({len(written)} file(s)). "
                "Review on Changes → Push, then restart Home Assistant to activate YAML mode."
            )
            self.state.log_event("info", "convert", msg)
            return {
                "converted": n, "files": written, "dashboards": conv.dashboards,
                "resources": conv.resources, "warnings": conv.warnings,
                "backup_slug": backup, "message": msg,
            }

    # ---- status -------------------------------------------------------------
    async def versions(self) -> dict:
        if self._versions is None and self.supervisor.available:
            self._versions = {
                "home_assistant": await self.supervisor.ha_version(),
                "supervisor": await self.supervisor.supervisor_version(),
                "addon": settings.ADDON_VERSION,
            }
        return self._versions or {"addon": settings.ADDON_VERSION}

    async def get_status(self) -> dict:
        connected = self.connected
        sync_state = SyncState.NOT_CONNECTED
        changes = LocalChanges()
        readiness_report = self.state.latest_readiness()
        if connected:
            try:
                sync_state, changes = await self.compute_sync_state()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                sync_state = SyncState.BLOCKED  # connected but state is unknown
            if readiness_report and not readiness_report.get("deployable", True):
                sync_state = SyncState.BLOCKED

        deployments = self.state.list_deployments(limit=1)
        return {
            "connected": connected,
            "has_token": self._token is not None,
            "connection": self.connection.to_dict() if self.connection else None,
            "sync_state": sync_state.value,
            "local_changes": changes.to_dict(),
            "last_deployed_sha": self.state.last_deployed_sha,
            "last_remote_sha": self.state.last_remote_sha,
            "sync_base_sha": self.state.sync_base_sha,
            "monitoring_enabled": self.state.monitoring_enabled,
            "interval_seconds": self.options.interval_seconds,
            "auto_deploy": self.options.auto_deploy,
            "busy": self.busy,
            "last_poll_ts": self.last_poll_ts,
            "last_error": self.last_error,
            "last_deployment": deployments[0] if deployments else None,
            "readiness": readiness_report,
            "open_conflict": self.state.get_open_conflict(),
            "options": self.options.to_dict(),
        }
