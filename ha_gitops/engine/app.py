"""FastAPI application: lifespan wiring, Ingress support, routers, UI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .core import secrets_store
from .core.engine import Engine
from .core.options import Options

WEB_DIR = Path(__file__).parent / "web"

_HA_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


def _configure_logging() -> None:
    level = _HA_LEVELS.get(Options.load().log_level, logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(secrets_store.RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "ha_gitops"):
        logging.getLogger(name).addFilter(secrets_store.RedactingFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    log = logging.getLogger("ha_gitops")
    engine = Engine()
    app.state.engine = engine
    try:
        engine.scheduler.start()
        log.info("HA-GitOps started (connected=%s)", engine.connected)
        yield
    finally:
        await engine.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="HA-GitOps", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    @app.middleware("http")
    async def ingress_root_path(request: Request, call_next):
        # Supervisor sets X-Ingress-Path to the base path the browser is using.
        ingress = request.headers.get("X-Ingress-Path")
        if ingress:
            request.scope["root_path"] = ingress.rstrip("/")
        return await call_next(request)

    from .api import routes, web

    app.include_router(routes.router)
    app.include_router(web.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app


app = create_app()
