"""Client for the Supervisor API and the proxied Home Assistant Core API.

Supervisor's own endpoints (``/info``, ``/core/restart``, ``/backups/...``) return a
``{"result": ..., "data": ...}`` envelope. The ``/core/api/...`` proxy returns the raw
Core API response.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .. import settings
from ..models import ValidationResult

log = logging.getLogger("ha_gitops.supervisor")

# This add-on's slug, so we can include our own data in pre-deploy backups.
ADDON_SLUG = "ha_gitops"


class SupervisorError(RuntimeError):
    pass


class SupervisorClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
    ) -> None:
        self._base = (base_url or settings.SUPERVISOR_URL).rstrip("/")
        self._token = token if token is not None else settings.SUPERVISOR_TOKEN
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return bool(self._token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _supervisor(self, method: str, path: str, *, timeout: float = 30.0, **kw) -> Any:
        resp = await self._http().request(method, path, timeout=timeout, **kw)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise SupervisorError(f"Unexpected Supervisor response: {type(body).__name__}")
        if body.get("result") == "error":
            raise SupervisorError(body.get("message", "supervisor error"))
        return body.get("data")

    async def _core_api(self, method: str, path: str, *, timeout: float = 60.0, **kw) -> httpx.Response:
        resp = await self._http().request(method, f"/core/api{path}", timeout=timeout, **kw)
        return resp

    # ---- versions / info ----------------------------------------------------
    async def info(self) -> dict:
        return await self._supervisor("GET", "/info")

    async def core_info(self) -> dict:
        return await self._supervisor("GET", "/core/info")

    async def ha_version(self) -> str:
        try:
            return (await self.core_info()).get("version", "unknown")
        except Exception:  # noqa: BLE001
            return "unknown"

    async def supervisor_version(self) -> str:
        try:
            return (await self.info()).get("supervisor", "unknown")
        except Exception:  # noqa: BLE001
            return "unknown"

    # ---- validation ---------------------------------------------------------
    async def check_config(self) -> ValidationResult:
        """Run Home Assistant's authoritative configuration check (no restart)."""
        resp = await self._core_api(
            "POST", "/config/core/check_config", timeout=180.0
        )
        if resp.status_code >= 400:
            return ValidationResult(
                ok=False,
                errors=[f"check_config HTTP {resp.status_code}: {resp.text[:500]}"],
            )
        data = resp.json()
        if data.get("result") == "valid":
            return ValidationResult(ok=True)
        errs = data.get("errors")
        if isinstance(errs, list):
            errors = [str(e) for e in errs] or ["invalid configuration"]
        else:
            errors = [errs or "invalid configuration"]
        return ValidationResult(ok=False, errors=errors)

    # ---- restart ------------------------------------------------------------
    async def restart_core(self) -> None:
        await self._supervisor("POST", "/core/restart", timeout=120.0)

    async def wait_for_core(self, timeout: float = 300.0, interval: float = 5.0) -> bool:
        """Poll the Core API until it answers, or time out."""
        deadline = asyncio.get_event_loop().time() + timeout
        # Give Core a moment to actually go down first.
        await asyncio.sleep(interval)
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await self._core_api("GET", "/", timeout=10.0)
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(interval)
        return False

    # ---- backups ------------------------------------------------------------
    async def create_partial_backup(self, name: str) -> str | None:
        payload = {
            "name": name[:100],
            "homeassistant": True,
            "addons": [ADDON_SLUG],
            "compressed": True,
        }
        data = await self._supervisor(
            "POST", "/backups/new/partial", json=payload, timeout=600.0
        )
        return data.get("slug") if isinstance(data, dict) else None

    # ---- notifications / services ------------------------------------------
    async def persistent_notification(
        self, title: str, message: str, notification_id: str
    ) -> None:
        resp = await self._core_api(
            "POST",
            "/services/persistent_notification/create",
            json={"title": title, "message": message, "notification_id": notification_id},
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise SupervisorError(
                f"persistent_notification failed: HTTP {resp.status_code}"
            )

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        resp = await self._core_api(
            "POST", f"/services/{domain}/{service}", json=data, timeout=30.0
        )
        if resp.status_code >= 400:
            raise SupervisorError(
                f"service {domain}.{service} failed: HTTP {resp.status_code}"
            )
