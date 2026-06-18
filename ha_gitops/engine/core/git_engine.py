"""Async wrapper around the ``git`` CLI.

Network operations authenticate via ``GIT_ASKPASS`` so the token is never placed in
a remote URL, ``.git/config`` or process arguments (it lives only in the child
process's environment). All git work happens in ``/data/repo`` — git never operates
on the live Home Assistant config directory.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
from pathlib import Path

from .. import settings
from . import secrets_store

log = logging.getLogger("ha_gitops.git")

_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "${GIT_USERNAME}" ;;
  *)         printf '%s' "${GIT_PASSWORD}" ;;
esac
"""


class GitError(RuntimeError):
    pass


class GitEngine:
    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = Path(repo_dir or settings.REPO_DIR)

    # ---- low level ----------------------------------------------------------
    def _ensure_askpass(self) -> Path:
        path = settings.ASKPASS_FILE
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_ASKPASS_SCRIPT)
            path.chmod(stat.S_IRWXU)
        return path

    async def _run(
        self,
        *args: str,
        cwd: Path | None = None,
        token: str | None = None,
        check: bool = True,
    ) -> tuple[int, str, str]:
        env = dict(os.environ)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "HOME": str(settings.DATA_DIR),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
        )
        if token:
            env["GIT_ASKPASS"] = str(self._ensure_askpass())
            env["GIT_USERNAME"] = "x-access-token"
            env["GIT_PASSWORD"] = token

        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out_b, err_b = await proc.communicate()
        out = out_b.decode("utf-8", "replace")
        err = secrets_store.redact(err_b.decode("utf-8", "replace"))
        if check and proc.returncode != 0:
            raise GitError(f"git {args[0]} failed: {err.strip() or out.strip()}")
        return proc.returncode or 0, out, err

    async def _run_bytes(self, *args: str, cwd: Path | None = None) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(cwd or self.repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HOME": str(settings.DATA_DIR)},
        )
        out_b, err_b = await proc.communicate()
        if proc.returncode != 0:
            raise GitError(f"git {args[0]} failed: {err_b.decode('utf-8', 'replace').strip()}")
        return out_b

    # ---- repository lifecycle ----------------------------------------------
    @property
    def is_cloned(self) -> bool:
        return (self.repo_dir / ".git").exists()

    async def ensure_repo(self, clone_url: str, token: str, branch: str) -> None:
        if self.is_cloned:
            await self._run("remote", "set-url", "origin", clone_url, cwd=self.repo_dir, check=False)
            await self.set_identity()
            return
        if self.repo_dir.exists():
            shutil.rmtree(self.repo_dir)
        self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
        # Clone (works for empty repos too, leaving an unborn branch).
        await self._run("clone", clone_url, str(self.repo_dir), token=token)
        await self.set_identity()
        # Best-effort checkout of the requested branch if it exists.
        await self._run("checkout", branch, cwd=self.repo_dir, check=False)

    async def reset_clone(self, clone_url: str, token: str) -> None:
        """Remove any existing clone and clone fresh.

        Used by the empty-repo init so a previous *failed* init (which may have
        left a local commit with an oversized file) can't poison the retry.
        """
        if self.repo_dir.exists():
            shutil.rmtree(self.repo_dir)
        self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
        await self._run("clone", clone_url, str(self.repo_dir), token=token)
        await self.set_identity()

    async def set_identity(self) -> None:
        await self._run("config", "user.name", "HA-GitOps", cwd=self.repo_dir, check=False)
        await self._run(
            "config", "user.email", "ha-gitops@users.noreply.github.com",
            cwd=self.repo_dir, check=False,
        )

    async def fetch(self, branch: str, token: str) -> None:
        await self._run("fetch", "--prune", "origin", cwd=self.repo_dir, token=token)

    async def remote_head(self, branch: str) -> str | None:
        rc, out, _ = await self._run(
            "rev-parse", f"origin/{branch}", cwd=self.repo_dir, check=False
        )
        return out.strip() if rc == 0 and out.strip() else None

    async def local_head(self) -> str | None:
        rc, out, _ = await self._run("rev-parse", "HEAD", cwd=self.repo_dir, check=False)
        return out.strip() if rc == 0 and out.strip() else None

    async def reset_to(self, branch: str, ref: str) -> None:
        """Point the local branch at ``ref`` and check it out (discards repo-local edits)."""
        await self._run("checkout", "-f", "-B", branch, ref, cwd=self.repo_dir)

    async def ls_tree(self, ref: str, subdir: str = "") -> dict[str, str]:
        """Return ``{config-relative path: blob sha}`` for the tracked files under ``subdir``."""
        args = ["ls-tree", "-r", ref]
        prefix = subdir.strip("/")
        if prefix:
            args += ["--", prefix + "/"]
        rc, out, _ = await self._run(*args, cwd=self.repo_dir, check=False)
        result: dict[str, str] = {}
        if rc != 0:
            return result
        for line in out.splitlines():
            if not line.strip():
                continue
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            blob_sha = parts[2]
            rel = path
            if prefix:
                rel = path[len(prefix) + 1:] if path.startswith(prefix + "/") else path
            result[rel] = blob_sha
        return result

    async def show_bytes(self, ref: str, path: str) -> bytes | None:
        try:
            return await self._run_bytes("show", f"{ref}:{path}")
        except GitError:
            return None

    # ---- worktrees (for isolated pre-validation) ----------------------------
    async def worktree_add(self, path: Path, ref: str) -> None:
        await self.worktree_remove(path)
        if path.exists():
            shutil.rmtree(path)
        await self._run("worktree", "add", "-f", "--detach", str(path), ref, cwd=self.repo_dir)

    async def worktree_remove(self, path: Path) -> None:
        await self._run("worktree", "remove", "--force", str(path), cwd=self.repo_dir, check=False)
        await self._run("worktree", "prune", cwd=self.repo_dir, check=False)

    # ---- commit & push ------------------------------------------------------
    async def stage_all(self, subdir: str = "") -> None:
        target = (subdir.strip("/") or ".")
        await self._run("add", "-A", "--", target, cwd=self.repo_dir)

    async def has_staged_changes(self) -> bool:
        rc, _, _ = await self._run("diff", "--cached", "--quiet", cwd=self.repo_dir, check=False)
        return rc != 0

    async def commit(self, message: str) -> str:
        await self._run("commit", "-m", message, cwd=self.repo_dir)
        head = await self.local_head()
        if not head:
            raise GitError("commit produced no HEAD")
        return head

    async def rename_branch(self, branch: str) -> None:
        await self._run("branch", "-M", branch, cwd=self.repo_dir, check=False)

    async def push(self, branch: str, token: str, set_upstream: bool = False) -> None:
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args += ["origin", branch]
        await self._run(*args, cwd=self.repo_dir, token=token)
