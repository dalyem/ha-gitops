from engine.core import conflicts
from engine.models import SyncState


def test_in_sync():
    assert conflicts.evaluate("abc", "abc", False, False) is SyncState.IN_SYNC


def test_remote_changes():
    assert conflicts.evaluate("abc", "def", False, False) is SyncState.REMOTE_CHANGES


def test_local_changes():
    assert conflicts.evaluate("abc", "abc", True, False) is SyncState.LOCAL_CHANGES


def test_conflict():
    assert conflicts.evaluate("abc", "def", True, False) is SyncState.CONFLICT


def test_empty_repo():
    assert conflicts.evaluate(None, None, False, True) is SyncState.EMPTY_REPO
    assert conflicts.evaluate("abc", None, False, True) is SyncState.EMPTY_REPO


def test_first_deploy_when_never_synced():
    assert conflicts.evaluate(None, "def", False, False) is SyncState.REMOTE_CHANGES


def test_remote_head_missing_after_sync():
    # base set but remote head gone (deleted/unreachable) -> not IN_SYNC/LOCAL.
    assert conflicts.evaluate("abc", None, False, False) is SyncState.EMPTY_REPO
    assert conflicts.evaluate("abc", None, True, False) is SyncState.EMPTY_REPO
