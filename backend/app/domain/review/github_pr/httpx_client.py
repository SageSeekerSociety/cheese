"""`HttpxGitHubPrClient` — the real client, and the one the process hands out.

Plain REST calls against api.github.com with a token the caller passes per call,
plus the two GraphQL calls the merge queue needs. Behind `default_client()` —
one process-wide instance, which is why the zero-check-runs grace period is
bookkeeping on `self` that outlives a poll.
"""

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import httpx
from cachetools import TTLCache

from app.core import forge_quota
from app.core.config import settings
from app.core.forge_http import forge_client
from app.core.sentences import say
from app.domain.review.github_pr._shared import (
    _FAILED_CONCLUSIONS,
    _NO_VERDICT_CONCLUSIONS,
    CheckState,
    GitHubPrError,
    MergeResult,
    PullRequest,
    _enqueue_if_required,
    _github_message,
    _queue_pr,
    _queue_request,
    _refused,
    logger,
)
from app.domain.review.github_pr.failure_logs import (
    _FAILURE_DETAIL_MAX_JOBS,
    _LOG_TAIL_BYTES,
    _clean_log_line,
    _download_log_tail,
    _error_excerpt,
    _failure_headline,
)
from app.domain.review.github_pr.branch_comparisons import read_branch_comparisons
from app.domain.review.github_pr.protocol import (
    BranchComparisonResult,
    BranchPair,
    GitHubPrClient,
)
from app.domain.review.github_pr_status import (
    PullRequestStatus,
    parse_pull_request_status,
    settled,
)
from app.domain.review.pr_signals import (
    ReviewSignal,
    review_comment_signal,
    review_signal,
)
from app.domain.review.superseded_runs import actions_job_id, drop_superseded


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


#: How long a ref may sit at "zero check-runs, no github-actions check-suite
#: either" before we trust that as "genuinely nothing will ever check this"
#: rather than "GitHub hasn't caught up with a fresh push yet". See
#: `HttpxGitHubPrClient._resolve_zero_checks` for the reasoning — this is a
#: defense-in-depth backstop on top of the check-suites signal, which is
#: already fast (observed same-second as the triggering push in practice),
#: not the primary mechanism.
_ZERO_CHECKS_GRACE_SECONDS = 120.0

