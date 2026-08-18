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

import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

import httpx

from app.core.config import settings
from app.domain.agent.github_app import GitHubAppTokens

logger = logging.getLogger(__name__)

#: `no_checks` (人类授权动作前移, 2026-08-10) is NOT a flavour of success: it
#: means "no workflow will ever produce a check for this ref" (every workflow's
#: `paths-ignore` skipped it). The zero-check deadlock fix still holds — the
#: poller stops waiting — but a ref nothing checked has never had its tests
#: run, so it does not get the machine's免人 auto-merge. See
#: `_resolve_zero_checks` here and `_authorization_exception` in services.py.
CheckState = Literal["pending", "success", "failure", "no_checks"]


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
class PullRequestStatus:
    """One PR's live state, as the poller needs it.

    `merge_commit_sha` is ONLY meaningful when `merged` is True. GitHub
    populates it on OPEN pull requests too — with the sha of a throwaway
    *test-merge* commit that exists on no branch at all (verified against
    api.github.com on 2026-08-10: open PR astral-sh/ruff#27626 reports
    `merged: false` and a non-null `merge_commit_sha` which
    `compare <sha>...main` puts 2 commits AHEAD of main, i.e. not on it;
    merged PR astral-sh/ruff#20000 reports `merged: true` and a
    `merge_commit_sha` that compare puts squarely ON main). Trusting it
    unconditionally would hand stage 2 a sha no deploy run can ever match,
    and the card would wait for a deploy forever."""

    head_sha: str
    state: str  # "open" | "closed" — GitHub says "closed" for merged PRs too
    merged: bool
    merge_commit_sha: str | None = None
    merged_at: datetime | None = None
    #: The PR's OWN head branch (`head.ref`), as GitHub reports it. The poller
    #: re-pushes 芝士's fixes to this branch, and it cannot be derived from the
    #: topic id: the two lanes name it differently (`pr_branch_name` →
    #: `cheesex/<hex8>` for the personal-token lane, `ws.branch_for_topic` →
    #: `topic/<hex8>` for the App lane). Deriving it pushed App cards' fixes to
    #: a branch no PR was open on — the commit landed, the PR never saw it.
    #: Empty only for a fake/older payload; callers fall back to the derived
    #: name, which is what the personal-token lane always used.
    head_ref: str = ""
    #: GitHub's own answer to "can this be merged at all" — a pure git conflict
    #: verdict, computed in the background and **unrelated to CI, to branch
    #: protection, or to the repo's plan** (verified 2026-08-17 on this repo,
    #: which has no branch protection at all: #474/#200 report
    #: `false`/`dirty`, #509 `true`/`unstable`, #506 `true`/`clean`).
    #:
    #: `None` means GET /pulls/{n} did not know yet — GitHub computes it
    #: asynchronously and the request itself is what kicks that off. It is
    #: **"don't know", never "no conflict"**: treating it as either verdict
    #: would make the poller act on a coin flip right after every push.
    mergeable: bool | None = None
    #: `dirty` = conflicting. The rest (`clean` / `unstable` / `behind` /
    #: `blocked` / `unknown`) are kept raw and only ever compared against
    #: `dirty`: `blocked`/`behind` need branch protection, which this repo's
    #: plan cannot buy, so they never appear here and nothing may depend on
    #: them. Empty for a fake/older payload.
    mergeable_state: str = ""


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


@dataclass
class WorkflowRun:
    """One run of a workflow file, as the deploy gate needs to judge it.

    `conclusion` is GitHub's own word and is deliberately kept raw: only the
    literal `"success"` means the deploy actually succeeded. A run that merely
    *finished* (`status == "completed"`) can carry `cancelled` / `failure` /
    `skipped`, and `conclusion` is None while it is still running."""

    head_sha: str
    status: str  # "queued" | "in_progress" | "completed"
    conclusion: str | None
    created_at: datetime | None
    url: str
    branch: str = ""
    #: Actions run id — the handle for `workflow_run_jobs`. A run's conclusion
    #: alone cannot say whether it DID anything (see `WorkflowJob`).
    id: int = 0


