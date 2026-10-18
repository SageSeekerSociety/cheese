"""Bring a draft PR's branch onto its current base before the ready flag.

Two paths turn a draft into 「请人来看」: the task's own `POST /ready`
(`decisions.mark_ready`) and a card being filed (`pr_publish.open_pr_for_card`).
Both start the full CI the moment the flag flips, and a branch's CI runs the
workflows on its own head — so a behind branch spends a whole round on the check
set it was cut from, and the merge into main runs the current one again anyway.
This module is the one place that decides «behind? then update the branch»; both
paths call it, with their own way of getting credentials and a forge client.

The head can only move here: after acceptance a new commit makes the platform's
merge call 409 and voids the approval the person gave for the sha they saw.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.review.github_pr_status import PullRequestStatus

if TYPE_CHECKING:
    from app.domain.review.forgejo_pr import ForgejoPRClient
    from app.domain.review.github_pr import GitHubPRClient

logger = logging.getLogger(__name__)

#: What the ready paths answer when the branch cannot be brought current.
READY_CONFLICT = "分支与目标分支冲突，先在分支上解掉冲突再转可评审"
READY_REBASE_REFUSED = (
    "分支落后于目标分支，自动更新分支被拒：可能撞了冲突，也可能稍后重试即可"
)

#: How long to wait for an accepted update-branch to land. GitHub answers 202
#: and merges in the background (`"Updating pull request branch."`), so the
#: head read right after the call can still be the old one; flipping the ready
#: flag then starts the full CI on the head we are replacing. Bounded — a miss
#: only costs the wait, never the delivery.
UPDATE_BRANCH_WAITS_S = (0.0, 1.0, 2.0)

#: (write token, read token) of the project's repository, or None.
Credentials = Callable[[], Awaitable[tuple[str, str] | None]]


@dataclass(frozen=True)
class ReadyRebase:
    """What the rebase step found.

    `refused` is the answer to hand back when the branch cannot be brought
    current (None otherwise). `head` is the newest head this step saw — the one
    after a rebase when it landed, else the one it read — or None when it never
    read the PR. `rebased` is True when the forge accepted an update-branch."""

    refused: dict | None = None
    head: str | None = None
    rebased: bool = False


def conflicts_with_base(status: PullRequestStatus) -> bool:
    """The forge's own word that the two sides cannot merge.

    `draft` is checked first, like `merge_state.compute_merge_state` does:
    Forgejo reports `mergeable: false` for every work-in-progress PR (its
    draft flag IS the `WIP:` title there), and GitHub reports no mergeability
    at all until the PR is ready. Reading either raw `False` as a conflict
    would refuse to mark any draft ready."""
    if status.draft:
        return status.mergeable_state == "dirty"
    return status.mergeable is False or status.mergeable_state == "dirty"


async def head_after_update(
    client: GitHubPRClient | ForgejoPRClient, number: int, old_head: str
) -> PullRequestStatus | None:
    """The PR's status once its head has moved, or None if it has not."""
    for wait in UPDATE_BRANCH_WAITS_S:
        if wait:
            await asyncio.sleep(wait)
        try:
            status = await client.pr_status(number)
        except Exception:  # 读不到就当没动，照旧翻
            return None
        if status.head_sha and status.head_sha != old_head:
            return status
    return None


async def project_credentials(
    project_id: uuid.UUID, session: AsyncSession
) -> tuple[str, str] | None:
    """The App's two installation tokens for a project, for callers that have
    no AcceptService to ask. None when the project has no usable credentials."""
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import tokens_for_project

    tokens = await tokens_for_project(project_id, session)
    if tokens is None:
        return None
    write, _ = await tokens.write_token()
    read, _ = await tokens.installation_token()
    return write, read


async def rebase_behind_before_ready(
    *,
    session: AsyncSession,
    client: GitHubPRClient | ForgejoPRClient,
    project_id: uuid.UUID,
    base_branch: str | None,
    number: int,
    credentials: Credentials,
    status_client: Callable[[], Awaitable[Any]],
) -> ReadyRebase:
    """Update a draft's branch when it is behind its base, before the ready flag.

    Every failure other than a conflict degrades to "nothing refused" — a forge
    hiccup here must not stop a delivery, it only costs the old check set one
    more round. GitHub answers 422 for «already up to date», which counts as
    not needing a rebase."""
    # deferred-import: tests replace these names on app.domain.project.forge
    from app.domain.project.forge import binding_for_project, default_branch

    try:
        binding = await binding_for_project(project_id, session)
        base = base_branch or await default_branch(project_id, session)
    except Exception:  # 连仓库都读不出来时照旧翻，不卡交付
        logger.warning("ready: cannot resolve the repository", exc_info=True)
        return ReadyRebase()
    if binding is None:
        return ReadyRebase()
    try:
        status = await client.pr_status(number)
    except Exception:  # 读不到合并态就照旧翻，不卡交付
        logger.warning("ready: cannot read PR %s's status", number, exc_info=True)
        return ReadyRebase()
    head = status.head_sha or None
    if conflicts_with_base(status):
        return ReadyRebase({"ready": False, "reason": READY_CONFLICT}, head)

    try:
        creds = await credentials()
        owner, repo = binding.repo.split("/", 1)
        forge = await status_client()
    except Exception:  # 拿不到凭据/客户端就没有换基，照旧翻
        logger.warning("ready: no forge client to rebase with", exc_info=True)
        return ReadyRebase(head=head)
    if creds is None:
        return ReadyRebase(head=head)
    write, read = creds
    try:
        ancestry = await forge.compare_status(
            owner=owner, repo=repo, base=base, head=status.head_sha, token=read
        )
    except Exception:  # ancestry 读不到不拦（merge_state 的规矩）
        logger.warning("ready: cannot compare %s with %s", status.head_sha, base)
        ancestry = None
    if ancestry not in ("behind", "diverged"):
        return ReadyRebase(head=head)

    try:
        updated = await forge.update_branch(
            owner=owner, repo=repo, number=number, token=write
        )
    except Exception:  # 换基失败不卡交付
        logger.warning("ready: update-branch refused for #%s", number, exc_info=True)
        return ReadyRebase(head=head)
    if updated:
        after = await head_after_update(client, number, status.head_sha)
        if after is not None and conflicts_with_base(after):
            return ReadyRebase(
                {"ready": False, "reason": READY_CONFLICT}, after.head_sha, True
            )
        return ReadyRebase(
            head=after.head_sha if after is not None else head, rebased=True
        )

    # Refused. That is a conflict, somebody moved the branch under us a moment
    # ago (GitHub answers 422 for "already up to date" too), or the forge
    # cannot update this branch. Read once more so the three do not share one
    # sentence, and let the already-current case through.
    try:
        after = await client.pr_status(number)
    except Exception:  # 见上
        return ReadyRebase({"ready": False, "reason": READY_REBASE_REFUSED}, head)
    head = after.head_sha or head
    if conflicts_with_base(after):
        return ReadyRebase({"ready": False, "reason": READY_CONFLICT}, head)
    try:
        still_behind = await forge.compare_status(
            owner=owner, repo=repo, base=base, head=after.head_sha, token=read
        )
    except Exception:  # 见上
        still_behind = ancestry
    if still_behind not in ("behind", "diverged"):
        return ReadyRebase(head=head)
    return ReadyRebase({"ready": False, "reason": READY_REBASE_REFUSED}, head)
