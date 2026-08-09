"""GitHub PR client for PR-based accept (#188 §5.1) — backend-internal.

Wraps the four calls the accept pipeline needs: open a PR for a topic branch,
read it, merge it, and list its check runs. Auth is the cheesex-app installation
token, minted per call family: pull/merge use the write mint, check runs use the
read-only mint (same one sandboxes get). Tokens never leave the process.

The client is deliberately dumb — no retries, no state. The caller decides what
a failure means (PR opening is best-effort enrichment; a merge refusal routes to
the conflict flow; everything else surfaces).
"""

import re

import httpx

from app.domain.agent.github_app import GitHubAppTokens

# Upstream URL shapes eligible for PR-based accept. SSH remotes are excluded on
# purpose: an installation token only authenticates over https.
_GITHUB_HTTPS_RE = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$"
)


def parse_github_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) when the upstream is an https GitHub remote, else None."""
    if not url:
        return None
    match = _GITHUB_HTTPS_RE.match(url.strip())
    if match is None:
        return None
    return match.group("owner"), match.group("repo")


class GitHubPRError(RuntimeError):
    """GitHub refused an operation (or the network did)."""


class GitHubPRMergeBlocked(GitHubPRError):
    """The merge was refused because the PR is not mergeable (conflict)."""


class GitHubPRClient:
    def __init__(
        self,
        owner: str,
        repo: str,
        tokens: GitHubAppTokens,
        *,
        api_base: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._owner = owner
        self._repo = repo
        self._tokens = tokens
        self._api_base = api_base.rstrip("/")
        self._transport = transport

    def _url(self, path: str) -> str:
        return f"{self._api_base}/repos/{self._owner}/{self._repo}{path}"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    async def open_pr(self, *, head: str, base: str, title: str, body: str) -> dict:
        """Open (or find the already-open) PR for a branch.

        Re-submitting a card for the same topic must not fail on GitHub's
        "a pull request already exists" — the existing PR IS this topic's PR.
        """
        token, _ = await self._tokens.write_token()
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.post(
                self._url("/pulls"),
                json={"title": title, "head": head, "base": base, "body": body},
                headers=self._headers(token),
            )
            if resp.status_code == 201:
                return resp.json()
            if resp.status_code == 422 and "already exist" in resp.text:
                listing = await client.get(
                    self._url("/pulls"),
                    params={"head": f"{self._owner}:{head}", "state": "open"},
                    headers=self._headers(token),
                )
                if listing.status_code == 200 and listing.json():
                    return listing.json()[0]
            raise GitHubPRError(
                f"PR creation failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

    async def pr_view(self, number: int) -> dict:
        token, _ = await self._tokens.write_token()
        async with httpx.AsyncClient(transport=self._transport, timeout=20.0) as client:
            resp = await client.get(
                self._url(f"/pulls/{number}"), headers=self._headers(token)
            )
        if resp.status_code != 200:
            raise GitHubPRError(
                f"PR read failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()

    async def merge_pr(self, number: int, *, title: str, message: str) -> dict:
        """Merge the PR with a merge commit (matches the platform's history).

        405 (not mergeable) raises GitHubPRMergeBlocked — the caller routes it
        to the existing conflict-resolution flow. Anything else is a plain
        GitHubPRError.
        """
        token, _ = await self._tokens.write_token()
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.put(
                self._url(f"/pulls/{number}/merge"),
                json={
                    "merge_method": "merge",
                    "commit_title": title,
                    "commit_message": message,
                },
                headers=self._headers(token),
            )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 405:
            raise GitHubPRMergeBlocked(
                f"PR #{number} is not mergeable: {resp.text[:300]}"
            )
        raise GitHubPRError(
            f"PR merge failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    async def check_runs(self, ref: str) -> list[dict]:
        """Simplified check runs for a ref (branch name or sha) — display only.

        Uses the read-only mint (checks:read); the write mint has no checks
        permission by design.
        """
        token, _ = await self._tokens.readonly_token()
        async with httpx.AsyncClient(transport=self._transport, timeout=20.0) as client:
            resp = await client.get(
                self._url(f"/commits/{ref}/check-runs"),
                params={"per_page": 50},
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            raise GitHubPRError(
                f"check-runs read failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return [
            {
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "url": run.get("html_url"),
            }
            for run in resp.json().get("check_runs", [])
        ]
