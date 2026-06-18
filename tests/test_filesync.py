from engine.core import filesync
from engine.models import ChangeSet


def test_compute_change_set():
    old = {"a.yaml": "1", "b.yaml": "2", "gone.yaml": "9"}
    new = {"a.yaml": "1", "b.yaml": "3", "c.yaml": "4"}
    change = filesync.compute_change_set(old, new)
    assert change.writes == ["b.yaml", "c.yaml"]
    assert change.deletes == ["gone.yaml"]


def test_apply_and_restore(tmp_path):
    source = tmp_path / "src"
    ha = tmp_path / "ha"
    snap = tmp_path / "snap"
    (source / "sub").mkdir(parents=True)
    ha.mkdir()
    (source / "new.yaml").write_text("new")
    (source / "sub" / "edit.yaml").write_text("v2")
    (ha / "sub").mkdir()
    (ha / "sub" / "edit.yaml").write_text("v1")     # will be overwritten
    (ha / "old.yaml").write_text("remove me")        # will be deleted

    change = ChangeSet(writes=["new.yaml", "sub/edit.yaml"], deletes=["old.yaml"])
    filesync.apply_change_set(change, source, ha, snap)

    assert (ha / "new.yaml").read_text() == "new"
    assert (ha / "sub" / "edit.yaml").read_text() == "v2"
    assert not (ha / "old.yaml").exists()

    filesync.restore_snapshot(snap, ha)
    assert not (ha / "new.yaml").exists()             # added -> removed
    assert (ha / "sub" / "edit.yaml").read_text() == "v1"  # overwritten -> restored
    assert (ha / "old.yaml").read_text() == "remove me"    # deleted -> restored


def test_detect_local_changes(tmp_path):
    ha = tmp_path / "ha"
    ha.mkdir()
    (ha / "configuration.yaml").write_text("a: 1\n")
    (ha / "automations.yaml").write_text("orig\n")
    manifest = filesync.build_manifest(["configuration.yaml", "automations.yaml"], ha)

    # modify, add a tracked-able file, add an ignored secret, delete one
    (ha / "automations.yaml").write_text("changed\n")
    (ha / "scenes.yaml").write_text("new\n")
    (ha / "secrets.yaml").write_text("token: abc\n")        # must be ignored
    (ha / "home-assistant_v2.db").write_text("binary")       # must be ignored
    (ha / "home-assistant.log.1").write_text("rotated log")  # must be ignored
    (ha / "configuration.yaml").unlink()

    changes = filesync.detect_local_changes(manifest, ha, gitignore_text=None)
    assert "automations.yaml" in changes.modified
    assert "configuration.yaml" in changes.deleted
    assert "scenes.yaml" in changes.added
    assert "secrets.yaml" not in changes.added
    assert "home-assistant_v2.db" not in changes.added
    assert "home-assistant.log.1" not in changes.added       # rotated logs ignored


def test_recommended_gitignore_covers_essentials():
    spec = filesync.build_ignore_spec(None)
    for p in [
        "secrets.yaml", ".storage/core.auth", "home-assistant_v2.db",
        "x.log", "home-assistant.log.1", "home-assistant.log.2.gz",
    ]:
        assert spec.match_file(p), p
