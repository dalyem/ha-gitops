"""Server-rendered web UI (served through Home Assistant Ingress)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..core import conflicts
from ..core.engine import Engine

router = APIRouter()


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _render(request: Request, name: str, **extra) -> HTMLResponse:
    templates = request.app.state.templates
    context = {"base_path": request.scope.get("root_path", ""), **extra}
    return templates.TemplateResponse(request, name, context)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    engine = _engine(request)
    status = await engine.get_status()
    return _render(request, "dashboard.html", status=status, page="dashboard")


@router.get("/setup", response_class=HTMLResponse)
async def setup(request: Request):
    engine = _engine(request)
    status = await engine.get_status()
    return _render(request, "setup.html", status=status, page="setup")


@router.get("/readiness", response_class=HTMLResponse)
async def readiness(request: Request):
    engine = _engine(request)
    report = engine.state.latest_readiness()
    return _render(request, "readiness.html", report=report, page="readiness")


@router.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    engine = _engine(request)
    deployments = engine.state.list_deployments(limit=100)
    return _render(request, "history.html", deployments=deployments, page="history")


@router.get("/changes", response_class=HTMLResponse)
async def changes(request: Request):
    engine = _engine(request)
    status = await engine.get_status()
    return _render(request, "changes.html", status=status, page="changes")


@router.get("/conflict", response_class=HTMLResponse)
async def conflict(request: Request):
    engine = _engine(request)
    return _render(
        request,
        "conflict.html",
        conflict=engine.state.get_open_conflict(),
        options=conflicts.RESOLUTION_OPTIONS,
        history=engine.state.list_conflicts(limit=25),
        page="conflict",
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    engine = _engine(request)
    versions = await engine.versions()
    return _render(
        request,
        "settings.html",
        options=engine.options.to_dict(),
        monitoring=engine.state.monitoring_enabled,
        versions=versions,
        events=engine.state.recent_events(50),
        page="settings",
    )
