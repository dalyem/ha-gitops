"""User-facing add-on options (Supervisor writes ``/data/options.json``)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .. import settings

DEFAULTS: dict = {
    "log_level": "info",
    "check_interval": "5m",
    "auto_deploy": True,
    "notify_on_success": False,
    "notify_on_failure": True,
    "notify_service": "",
    "create_github_issues_on_failure": False,
    "backup_before_deploy": True,
}

_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "": 60}

MIN_INTERVAL = 30          # guard against hammering the API
MAX_INTERVAL = 24 * 3600


def parse_interval(value: str, default: int = 300) -> int:
    """Parse ``1m`` / ``90s`` / ``2h`` / ``30`` (bare = minutes) to seconds."""
    if value is None:
        return default
    match = _INTERVAL_RE.match(str(value))
    if not match:
        return default
    amount = int(match.group(1))
    unit = match.group(2).lower()
    seconds = amount * _UNIT_SECONDS[unit]
    return max(MIN_INTERVAL, min(MAX_INTERVAL, seconds))


@dataclass(slots=True)
class Options:
    log_level: str = "info"
    check_interval: str = "5m"
    auto_deploy: bool = True
    notify_on_success: bool = False
    notify_on_failure: bool = True
    notify_service: str = ""
    create_github_issues_on_failure: bool = False
    backup_before_deploy: bool = True

    @property
    def interval_seconds(self) -> int:
        return parse_interval(self.check_interval)

    @classmethod
    def load(cls) -> Options:
        data = dict(DEFAULTS)
        try:
            raw = json.loads(settings.OPTIONS_FILE.read_text())
            if isinstance(raw, dict):
                data.update({k: raw[k] for k in DEFAULTS if k in raw})
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return cls(**data)

    def to_dict(self) -> dict:
        return {
            "log_level": self.log_level,
            "check_interval": self.check_interval,
            "interval_seconds": self.interval_seconds,
            "auto_deploy": self.auto_deploy,
            "notify_on_success": self.notify_on_success,
            "notify_on_failure": self.notify_on_failure,
            "notify_service": self.notify_service,
            "create_github_issues_on_failure": self.create_github_issues_on_failure,
            "backup_before_deploy": self.backup_before_deploy,
        }
