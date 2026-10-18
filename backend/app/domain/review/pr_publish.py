"""每一批活在 GitHub 上的那个 PR —— 开出来、改文案、翻出 draft。

Two moments put a PR here, and they are not alternatives:

- **有东西就有 PR** (#718 拍板①). The task's FIRST COMMIT opens a DRAFT PR, with
  no card and nobody asked to accept anything yet — draft is GitHub's word for
  进行中. `sweep_draft_prs` does it, and its docstring says why the platform has
  to OBSERVE that commit rather than hook it.
- **递卡** dispatches `open_pr_for_card` fire-and-forget: it pushes the branch,
  ADOPTS the draft PR that is already there (or opens one, for a task that had
  none), rewrites its title and body from the card, and takes it out of draft —
  递卡 means「请人来看」. The background dispatch is best-effort: any failure
  leaves the card PR-less, visibly (`PR_OPEN_FAILED_PREFIX`).

Acceptance does NOT fall back to a local merge for a PR-less card — the accept
path retries the publish synchronously via `open_pr_for_card` and stops, visibly,
if opening the PR still fails (AcceptService._publish_pr_for_accept): a merge
commit direct-pushed to main with no PR is exactly what #296 exists to end.

Same task-reference pattern as review/gate.py.

**This module is now the facade of a sibling too.** On 2026-10-16 the forge-
facing text half moved to `pr_publish_payload.py`: what a card's PR says
(`draft_title`, `is_draft_title`, the two prefixes, `_open_pr_of_task`,
`_pr_text`, `sync_pr_text`) and the two writes that record the outcome
(`record_pr`, `_record_failure`). Every name that moved is re-exported here,
so an import that named this module for one of them still resolves.

What stayed is the deciding half — whether a PR should exist at all (`enabled`,
`dispatch`, `_run`, `open_pr_for_card`), the sweep that finds branches with
commits and none (`sweep_draft_prs`, `_draft_pr_for_one_task`, `_branch_ahead`,
`_open_draft_for_task`) — and that is not only about what the two halves are
about. Tests replace `enabled`, `dispatch`, `branch_head`, `proposal_client`,
`sweep_draft_prs` and `_branch_ahead` on THIS module object, so every body that
reads one of them has to live here; in the sibling it would read the sibling's
own copy and the replacement would quietly stop taking effect.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.db import release_read_session
from app.core.sentences import say
from app.domain.block.models import AGENT_NOTICE_META_KEY, Block
from app.domain.conversation.services import of_room
from app.domain.delivery_events import DeliveryEvent
from app.domain.idempotency import store as idem
from app.domain.idempotency.keys import action_key
from app.domain.notice_meta import (
    EVENT_DEPENDENCY_CLOSED,
    EVENT_DEPENDENCY_REJECTED,
    SEVERITY_INFO,
    WHO_CHEESE,
    notice,
)
from app.domain.notification.models import NotificationType
from app.domain.project.forge import (
    ForgeRateLimitedError,
    background_quota,
    branch_head,
    installation_known_closed,
    quota_serves_background,
)
from app.domain.repository import identity
from app.domain.review import archive, pr_text
from app.domain.review.forge_clients import proposal_client, status_client
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
from app.domain.review.ports import Announce, RecordInstruction
from app.domain.review.pr_publish_payload import (
    DRAFT_TITLE_PREFIX as DRAFT_TITLE_PREFIX,
)
from app.domain.review.pr_publish_payload import (
    PR_OPEN_FAILED_PREFIX as PR_OPEN_FAILED_PREFIX,
)
from app.domain.review.pr_publish_payload import (
    _open_pr_of_task as _open_pr_of_task,
)
from app.domain.review.pr_publish_payload import (
    _pr_text as _pr_text,
)
from app.domain.review.pr_publish_payload import (
    _record_failure as _record_failure,
)
from app.domain.review.pr_publish_payload import (
    draft_title as draft_title,
)
from app.domain.review.pr_publish_payload import (
    is_draft_title as is_draft_title,
)
from app.domain.review.pr_publish_payload import (
    record_pr as record_pr,
)
from app.domain.review.pr_publish_payload import (
    sync_pr_text as sync_pr_text,
)
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import PlaceResolver
from app.domain.topic.models import Topic

logger = logging.getLogger("cheesex.pr_publish")

_TASKS: set[asyncio.Task] = set()


def enabled() -> bool:
    """Cheap pre-check: the GitHub App is configured at all. The per-project
    eligibility (which installation, upstream is a GitHub https remote) is
    resolved in the task itself — it needs the DB and a subprocess."""
    return bool(settings.forgejo_url) or (
        bool(settings.github_app_id) and bool(settings.github_app_private_key_path)
    )


def dispatch(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Start the PR publication in the background; returns immediately."""
    task = asyncio.create_task(
        _run(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _run(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    try:
        pr = await _publish(
            session_factory,
            card_id=card_id,
            topic_id=topic_id,
            project_id=project_id,
        )
    except Exception as exc:  # card stays PR-less, but never silently (#362)
        logger.exception("PR publication failed for card %s", card_id)
        await _record_failure(session_factory, card_id=card_id, exc=exc)
        return
    if pr is None:
        return
    await record_pr(session_factory, card_id=card_id, pr=pr)


async def _publish(
    session_factory: async_sessionmaker,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    async with session_factory() as session:
        return await open_pr_for_card(
            session, card_id=card_id, topic_id=topic_id, project_id=project_id
        )


async def open_pr_for_card(
    session: AsyncSession,
    *,
    card_id: uuid.UUID,
    topic_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict | None:
    """Push the topic branch and open (or adopt) its App PR — the shared core
    of the fire-and-forget publish above and the accept-time publish for
    legacy PR-less cards (AcceptService._publish_pr_for_accept).

    Returns the PR json, or None when the PR path is NOT APPLICABLE to this
    card: no App installation for the project, a non-GitHub upstream, or a
    discussion-only topic with no branch to put in a PR. Raises when opening
    the PR FAILED — the two callers treat that differently (background: log
    and leave the card PR-less; accept: stop the accept, never direct-merge).
    Only ever reads through `session`; the card row is written by whoever
    owns it (`record_pr` below, or the accept transaction)."""
    # #192: the installation is resolved from this card's project, not a global.
    client = await proposal_client(project_id, session)
    if client is None:
        return None
    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    card = await AcceptCardRepository(session).get(card_id)
    if card is None or card.task_id is None:
        return None
    if (
        card.pr_number is None
        and not card.delivered_task_ids
        and card.deliverable_kind
        in (
            DeliverableKind.file,
            DeliverableKind.link,
        )
    ):
        return None
    task = await TaskService(session).require_in_room(topic_id, card.task_id)
    if not task.branch_name or not await branch_head(
        project_id, session, task.branch_name
    ):
        return None  # discussion-only topic — nothing a PR could carry

    branch = task.branch_name
    base = task.base_branch
    if base is None:
        raise ValueError("Task has no target branch")

    title, body = await _pr_text(
        session, card_id=card_id, topic_id=topic_id, branch=branch
    )
    pr = await _open_pr_of_task(client, task)
    if pr is None:
        opened = await client.open_pr(
            head=branch,
            base=base,
            title=title,
            body=body,
        )
        pr = opened.pr
    # The PR very often already exists by now: the task's draft PR was opened
    # at its first commit (#718 拍板①) and `open_pr` adopts it rather than
    # failing on GitHub's "already exists". An adopted PR still carries the
    # placeholder words the draft opened with, so the card's own subject/body
    # has to be written onto it — otherwise the reviewer reads 「WIP: 房间名」
    # while the squash commit says something else entirely.
    await sync_pr_text(client, pr, title=title, body=body)
    # 递卡的语义就是「请人来看」，所以卡一递出去，PR 就不再是 draft (#718 拍板①)。
    # Not best-effort: a card that says 等验收 while GitHub still says 草稿 is a
    # delivery nobody can review, and nothing else would ever say so.
    if pr.get("draft") and pr.get("node_id"):
        await client.mark_ready_for_review(str(pr["node_id"]))
        pr["draft"] = False
    logger.info(
        "PR #%s ready for card %s (%s)", pr.get("number"), card_id, pr.get("html_url")
    )
    return pr


async def sweep_draft_prs(
    session_factory: async_sessionmaker,
    project_id: uuid.UUID | None = None,
    *,
    announce: Announce,
    record_instruction: RecordInstruction,
    quiet_window_s: int | None = None,
) -> dict[str, int]:
    """Open draft PRs for task branches with commits and no existing PR.

    Commits arrive from different executors through Git; the shared PR poller
    observes them after synchronization. Delay is one sweep interval plus the
    time spent on earlier tasks in this pass. Each task uses its own session
    so a failed write cannot poison the remaining deliveries.

    ``quiet_window_s`` limits a pass to tasks whose last activity (newest
    block in their own conversation, or their creation) is inside that many
    seconds: each candidate costs two GitHub requests (`branch_head` +
    `compare_status`), and a branch with nothing said for hours is not being
    pushed to. Only the periodic clock passes it — that is where the cost is.
    An event-driven or catch-up pass leaves it ``None`` and sweeps every open
    task, because those passes are exactly how a push missed while the relay
    was down, or a pass lost to a spent quota, gets made good. Zero or less
    also keeps every open task.
    """
    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    counts = {"opened": 0, "skipped": 0, "failed": 0}
    await retarget_completed_dependencies(
        session_factory,
        project_id,
        announce=announce,
        record_instruction=record_instruction,
    )
    if not enabled():
        return counts
    active_since = (
        datetime.now(UTC) - timedelta(seconds=quiet_window_s)
        if quiet_window_s is not None and quiet_window_s > 0
        else None
    )
    async with session_factory() as session:
        wanted = [
            (t.id, t.project_id)
            for t in await TaskService(session).open_without_pr(
                active_since=active_since
            )
            if project_id is None or t.project_id == project_id
        ]
    # A project whose forge refused for quota in this pass: every later task of
    # it would meet the same refusal, so they wait for the next pass.
    out_of_quota: set[uuid.UUID] = set()
    for task_id, task_project in wanted:
        if task_project in out_of_quota:
            counts["skipped"] += 1
            continue
        try:
            # Most passes find a branch with nothing new on it. Asking the
            # forge that without the row lock keeps the connection free while
            # the request is in flight; only a branch that is ahead goes on
            # to the locked path, which asks again before it opens anything.
            async with session_factory() as session:
                ahead = await _branch_ahead(session, task_id)
            if not ahead:
                counts["skipped"] += 1
                continue
            async with session_factory() as session:
                opened = await _draft_pr_for_one_task(session, task_id)
                await session.commit()
        except ForgeRateLimitedError as exc:
            counts["failed"] += 1
            out_of_quota.add(task_project)
            logger.warning(
                "draft PRs of project %s wait for the next pass: %s", task_project, exc
            )
            continue
        except Exception:  # one bad task must not end the sweep
            counts["failed"] += 1
            logger.warning("draft PR not opened for task %s", task_id, exc_info=True)
            continue
        counts["opened" if opened else "skipped"] += 1
    return counts


async def retarget_completed_dependencies(
    session_factory: async_sessionmaker,
    project_id: uuid.UUID | None = None,
    *,
    announce: Announce,
    record_instruction: RecordInstruction,
) -> None:
    """Retarget delivered dependencies and persist instructions for the executor."""

    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    parent = aliased(Task)
    query = (
        select(Task.id)
        .join(parent, Task.base_task_id == parent.id)
        .where(
            Task.status == TaskStatus.open,
            or_(
                parent.status == TaskStatus.closed,
                # A step of the parent landed and it went on on a new branch:
                # what was stacked on the old one now stacks on the target.
                and_(
                    parent.delivered_head.is_not(None),
                    Task.base_branch != parent.branch_name,
                    Task.base_branch != parent.base_branch,
                ),
                select(AcceptCard.id)
                .where(
                    AcceptCard.task_id == parent.id,
                    AcceptCard.status == AcceptStatus.rejected,
                )
                .exists(),
            ),
        )
    )
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    async with session_factory() as session:
        wanted = list(await session.scalars(query))
    for task_id in wanted:
        try:
            # Decide on an unlocked read, call GitHub with no transaction open,
            # then lock the row only to record it. A row lock held across the
            # GitHub call would queue every other writer of this task behind
            # it with a pool connection each (dev outage of 2026-09-18).
            async with session_factory() as session:
                task = await session.get(Task, task_id)
                if (
                    task is None
                    or task.status != TaskStatus.open
                    or task.base_task_id is None
                ):
                    continue
                ancestor = await TaskService(session).get(task.base_task_id)
                if ancestor is None:
                    continue
                parent_card = (
                    await AcceptCardRepository(session).latest_by_task([ancestor.id])
                ).get(ancestor.id)
                rejected_id = (
                    parent_card.id
                    if parent_card is not None
                    and parent_card.status == AcceptStatus.rejected
                    else None
                )
                stepped = (
                    ancestor.status == TaskStatus.open
                    and ancestor.delivered_head is not None
                    and task.base_branch
                    not in (ancestor.branch_name, ancestor.base_branch)
                )
                if (
                    ancestor.status != TaskStatus.closed
                    and rejected_id is None
                    and not stepped
                ):
                    continue
                event_type = (
                    EVENT_DEPENDENCY_CLOSED
                    if ancestor.status == TaskStatus.closed or stepped
                    else EVENT_DEPENDENCY_REJECTED
                )
                delivered = bool(ancestor.accepted_at or ancestor.delivered_head)
                base = ancestor.base_branch
                if delivered and base is None:
                    raise ValueError("Parent task has no target branch")
                seen = (task.base_task_id, task.base_branch, task.pr_number)
                parent_state = (
                    ancestor.status,
                    ancestor.closed_at,
                    ancestor.accepted_at,
                    ancestor.delivered_head,
                    rejected_id,
                )
                key = action_key(task.id, event_type, ancestor.id, *parent_state)
                if await idem.stored_result(session, key) is not None:
                    continue
                # The parent's branch, or one of its earlier steps' branches.
                retarget = delivered and task.base_branch != base
                client = None
                if retarget and task.pr_number is not None:
                    room = await session.get(Topic, task.room_id)
                    if room is None:
                        raise ValueError("Task room is missing")
                    # Ask the forge layer for the client directly. Going
                    # through the accept service only wrapped this call, and
                    # importing that service from here made the two modules a
                    # cycle.
                    client = await proposal_client(room.project_id, session)
                    if client is None:
                        raise RuntimeError(
                            "Cannot retarget the task PR without its forge connection"
                        )
            if client is not None:
                # Setting the base is idempotent: a sweep that records nothing
                # below simply sets it again next time.
                await client.update_pr(seen[2], base=base)
            async with session_factory() as session:
                task = await session.get(Task, task_id, with_for_update=True)
                if (
                    task is None
                    or task.status != TaskStatus.open
                    or (task.base_task_id, task.base_branch, task.pr_number) != seen
                ):
                    continue
                ancestor = await session.get(Task, task.base_task_id)
                cards = AcceptCardRepository(session)
                parent_card = (
                    (await cards.latest_by_task([ancestor.id])).get(ancestor.id)
                    if ancestor is not None
                    else None
                )
                if (
                    ancestor is None
                    or (
                        ancestor.status,
                        ancestor.closed_at,
                        ancestor.accepted_at,
                        ancestor.delivered_head,
                        parent_card.id
                        if parent_card is not None
                        and parent_card.status == AcceptStatus.rejected
                        else None,
                    )
                    != parent_state
                ):
                    continue
                if not await idem.claim(
                    session, key, action=event_type, scope_id=str(task.id)
                ):
                    continue
                if retarget:
                    task.base_branch = base
                outcome = (
                    say("dependencyOutcomeMerged")
                    if delivered
                    else say("dependencyOutcomeClosed")
                    if ancestor.status == TaskStatus.closed
                    else say("dependencyOutcomeReturned")
                )
                headline = say("dependencyHeadline", outcome=outcome)
                rejection = (
                    parent_card
                    if not delivered
                    and parent_card is not None
                    and parent_card.status == AcceptStatus.rejected
                    else None
                )
                # A parent can detach from a rejected ancestor before merging.
                # Its durable notices retain the review context after that link
                # is cleared; pass it on so descendants do not revive old work.
                review_history = {}
                parent_notices = await session.scalars(
                    select(Block)
                    .where(
                        of_room(Block.conversation_id, ancestor.room_id),
                        Block.meta["dependency_task_id"].as_string()
                        == str(ancestor.id),
                    )
                    .order_by(Block.created_at)
                )
                for parent_notice in parent_notices:
                    previous_reviews = list(
                        parent_notice.meta.get("dependency_review_history", [])
                    )
                    previous_rejection = parent_notice.meta.get("dependency_rejection")
                    if previous_rejection is not None:
                        previous_reviews.append(previous_rejection)
                    for previous_review in previous_reviews:
                        review_history[previous_review["card_id"]] = previous_review
                instruction = say(
                    "dependencyInstruction",
                    task=task.id,
                    parent=ancestor.id,
                    outcome=outcome,
                    command=f'cd "$(cheese worktree {task.id})"',
                    action=(
                        say("dependencyActionDelivered", branch=task.base_branch)
                        if delivered
                        else say("dependencyActionUndelivered")
                    ),
                    history=(
                        say(
                            "dependencyHistory",
                            items=[
                                say(
                                    "dependencyHistoryItem",
                                    card=previous_review["card_id"],
                                    reason=previous_review.get("reason")
                                    or say("dependencyNoReason"),
                                )
                                for previous_review in review_history.values()
                            ],
                        )
                        if review_history
                        else ""
                    ),
                    rejection=(
                        say(
                            "dependencyRejection",
                            card=rejection.id,
                            why=(
                                say("dependencyRejectionNote", note=rejection.note)
                                if rejection.note
                                else say("dependencyRejectionNoNote")
                            ),
                        )
                        if rejection is not None
                        else ""
                    ),
                )
                for card in await cards.list_for_task(task.id):
                    if card.status in ("pending", "conflict"):
                        await cards.clear_approvals(card.id)
                        card.auto_merge_armed_by = None
                        card.auto_merge_armed_at = None
                        card.note = headline
                block = await announce(
                    session,
                    place_id=task.room_id,
                    task_id=task.id,
                    content=headline,
                    meta={
                        **notice(
                            event_type,
                            severity=SEVERITY_INFO,
                            who=WHO_CHEESE,
                            detail=instruction,
                            detail_label=say("labelNextStep"),
                        ),
                        AGENT_NOTICE_META_KEY: instruction,
                        "dependency_task_id": str(task.id),
                        "dependency_review_history": list(review_history.values()),
                        "dependency_rejection": (
                            {
                                "card_id": str(rejection.id),
                                "decided_by": rejection.decided_by,
                                "reason": rejection.note,
                            }
                            if rejection is not None
                            else None
                        ),
                    },
                )
                if block is None:
                    raise ValueError("Task room is missing")

                await record_instruction(
                    session,
                    DeliveryEvent(
                        id=block.id,
                        type=NotificationType.ROOM_NOTICE,
                        payload={"content": instruction},
                        occurred_at=block.created_at,
                    ),
                    task_id=task.id,
                    content=instruction,
                )
                await idem.record_result(session, key, {"block_id": str(block.id)})
                await session.commit()
        except Exception:
            logger.warning(
                "Task %s target branch update failed; retry next sweep",
                task_id,
                exc_info=True,
            )


async def _draft_pr_for_one_task(session: AsyncSession, task_id: uuid.UUID) -> bool:
    """Open and record this task's draft PR. False = there was nothing to do.

    **A task with a card still waiting to be settled is not this sweep's
    business**, and that — not the row lock — is what keeps it off a merged
    branch. The task's `status` cannot answer "has this been merged on GitHub",
    because the merge happens FIRST and the row is updated after
    (`AcceptService._merge_pr_for_accept`: merge API, then
    `_mark_task_merged`). A sweep holding the row lock in between sees
    `open` while the branch is, on GitHub, already squashed into main — and
    opens a PR nobody can ever close by merging it. Locking the row makes that
    window deterministic instead of removing it. The open card does remove it:
    a filed card is the delivery, from that moment the PR belongs to it
    (`open_pr_for_card`), and no task is ever merged in that window without
    one. So the question this asks is the question that has a stable answer.

    Settled cards are a different matter, and asking about them was wrong: a
    task that delivers in steps starts its second step with step one's card
    already `accepted`, its `pr_number` back to None and a branch of its own
    (`TaskService.next_step`) — nothing but this check stood between it and the
    draft PR it needs, so the sweep never brought it one and the step sat
    waiting for a review that could not start. A settled card holds no PR
    anybody is still going to merge, which is why `archive.OPEN_CARD_STATUSES`
    — the same "not settled yet" the rest of review uses — is the filter.

    Every merging path goes through a card, and the card is open while the
    merge is in flight — the ordinary accept, an auto-merged armed card, a
    manual override, and the PR somebody merged on GitHub that the platform
    then settles — so all of them are excluded by the same one check.

    The row is still locked and re-read, for the narrower job it can actually
    do: two overlapping passes over one task must not both open a PR. The second
    sees `pr_number` set. Even if it somehow did not, `open_pr` adopts the PR
    already open on that head rather than creating a second — that is the belt,
    this is the braces.
    """
    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    tasks = TaskService(session)
    task = await tasks.claim_for_pr(task_id)
    if task is None:
        return False
    cards = AcceptCardRepository(session)
    if any(
        card.status in archive.OPEN_CARD_STATUSES
        for card in await cards.list_for_task(task.id)
    ):
        return False
    # The row is held here, so GitHub is not asked: `_branch_ahead` asked it
    # live a moment ago, and what it answered is in the process's quota record.
    quota = await background_quota(task.project_id, session)
    if quota is not None and installation_known_closed(quota):
        return False
    pr = await _open_draft_for_task(session, task)
    if pr is None:
        return False
    await tasks.record_pr(
        task, number=int(pr["number"]), url=str(pr.get("html_url") or "")
    )
    return True


async def _branch_ahead(session: AsyncSession, task_id: uuid.UUID) -> bool:
    """Could this task need a draft PR now — read without locking its row.

    The same questions `_draft_pr_for_one_task` and `_open_draft_for_task`
    ask, in the same order, minus the lock. The session is released before
    each forge request, so a sweep over many quiet branches does not keep a
    database connection waiting on the forge.
    """
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    # deferred-import: tests replace this name on app.domain.room_task.services
    from app.domain.room_task.services import TaskService

    task = await TaskService(session).get(task_id)
    if (
        task is None
        or task.status != TaskStatus.open
        or not task.branch_name
        or task.pr_number is not None
    ):
        return False
    if any(
        card.status in archive.OPEN_CARD_STATUSES
        for card in await AcceptCardRepository(session).list_for_task(task.id)
    ):
        return False
    project_id, branch, base = task.project_id, task.branch_name, task.base_branch
    quota = await background_quota(project_id, session)
    if base is None:
        # No base to compare against here: the locked path decides, on this
        # quota answer — it does not ask GitHub itself while holding the row.
        await release_read_session(session)
        return await quota_serves_background(quota)
    client = await proposal_client(project_id, session)
    if client is None:
        return False
    binding = await binding_for_project(project_id, session)
    if binding is None:
        return False
    owner, repo = binding.repo.split("/", 1)
    reader = await status_client(project_id, session)
    await release_read_session(session)
    if not await quota_serves_background(quota):
        return False
    head = await branch_head(project_id, session, branch, release_session=True)
    if head is None:
        return False
    await release_read_session(session)
    token, _ = await client.tokens.installation_token()
    difference = await reader.compare_status(
        owner=owner, repo=repo, base=base, head=head, token=token
    )
    return difference in ("ahead", "diverged")


async def _open_draft_for_task(session: AsyncSession, task) -> dict | None:
    """The draft PR for one task, or None when this task cannot have one yet.

    None (not an error) for: a project with no App installation, a non-GitHub
    upstream, and — the ordinary case, on every tick — a branch with nothing on
    it. A newly created task has no changes to review until its first commit.
    """

    project_id = task.project_id
    client = await proposal_client(project_id, session)
    if client is None:
        return None
    branch = task.branch_name
    head = await branch_head(project_id, session, branch)
    if head is None:
        return None
    token, _ = await client.tokens.installation_token()
    reader = await status_client(project_id, session)
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    binding = await binding_for_project(project_id, session)
    if binding is None:
        return None
    owner, repo = binding.repo.split("/", 1)
    difference = await reader.compare_status(
        owner=owner, repo=repo, base=task.base_branch, head=head, token=token
    )
    if difference not in ("ahead", "diverged"):
        return None
    place = await PlaceResolver(session).resolve(task.room_id)
    if place is None:
        return None
    room = place.room

    base = task.base_branch
    who = await identity.attribution(session, room, task_id=task.id)
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import ensure_author_email

    if who.author:
        await ensure_author_email(project_id, session, who.author.email)
    opened = await client.open_pr(
        head=branch,
        base=base,
        title=draft_title(task.title or branch),
        body=pr_text.pr_body(room, "", None, who),
        draft=True,
    )
    pr = opened.pr
    logger.info(
        "draft PR #%s opened for task %s (%s)",
        pr.get("number"),
        task.id,
        pr.get("html_url"),
    )
    return pr
