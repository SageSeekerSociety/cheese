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
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from app.core.config import settings
from app.domain.agent.github_app import GitHubAppTokens

CheckState = Literal["pending", "success", "failure"]


@dataclass
class PullRequest:
    number: int
    url: str
    head_sha: str
    #: True when this PR was already open on the head branch and we adopted it
    #: instead of creating one (GitHub 422 "a pull request already exists").
    #: Callers use it for wording only — a claimed PR is this topic's PR and is
    #: driven through CI/merge/deploy exactly like a freshly opened one.
    already_existed: bool = False


@dataclass
class MergeResult:
    """Outcome of one merge attempt.

    Exactly one side is set: `sha` when GitHub actually merged, else
    `blocked_reason` — a human-readable, secret-free explanation of why
    GitHub refused (405/409). The refusal MUST carry a reason: returning a
    bare None here is what hid the squash-only bug for half a day (405 on a
    disabled merge_method never clears, so "just retry next tick" looped
    forever with nothing written anywhere)."""

    sha: str | None = None
    blocked_reason: str | None = None


class GitHubPrError(RuntimeError):
    """A GitHub API call failed outright (bad token, repo gone, rate limit,
    GitHub outage, ...). The caller treats this as "mechanism unavailable"
    and degrades — never as "the work is bad"."""


def _github_message(resp: httpx.Response) -> str:
    """`HTTP 405：Merge commits are not allowed on this repository` — GitHub's
    own explanation, which is the whole point (the status code alone doesn't
    tell you the merge_method is disabled). Falls back to the raw body when
    the response isn't the usual `{"message": ...}` error shape."""
    detail = ""
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = str(payload.get("message") or "")
    if not detail:
        detail = resp.text
    if not detail:
        return f"HTTP {resp.status_code}"
    return f"HTTP {resp.status_code}：{detail[:300]}"


