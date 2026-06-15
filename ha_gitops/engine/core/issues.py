"""(Future feature) open a GitHub issue when validation/deployment fails.

Gated behind the ``create_github_issues_on_failure`` option. De-duplication uses a
short fingerprint of the failure signature embedded in the issue body plus a label.
"""
from __future__ import annotations

import hashlib
import logging

from .. import settings
from ..models import Connection
from .github_client import GitHubClient, GitHubError

log = logging.getLogger("ha_gitops.issues")

_MARKER = "<!-- ha-gitops-fingerprint:{fp} -->"


def fingerprint(errors: list[str]) -> str:
    signature = "\n".join(sorted(errors))
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]


def _body(
    errors: list[str], sha: str, branch: str, addon_version: str, ha_version: str, fp: str
) -> str:
    error_block = "\n".join(f"- {e}" for e in errors) or "- (none captured)"
    return (
        "Automated report from the **HA-GitOps** add-on.\n\n"
        f"- **Commit:** `{sha}`\n"
        f"- **Branch:** `{branch}`\n"
        f"- **Add-on version:** {addon_version}\n"
        f"- **Home Assistant version:** {ha_version}\n\n"
        "### Validation errors\n"
        f"{error_block}\n\n"
        "### Suggested fixes\n"
        "Run `check_config` locally, ensure all `!secret` keys exist, and confirm no "
        "forbidden files (secrets, databases, `.storage`) are committed.\n\n"
        f"{_MARKER.format(fp=fp)}"
    )


async def report_failure(
    gh: GitHubClient,
    conn: Connection,
    errors: list[str],
    sha: str,
    branch: str,
    addon_version: str,
    ha_version: str,
) -> str | None:
    fp = fingerprint(errors)
    try:
        existing = await gh.search_issues(conn.owner, conn.repo, f"is:open label:{settings.ISSUE_LABEL}")
        for issue in existing:
            if fp in (issue.get("body") or ""):
                log.info("issue for fingerprint %s already exists (#%s)", fp, issue.get("number"))
                return issue.get("html_url")
        title = f"[HA-GitOps] Deployment validation failed ({sha[:8]})"
        body = _body(errors, sha, branch, addon_version, ha_version, fp)
        created = await gh.create_issue(conn.owner, conn.repo, title, body, [settings.ISSUE_LABEL])
        return created.get("html_url") if created else None
    except GitHubError as exc:
        log.warning("could not create issue: %s", exc)
        return None