@dataclass
class WorkflowJob:
    """One job inside a run, with its steps' conclusions.

    Exists because `conclusion == "success"` on a deploy run does NOT mean the
    deploy happened: `deploy-dev.yml`'s deploy job skips its own login+deploy
    steps for a docs-only commit and still reports success. A skipped step in
    an otherwise green job is the workflow saying "I deliberately did nothing",
    and the archive gate has to be able to see that."""

    name: str
    conclusion: str | None
    #: (step name, conclusion) in the workflow's own order.
    steps: list[tuple[str, str]]


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


def _parse_github_time(raw: object) -> datetime | None:
    """GitHub's `2026-08-09T22:03:59Z` → an aware UTC datetime (项目约定:
    never a naive one). Anything unparseable is None so the caller can fall
    back to "now" rather than blow up a poll tick on a format surprise."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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

    async def compare_files(
        self, *, owner: str, repo: str, base: str, head: str, token: str
    ) -> list[tuple[str, str]] | None:
        """(status, path) for every file `head` changes relative to its merge
        base with `base` — i.e. exactly the diff a PR from `head` onto `base`
        would show. `status` is GitHub's own word (`added` / `modified` /
        `removed` / `renamed` / ...).

        `base...head` (merge-base) semantics, NOT `base..head`, is the whole
        point: a topic branch that merged the base branch in (every re-push
        does — see `_repush_if_local_head_moved`) would otherwise report every
        file main moved as if this branch had touched it.

        None means GitHub gave no usable file list (diff too large — the
        compare API caps at 300 files — or an unexpected shape). Callers must
        treat None as "scope unknown" and fail CLOSED (ask a human), never as
        "nothing changed"."""
        ...

    async def pull_request_head_sha(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> str:
        """The PR's CURRENT head commit — re-fetched after 芝士's fix is
        pushed because that push moves it; polling a sha frozen at PR-open
        time would check the original (failing) commit forever."""
        ...

    async def pull_request_status(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> PullRequestStatus:
        """The PR's live state — head, open/closed, and whether SOMEONE ELSE
        already merged it. The poller reads this first thing every tick: a PR
        merged by hand on GitHub is invisible to every other signal here (its
        checks can be red, its branch unpushable), and without noticing it the
        card sits at `pr_open` forever."""
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

    async def recent_workflow_runs(
        self, *, owner: str, repo: str, workflow_file: str, token: str, limit: int = 30
    ) -> list[WorkflowRun]:
        """The workflow file's most recent runs across ALL commits (NOT filtered
        by `head_sha`), newest first — GitHub's own ordering.

        `workflow_run_state` answers "how did MY commit's deploy end"; this one
        exists for the follow-up question the 被顶替 check asks: "did some LATER
        deploy already ship my commit anyway". Implementations must return the
        raw `conclusion` per run and never collapse runs into one verdict."""
        ...

    async def workflow_run_jobs(
        self, *, owner: str, repo: str, run_id: int, token: str
    ) -> list[WorkflowJob]:
        """Every job of one run, with per-step conclusions — the only place
        GitHub says whether a green run actually DID its work. See
        `WorkflowJob` for why the run-level conclusion is not enough."""
        ...

    async def compare_status(
        self, *, owner: str, repo: str, base: str, head: str, token: str
    ) -> str | None:
        """GitHub's `status` for `base...head`: `identical`, `ahead`, `behind`
        or `diverged` — None when GitHub answers in an unexpected shape.

        Same endpoint as `compare_files`, different field: this one is about
        ancestry, not the file list. With `base` = another commit and `head` =
        ours, `behind`/`identical` means that other commit CONTAINS ours."""
        ...

    async def check_run_names(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> set[str]:
        """Names of every check-run that EXISTS on `ref` (#468 tier-2). The
        poller compares this against the required list: a required name not in
        this set has never reported, and its absence blocks the merge — a
        path-filtered or broken workflow must read as "still waiting", never
        as "nothing failed"."""
        ...

    async def update_branch(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> bool:
        """Merge the base branch into the PR's head (GitHub's Update branch
        button; #468 strict up-to-date). True = accepted (202); False = GitHub
        declined non-fatally (already up to date, or the head moved — 422),
        which the poller just retries next tick.

        `token` must be a WRITE mint — this endpoint commits to the head
        branch. Passing the read mint fails 403 and the failure is invisible
        on the card; see the implementation's docstring."""
        ...


_FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "stale"}
_OK_CONCLUSIONS = {"success", "neutral", "skipped"}


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


