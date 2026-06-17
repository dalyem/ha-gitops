"""Persistent + mobile notifications via the Home Assistant Core API."""
from __future__ import annotations

import logging

from ..models import DeployResult, ReadinessReport
from . import secrets_store
from .options import Options
from .supervisor import SupervisorClient

log = logging.getLogger("ha_gitops.notifier")

NID_DEPLOY = "ha_gitops_deploy"
NID_CONFLICT = "ha_gitops_conflict"
NID_READINESS = "ha_gitops_readiness"


class Notifier:
    def __init__(self, supervisor: SupervisorClient, options: Options) -> None:
        self.supervisor = supervisor
        self.options = options

    async def _persistent(self, title: str, message: str, nid: str) -> None:
        try:
            await self.supervisor.persistent_notification(
                title, secrets_store.redact(message), nid
            )
        except Exception as exc:  # noqa: BLE001 - notifications are best-effort
            log.warning("persistent notification failed: %s", exc)

    async def _mobile(self, title: str, message: str) -> None:
        service = (self.options.notify_service or "").strip()
        if not service or "." not in service:
            return
        domain, _, name = service.partition(".")
        try:
            await self.supervisor.call_service(
                domain, name, {"title": title, "message": secrets_store.redact(message)}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("mobile notification failed: %s", exc)

    async def deploy_success(self, result: DeployResult) -> None:
        if not self.options.notify_on_success:
            return
        title = "HA-GitOps: deploy succeeded"
        msg = (
            f"Deployed `{(result.sha or '')[:8]}` on `{result.branch}` "
            f"({result.files_changed} file(s) changed)."
        )
        await self._persistent(title, msg, NID_DEPLOY)
        await self._mobile(title, msg)

    async def deploy_failure(self, result: DeployResult) -> None:
        if not self.options.notify_on_failure:
            return
        title = "HA-GitOps: deploy FAILED"
        errors = "\n".join(f"- {e}" for e in result.errors[:10]) or "Unknown error."
        msg = (
            f"**Commit:** `{(result.sha or 'n/a')[:12]}`\n"
            f"**Branch:** `{result.branch}`\n"
            f"**Status:** {result.status.value}\n\n"
            f"**Errors:**\n{errors}\n\n"
            "Home Assistant was **not** restarted and the previous configuration is intact."
        )
        await self._persistent(title, msg, NID_DEPLOY)
        await self._mobile(title, f"Deploy failed for {(result.sha or '')[:8]}. HA untouched.")

    async def conflict(self, base_sha: str, remote_sha: str, local_count: int) -> None:
        if not self.options.notify_on_failure:
            return
        title = "HA-GitOps: conflict detected"
        msg = (
            "Both GitHub and your local configuration changed since the last sync.\n\n"
            f"**Base:** `{(base_sha or 'n/a')[:8]}`  **Remote:** `{(remote_sha or 'n/a')[:8]}`  "
            f"**Local changes:** {local_count} file(s)\n\n"
            "Auto-deploy is **blocked**. Resolve it from the GitOps panel."
        )
        await self._persistent(title, msg, NID_CONFLICT)
        await self._mobile(title, "Config conflict — auto-deploy blocked.")

    async def readiness_failure(self, report: ReadinessReport) -> None:
        if not self.options.notify_on_failure:
            return
        blockers = [f for f in report.findings if f.severity.value == "blocker"]
        title = "HA-GitOps: repository not ready"
        msg = "Deployment is blocked until these are resolved:\n" + "\n".join(
            f"- {f.title}" for f in blockers[:10]
        )
        await self._persistent(title, msg, NID_READINESS)