_GITHUB_ACTIONS_APP_SLUG = "github-actions"
_FAILURE_LOG_CACHE_SECONDS = 300.0
_FAILURE_LOG_CACHE_ENTRIES = 256


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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._transport = transport
        self._sleep = sleep
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
        # The process-wide client owns these bounded excerpts. Check-runs are
        # still read live; rerunning a job gives its new log a different key.
        self._job_logs = TTLCache(
            maxsize=_FAILURE_LOG_CACHE_ENTRIES,
            ttl=_FAILURE_LOG_CACHE_SECONDS,
            timer=clock,
        )

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
        async with forge_client(transport=self._transport, timeout=30.0) as client:
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
        raise _refused("githubRefusedOpenPr", resp)

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

    async def _check_runs(
        self,
        owner: str,
        repo: str,
        ref: str,
        token: str,
        key: str = "githubRefusedCheckRuns",
    ) -> list[dict]:
        """`/commits/{ref}/check-runs` 的读，去掉被顶替运行留下的那些。

        去掉这一步（`superseded_runs`）只在真的有「跑完了、结论不绿」的 check
        run 时才多读一次 Actions 的运行列表 —— 全绿和还在跑的稳态仍是一问一答，
        轮询不会凭空多一次往返。
        """
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/commits/{ref}/check-runs",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise _refused(key, resp)
        runs = resp.json().get("check_runs") or []
        kept = [run for run in runs if isinstance(run, dict)]
        return await drop_superseded(
            kept,
            self._api_base,
            owner,
            repo,
            ref,
            self._headers(token),
            self._transport,
        )

    async def check_state(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> tuple[CheckState, str]:
        runs = await self._check_runs(
            owner, repo, ref, token, "githubRefusedCheckStatus"
        )
        if runs:
            # Real check-runs showed up — whatever ambiguity there was about
            # this ref is resolved, forget any grace-period bookkeeping.
            self._zero_checks_first_seen.pop((owner, repo, ref), None)
            state, tail = _summarize_runs(runs)
            if state == "failure":
                # The headline stays the first line (it is what lands on the
                # card's one-line note); everything the reader actually needs
                # to start fixing is appended below it.
                tail += await self._failure_detail(
                    owner=owner, repo=repo, token=token, runs=runs
                )
            return state, tail
        return await self._resolve_zero_checks(
            owner=owner, repo=repo, ref=ref, token=token
        )

    async def _failure_detail(
        self, *, owner: str, repo: str, token: str, runs: list[dict]
    ) -> str:
        """Everything beyond `"job: failure"` — per failed job, its Actions
        page link, whatever the check-run's own `output` carries, and the
        error slice of its log.

        Never raises and never returns a partial-looking failure: a poll tick
        that can't reach the logs must still deliver the headline it already
        has, so every fetch degrades to "" rather than propagating.
        """
        failed = [
            run
            for run in runs
            if run.get("status") == "completed"
            and run.get("conclusion") in _FAILED_CONCLUSIONS
        ]
        blocks: list[str] = []
        for run in failed[:_FAILURE_DETAIL_MAX_JOBS]:
            block = _failure_headline(run)
            job_id = actions_job_id(run)
            if job_id is not None:
                excerpt = await self._job_log_excerpt(
                    owner=owner, repo=repo, job_id=job_id, token=token
                )
                if excerpt:
                    block += "\n" + "\n".join(
                        f"  {line}" for line in excerpt.splitlines()
                    )
            blocks.append(block)
        hidden = len(failed) - len(blocks)
        if hidden > 0:
            blocks.append(f"（另有 {hidden} 个失败的 job 未展开）")
        return "\n\n" + "\n\n".join(blocks) if blocks else ""

    async def _job_log_excerpt(
        self, *, owner: str, repo: str, job_id: int, token: str
    ) -> str:
        """The error slice of one completed job's log, or "" if unreachable."""
        url = f"{self._api_base}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        headers = self._headers(token)
        installation = forge_quota.installation_of(
            httpx.Request("GET", url, headers=headers)
        )
        # Unlike an ETag read, a cache hit asks no authorization question.
        # Keep excerpts credential-scoped even within the same installation.
        key = (
            (installation, owner, repo, job_id, hashlib.sha256(token.encode()).digest())
            if installation is not None
            else None
        )
        if key is not None and (kept := self._job_logs.get(key)) is not None:
            return kept
        try:
            async with forge_client(transport=self._transport, timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code in (301, 302, 303, 307, 308):
                    # GitHub answers with a 302 to a pre-signed blob URL on a
                    # THIRD-PARTY host (`*.blob.core.windows.net`). The
                    # redirect is followed by hand, with the headers dropped,
                    # precisely because httpx would otherwise replay
                    # `Authorization: Bearer <installation token>` to that
                    # host. The pre-signed URL needs no auth of ours.
                    location = resp.headers.get("location", "")
                    if not location:
                        return ""
                    text = await _download_log_tail(client, location)
                elif resp.status_code == 200:
                    text = resp.text[-_LOG_TAIL_BYTES:]
                else:
                    return ""
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.info("could not read job %s log: %s", job_id, exc)
            return ""
        excerpt = _error_excerpt([_clean_log_line(ln) for ln in text.splitlines()])
        if key is not None and excerpt:
            self._job_logs[key] = excerpt
        return excerpt

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
        this returns "no_checks", as a backstop against the residual sliver of
        race between "we just pushed" and "GitHub has processed the push far
        enough to create even the check-suite". Losing this bookkeeping
        (process restart) only restarts the grace period — it can never
        shorten it, so it can't turn into a false "no_checks".

        人类授权动作前移 (2026-08-10): the settled verdict is `no_checks`, not
        `success`. Both end the wait (the deadlock fix is intact), but only
        `success` means checks actually ran and passed — see `CheckState`.
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
        return "no_checks", "没有任何 workflow 会对这次改动触发检查（真 CI 从未跑过）"

    async def _check_suites(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> list[dict]:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/commits/{ref}/check-suites",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise _refused("githubRefusedCheckSuites", resp)
        return resp.json().get("check_suites", [])

    async def compare_files(
        self, *, owner: str, repo: str, base: str, head: str, token: str
    ) -> list[tuple[str, str]] | None:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/compare/{base}...{head}",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise _refused("githubRefusedCompare", resp)
        payload = resp.json()
        files = payload.get("files")
        if not isinstance(files, list):
            # Truncated/oversized compare — GitHub omits `files` entirely.
            # "Scope unknown" is not "scope unchanged" (see the Protocol).
            return None
        out: list[tuple[str, str]] = []
        for item in files:
            if not isinstance(item, dict):
                return None
            filename = item.get("filename")
            if not isinstance(filename, str):
                return None
            out.append((str(item.get("status") or ""), filename))
        return out

    async def pull_request_head_sha(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> str:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}",
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            raise _refused("githubRefusedPrStatus", resp)
        return resp.json()["head"]["sha"]

    async def pull_request_status(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> PullRequestStatus:
        return await settled(
            lambda: self._read_pull_request(owner, repo, number, token), self._sleep
        )

    async def _read_pull_request(
        self, owner: str, repo: str, number: int, token: str
    ) -> PullRequestStatus:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}",
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            raise _refused("githubRefusedPrStatus", resp)
        return parse_pull_request_status(resp.json())

    async def merge_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        token: str,
        commit_title: str | None = None,
        commit_message: str | None = None,
        sha: str | None = None,
    ) -> MergeResult:
        method = self._merge_method or settings.accept_pr_merge_method
        body: dict = {"merge_method": method}
        if commit_title:
            body["commit_title"] = commit_title
        if commit_message:
            body["commit_message"] = commit_message
        if sha:
            # 合的是人看到的那个 commit (#718): GitHub 409s when the PR head no
            # longer matches, instead of merging whatever is there now.
            body["sha"] = sha
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            queued = await _enqueue_if_required(
                client, self._api_base, self._headers(token), owner, repo, number, sha
            )
            if queued is not None:
                return queued
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
            # branch protection. 409 = the head moved from the `sha` the
            # caller vouched for (or the base conflicts) — the "refresh and
            # look again" case, flagged as `stale_head`. Neither is a
            # GitHubPrError: the reason travels with the result so the caller
            # can put it on the card instead of retrying blind.
            return MergeResult(
                blocked_reason=_github_message(resp),
                stale_head=resp.status_code == 409,
            )
        raise _refused("githubRefusedMerge", resp)

    async def merge_queue_entry(self, *, owner, repo, number, token) -> bool:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            pr = await _queue_pr(
                client, self._api_base, self._headers(token), owner, repo, number
            )
        return bool(pr.get("mergeQueueEntry"))

    async def merge_group_run_started(
        self, *, owner, repo, number, token, since: datetime
    ) -> bool:
        """Has a `merge_group` workflow run been created for PR `number` since
        `since` (the moment it entered the queue)? Older runs of the same PR belong
        to an earlier entry and do not count.

        GitHub names the queue's temporary branch
        `gh-readonly-queue/<base>/pr-<n>-<sha>`, and the runs it triggers carry
        that name as `head_branch`. Read-only: the newest 100 runs of the event
        are enough, a stalled entry's run is either among them or not yet there."""
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/actions/runs",
                headers=self._headers(token),
                params={
                    "event": "merge_group",
                    "per_page": 100,
                    "created": f">={since.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ}",
                },
            )
        if resp.status_code != 200:
            raise _refused("githubRefusedCheckRuns", resp)
        marker = f"/pr-{number}-"
        return any(
            marker in str(run.get("head_branch") or "")
            for run in resp.json().get("workflow_runs") or []
            if isinstance(run, dict)
        )

    async def dequeue_pull_request(self, *, owner, repo, number, token) -> None:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            pr = await _queue_pr(
                client, self._api_base, self._headers(token), owner, repo, number
            )
            if pr.get("mergeQueueEntry"):
                await _queue_request(
                    client,
                    self._api_base,
                    self._headers(token),
                    "mutation($id:ID!){dequeuePullRequest(input:{id:$id}){clientMutationId}}",
                    {"id": pr["id"]},
                )

    async def list_check_runs(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> list[dict]:
        return await self._check_runs(owner, repo, ref, token)

    async def branch_comparisons(
        self,
        *,
        owner: str,
        repo: str,
        pairs: Sequence[BranchPair],
        token: str,
    ) -> dict[BranchPair, BranchComparisonResult]:
        return await read_branch_comparisons(
            api_base=self._api_base,
            transport=self._transport,
            headers=self._headers(token),
            owner=owner,
            repo=repo,
            pairs=pairs,
        )

    async def compare_status(
        self, *, owner: str, repo: str, base: str, head: str, token: str
    ) -> str | None:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/compare/{base}...{head}",
                headers=self._headers(token),
                # The file list is dead weight here — only `status` is read, and
                # a 300-file compare would otherwise ship megabytes per poll.
                params={"per_page": 1},
            )
        if resp.status_code != 200:
            raise _refused("githubRefusedCommitOrder", resp)
        status = resp.json().get("status")
        return status if isinstance(status, str) else None

    async def review_signals(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        token: str,
        with_comments: bool = True,
    ) -> list[ReviewSignal]:
        base = f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}"
        out: list[ReviewSignal] = []
        for path, builder in (
            ("reviews", review_signal),
            *((("comments", review_comment_signal),) if with_comments else ()),
        ):
            for item in await self._review_page(f"{base}/{path}", token=token):
                signal = builder(item)
                if signal is not None:
                    out.append(signal)
        return out

    async def _review_page(self, url: str, *, token: str) -> list[dict]:
        """One page of a reviews/comments listing, or [] if unreadable.

        Degrading to [] rather than raising is the whole reason this is its
        own method: the reviews API is the LAST thing a poll tick reads, and a
        403 on it must not take the CI failure the same tick already found
        down with it.
        """
        try:
            async with forge_client(transport=self._transport, timeout=30.0) as c:
                resp = await c.get(
                    url, headers=self._headers(token), params={"per_page": 100}
                )
            if resp.status_code != 200:
                logger.info("could not read %s: HTTP %s", url, resp.status_code)
                return []
            payload = resp.json()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            logger.info("could not read %s: %s", url, exc)
            return []
        return (
            [item for item in payload if isinstance(item, dict)]
            if (isinstance(payload, list))
            else []
        )

    async def update_branch(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> bool:
        async with forge_client(transport=self._transport, timeout=30.0) as client:
            resp = await client.put(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}/update-branch",
                headers=self._headers(token),
            )
        if resp.status_code == 202:
            return True
        if resp.status_code == 422:
            # Already up to date, or the head moved under us — both are
            # non-fatal; the next poll re-evaluates from scratch.
            return False
        raise GitHubPrError(
            say(
                "githubRefusedUpdateBranch",
                number=number,
                status=resp.status_code,
                reply=resp.text[:300],
            )
        )


def _summarize_runs(runs: list[dict]) -> tuple[CheckState, str]:
    if not runs:
        return "pending", "还没有检查报告"
    pending_names = []
    failed = []
    no_verdict = []
    for run in runs:
        name = run.get("name", "?")
        if run.get("status") != "completed":
            pending_names.append(name)
            continue
        conclusion = run.get("conclusion")
        if conclusion in _FAILED_CONCLUSIONS:
            failed.append(f"{name}: {conclusion}")
        elif conclusion in _NO_VERDICT_CONCLUSIONS:
            no_verdict.append(f"{name}: {conclusion}")
    if failed:
        return "failure", "、".join(failed[:10])
    if pending_names or no_verdict:
        # 「已取消」不是结论，所以这一档不能报成通过；但它也不是「没通过」，
        # 所以要说清楚它是什么（卡面照这句标「已取消」）。
        parts = []
        if pending_names:
            parts.append("等待中：" + "、".join(pending_names[:10]))
        if no_verdict:
            parts.append("已取消：" + "、".join(no_verdict[:10]))
        return "pending", "；".join(parts)
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
