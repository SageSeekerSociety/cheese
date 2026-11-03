"""A pull request's live state as GitHub reports it, read until GitHub has
worked out whether it can merge."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.repository.identity import noreply_email


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
    #: `cheesex/<hex8>` for the personal-token lane, `ws.branch_for_tree` →
    #: `topic/<hex8>` for the App lane). Deriving it pushed App cards' fixes to
    #: a branch no PR was open on — the commit landed, the PR never saw it.
    #: Empty only for a fake/older payload; callers fall back to the derived
    #: name, which is what the personal-token lane always used.
    head_ref: str = ""
    base_ref: str = ""
    #: The commit the base branch is at as the forge answers (`base.sha`).
    #: With `head_sha` it names the pair a test merge is about
    #: (`merge_check`); a push to either side is a new pair.
    base_sha: str = ""
    #: GitHub's `mergeable`. **Three-valued on purpose**: True = git can merge
    #: it, False = it conflicts with the base, and None = GitHub has not
    #: finished computing it yet (it does that asynchronously on the first
    #: read after any push). Only `False` is a conflict — treating None as one
    #: would announce a conflict on every freshly-pushed PR, and treating it as
    #: True would silently drop a real one.
    mergeable: bool | None = None
    #: GitHub REST's `mergeable_state` — the lowercase twin of GraphQL's
    #: `mergeStateStatus` (clean/unstable/blocked/behind/dirty/draft/unknown/
    #: has_hooks…). Kept RAW on purpose: the verdict a card shows is computed
    #: in one place (`merge_state.compute_merge_state`, #718), and this field
    #: is that function's input, not a judgement of its own. None = the
    #: payload didn't carry it (fake/older payload) — distinct from the
    #: string "unknown", which is GitHub saying it hasn't computed one yet.
    mergeable_state: str | None = None
    #: How many INLINE review comments the PR carries, from the PR payload
    #: itself. The poller uses it to decide whether the extra request that
    #: lists those comments is worth making — most ticks it is 0.
    review_comment_count: int = 0
    #: GitHub's own `draft` flag. `mergeable_state == "draft"` usually says the
    #: same thing, but the two are separate fields in the payload and the
    #: verdict must be draft-blocked when EITHER says so
    #: (`merge_state.compute_merge_state` takes both).
    draft: bool = False
    #: The PR's CURRENT title, raw. The platform writes it from the card's
    #: subject and the repository's own checks judge it (a `pull_request` edit
    #: re-runs them), while the squash commit lands that same subject
    #: (`pr_text.merge_commit_title`) — so both merge paths compare this
    #: against the card and stop when they differ
    #: (`pr_text.pr_title_matches_subject`). Empty only for a fake/older
    #: payload; that reads as "cannot confirm" and stops the merge, which is
    #: the safe side of a title nobody can check.
    title: str = ""
    #: The address of the human whose account opened the PR, None when a bot
    #: (the App) did. GitHub makes the opener the author of the squash commit,
    #: so this decides who the landed commit is credited to.
    opened_by: str | None = None
    #: Who GitHub says merged the PR: its numeric id and login. Gated on
    #: `merged` like `merge_commit_sha` — the field names nobody on an open
    #: PR. A card merged on GitHub without a platform accept click has no
    #: `decided_by`; this is how the progress line still names the person
    #: (`project_progress.feed`). None when the payload carries no merger, or
    #: on an unmerged PR.
    merged_by_github_id: str | None = None
    merged_by_login: str | None = None


def pr_opener(data: dict) -> str | None:
    """The GitHub no-reply address of the person who opened the PR in `data`,
    None when it was a bot or the payload does not say."""
    user = data.get("user") or {}
    if user.get("type") != "User" or not user.get("id") or not user.get("login"):
        return None
    return noreply_email(str(user["id"]), str(user["login"]))


def pr_merger(data: dict) -> tuple[str | None, str | None]:
    """GitHub's `merged_by` in ``data`` as ``(numeric id, login)``, or
    ``(None, None)`` when the payload names nobody. Unlike `pr_opener` this
    keeps bots: a merge queue or an app can be the account that pressed the
    button, and its login is a truer answer than an empty cell."""
    user = data.get("merged_by") or {}
    if not user.get("id") or not user.get("login"):
        return None, None
    return str(user["id"]), str(user["login"])


def merge_actor(status: PullRequestStatus) -> dict:
    """The fact a card keeps about who merged it: GitHub's id and login, or
    `{}` when GitHub named nobody.

    `{}` is deliberately distinct from a NULL column (`AcceptCard.merged_by`
    documents both): NULL means nobody has asked yet, and the backfill must
    keep asking; `{}` means the question was answered and there is no person
    to show — asking again would spend a GitHub read every tick forever."""
    if status.merged_by_github_id and status.merged_by_login:
        return {
            "github_id": status.merged_by_github_id,
            "login": status.merged_by_login,
        }
    return {}


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


def parse_pull_request_status(data: dict) -> PullRequestStatus:
    """One REST PR payload → `PullRequestStatus`, shared by BOTH read paths
    (`HttpxGitHubPrClient.pull_request_status` and `GitHubPRClient.pr_status`).

    Extracted (#718) so the two clients cannot drift on the subtle fields:
    the three-valued `mergeable`, the merged-gated `merge_commit_sha`, and
    `mergeable_state` all encode traps documented on `PullRequestStatus`."""
    merged = bool(data.get("merged"))
    mergeable = data.get("mergeable")
    mergeable_state = data.get("mergeable_state")
    review_comments = data.get("review_comments")
    merged_by_github_id, merged_by_login = pr_merger(data) if merged else (None, None)
    return PullRequestStatus(
        head_sha=data["head"]["sha"],
        head_ref=str(data["head"].get("ref") or ""),
        base_ref=str((data.get("base") or {}).get("ref") or ""),
        base_sha=str((data.get("base") or {}).get("sha") or ""),
        state=str(data.get("state") or ""),
        merged=merged,
        # Anything that isn't a real bool stays None — "GitHub hasn't said
        # yet" and "GitHub said no" must not collapse (see the field).
        mergeable=mergeable if isinstance(mergeable, bool) else None,
        # Raw and lowercased, absent stays None — never collapsed into
        # "unknown", which is a value GitHub actually sends (see the field).
        mergeable_state=(
            mergeable_state.lower()
            if isinstance(mergeable_state, str) and mergeable_state
            else None
        ),
        review_comment_count=(
            review_comments if isinstance(review_comments, int) else 0
        ),
        draft=bool(data.get("draft")),
        title=str(data.get("title") or ""),
        opened_by=pr_opener(data),
        # Gated on `merged` on purpose — see PullRequestStatus's docstring
        # for what this field holds on an unmerged PR.
        merge_commit_sha=(data.get("merge_commit_sha") or None) if merged else None,
        merged_at=_parse_github_time(data.get("merged_at")) if merged else None,
        merged_by_github_id=merged_by_github_id,
        merged_by_login=merged_by_login,
    )


#: Seconds between re-reads of a PR whose mergeability GitHub is still
#: working out; after the last one, `unknown` is what the PR is mirrored as.
_MERGEABILITY_WAITS_S = (1.0, 2.0, 4.0)


async def settled(
    read: Callable[[], Awaitable[PullRequestStatus]],
    sleep: Callable[[float], Awaitable[None]],
) -> PullRequestStatus:
    """`read` once, and again while GitHub is still working the PR's
    mergeability out.

    GitHub works mergeability out lazily: the first read after the PR or its
    base branch moves answers `unknown` and starts the job, and a read a moment
    later has the answer. Mirroring that first read left every open PR's card
    showing its state as still updating after each merge to main, until a poll
    minutes later happened to come after it."""
    status = await read()
    for wait in _MERGEABILITY_WAITS_S:
        if status.state != "open" or status.mergeable_state != "unknown":
            break
        await sleep(wait)
        status = await read()
    return status
