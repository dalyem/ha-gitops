"""SQLite-backed state: pointers, deployment/sync history, conflicts, events.

A single connection guarded by a lock — the workload is tiny and local, so this is
both simpler and safe across the FastAPI thread-pool and the asyncio scheduler.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS deployments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    finished_ts   TEXT,
    direction     TEXT NOT NULL,
    sha           TEXT,
    branch        TEXT,
    status        TEXT NOT NULL,
    files_changed INTEGER DEFAULT 0,
    errors        TEXT,
    backup_slug   TEXT,
    restarted     INTEGER DEFAULT 0,
    message       TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    base_sha      TEXT,
    remote_sha    TEXT,
    local_summary TEXT,
    status        TEXT NOT NULL DEFAULT 'open',
    resolution    TEXT
);
CREATE TABLE IF NOT EXISTS readiness_reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    score        INTEGER,
    is_valid     INTEGER,
    is_empty     INTEGER,
    has_blockers INTEGER,
    report       TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    level    TEXT,
    category TEXT,
    message  TEXT
);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class State:
    def __init__(self, db_path: Path | None = None) -> None:
        self._path = Path(db_path or settings.STATE_DB)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- key/value pointers -------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM kv WHERE key=?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO kv(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            self._conn.commit()

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def set_json(self, key: str, obj: Any) -> None:
        self.set(key, json.dumps(obj))

    # Named pointer convenience.
    @property
    def last_deployed_sha(self) -> str | None:
        return self.get("last_deployed_sha")

    @last_deployed_sha.setter
    def last_deployed_sha(self, sha: str | None) -> None:
        self.set("last_deployed_sha", sha)

    @property
    def last_remote_sha(self) -> str | None:
        return self.get("last_remote_sha")

    @last_remote_sha.setter
    def last_remote_sha(self, sha: str | None) -> None:
        self.set("last_remote_sha", sha)

    @property
    def sync_base_sha(self) -> str | None:
        return self.get("sync_base_sha")

    @sync_base_sha.setter
    def sync_base_sha(self, sha: str | None) -> None:
        self.set("sync_base_sha", sha)

    @property
    def monitoring_enabled(self) -> bool:
        return self.get("monitoring_enabled", "1") == "1"

    @monitoring_enabled.setter
    def monitoring_enabled(self, enabled: bool) -> None:
        self.set("monitoring_enabled", "1" if enabled else "0")

    def get_manifest(self) -> dict[str, str]:
        return self.get_json("manifest", {}) or {}

    def set_manifest(self, manifest: dict[str, str]) -> None:
        self.set_json("manifest", manifest)

    # ---- deployments --------------------------------------------------------
    def record_deployment(
        self, direction: str, sha: str | None, branch: str | None, status: str
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO deployments(ts, direction, sha, branch, status) "
                "VALUES(?,?,?,?,?)",
                (utcnow(), direction, sha, branch, status),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_deployment(self, deployment_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "errors" in fields and isinstance(fields["errors"], (list, dict)):
            fields["errors"] = json.dumps(fields["errors"])
        if "restarted" in fields:
            fields["restarted"] = 1 if fields["restarted"] else 0
        cols = ", ".join(f"{k}=?" for k in fields)
        params = list(fields.values()) + [deployment_id]
        with self._lock:
            self._conn.execute(f"UPDATE deployments SET {cols} WHERE id=?", params)
            self._conn.commit()

    def finish_deployment(self, deployment_id: int, **fields: Any) -> None:
        fields.setdefault("finished_ts", utcnow())
        self.update_deployment(deployment_id, **fields)

    def list_deployments(self, limit: int = 50, direction: str | None = None) -> list[dict]:
        query = "SELECT * FROM deployments"
        params: list[Any] = []
        if direction:
            query += " WHERE direction=?"
            params.append(direction)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._deployment_row(r) for r in rows]

    @staticmethod
    def _deployment_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        if d.get("errors"):
            try:
                d["errors"] = json.loads(d["errors"])
            except (json.JSONDecodeError, TypeError):
                d["errors"] = [d["errors"]]
        else:
            d["errors"] = []
        d["restarted"] = bool(d.get("restarted"))
        return d

    # ---- conflicts ----------------------------------------------------------
    def record_conflict(
        self, base_sha: str | None, remote_sha: str | None, local_summary: str
    ) -> int:
        # Hold the lock across check-and-insert so concurrent callers can't both
        # see "no open conflict" and insert duplicates for the same remote SHA.
        with self._lock:
            row = self._conn.execute(
                "SELECT id, remote_sha FROM conflicts WHERE status='open' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row["remote_sha"] == remote_sha:
                return int(row["id"])
            cur = self._conn.execute(
                "INSERT INTO conflicts(ts, base_sha, remote_sha, local_summary) "
                "VALUES(?,?,?,?)",
                (utcnow(), base_sha, remote_sha, local_summary),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_open_conflict(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conflicts WHERE status='open' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def resolve_conflict(self, conflict_id: int, resolution: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conflicts SET status='resolved', resolution=? WHERE id=?",
                (resolution, conflict_id),
            )
            self._conn.commit()

    def clear_open_conflicts(self, resolution: str = "auto-cleared") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conflicts SET status='resolved', resolution=? WHERE status='open'",
                (resolution,),
            )
            self._conn.commit()

    def list_conflicts(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conflicts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- readiness ----------------------------------------------------------
    def record_readiness(self, report: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO readiness_reports(ts, score, is_valid, is_empty, "
                "has_blockers, report) VALUES(?,?,?,?,?,?)",
                (
                    utcnow(),
                    report.get("score"),
                    1 if report.get("is_valid_repo") else 0,
                    1 if report.get("is_empty") else 0,
                    1 if report.get("has_blockers") else 0,
                    json.dumps(report),
                ),
            )
            self._conn.commit()

    def latest_readiness(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT report FROM readiness_reports ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["report"])
        except (json.JSONDecodeError, TypeError):
            return None

    # ---- events -------------------------------------------------------------
    def log_event(self, level: str, category: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(ts, level, category, message) VALUES(?,?,?,?)",
                (utcnow(), level, category, message),
            )
            self._conn.commit()

    def recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def reset_sync_state(self) -> None:
        """Forget pointers when the connection target changes."""
        for key in ("last_deployed_sha", "last_remote_sha", "sync_base_sha", "manifest"):
            self.set(key, None)
        self.clear_open_conflicts(resolution="connection-changed")
