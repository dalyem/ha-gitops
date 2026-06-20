"""JSON API used by the web UI (and, later, the HACS companion integration)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..core import conflicts
from ..core.engine import Engine
from ..core.git_engine import GitError
from ..core.github_client import GitHubError

log = logging.getLogger("ha_gitops.api")
router = APIRouter(prefix="/api")


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


class TokenIn(BaseModel):
    token: str


class ConnectIn(BaseModel):
    owner: str
    repo: str
    branch: str
    config_path: str = ""
    token: str | None = None


class PushIn(BaseModel):
    message: str = "Sync Home Assistant local configuration changes"


class DeployIn(BaseModel):
    target_sha: str | None = None


class MonitoringIn(BaseModel):
    enabled: bool


class ResolveIn(BaseModel):
    resolution: str
    message: str = "Resolve conflict: keep local configuration"


def _fail(exc: Exception) -> HTTPException:
    msg = str(exc)
    if isinstance(exc, GitHubError):
        return HTTPException(status_code=exc.status if exc.status is not None else 400, detail=msg)
    if msg.startswith("Busy"):
        return HTTPException(status_code=409, detail=msg)
    if isinstance(exc, GitError):
        # Failure talking to git/GitHub (message is already token-redacted).
        log.exception("git operation failed")
        return HTTPException(status_code=502, detail=msg)
    if isinstance(exc, (ValueError, RuntimeError)):
        # Deliberate precondition/validation errors (not connected, no token, …).
        return HTTPException(status_code=400, detail=msg)
    log.exception("unhandled API error")
    return HTTPException(status_code=500, detail="Internal server error")


@router.get("/status")
async def status(request: Request):
    return await get_engine(request).get_status()


@router.post("/token")
async def set_token(request: Request, body: TokenIn):
    engine = get_engine(request)
    try:
        user = await engine.authenticate(body.token)
        return {"ok": True, "login": user.get("login")}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.get("/repos")
async def repos(request: Request):
    try:
        return {"repos": await get_engine(request).list_repos()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.get("/branches")
async def branches(request: Request, owner: str = Query(...), repo: str = Query(...)):
    try:
        return {"branches": await get_engine(request).list_branches(owner, repo)}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/connect")
async def connect(request: Request, body: ConnectIn):
    try:
        report = await get_engine(request).connect(
            body.owner, body.repo, body.branch, body.config_path, body.token
        )
        return report.to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.get("/readiness")
async def readiness(request: Request):
    report = get_engine(request).state.latest_readiness()
    if report is None:
        raise HTTPException(status_code=404, detail="No readiness report yet.")
    return report


@router.post("/readiness/refresh")
async def readiness_refresh(request: Request):
    try:
        return (await get_engine(request).run_readiness()).to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/initialize")
async def initialize(request: Request):
    try:
        return (await get_engine(request).initialize_repo()).to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.get("/dashboards/preview")
async def dashboards_preview(request: Request):
    try:
        return await get_engine(request).preview_dashboard_conversion()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/dashboards/convert")
async def dashboards_convert(request: Request):
    try:
        return await get_engine(request).convert_dashboards()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/deploy")
async def deploy(request: Request, body: DeployIn | None = None):
    try:
        result = await get_engine(request).deploy_now(body.target_sha if body else None)
        return result.to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.get("/local-changes")
async def local_changes(request: Request):
    try:
        return (await get_engine(request).detect_changes()).to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.post("/push")
async def push(request: Request, body: PushIn):
    try:
        return (await get_engine(request).push_local(body.message)).to_dict()
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc


@router.get("/history")
async def history(
    request: Request,
    direction: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    return {"deployments": get_engine(request).state.list_deployments(limit, direction)}


@router.get("/conflict")
async def conflict(request: Request):
    engine = get_engine(request)
    return {
        "conflict": engine.state.get_open_conflict(),
        "options": conflicts.RESOLUTION_OPTIONS,
        "history": engine.state.list_conflicts(limit=25),
    }


@router.post("/conflict/resolve")
async def resolve(request: Request, body: ResolveIn):
    engine = get_engine(request)
    open_conflict = engine.state.get_open_conflict()
    if not open_conflict:
        raise HTTPException(status_code=404, detail="No open conflict.")
    if body.resolution == "push":
        try:
            result = await engine.push_local(body.message)
            return {"resolved": result.status.value == "success", "result": result.to_dict()}
        except Exception as exc:  # noqa: BLE001
            raise _fail(exc) from exc
    # pull / branch / manual: record the choice; execution lands in a later phase.
    if body.resolution not in {"pull", "branch", "manual"}:
        raise HTTPException(status_code=400, detail="Invalid resolution.")
    engine.state.resolve_conflict(int(open_conflict["id"]), body.resolution)
    return {
        "resolved": True,
        "note": "Recorded. Automatic pull/branch resolution is a future enhancement; "
        "for now resolve via git and press Deploy Now once local is clean.",
    }


@router.get("/logs")
async def logs(request: Request, limit: int = Query(100, ge=1, le=1000)):
    return {"events": get_engine(request).state.recent_events(limit)}


@router.post("/monitoring")
async def monitoring(request: Request, body: MonitoringIn):
    engine = get_engine(request)
    engine.state.monitoring_enabled = body.enabled
    if body.enabled:
        engine.scheduler.trigger()
    return {"monitoring_enabled": engine.state.monitoring_enabled}


@router.post("/settings/reload")
async def reload_settings(request: Request):
    engine = get_engine(request)
    engine.reload_options()
    engine.scheduler.trigger()
    return engine.options.to_dict()
