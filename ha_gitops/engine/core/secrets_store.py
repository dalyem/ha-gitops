"""On-device GitHub token storage + a global redaction filter.

The token is kept out of ``options.json`` (which ends up in add-on backups) and is
written to ``/data/.credentials`` with ``0600`` permissions. Every token we ever hold
is registered for redaction so it can never leak into logs, diffs, notifications or
GitHub issue bodies.
"""
from __future__ import annotations

import json
import logging
import os
import stat

from .. import settings

_REDACTIONS: set[str] = set()


def _register_redaction(token: str) -> None:
    if token and len(token) >= 8:
        _REDACTIONS.add(token)


def redact(text: str) -> str:
    """Replace any known token with ``***`` in arbitrary text."""
    if not text:
        return text
    for token in _REDACTIONS:
        if token in text:
            text = text.replace(token, "***redacted***")
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs known tokens from formatted messages."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if record.args:
                record.msg = record.getMessage()
                record.args = ()
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
        except Exception:  # pragma: no cover - never let logging break the app
            pass
        return True


def save_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("token must not be empty")
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"auth_kind": "pat", "token": token})
    # Write then tighten perms (0600).
    fd = os.open(str(settings.CREDENTIALS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(settings.CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)
    _register_redaction(token)


def load_token() -> str | None:
    try:
        data = json.loads(settings.CREDENTIALS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("token")
    if token:
        _register_redaction(token)
    return token


def has_token() -> bool:
    return load_token() is not None


def clear_token() -> None:
    try:
        settings.CREDENTIALS_FILE.unlink()
    except FileNotFoundError:
        pass
