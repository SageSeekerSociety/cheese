"""What the forge says about a merge commit on the default branch.

The reads `landing_watch` needs, one object per project: the commit a PR's
merge put on the default branch, the checks that ran on a commit, a commit's
parent, and the first deployment that included a commit. GitHub and Forgejo
answer the first three; deployments are GitHub's, read only when the App's
installation was granted them, and a repository that records none simply has
none to report.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.forge_http import forge_client
from app.domain.project.forge import (
    binding_for_project,
    proposal_client,
    tokens_for_project,
)


class ForgeUnavailable(Exception):
    """The forge will not say: the repository or the permission is not there."""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    #: queued / in_progress / completed
    status: str
    #: success / failure / cancelled / skipped / timed_out / … once completed
    conclusion: str | None
    url: str


@dataclass(frozen=True, slots=True)
class Deployment:
    environment: str
    #: success / failure / error
    state: str
    url: str


class LandingReads(Protocol):
    async def merge_sha(self, pr_number: int) -> str | None: ...
    async def checks(self, sha: str) -> list[Check]: ...
    async def parent(self, sha: str) -> str | None: ...
    async def deployment(self, sha: str, since: datetime) -> Deployment | None:
        """The first finished deployment since ``since`` that includes ``sha``;
        None while there is none yet. Raises `ForgeUnavailable` when the forge
        records none the platform may read."""
        ...


async def reads_for(
    project_id: uuid.UUID, session: AsyncSession
) -> LandingReads | None:
    """The project's forge, or None when it has no repository."""
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    client = await proposal_client(project_id, session)
    tokens = await tokens_for_project(project_id, session)
    owner, repo = binding.repo.split("/", 1)
    if binding.kind == "github_app":
        return GitHubLandingReads(client, tokens, binding.api_url, owner, repo)
    return ForgejoLandingReads(client, tokens, binding.api_url, owner, repo)


_FINISHED_DEPLOYMENT = ("success", "failure", "error")


class GitHubLandingReads:
    def __init__(
        self, client, tokens, api_base: str, owner: str, repo: str, transport=None
    ):
        self._client = client
        self._tokens = tokens
        self._transport = transport
        self._base = f"{api_base.rstrip('/')}/repos/{quote(owner)}/{quote(repo)}"

    async def _get(self, path: str, params: dict | None = None):
        token, _ = await self._tokens.installation_token()
        async with forge_client(transport=self._transport, timeout=20.0) as http:
            resp = await http.get(
                self._base + path,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code in (403, 404):
            raise ForgeUnavailable
        resp.raise_for_status()
        return resp.json()

    async def merge_sha(self, pr_number: int) -> str | None:
        return (await self._client.pr_status(pr_number)).merge_commit_sha

    async def checks(self, sha: str) -> list[Check]:
        found: list[Check] = []
        page = 1
        while True:
            data = await self._get(
                f"/commits/{sha}/check-runs", {"per_page": 100, "page": page}
            )
            runs = data.get("check_runs") or []
            found += [
                Check(
                    name=run.get("name") or "",
                    status=run.get("status") or "",
                    conclusion=run.get("conclusion"),
                    url=run.get("html_url") or "",
                )
                for run in runs
            ]
            if len(runs) < 100:
                return found
            page += 1

    async def parent(self, sha: str) -> str | None:
        parents = (await self._get(f"/commits/{sha}")).get("parents") or []
        return parents[0].get("sha") if parents else None

    async def deployment(self, sha: str, since: datetime) -> Deployment | None:
        if "deployments" not in await self._tokens.granted_permissions():
            raise ForgeUnavailable
        recent = await self._get("/deployments", {"per_page": 30})
        later = [
            d
            for d in recent
            if datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")) >= since
        ]
        # Oldest first. A later deployment that went through settles a failed
        # one before it: the commit is deployed.
        failed: Deployment | None = None
        for d in sorted(later, key=lambda d: d["created_at"]):
            if not await self._includes(d["sha"], sha):
                continue
            statuses = await self._get(
                f"/deployments/{d['id']}/statuses", {"per_page": 1}
            )
            state = statuses[0]["state"] if statuses else "pending"
            if state not in _FINISHED_DEPLOYMENT:
                return None
            seen = Deployment(
                environment=d.get("environment") or "",
                state=state,
                url=statuses[0].get("log_url") or statuses[0].get("target_url") or "",
            )
            if state == "success":
                return seen
            failed = failed or seen
        return failed

    async def _includes(self, deployed: str, sha: str) -> bool:
        if deployed == sha:
            return True
        compared = await self._get(f"/compare/{sha}...{deployed}")
        return compared.get("status") in ("ahead", "identical")


class ForgejoLandingReads:
    def __init__(self, client, tokens, api_base: str, owner: str, repo: str):
        self._client = client
        self._tokens = tokens
        self._repo = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"

    async def merge_sha(self, pr_number: int) -> str | None:
        return (await self._client.pr_status(pr_number)).merge_commit_sha

    async def checks(self, sha: str) -> list[Check]:
        return [
            Check(
                name=run["name"] or "",
                status=run["status"] or "",
                conclusion=run["conclusion"],
                url=run.get("url") or "",
            )
            for run in await self._client.check_runs(sha)
        ]

    async def parent(self, sha: str) -> str | None:
        token, _ = await self._tokens.installation_token()
        forgejo = self._client.client
        data = forgejo._data(
            await forgejo.request("GET", f"{self._repo}/git/commits/{sha}", token=token)
        )
        parents = (data or {}).get("parents") or []
        return parents[0].get("sha") if parents else None

    async def deployment(self, sha: str, since: datetime) -> Deployment | None:
        raise ForgeUnavailable