def _is_actions_check(entry: dict) -> bool:
    """Is this check-run / check-suite one of OURS — posted by GitHub Actions?

    Every check-run carries the app that created it, and a commit's check-runs
    are a shared bulletin board: copilot's reviewer, codecov and anything else
    installed on the repo post theirs alongside the workflows'. Only Actions
    runs are the CI this platform gates on, so only they get to colour a ref.

    Strict on purpose — a run with no recognisable `app` is treated as NOT
    ours. Guessing the other way would let one unidentifiable third-party run
    keep a card waiting (or, worse, sink it) forever, whereas being wrong in
    this direction lands on the `_resolve_zero_checks` path, which is already
    built to be careful about "nothing of ours is here".
    """
    app = entry.get("app")
    return isinstance(app, dict) and app.get("slug") == _GITHUB_ACTIONS_APP_SLUG


# --- 失败详情 (CI失败要把日志送到芝士眼前) ------------------------------------
#
# A red check used to reach 芝士 as `"shell-tests: failure"` and nothing else,
# so every CI failure cost a round trip: read the useless line, go dig the log
# out of GitHub by hand, only then start fixing. The backend already holds a
# token that can read both, so it digs once and puts the answer in the message.
#
# How many failed jobs get their log pulled. Beyond this only the headline
# survives — a PR that reddens ten jobs is one root cause repeated, not ten
# investigations, and every extra job is another GitHub round trip per poll.
_FAILURE_DETAIL_MAX_JOBS = 3
#: Hard stop on how much of one job's log is downloaded before giving up.
_LOG_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
#: How much of the download is KEPT — the tail, because that's where a failing
#: run ends up. A log longer than this streams past a sliding window rather
#: than being cut at the front, so the end is always the part we hold.
_LOG_TAIL_BYTES = 256 * 1024
#: Lines of run-up kept before the error marker. See `_error_excerpt`.
_LOG_CONTEXT_LINES = 15
_LOG_EXCERPT_MAX_CHARS = 1200
_LOG_LINE_MAX_CHARS = 200
_LOG_OUTPUT_FIELD_MAX_CHARS = 400

