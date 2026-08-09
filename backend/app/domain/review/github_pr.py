"""GitHub PR client for 两阶段采纳 (PR迭代式, 2026-08-09).

A small Protocol so `AcceptService.accept()` and `PrPollRunner` (scheduler)
can be driven by a fake in tests without touching real GitHub — same shape as
`app.domain.agent.github_app.GitHubAppTokens`, but for the write-side PR
operations (open/merge) plus read-side check/workflow status, both keyed off
the approving human's own connected token, not the App's installation token.

Not implemented here: opening the App's own write-scoped installation token
(#188 §5.1 "reserved for PR-based accept") — per 2026-08-09 拍板 this feature
uses the approver's connected GitHub account token instead, so that minting
path stays untouched.
"""

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

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