def _is_pr_already_exists(resp: httpx.Response) -> bool:
    """True only for GitHub's "a pull request already exists for X" 422.

    Deliberately narrow. 422 is `POST /pulls`'s catch-all Validation Failed
    and covers plenty of genuine, unrecoverable mistakes — a base branch that
    doesn't exist, head == base, no commits between the two. Those must keep
    degrading to the local merge path. Only the structured error entry
    (`resource: "PullRequest"` + an "already exists" message) says the PR we
    were about to open is already sitting there:

        {"message": "Validation Failed",
         "errors": [{"resource": "PullRequest", "code": "custom",
                     "message": "A pull request already exists for owner:branch."}]}

    Matching on the structured `errors[]` rather than a substring of the whole
    body also keeps a branch or PR title that happens to contain the words
    "already exists" from being read as this case.
    """
    if resp.status_code != 422:
        return False
    try:
        payload = resp.json()
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(err, dict)
        and err.get("resource") == "PullRequest"
        and "already exist" in str(err.get("message") or "").lower()
        for err in errors
    )


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
    ) -> PullRequest:
        """Open a PR for `head` — or adopt the one that is already open on it.

        GitHub answers 422 "a pull request already exists for <owner>:<head>"
        when the branch already has an open PR. That is NOT the mechanism
        being unavailable: the head branch is derived from the topic id
        (`pr_branch_name`), so the PR that exists IS this topic's PR, and the
        caller degrading to a local merge + direct push over it is what
        produces an orphan PR (open forever, CI burning, never merged, code
        already on main by another route). Implementations must resolve that
        case into the existing PR and set `already_existed=True`; every OTHER
        failure — including every other 422 — still raises `GitHubPrError`
        so the caller degrades as before."""
        ...

    async def check_state(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> tuple[CheckState, str]:
        """(state, human-readable tail) for every check-run on `ref` (a commit
        SHA). All-queued → pending; any real failure → failure (even with
        others still running — no point waiting out a doomed run); all
        completed + none failed → success.

        Zero check-runs is ambiguous on its own — it means either "nothing
        will ever check this ref" (e.g. a docs-only change every workflow's
        paths-ignore skips) or "GitHub hasn't created the check-runs yet"
        (freshly pushed/re-pushed, still racing the webhook). Implementations
        must resolve that ambiguity themselves rather than always returning
        one side — see `HttpxGitHubPrClient` for how."""
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
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> MergeResult:
        """Merge the PR. `commit_title`/`commit_message` are GitHub's two
        squash-commit fields (title line / body) — see the caller in
        `review/services.py` for why both are passed explicitly.

        Returns the merge commit SHA on success, else a `blocked_reason` the
        poller surfaces on the card — never a silent "try again later"."""
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


#: How long a ref may sit at "zero check-runs, no github-actions check-suite
#: either" before we trust that as "genuinely nothing will ever check this"
#: rather than "GitHub hasn't caught up with a fresh push yet". See
#: `HttpxGitHubPrClient._resolve_zero_checks` for the reasoning — this is a
#: defense-in-depth backstop on top of the check-suites signal, which is
#: already fast (observed same-second as the triggering push in practice),
#: not the primary mechanism.
_ZERO_CHECKS_GRACE_SECONDS = 120.0

_GITHUB_ACTIONS_APP_SLUG = "github-actions"


class HttpxGitHubPrClient:
    """Real implementation — plain REST calls against api.github.com."""

    def __init__(
        self,
        api_base: str = "https://api.github.com",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        zero_checks_grace_s: float = _ZERO_CHECKS_GRACE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        merge_method: str | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._transport = transport
        # None → read `settings.accept_pr_merge_method` at merge time (not at
        # construction): `default_client()` builds one process-wide singleton,
        # so binding the value here would freeze whatever settings looked like
        # at first use.
        self._merge_method = merge_method
        self._zero_checks_grace_s = zero_checks_grace_s
        self._clock = clock
        # ref → monotonic time it was first seen with zero check-runs AND no
        # github-actions check-suite. Instance-scoped (not persisted): the
        # process-wide singleton from `default_client()` lives for the whole
        # poller's lifetime, so this survives across polls the same way a DB
        # column would — losing it on restart only ever makes the grace
        # period start over, never shortens it, so it can't cause a
        # premature "success".
        self._zero_checks_first_seen: dict[tuple[str, str, str], float] = {}

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
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.post(
                f"{self._api_base}/repos/{owner}/{repo}/pulls",
                headers=self._headers(token),
                json={"title": title, "body": body, "head": head, "base": base},
            )
            if resp.status_code == 201:
                data = resp.json()
                return PullRequest(
                    number=data["number"],
                    url=data["html_url"],
                    head_sha=data["head"]["sha"],
                )
            if _is_pr_already_exists(resp):
                claimed = await self._find_open_pull_request(
                    client=client,
                    owner=owner,
                    repo=repo,
                    head=head,
                    base=base,
                    token=token,
                )
                # Falls through to the raise when the listing can't confirm it
                # (API hiccup, or the PR was closed between the two calls):
                # degrading on an unverified guess is worse than degrading on
                # the error GitHub actually gave us.
                if claimed is not None:
                    return claimed
        raise GitHubPrError(
            f"GitHub 拒绝开 PR（HTTP {resp.status_code}）：{resp.text[:300]}"
        )

    async def _find_open_pull_request(
        self,
        *,
        client: httpx.AsyncClient,
        owner: str,
        repo: str,
        head: str,
        base: str,
        token: str,
    ) -> PullRequest | None:
        """The open PR on `owner:head`, or None if it can't be established.

        Never raises: the only caller is already holding a GitHubPrError it
        can raise instead, and a failure here must not turn a plain degrade
        into a different-looking crash."""
        try:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/pulls",
                headers=self._headers(token),
                params={"head": f"{owner}:{head}", "state": "open", "per_page": 100},
            )
            if resp.status_code != 200:
                return None
            items = resp.json()
            if not isinstance(items, list) or not items:
                return None
            # One head branch can carry open PRs against several bases; prefer
            # the one we were trying to open, fall back to the only/first one.
            chosen = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and item.get("base", {}).get("ref") == base
                ),
                items[0],
            )
            return PullRequest(
                number=chosen["number"],
                url=chosen["html_url"],
                head_sha=chosen["head"]["sha"],
                already_existed=True,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return None

    async def check_state(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> tuple[CheckState, str]:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
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
        if runs:
            # Real check-runs showed up — whatever ambiguity there was about
            # this ref is resolved, forget any grace-period bookkeeping.
            self._zero_checks_first_seen.pop((owner, repo, ref), None)
            return _summarize_runs(runs)
        return await self._resolve_zero_checks(
            owner=owner, repo=repo, ref=ref, token=token
        )

    async def _resolve_zero_checks(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> tuple[CheckState, str]:
        """Zero check-runs is ambiguous — this ref may genuinely never get one
        (e.g. a docs-only push every workflow's `paths-ignore` skips), or
        GitHub may simply not have created them yet (a push/re-push race:
        the poller can query within moments of a push, before GitHub has
        caught up).

        The two cases are told apart with the check-suites API
        (`GET .../commits/{ref}/check-suites`), not check-runs: GitHub
        creates a check-suite for the "github-actions" app as soon as a push
        matches ANY workflow's trigger — observed in this repo landing in
        the same second as the triggering push, well before that workflow's
        individual check-runs exist. So:

        - a "github-actions" suite present (any status) → a workflow *did*
          match → runs are on the way → stay pending, no matter how long
          check-runs stays empty.
        - no "github-actions" suite at all → nothing matched (paths-ignore
          skipped every workflow) → check-runs will stay empty forever.

        Other apps' suites (e.g. codecov) are ignored for this decision —
        they don't run our CI and can sit "queued" indefinitely on their own
        (observed on a real docs-only PR), which would make "any suite at
        all" the wrong signal to use here.

        Because check-suite creation, while fast, isn't provably
        instantaneous, "no github-actions suite" alone isn't trusted
        immediately either — it must hold for `_zero_checks_grace_s`
        (default 120s, two poll rounds at the default 60s interval) before
        this returns "success", as a backstop against the residual sliver of
        race between "we just pushed" and "GitHub has processed the push far
        enough to create even the check-suite". Losing this bookkeeping
        (process restart) only restarts the grace period — it can never
        shorten it, so it can't turn into a false "success".
        """
        suites = await self._check_suites(owner=owner, repo=repo, ref=ref, token=token)
        key = (owner, repo, ref)
        if any(
            s.get("app", {}).get("slug") == _GITHUB_ACTIONS_APP_SLUG for s in suites
        ):
            self._zero_checks_first_seen.pop(key, None)
            return "pending", "workflow 已被触发，检查还在准备中"

        first_seen = self._zero_checks_first_seen.get(key)
        now = self._clock()
        if first_seen is None:
            self._zero_checks_first_seen[key] = now
            return "pending", "还没有检查报告"
        if now - first_seen < self._zero_checks_grace_s:
            return "pending", "还没有检查报告"
        self._zero_checks_first_seen.pop(key, None)
        return "success", "没有任何 workflow 会对这次改动触发检查，判定为通过"

    async def _check_suites(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> list[dict]:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/commits/{ref}/check-suites",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝查 check-suites"
                f"（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        return resp.json().get("check_suites", [])

    async def pull_request_head_sha(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> str:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
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
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> MergeResult:
        method = self._merge_method or settings.accept_pr_merge_method
        body: dict = {"merge_method": method}
        if commit_title:
            body["commit_title"] = commit_title
        if commit_message:
            body["commit_message"] = commit_message
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.put(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}/merge",
                headers=self._headers(token),
                json=body,
            )
        if resp.status_code == 200:
            return MergeResult(sha=resp.json().get("sha"))
        if resp.status_code in (405, 409):
            # 405 = GitHub REFUSED the merge, and NOT only for transient
            # reasons: a merge_method the repo disabled (this repo is
            # squash-only) refuses forever, as do draft PRs and unsatisfied
            # branch protection. 409 = the head moved under us / conflict.
            # Both are safe to retry next poll, so this is not a
            # GitHubPrError — but the reason travels with it so the poller
            # can put it on the card instead of retrying blind.
            return MergeResult(blocked_reason=_github_message(resp))
        raise GitHubPrError(
            f"GitHub 拒绝合并 PR（HTTP {resp.status_code}）：{resp.text[:300]}"
        )

    async def workflow_run_state(
        self, *, owner: str, repo: str, workflow_file: str, head_sha: str, token: str
    ) -> tuple[CheckState, str]:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
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
        """Merge the PR using `settings.accept_pr_merge_method` — same reason
        as the 两阶段采纳 client above: this used to hardcode a merge commit,
        which a squash-only repo (ours) refuses with 405 forever.

        405 (not mergeable) raises GitHubPRMergeBlocked — the caller routes it
        to the existing conflict-resolution flow. Anything else is a plain
        GitHubPRError.
        """
        token, _ = await self._tokens.write_token()
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.put(
                self._url(f"/pulls/{number}/merge"),
                json={
                    "merge_method": settings.accept_pr_merge_method,
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
