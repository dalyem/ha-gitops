"""Thin GitHub REST client (httpx) with conditional-request (ETag) support.

Only the read endpoints needed for polling and browsing live here; git data
operations (clone/fetch/push) go through :mod:`git_engine`, and issue creation
through :mod:`issues`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .. import settings

log = logging.getLogger("ha_gitops.github")


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class HeadResult:
    sha: str | None
    etag: str | None
    not_modified: bool


class GitHubClient:
    def __init__(self, token: str, api_url: str | None = None) -> None:
        self._token = token
        self._api = (api_url or settings.GITHUB_API_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "ha-gitops",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, **kw) -> httpx.Response:
        resp = await self._http().get(path, **kw)
        if resp.status_code == 401:
            raise GitHubError("Authentication failed — check the token.", 401)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            raise GitHubError("GitHub API rate limit exceeded.", 403)
        return resp

    async def verify_token(self) -> dict:
        resp = await self._get("/user")
        if resp.status_code >= 400:
            raise GitHubError(f"Token verification failed (HTTP {resp.status_code}).", resp.status_code)
        return resp.json()

    async def list_repos(self, max_pages: int = 5) -> list[dict]:
        repos: list[dict] = []
        for page in range(1, max_pages + 1):
            resp = await self._get(
                "/user/repos",
                params={"per_page": 100, "page": page, "sort": "updated"},
            )
            if resp.status_code >= 400:
                raise GitHubError(f"Could not list repositories (HTTP {resp.status_code}).", resp.status_code)
            batch = resp.json()
            if not batch:
                break
            for r in batch:
                repos.append(
                    {
                        "full_name": r["full_name"],
                        "owner": r["owner"]["login"],
                        "name": r["name"],
                        "private": r.get("private", False),
                        "default_branch": r.get("default_branch", "main"),
                        "can_push": (r.get("permissions") or {}).get("push", False),
                    }
                )
            if len(batch) < 100:
                break
        return repos

    async def get_repo(self, owner: str, repo: str) -> dict:
        resp = await self._get(f"/repos/{owner}/{repo}")
        if resp.status_code == 404:
            raise GitHubError("Repository not found or token lacks access.", 404)
        if resp.status_code >= 400:
            raise GitHubError(f"Could not read repository (HTTP {resp.status_code}).", resp.status_code)
        return resp.json()

    async def list_branches(self, owner: str, repo: str) -> list[str]:
        branches: list[str] = []
        for page in range(1, 4):
            resp = await self._get(
                f"/repos/{owner}/{repo}/branches",
                params={"per_page": 100, "page": page},
            )
            if resp.status_code == 409:  # empty repository
                return []
            if resp.status_code >= 400:
                raise GitHubError(f"Could not list branches (HTTP {resp.status_code}).", resp.status_code)
            batch = resp.json()
            if not batch:
                break
            branches.extend(b["name"] for b in batch)
            if len(batch) < 100:
                break
        return branches

    async def get_branch_head(
        self, owner: str, repo: str, branch: str, etag: str | None = None
    ) -> HeadResult:
        """Latest commit SHA of a branch, using ``If-None-Match`` when possible.

        A 304 response means "unchanged" and does *not* count against the rate limit.
        """
        headers = {"If-None-Match": etag} if etag else {}
        resp = await self._get(f"/repos/{owner}/{repo}/branches/{branch}", headers=headers)
        if resp.status_code == 304:
            return HeadResult(sha=None, etag=etag, not_modified=True)
        if resp.status_code == 404 or resp.status_code == 409:
            return HeadResult(sha=None, etag=None, not_modified=False)
        if resp.status_code >= 400:
            raise GitHubError(f"Could not read branch head (HTTP {resp.status_code}).", resp.status_code)
        data = resp.json()
        return HeadResult(
            sha=data["commit"]["sha"],
            etag=resp.headers.get("ETag"),
            not_modified=False,
        )

    # ---- issues (used by the future issue-reporting feature) ----------------
    async def search_issues(self, owner: str, repo: str, query: str) -> list[dict]:
        q = f"repo:{owner}/{repo} is:issue {query}"
        resp = await self._get("/search/issues", params={"q": q})
        if resp.status_code >= 400:
            return []
        return resp.json().get("items", [])

    async def create_issue(
        self, owner: str, repo: str, title: str, body: str, labels: list[str]
    ) -> dict:
        resp = await self._http().post(
            f"/repos/{owner}/{repo}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        if resp.status_code >= 400:
            raise GitHubError(f"Could not create issue (HTTP {resp.status_code}).", resp.status_code)
        return resp.json()
