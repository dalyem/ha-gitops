"""Divergence (conflict) detection.

A conflict exists when **both** sides have moved from the common base since the last
successful sync. The MVP detects and blocks; resolution actions (pull / push / branch)
are a future enhancement that will reuse the validated deploy and sync pipelines.
"""
from __future__ import annotations

from ..models import LocalChanges, SyncState


def evaluate(
    base_sha: str | None,
    remote_sha: str | None,
    local_dirty: bool,
    repo_empty: bool,
) -> SyncState:
    if repo_empty:
        return SyncState.EMPTY_REPO

    # Never deployed yet.
    if base_sha is None:
        if remote_sha is None:
            return SyncState.EMPTY_REPO
        return SyncState.REMOTE_CHANGES

    remote_moved = remote_sha is not None and remote_sha != base_sha

    if remote_moved and local_dirty:
        return SyncState.CONFLICT
    if remote_moved:
        return SyncState.REMOTE_CHANGES
    if local_dirty:
        return SyncState.LOCAL_CHANGES
    return SyncState.IN_SYNC


def summarize_local(changes: LocalChanges) -> str:
    return (
        f"{len(changes.modified)} modified, "
        f"{len(changes.added)} added, "
        f"{len(changes.deleted)} deleted"
    )


# Resolution choices surfaced to the user once a conflict is detected. The
# engine currently records the chosen action; execution lands in a later phase.
RESOLUTION_OPTIONS = [
    {"id": "pull", "label": "Use the GitHub version (discard local changes)"},
    {"id": "push", "label": "Use the local version (push it to GitHub)"},
    {"id": "branch", "label": "Save local changes to a new branch"},
    {"id": "manual", "label": "I'll resolve it manually"},
]
