"""What both GitHub PR client shapes agree on.

`app.domain.review.github_pr` holds two ways of talking to GitHub — the
token-per-call `GitHubPrClient` / `HttpxGitHubPrClient`, and the
App-installation-token `GitHubPRClient`. They reach the API differently and say
their refusals differently, but they agree on what a PR is, what one merge
attempt came to, which conclusions count as red, and how the merge queue is
entered. That agreement is this module.

The error here is the lowercase shape's; the capital-PR client raises its own
pair, which are unrelated siblings — see `legacy`.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.sentences import say
from app.domain.review.merge_state import RED_CONCLUSIONS

#: Named for the package rather than for this file: before the split every
#: record from this code carried the one module's name, and the pieces that
#: log keep carrying it. `preparation/` does the same, for the same reason.
logger = logging.getLogger("app.domain.review.github_pr")


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


def _refused(key: str, resp: httpx.Response) -> "GitHubPrError":
    """GitHub said no: sentence ``key``, with the status and GitHub's own words."""
    return GitHubPrError(say(key, status=resp.status_code, reply=resp.text[:300]))


#: `no_checks` is NOT a flavour of success: it means "no workflow will ever
#: produce a check for this ref" (every workflow's `paths-ignore` skipped it).
#: The zero-check deadlock fix still holds — the reader stops waiting — but a
#: ref nothing checked has never had its tests run, and the wording must not
#: claim otherwise. See `_resolve_zero_checks`.
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
class MergeResult:
    """Outcome of one merge attempt.

    `queued` means GitHub accepted queue entry; it is not a completed merge.
    Otherwise `sha` records an actual merge, or `blocked_reason` explains why
    GitHub refused (405/409). The refusal MUST carry a reason: returning a
    bare None here is what hid the squash-only bug for half a day (405 on a
    disabled merge_method never clears, so "just retry next tick" looped
    forever with nothing written anywhere).

    `stale_head` is the 409 half told apart from the 405 half (#718): the
    merge was called with the `sha` the human saw, and GitHub answered 409 —
    the head moved under them (or the base conflicts). The accept path treats
    it as "refresh the card and ask the human to look again", which is a
    different instruction from a 405's "GitHub is refusing this merge"."""

    sha: str | None = None
    blocked_reason: str | None = None
    stale_head: bool = False
    queued: bool = False


async def _queue_request(client, api_base, headers, query, variables):
    response = await client.post(
        f"{api_base}/graphql",
        headers=headers,
        json={"query": query, "variables": variables},
    )
    if response.status_code != 200:
        raise GitHubPrError(f"GitHub queue request failed: {_github_message(response)}")
    payload = response.json()
    if payload.get("errors") or not payload.get("data"):
        raise GitHubPrError(
            f"GitHub queue request failed: {str(payload.get('errors'))[:300]}"
        )
    return payload["data"]


async def _queue_pr(client, api_base, headers, owner, repo, number):
    data = await _queue_request(
        client,
        api_base,
        headers,
        "query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){"
        "pullRequest(number:$number){id headRefOid isMergeQueueEnabled "
        "mergeQueueEntry{id}}}}",
        {"owner": owner, "repo": repo, "number": number},
    )
    pr = (data.get("repository") or {}).get("pullRequest")
    if not pr:
        raise GitHubPrError("GitHub queue request returned no pull request")
    return pr


async def _enqueue_if_required(client, api_base, headers, owner, repo, number, sha):
    pr = await _queue_pr(client, api_base, headers, owner, repo, number)
    if not pr["isMergeQueueEnabled"]:
        return None
    if sha and sha != pr["headRefOid"]:
        return MergeResult(blocked_reason="Pull request head changed", stale_head=True)
    if not pr.get("mergeQueueEntry"):
        data = await _queue_request(
            client,
            api_base,
            headers,
            "mutation($id:ID!,$sha:GitObjectID!){enqueuePullRequest(input:{"
            "pullRequestId:$id,expectedHeadOid:$sha}){mergeQueueEntry{id}}}",
            {"id": pr["id"], "sha": sha or pr["headRefOid"]},
        )
        if not (data.get("enqueuePullRequest") or {}).get("mergeQueueEntry"):
            raise GitHubPrError("GitHub did not confirm queue entry")
    return MergeResult(queued=True)


#: 算「红」的结论词只有一份，在 `merge_state`（它同时管必跑名单的判定）：
#: `cancelled` 不在里面 —— 被顶替、被人工取消、作业被取消都不是「没通过」，
#: 见 `superseded_runs`。
_FAILED_CONCLUSIONS = RED_CONCLUSIONS

#: 既不算过、也不算没通过的那些：说出来（卡面标「已取消」），别说成全绿。
_NO_VERDICT_CONCLUSIONS = frozenset({"cancelled", "stale"})


def pr_branch_name(topic_id: uuid.UUID) -> str:
    """Deterministic head branch name pushed to the upstream GitHub repo for a
    topic's PR — distinct from the local topic branch name so a re-push after
    芝士 fixes something is an update, not a new branch."""
    return f"cheesex/{topic_id.hex[:8]}"
