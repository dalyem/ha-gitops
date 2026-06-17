"""End-to-end git engine test against a local bare repo (no network)."""
import asyncio
import shutil
import subprocess

import pytest
from engine.core.git_engine import GitEngine

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_git_roundtrip(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    repo_dir = tmp_path / "repo"
    ge = GitEngine(repo_dir)

    async def run():
        # Clone the empty repo, populate it, and push an initial commit.
        await ge.ensure_repo(f"file://{remote}", "", "main")
        (repo_dir / "configuration.yaml").write_text("a: 1\n")
        (repo_dir / "sub").mkdir()
        (repo_dir / "sub" / "x.yaml").write_text("b: 2\n")
        await ge.stage_all("")
        assert await ge.has_staged_changes()
        sha = await ge.commit("init")
        await ge.rename_branch("main")
        await ge.push("main", "", set_upstream=True)

        # Remote now points at our commit.
        await ge.fetch("main", "")
        assert await ge.remote_head("main") == sha

        # ls_tree returns config-relative blob paths.
        tree = await ge.ls_tree(sha, "")
        assert "configuration.yaml" in tree
        assert "sub/x.yaml" in tree

        # subdir scoping strips the prefix.
        subtree = await ge.ls_tree(sha, "sub")
        assert set(subtree) == {"x.yaml"}

        # file content readout.
        assert await ge.show_bytes(sha, "configuration.yaml") == b"a: 1\n"
        assert await ge.show_bytes(sha, "missing.yaml") is None

    asyncio.run(run())


def test_change_set_after_second_commit(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    repo_dir = tmp_path / "repo"
    ge = GitEngine(repo_dir)

    async def run():
        await ge.ensure_repo(f"file://{remote}", "", "main")
        (repo_dir / "a.yaml").write_text("1\n")
        (repo_dir / "b.yaml").write_text("1\n")
        await ge.stage_all("")
        sha1 = await ge.commit("first")
        await ge.rename_branch("main")
        await ge.push("main", "", set_upstream=True)

        (repo_dir / "a.yaml").write_text("2\n")     # modify
        (repo_dir / "b.yaml").unlink()               # delete
        (repo_dir / "c.yaml").write_text("new\n")    # add
        await ge.stage_all("")
        sha2 = await ge.commit("second")

        from engine.core import filesync
        old = await ge.ls_tree(sha1, "")
        new = await ge.ls_tree(sha2, "")
        change = filesync.compute_change_set(old, new)
        assert "a.yaml" in change.writes
        assert "c.yaml" in change.writes
        assert change.deletes == ["b.yaml"]

    asyncio.run(run())
