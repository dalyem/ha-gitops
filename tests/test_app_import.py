"""Smoke test: the FastAPI app builds and serves /healthz. Skipped without FastAPI."""
import pytest

pytest.importorskip("fastapi")


def test_app_builds_and_healthz(monkeypatch, tmp_path):
    monkeypatch.setattr("engine.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("engine.settings.STATE_DB", tmp_path / "state.db")
    monkeypatch.setattr("engine.settings.CONNECTION_FILE", tmp_path / "connection.json")
    monkeypatch.setattr("engine.settings.CREDENTIALS_FILE", tmp_path / ".credentials")
    monkeypatch.setattr("engine.settings.SUPERVISOR_TOKEN", "")

    from engine.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        status = client.get("/api/status").json()
        assert status["connected"] is False
        assert status["sync_state"] == "not_connected"
        # Dashboard renders for a fresh install.
        assert client.get("/").status_code == 200
