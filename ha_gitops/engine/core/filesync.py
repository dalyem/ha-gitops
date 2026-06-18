"""Controlled file application between the clone and the live config directory.

Git never touches ``/homeassistant``. Instead we compute a change set from git
blob hashes and apply it here with a per-deploy **snapshot** so any apply can be
reverted precisely if validation fails. Local-drift detection compares live file
hashes against the manifest captured at the last successful sync.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

import pathspec

from ..models import ChangeSet, LocalChanges

# Patterns we ALWAYS treat as runtime/secret artifacts, regardless of the repo's
# own .gitignore. Used both to scan for untracked local additions and as a final
# guard so secrets/databases can never be proposed for commit.
ALWAYS_IGNORE: tuple[str, ...] = (
    ".git/",
    "secrets.yaml",
    ".storage/",
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "*.sqlite",
    "*.log",
    "*.log.*",   # rotated logs: home-assistant.log.1, .2, .gz, .fault, …
    ".HA_VERSION",
    ".ha_run.lock",
    ".cloud/",
    "deps/",
    "tts/",
    "__pycache__/",
    "backups/",
    "*.tar",
    "*.tar.gz",
    ".uuid",
    "known_devices.yaml",
    "ip_bans.yaml",
    "*.token",
    ".google.token",
)

# Hard safety cap: never stage a file larger than this for commit, regardless of
# patterns. Well under GitHub's 100 MB hard reject (and its 50 MB warning).
MAX_COMMIT_FILE_BYTES = 50 * 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_ignore_spec(gitignore_text: str | None = None) -> pathspec.GitIgnoreSpec:
    lines = list(ALWAYS_IGNORE)
    if gitignore_text:
        lines += gitignore_text.splitlines()
    return pathspec.GitIgnoreSpec.from_lines(lines)


def compute_change_set(old_map: dict[str, str], new_map: dict[str, str]) -> ChangeSet:
    writes = [
        rel for rel, sha in new_map.items() if old_map.get(rel) != sha
    ]
    deletes = [rel for rel in old_map if rel not in new_map]
    return ChangeSet(writes=sorted(writes), deletes=sorted(deletes))


def apply_change_set(
    change: ChangeSet, source_dir: Path, ha_dir: Path, snapshot_dir: Path
) -> dict:
    """Apply ``change`` from ``source_dir`` into ``ha_dir``, snapshotting first.

    Returns a snapshot record (also written to ``snapshot_dir/record.json``) that
    :func:`restore_snapshot` can use to revert exactly.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    record = {"overwritten": [], "added": [], "deleted": []}

    for rel in change.writes:
        src = source_dir / rel
        dst = ha_dir / rel
        if not src.exists():
            continue
        if dst.exists():
            _backup(dst, snapshot_dir / "overwritten" / rel)
            record["overwritten"].append(rel)
        else:
            record["added"].append(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel in change.deletes:
        dst = ha_dir / rel
        if dst.exists():
            _backup(dst, snapshot_dir / "deleted" / rel)
            record["deleted"].append(rel)
            dst.unlink()

    (snapshot_dir / "record.json").write_text(json.dumps(record, indent=2))
    return record


def restore_snapshot(snapshot_dir: Path, ha_dir: Path) -> None:
    record_path = snapshot_dir / "record.json"
    if not record_path.exists():
        return
    record = json.loads(record_path.read_text())
    for rel in record.get("overwritten", []):
        backup = snapshot_dir / "overwritten" / rel
        if backup.exists():
            (ha_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, ha_dir / rel)
    for rel in record.get("deleted", []):
        backup = snapshot_dir / "deleted" / rel
        if backup.exists():
            (ha_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, ha_dir / rel)
    for rel in record.get("added", []):
        target = ha_dir / rel
        if target.exists():
            target.unlink()


def build_manifest(rels: Iterable[str], ha_dir: Path) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for rel in rels:
        path = ha_dir / rel
        if path.is_file():
            manifest[rel] = sha256_file(path)
    return manifest


def detect_local_changes(
    manifest: dict[str, str],
    ha_dir: Path,
    gitignore_text: str | None = None,
) -> LocalChanges:
    """Compare the live config dir against the manifest of the last synced tree."""
    spec = build_ignore_spec(gitignore_text)
    changes = LocalChanges()

    # Modified / deleted among tracked files.
    for rel, known_hash in manifest.items():
        path = ha_dir / rel
        if not path.is_file():
            changes.deleted.append(rel)
        elif sha256_file(path) != known_hash:
            changes.modified.append(rel)

    # New, non-ignored files that aren't tracked yet.
    tracked = set(manifest)
    for rel in _walk_relpaths(ha_dir, spec):
        if rel not in tracked and not spec.match_file(rel):
            changes.added.append(rel)

    changes.modified.sort()
    changes.added.sort()
    changes.deleted.sort()
    return changes


def _walk_relpaths(root: Path, spec: pathspec.PathSpec) -> Iterable[str]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        # Prune ignored directories (and .git) before descending.
        kept = []
        for d in dirnames:
            child = f"{rel_dir}/{d}" if rel_dir else d
            if d == ".git" or spec.match_file(child + "/"):
                continue
            kept.append(d)
        dirnames[:] = kept
        for f in filenames:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            yield rel


def _backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
