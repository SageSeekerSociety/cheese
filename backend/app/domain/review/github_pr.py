"""GitHub PR clients for PR-based accept — two mechanisms live here side by
side (2026-08-09):

- `GitHubPRClient` (capital PR) — #188 §5.1's original design. Auth is the
  cheesex-app installation token (write mint for pull/merge, read-only mint
  for check runs); a PR is opened fire-and-forget by `review/pr_publish.py`
  when a card turns pending (behind `settings.accept_via_pr`, off by
  default), and `AcceptService._accept_via_pr` merges it synchronously when
  a human clicks accept.
- `GitHubPrClient` (lowercase pr) Protocol + `HttpxGitHubPrClient` — the
  两阶段采纳 (PR迭代式, 2026-08-09) design. Auth is the APPROVING HUMAN's own
  connected GitHub token (attribution matters — see the PR trailer); a PR is
  opened when accept() is clicked and tracked asynchronously by the
  scheduler's poller through CI, merge, and the deploy workflow it triggers,
  before the topic finally archives.

Both are real, live code paths — see `AcceptService.accept()` for how they're
tried in order (an already-PR'd card is never re-published; a PR-less one
tries opening a fresh one via the human's token, then falls back to a local
merge). Not implemented here: opening the App's own write-scoped token for
the 两阶段采纳 flow — per 2026-08-09 拍板 that flow deliberately uses the
approver's own token instead, so `GitHubAppTokens`'s write-mint stays solely
`GitHubPRClient`'s concern.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.domain.agent.github_app import GitHubAppTokens

CheckState = Literal["pending", "success", "failure"]


@dataclass
class PullRequest:
    number: int
    url: str
    head_sha: str


class GitHubPrError(RuntimeError):
    """A GitHub API call failed outright (bad token, repo gone, rate limit,
    GitHub outage, ...). The caller treats this as "mechanism unavailable"
    and degrades — never as "the work is bad"."""


class GitHubPrClient(Protocol):
    async def open_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequest: ...

    async def check_state(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> tuple[CheckState, str]:
        """(state, human-readable tail) for every check-run on `ref` (a commit
        SHA). Empty/all-queued → pending; any real failure → failure (even
        with others still running — no point waiting out a doomed run);
        all completed + none failed → success."""
        ...

    async def pull_request_head_sha(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> str:
        """The PR's CURRENT head commit — re-fetched every poll because 芝士
        pushing a fix moves it; polling a sha frozen at PR-open time would
        check the original (failing) commit forever."""
        ...

    async def merge_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        token: str,
        commit_message: str | None = None,
    ) -> str | None:
        """The merge commit SHA once GitHub actually merged it, else None for
        a recoverable "not mergeable yet" — the poller just tries again next
        tick, not an error."""
        ...

    async def workflow_run_state(
        self, *, owner: str, repo: str, workflow_file: str, head_sha: str, token: str
    ) -> tuple[CheckState, str]:
        """Same tri-state as check_state, for the named workflow file's most
        recent run against `head_sha` (the merge commit). No matching run yet
        (workflow hasn't been picked up by the runner) → pending, not failure."""
        ...


_FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "stale"}
_OK_CONCLUSIONS = {"success", "neutral", "skipped"}


class HttpxGitHubPrClient:
    """Real implementation — plain REST calls against api.github.com."""

    def __init__(self, api_base: str = "https://api.github.com") -> None:
        self._api_base = api_base.rstrip("/")

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def open_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequest:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._api_base}/repos/{owner}/{repo}/pulls",
                headers=self._headers(token),
                json={"title": title, "body": body, "head": head, "base": base},
            )
        if resp.status_code != 201:
            raise GitHubPrError(
                f"GitHub 拒绝开 PR（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        data = resp.json()
        return PullRequest(
            number=data["number"],
            url=data["html_url"],
            head_sha=data["head"]["sha"],
        )

    async def check_state(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> tuple[CheckState, str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/commits/{ref}/check-runs",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝查检查状态（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        runs = resp.json().get("check_runs", [])
        return _summarize_runs(runs)

    async def pull_request_head_sha(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}",
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝查 PR 状态（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        return resp.json()["head"]["sha"]

    async def merge_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        token: str,
        commit_message: str | None = None,
    ) -> str | None:
        body: dict = {"merge_method": "merge"}
        if commit_message:
            body["commit_message"] = commit_message
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}/merge",
                headers=self._headers(token),
                json=body,
            )
        if resp.status_code == 200:
            return resp.json().get("sha")
        if resp.status_code in (405, 409):
            # Not mergeable yet (checks pending / behind base) — try again
            # next poll, not an error.
            return None
        raise GitHubPrError(
            f"GitHub 拒绝合并 PR（HTTP {resp.status_code}）：{resp.text[:300]}"
        )

    async def workflow_run_state(
        self, *, owner: str, repo: str, workflow_file: str, head_sha: str, token: str
    ) -> tuple[CheckState, str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/actions/workflows/"
                f"{workflow_file}/runs",
                headers=self._headers(token),
                params={"head_sha": head_sha, "per_page": 5},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝查部署 workflow 状态（HTTP {resp.status_code}）："
                f"{resp.text[:300]}"
            )
        runs = resp.json().get("workflow_runs", [])
        if not runs:
            return "pending", "部署 workflow 还没被触发/还没跑起来"
        run = runs[0]
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed":
            return "pending", f"部署 workflow 进行中（{status}）"
        if conclusion in _OK_CONCLUSIONS:
            return "success", f"部署 workflow 完成：{conclusion}"
        run_url = run.get("html_url", "")
        return "failure", f"部署 workflow 失败：{conclusion}（{run_url}）"


def _summarize_runs(runs: list[dict]) -> tuple[CheckState, str]:
    if not runs:
        return "pending", "还没有检查报告"
    pending_names = []
    failed = []
    for run in runs:
        name = run.get("name", "?")
        if run.get("status") != "completed":
            pending_names.append(name)
            continue
        conclusion = run.get("conclusion")
        if conclusion in _FAILED_CONCLUSIONS:
            failed.append(f"{name}: {conclusion}")
    if failed:
        return "failure", "、".join(failed[:10])
    if pending_names:
        return "pending", "等待中：" + "、".join(pending_names[:10])
    return "success", f"全部 {len(runs)} 项检查通过"


_default_client: GitHubPrClient | None = None


def default_client() -> GitHubPrClient:
    global _default_client
    if _default_client is None:
        _default_client = HttpxGitHubPrClient()
    return _default_client


def set_default_client(client: GitHubPrClient | None) -> None:
    """Test hook — swap in a fake for the whole process, or reset to real."""
    global _default_client
    _default_client = client


def pr_branch_name(topic_id: uuid.UUID) -> str:
    """Deterministic head branch name pushed to the upstream GitHub repo for a
    topic's PR — distinct from the local topic branch name so a re-push after
    芝士 fixes something is an update, not a new branch."""
    return f"cheesex/{topic_id.hex[:8]}"


# ---- #188 §5.1's original client (App installation token, sync accept) ----

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
