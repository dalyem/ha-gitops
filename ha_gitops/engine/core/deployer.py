"""The GitHub → Home Assistant deploy pipeline.

Assumes the engine mutex is already held. Order:
fetch → gate (conflict/up-to-date) → readiness → Layer-1 validate (staging) →
backup → snapshot+apply → Layer-2 check_config → restart-or-revert → record.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .. import settings
from ..models import DeployResult, DeployStatus, Direction, Severity, SyncState
from . import conflicts, filesync, issues, validator

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger("ha_gitops.deployer")


class Deployer:
    def __init__(self, engine: Engine) -> None:
        self.e = engine

    def _config_dir(self, root: Path, config_path: str) -> Path:
        cp = config_path.strip("/")
        return root / cp if cp else root

    async def run(self, target_sha: str | None = None, reason: str = "poll") -> DeployResult:
        e = self.e
        conn = e.require_connection()
        tok = e.require_token()
        result = DeployResult(
            status=DeployStatus.IN_PROGRESS, branch=conn.branch, direction=Direction.PULL
        )

        await e.git.fetch(conn.branch, tok)
        remote = await e.git.remote_head(conn.branch)
        if remote is None:
            result.status = DeployStatus.SKIPPED
            result.message = "Remote branch is empty; nothing to deploy."
            return result

        target = target_sha or remote
        result.sha = target
        deployed = e.state.last_deployed_sha
        base = e.state.sync_base_sha

        # --- gating ----------------------------------------------------------
        changes = await e.detect_changes()
        state = conflicts.evaluate(base, remote, changes.dirty, repo_empty=False)
        if state is SyncState.CONFLICT:
            e.state.record_conflict(base, remote, conflicts.summarize_local(changes))
            await e.notifier.conflict(base or "", remote, changes.count)
            result.status = DeployStatus.SKIPPED
            result.message = (
                "Conflict: GitHub and local configuration both changed since the last "
                "sync. Resolve it before deploying."
            )
            e.state.log_event("warning", "deploy", result.message)
            return result
        if changes.dirty:
            result.status = DeployStatus.SKIPPED
            result.message = (
                "You have local changes and there are no new commits to pull. "
                "Push your changes or resolve them first."
            )
            return result
        if target == deployed and target_sha is None:
            result.status = DeployStatus.SKIPPED
            result.message = "Already up to date."
            return result

        # --- readiness gate --------------------------------------------------
        report, new_map = await e.readiness_for(target)
        e.state.record_readiness(report.to_dict())
        if report.has_blockers:
            blockers = [f.title for f in report.findings if f.severity is Severity.BLOCKER]
            result.status = DeployStatus.VALIDATION_FAILED
            result.errors = blockers
            result.message = "Repository readiness blockers must be resolved before deploying."
            await e.notifier.readiness_failure(report)
            e.state.log_event("error", "deploy", result.message)
            return result

        deployment_id = e.state.record_deployment(
            Direction.PULL.value, target, conn.branch, DeployStatus.IN_PROGRESS.value
        )
        result.deployment_id = deployment_id
        snapshot_dir = settings.SNAPSHOT_DIR / str(deployment_id)
        applied = False

        try:
            # --- Layer-1 validation in an isolated staging worktree ----------
            await e.git.worktree_add(settings.STAGING_DIR, target)
            staging_cfg = self._config_dir(settings.STAGING_DIR, conn.config_path)
            secrets_path = settings.HA_CONFIG_DIR / "secrets.yaml"
            vr = await asyncio.to_thread(
                validator.validate_config_dir, staging_cfg, secrets_path
            )
            if not vr.ok:
                return await self._fail_validation(result, deployment_id, target, vr.errors)

            # --- pre-deploy backup ------------------------------------------
            if e.options.backup_before_deploy and e.supervisor.available:
                try:
                    result.backup_slug = await e.supervisor.create_partial_backup(
                        f"HA-GitOps pre-deploy {target[:8]}"
                    )
                except Exception as exc:  # noqa: BLE001 - don't block on backup failure
                    log.warning("pre-deploy backup failed: %s", exc)
                    e.state.log_event("warning", "backup", f"pre-deploy backup failed: {exc}")

            # --- compute + apply --------------------------------------------
            old_map = (
                await e.git.ls_tree(deployed, conn.config_path) if deployed else {}
            )
            change = filesync.compute_change_set(old_map, new_map)
            result.files_changed = change.count
            await e.git.reset_to(conn.branch, target)
            source_dir = self._config_dir(settings.REPO_DIR, conn.config_path)
            await asyncio.to_thread(
                filesync.apply_change_set, change, source_dir, settings.HA_CONFIG_DIR, snapshot_dir
            )
            applied = True

            # --- Layer-2 authoritative validation ---------------------------
            if e.supervisor.available:
                vr2 = await e.supervisor.check_config()
                if not vr2.ok:
                    await asyncio.to_thread(
                        filesync.restore_snapshot, snapshot_dir, settings.HA_CONFIG_DIR
                    )
                    applied = False
                    return await self._fail_validation(
                        result, deployment_id, target, vr2.errors
                    )

            # --- restart Core (never the host) ------------------------------
            if e.supervisor.available:
                await e.supervisor.restart_core()
                result.restarted = await e.supervisor.wait_for_core()
                if not result.restarted:
                    e.state.log_event(
                        "warning", "deploy",
                        "Core did not report healthy within the timeout after restart.",
                    )

            # --- record success ---------------------------------------------
            e.state.last_deployed_sha = target
            e.state.sync_base_sha = target
            manifest = await asyncio.to_thread(
                filesync.build_manifest, list(new_map.keys()), settings.HA_CONFIG_DIR
            )
            e.state.set_manifest(manifest)
            e.state.clear_open_conflicts("deployed")
            result.status = DeployStatus.SUCCESS
            result.message = f"Deployed {target[:8]} ({change.count} file(s))."
            e.state.finish_deployment(
                deployment_id,
                status=DeployStatus.SUCCESS.value,
                files_changed=change.count,
                backup_slug=result.backup_slug,
                restarted=result.restarted,
                message=result.message,
            )
            await e.notifier.deploy_success(result)
            e.state.log_event("info", "deploy", result.message)
            return result

        except Exception as exc:  # noqa: BLE001
            if applied:
                await asyncio.to_thread(
                    filesync.restore_snapshot, snapshot_dir, settings.HA_CONFIG_DIR
                )
            result.status = DeployStatus.FAILED
            result.errors = [str(exc)]
            result.message = f"Deploy failed: {exc}"
            e.state.finish_deployment(
                deployment_id, status=DeployStatus.FAILED.value, errors=result.errors,
                message=result.message,
            )
            e.state.log_event("error", "deploy", result.message)
            await e.notifier.deploy_failure(result)
            return result
        finally:
            await e.git.worktree_remove(settings.STAGING_DIR)

    async def _fail_validation(
        self, result: DeployResult, deployment_id: int, sha: str, errors: list[str]
    ) -> DeployResult:
        e = self.e
        result.status = DeployStatus.VALIDATION_FAILED
        result.errors = errors
        result.message = "Configuration validation failed; nothing was deployed."
        e.state.finish_deployment(
            deployment_id,
            status=DeployStatus.VALIDATION_FAILED.value,
            errors=errors,
            message=result.message,
        )
        e.state.log_event("error", "deploy", result.message)
        await e.notifier.deploy_failure(result)
        await self._maybe_open_issue(sha, errors)
        return result

    async def _maybe_open_issue(self, sha: str, errors: list[str]) -> None:
        e = self.e
        if not (e.options.create_github_issues_on_failure and e.github and e.connection):
            return
        versions = await e.versions()
        await issues.report_failure(
            e.github,
            e.connection,
            errors,
            sha,
            e.connection.branch,
            versions.get("addon", settings.ADDON_VERSION),
            versions.get("home_assistant", "unknown"),
        )
