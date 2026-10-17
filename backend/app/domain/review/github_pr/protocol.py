"""`GitHubPrClient` — the contract the accept click and the poller drive.

The lowercase-pr Protocol: one PR read or written per call with the caller's
own token, rather than a client bound to one repo. `HttpxGitHubPrClient` is the
real implementation behind it, and `default_client()` hands that one out.

Each method's docstring is the spec its callers rely on; read the one you are
about to change.
"""

from typing import Protocol

from app.domain.review.github_pr._shared import (
    CheckState,
    MergeResult,
    PullRequest,
)
from app.domain.review.github_pr_status import PullRequestStatus
from app.domain.review.pr_signals import ReviewSignal


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
        card waits forever."""
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
        sha: str | None = None,
    ) -> MergeResult:
        """Merge the PR. `commit_title`/`commit_message` are GitHub's two
        squash-commit fields (title line / body) — see the caller in
        `review/services.py` for why both are passed explicitly.

        `sha` is the merge API's own guard (#718): "SHA that pull request
        head must match to allow merge". Callers pass the head the human
        actually saw, so a push that lands between the click and the merge
        makes GitHub answer 409 (`stale_head` on the result) instead of
        merging a commit nobody looked at.

        Queue entry returns `queued=True` without a merge SHA. Otherwise the
        result carries the merged SHA or the refusal reason."""
        ...

    async def list_check_runs(
        self, *, owner: str, repo: str, ref: str, token: str
    ) -> list[dict]:
        """The raw check-runs on `ref`, one dict per run with at least
        `name` / `status` / `conclusion` — the input
        `merge_state.compute_merge_state` reads. Distinct from `check_state`,
        which collapses them into one verdict and fetches failure logs; this
        one translates nothing."""
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

    async def review_signals(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        token: str,
        with_comments: bool = True,
    ) -> list[ReviewSignal]:
        """Everything a human said on this PR that 芝士 may have to act on:
        `CHANGES_REQUESTED` / `COMMENTED` reviews, plus the inline comments
        when `with_comments` (the caller skips that request when the PR's own
        payload says it has none).

        Approvals are deliberately NOT returned — an approval asks the agent
        for nothing, and a card that keeps nudging on one would be spam.

        Never raises for a review it could not read: a poll tick that can't
        reach the reviews API must still deliver the CI failure it already
        has, so failures degrade to a short list rather than propagating.
        """
        ...

    async def update_branch(
        self, *, owner: str, repo: str, number: int, token: str
    ) -> bool:
        """Merge the base branch into the PR's head (GitHub's Update branch
        button; #468 strict up-to-date). True = accepted (202); False = GitHub
        declined non-fatally (already up to date, or the head moved — 422),
        which the poller just retries next tick."""
        ...
