"""Shared data models and enums."""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Severity(str, enum.Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    RECOMMENDATION = "recommendation"


class DeployStatus(str, enum.Enum):
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    FAILED = "failed"
    SKIPPED = "skipped"
    IN_PROGRESS = "in_progress"


class Direction(str, enum.Enum):
    PULL = "pull"   # GitHub -> Home Assistant
    PUSH = "push"   # Home Assistant -> GitHub
    INIT = "init"   # initial repository population


class SyncState(str, enum.Enum):
    NOT_CONNECTED = "not_connected"
    IN_SYNC = "in_sync"
    REMOTE_CHANGES = "remote_changes"   # remote moved, local clean
    LOCAL_CHANGES = "local_changes"     # local moved, remote at base
    CONFLICT = "conflict"               # both moved from the common base
    BLOCKED = "blocked"                 # readiness blockers present
    EMPTY_REPO = "empty_repo"


@dataclass(slots=True)
class Connection:
    owner: str
    repo: str
    branch: str
    config_path: str = ""        # subdir within the repo that is the HA config root
    full_name: str = ""

    def __post_init__(self) -> None:
        if not self.full_name:
            self.full_name = f"{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    def to_dict(self) -> dict:
        return {
            "owner": self.owner,
            "repo": self.repo,
            "branch": self.branch,
            "config_path": self.config_path,
            "full_name": self.full_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Connection:
        return cls(
            owner=data["owner"],
            repo=data["repo"],
            branch=data["branch"],
            config_path=data.get("config_path", ""),
            full_name=data.get("full_name", ""),
        )


@dataclass(slots=True)
class Finding:
    code: str
    severity: Severity
    title: str
    detail: str
    suggestion: str = ""
    paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "paths": self.paths,
        }


@dataclass(slots=True)
class ReadinessReport:
    is_empty: bool
    is_valid_repo: bool
    score: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return any(f.severity is Severity.BLOCKER for f in self.findings)

    @property
    def deployable(self) -> bool:
        return self.is_valid_repo and not self.has_blockers

    def to_dict(self) -> dict:
        return {
            "is_empty": self.is_empty,
            "is_valid_repo": self.is_valid_repo,
            "score": self.score,
            "has_blockers": self.has_blockers,
            "deployable": self.deployable,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


@dataclass(slots=True)
class ChangeSet:
    """Differences between two trees, expressed as repo-relative paths."""

    writes: list[str] = field(default_factory=list)   # added or modified
    deletes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.writes and not self.deletes

    @property
    def count(self) -> int:
        return len(self.writes) + len(self.deletes)


@dataclass(slots=True)
class LocalChanges:
    modified: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.modified or self.added or self.deleted)

    @property
    def count(self) -> int:
        return len(self.modified) + len(self.added) + len(self.deleted)

    def to_dict(self) -> dict:
        return {
            "dirty": self.dirty,
            "modified": self.modified,
            "added": self.added,
            "deleted": self.deleted,
        }


@dataclass(slots=True)
class DeployResult:
    status: DeployStatus
    sha: str | None = None
    branch: str | None = None
    direction: Direction = Direction.PULL
    message: str = ""
    errors: list[str] = field(default_factory=list)
    files_changed: int = 0
    backup_slug: str | None = None
    restarted: bool = False
    deployment_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "sha": self.sha,
            "branch": self.branch,
            "direction": self.direction.value,
            "message": self.message,
            "errors": self.errors,
            "files_changed": self.files_changed,
            "backup_slug": self.backup_slug,
            "restarted": self.restarted,
            "deployment_id": self.deployment_id,
        }
