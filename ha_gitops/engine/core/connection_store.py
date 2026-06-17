"""Persistence for the (non-secret) repository selection."""
from __future__ import annotations

import json

from .. import settings
from ..models import Connection


def save_connection(conn: Connection) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.CONNECTION_FILE.write_text(json.dumps(conn.to_dict(), indent=2))


def load_connection() -> Connection | None:
    try:
        data = json.loads(settings.CONNECTION_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    try:
        return Connection.from_dict(data)
    except (KeyError, TypeError):
        return None


def clear_connection() -> None:
    try:
        settings.CONNECTION_FILE.unlink()
    except FileNotFoundError:
        pass