#: Every Actions log line is prefixed `2026-08-11T07:44:58.9065765Z `.
_LOG_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: Actions masks its own secrets as `***`, but a log can still echo a token
#: that the runner never knew was one (a curl of our API, a `gh auth status`).
#: Redact the shapes GitHub itself hands out before any of this enters a topic.
_TOKEN_SHAPE_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})"
)
#: An Actions check-run's html_url: `.../actions/runs/<run>/job/<job>`.
_ACTIONS_JOB_URL_RE = re.compile(r"/actions/runs/\d+/job/(?P<job>\d+)")


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
        # Only OUR CI gets a vote on this ref's colour. Other apps post
        # check-runs on the same commit and a red one of theirs is not a CI
        # failure: on 2026-08-17 PR #506 had test/lint/e2e/guards/... all green
        # and only `copilot-pull-request-reviewer` failed — the poller read the
        # ref as red, summoned 芝士 to fix something no commit of its can fix,
        # and the card could never merge, while GitHub itself called that PR
        # `clean`. `_resolve_zero_checks` has always narrowed the *suite*
        # question to the github-actions app for the same reason; this puts the
        # *run* question on the same footing.
        runs = [
            run
            for run in resp.json().get("check_runs", [])
            if isinstance(run, dict) and _is_actions_check(run)
        ]
        if runs:
            # Real check-runs showed up — whatever ambiguity there was about
            # this ref is resolved, forget any grace-period bookkeeping. (Only
            # OURS count: popping this on a stray third-party run would restart
            # the grace clock every tick, so `no_checks` could never settle.)
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
            job_id = _actions_job_id(run)
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
        """The error slice of one Actions job's log, or "" if unreachable."""
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=30.0
            ) as client:
                resp = await client.get(
                    f"{self._api_base}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
                    headers=self._headers(token),
                )
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
        return _error_excerpt([_clean_log_line(ln) for ln in text.splitlines()])

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
        if any(isinstance(s, dict) and _is_actions_check(s) for s in suites):
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

    async def compare_files(
        self, *, owner: str, repo: str, base: str, head: str, token: str
    ) -> list[tuple[str, str]] | None:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/compare/{base}...{head}",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝比较改动范围（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
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

    async def pull_request_status(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> PullRequestStatus:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}",
                headers=self._headers(token),
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝查 PR 状态（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        data = resp.json()
        merged = bool(data.get("merged"))
        raw_mergeable = data.get("mergeable")
        return PullRequestStatus(
            head_sha=data["head"]["sha"],
            head_ref=str(data["head"].get("ref") or ""),
            state=str(data.get("state") or ""),
            merged=merged,
            # Gated on `merged` on purpose — see PullRequestStatus's docstring
            # for what this field holds on an unmerged PR.
            merge_commit_sha=(data.get("merge_commit_sha") or None) if merged else None,
            merged_at=_parse_github_time(data.get("merged_at")) if merged else None,
            # Free of charge: the poller already makes this exact GET every
            # tick. Anything that isn't a real bool stays None ("don't know") —
            # see the field's docstring; this must not collapse to False.
            mergeable=raw_mergeable if isinstance(raw_mergeable, bool) else None,
            mergeable_state=str(data.get("mergeable_state") or ""),
        )

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

    async def recent_workflow_runs(
        self, *, owner: str, repo: str, workflow_file: str, token: str, limit: int = 30
    ) -> list[WorkflowRun]:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/actions/workflows/"
                f"{workflow_file}/runs",
                headers=self._headers(token),
                params={"per_page": max(1, min(limit, 100))},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝列出部署 workflow 的运行记录"
                f"（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        runs = resp.json().get("workflow_runs", [])
        out: list[WorkflowRun] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            sha = run.get("head_sha")
            if not isinstance(sha, str) or not sha:
                continue
            conclusion = run.get("conclusion")
            run_id = run.get("id")
            out.append(
                WorkflowRun(
                    head_sha=sha,
                    status=str(run.get("status") or ""),
                    conclusion=conclusion if isinstance(conclusion, str) else None,
                    created_at=_parse_github_time(run.get("created_at")),
                    url=str(run.get("html_url") or ""),
                    branch=str(run.get("head_branch") or ""),
                    id=run_id if isinstance(run_id, int) else 0,
                )
            )
        return out

    async def workflow_run_jobs(
        self, *, owner: str, repo: str, run_id: int, token: str
    ) -> list[WorkflowJob]:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝查部署运行的 job 列表"
                f"（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        out: list[WorkflowJob] = []
        for job in resp.json().get("jobs", []):
            if not isinstance(job, dict):
                continue
            steps: list[tuple[str, str]] = []
            for step in job.get("steps") or []:
                if isinstance(step, dict):
                    steps.append(
                        (str(step.get("name") or ""), str(step.get("conclusion") or ""))
                    )
            conclusion = job.get("conclusion")
            out.append(
                WorkflowJob(
                    name=str(job.get("name") or ""),
                    conclusion=conclusion if isinstance(conclusion, str) else None,
                    steps=steps,
                )
            )
        return out

    async def compare_status(
        self, *, owner: str, repo: str, base: str, head: str, token: str
    ) -> str | None:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/compare/{base}...{head}",
                headers=self._headers(token),
                # The file list is dead weight here — only `status` is read, and
                # a 300-file compare would otherwise ship megabytes per poll.
                params={"per_page": 1},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝比较两个 commit 的先后关系"
                f"（HTTP {resp.status_code}）：{resp.text[:300]}"
            )
        status = resp.json().get("status")
        return status if isinstance(status, str) else None

    async def check_run_names(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> set[str]:
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
            resp = await client.get(
                f"{self._api_base}/repos/{owner}/{repo}/commits/{ref}/check-runs",
                headers=self._headers(token),
                params={"per_page": 100},
            )
        if resp.status_code != 200:
            raise GitHubPrError(
                f"GitHub 拒绝列出 check-runs（HTTP {resp.status_code}）："
                f"{resp.text[:300]}"
            )
        runs = resp.json().get("check_runs") or []
        return {
            str(run.get("name"))
            for run in runs
            if isinstance(run, dict) and run.get("name")
        }

    async def update_branch(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> bool:
        """Merge the base branch into the PR's head branch (GitHub's "Update
        branch" button).

        `token` MUST be a WRITE mint. This endpoint writes a commit to the head
        branch, so a read-only token gets a flat 403 — which lands in the
        `raise` below, and the poller's `except GitHubPrError` swallows it into
        one log line *before* anything is written to the card. The card then
        looks byte-for-byte like a healthy one still waiting on CI, forever
        (2026-08-16/17: #498 and #499 each sat ~5.5 hours that way until a
        human clicked the button on github.com).
        """
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
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
            f"GitHub 拒绝更新 PR #{number} 的分支"
            f"（HTTP {resp.status_code}）：{resp.text[:300]}"
        )


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


def _actions_job_id(run: dict) -> int | None:
    """The Actions job id behind a check-run, or None when it isn't one.

    Read out of the `/actions/runs/<run>/job/<job>` URL rather than the
    check-run's own `id`. The two are equal today (verified against this
    repo's check-runs on 2026-08-11), but only the URL shape *says* the
    check-run is an Actions job — other apps publish check-runs too (codecov
    and friends), and handing one of their ids to the jobs API just 404s.
    """
    for key in ("html_url", "details_url"):
        match = _ACTIONS_JOB_URL_RE.search(str(run.get(key) or ""))
        if match:
            return int(match.group("job"))
    return None


def _failure_headline(run: dict) -> str:
    """One failed check-run's name, conclusion, link, and `output` text.

    `output.title`/`output.summary` are where GitHub's docs say the failure
    summary lives, and where the odd non-Actions check-run does put it — but
    Actions itself leaves both null and files the detail as annotations
    instead (verified 2026-08-11), which is why the log excerpt below is the
    part that actually carries the error. Included when present, skipped
    silently when not.
    """
    lines = [f"▸ {run.get('name', '?')}（{run.get('conclusion')}）"]
    url = run.get("html_url") or run.get("details_url")
    if url:
        lines.append(f"  {url}")
    output = run.get("output") or {}
    for key in ("title", "summary"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            text = _redact(value.strip())[:_LOG_OUTPUT_FIELD_MAX_CHARS]
            lines.extend(f"  {ln}" for ln in text.splitlines())
    return "\n".join(lines)


def _redact(text: str) -> str:
    return _TOKEN_SHAPE_RE.sub("<已脱敏>", text)


def _clean_log_line(raw: str) -> str:
    line = _LOG_TIMESTAMP_RE.sub("", raw.rstrip("\r\n"))
    line = _ANSI_RE.sub("", line)
    return _redact(line)[:_LOG_LINE_MAX_CHARS]


def _error_excerpt(lines: list[str]) -> str:
    """The interesting slice of a failed job's log.

    Grepping `##[error]` on its own is close to useless: what a failed shell
    step actually emits is `##[error]Process completed with exit code 1.`,
    and the line that says WHAT broke (`FAIL: the secret file was not handed
    over`) is the one right above it — verified against this repo's
    shell-tests job on 2026-08-11. So the excerpt is the LAST error marker
    plus its run-up, and a log with no marker at all (a cancelled or
    timed-out job) falls back to its tail.
    """
    if not lines:
        return ""
    marks = [i for i, line in enumerate(lines) if "##[error]" in line]
    end = marks[-1] + 1 if marks else len(lines)
    start = max(0, end - _LOG_CONTEXT_LINES - 1)
    text = "\n".join(line for line in lines[start:end] if line.strip())
    if len(text) > _LOG_EXCERPT_MAX_CHARS:
        text = "…" + text[-_LOG_EXCERPT_MAX_CHARS:]
    return text


async def _download_log_tail(client: httpx.AsyncClient, url: str) -> str:
    """Stream `url` keeping only its last `_LOG_TAIL_BYTES`.

    A sliding window rather than a read-then-cut: an Actions log has no size
    ceiling, the part worth reading is at the END, and a naive
    `resp.text[:cap]` would faithfully deliver the setup steps of a job that
    failed twenty minutes later.
    """
    window = bytearray()
    downloaded = 0
    async with client.stream("GET", url, headers={}) as resp:
        if resp.status_code != 200:
            return ""
        async for chunk in resp.aiter_bytes():
            downloaded += len(chunk)
            window.extend(chunk)
            if len(window) > _LOG_TAIL_BYTES:
                del window[: len(window) - _LOG_TAIL_BYTES]
            if downloaded >= _LOG_DOWNLOAD_MAX_BYTES:
                break
    return window.decode("utf-8", errors="replace")


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

    async def open_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        as_user_token: str | None = None,
    ) -> dict:
        """Open (or find the already-open) PR for a branch.

        Re-submitting a card for the same topic must not fail on GitHub's
        "a pull request already exists" — the existing PR IS this topic's PR.

        `as_user_token` is the requester's own user-to-server token, and it
        decides WHOSE PR this is: GitHub attributes a PR to whoever's
        credential created it, and an App token makes every PR on the platform
        belong to the bot — no avatar, no "opened by you", no filter-by-author
        for the person whose work it is. An App can never impersonate a user,
        so the only way to open it as them is to use their token. Falls back to
        the App on any failure: a PR that exists under the wrong name beats no
        PR at all, and the fallback is invisible to everything downstream.
        """
        app_token, _ = await self._tokens.write_token()
        payload = {"title": title, "head": head, "base": base, "body": body}
        async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:

            async def _create(token: str) -> httpx.Response:
                return await client.post(
                    self._url("/pulls"), json=payload, headers=self._headers(token)
                )

            resp = None
            if as_user_token:
                resp = await _create(as_user_token)
                if resp.status_code == 201:
                    return resp.json()
                if resp.status_code != 422 or "already exist" not in resp.text:
                    # Their token may simply not reach this repo (left the org,
                    # authorization revoked, App uninstalled for them). Not an
                    # error worth surfacing — the App opens it instead.
                    logger.info(
                        "opening PR as the requester failed (HTTP %s); "
                        "falling back to the App token",
                        resp.status_code,
                    )
                    resp = None
            if resp is None:
                resp = await _create(app_token)
                if resp.status_code == 201:
                    return resp.json()
            if resp.status_code == 422 and "already exist" in resp.text:
                listing = await client.get(
                    self._url("/pulls"),
                    params={"head": f"{self._owner}:{head}", "state": "open"},
                    headers=self._headers(app_token),
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
