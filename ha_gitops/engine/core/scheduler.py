"""Background poll loop.

Cheap conditional poll of the branch head; when the remote moves and there is no
conflict, auto-deploy (if enabled). Conflicts and local-only drift raise a one-shot
notification rather than acting automatically.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..models import DeployStatus, SyncState
from .state import utcnow

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger("ha_gitops.scheduler")


class Scheduler:
    def __init__(self, engine: Engine) -> None:
        self.e = engine
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self._loop(), name="ha-gitops-poller")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def trigger(self) -> None:
        """Ask the loop to poll now (e.g. after connecting or changing settings)."""
        self._wake.set()

    async def _loop(self) -> None:
        log.info("poll loop started")
        while not self._stopping:
            interval = self.e.options.interval_seconds
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            self._wake.clear()
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                self.e.last_error = str(exc)
                log.warning("poll tick failed: %s", exc)

    async def _tick(self) -> None:
        e = self.e
        if not (e.connected and e.state.monitoring_enabled):
            return

        await e.check_remote()
        e.last_poll_ts = utcnow()

        state, changes = await e.compute_sync_state()
        prev = e.state.get("last_sync_state")
        e.state.set("last_sync_state", state.value)

        if state is SyncState.CONFLICT:
            e.state.record_conflict(
                e.state.sync_base_sha, e.state.last_remote_sha,
                f"{changes.count} local file(s) changed",
            )
            if prev != SyncState.CONFLICT.value:
                await e.notifier.conflict(
                    e.state.sync_base_sha or "", e.state.last_remote_sha or "", changes.count
                )
            return

        if state is SyncState.REMOTE_CHANGES and e.options.auto_deploy:
            try:
                result = await e.deploy_now()
                if result.status is DeployStatus.SUCCESS:
                    log.info("auto-deployed %s", (result.sha or "")[:8])
            except RuntimeError as exc:  # engine busy with a manual op
                log.info("skipping auto-deploy: %s", exc)
            return

        if state is SyncState.LOCAL_CHANGES and prev != SyncState.LOCAL_CHANGES.value:
            await e.notifier._persistent(
                "HA-GitOps: local changes detected",
                f"{changes.count} file(s) differ from GitHub. Review and push from the GitOps panel.",
                "ha_gitops_local",
            )
