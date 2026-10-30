"""Polling the forge, because a forge without a webhook tells us nothing.

结论 16: PR / 上游轮询是托管方实现在没有 webhook 时的内部物理事实——不是调度，是
review 领域自己补上的一条读路径。产出落各自的卡和房间，和 webhook 送来的事件走同
一条路 (`forge_repository_changed`)。

So this is the review domain's own clock. `app/domain/review/periodic.py` sets
how often it turns; everything about WHAT a tick does is here and in the services
it calls.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import or_, select

from app.core.sentences import say
from app.domain.delivery_events import DeliveryEvent, NotificationType, SettleCard
from app.domain.notice_meta import (
    EVENT_ACCEPT_DONE,
    EVENT_ACCEPT_READY,
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_PLATFORM,
    notice,
)
from app.domain.oauth.services import handles_for_github_ids
from app.domain.project.forge import (
    background_quota,
    quota_serves_background,
)
from app.domain.project.models import ProjectForge
from app.domain.review import archive, pr_publish
from app.domain.review.github_pr import _NO_VERDICT_CONCLUSIONS, GitHubPRError
from app.domain.review.github_pr.task_pr_metadata import (
    TaskPrMetadata,
    TaskPrMetadataFailure,
    TaskPrMetadataResult,
)
from app.domain.review.github_pr_status import PullRequestStatus
from app.domain.review.merge_state import RED_CONCLUSIONS
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.ports import (
    Announce,
    Chat,
    DispatchPending,
    RecordInstruction,
    SweepCheckouts,
    TaskTeammate,
)
from app.domain.review.room_notices import tell_room_undelivered
from app.domain.review.task_landing import delivery_landed, go_on_while_working
from app.domain.room_task.closing import close_overdue
from app.domain.room_task.lock_order import lock_task_for_change
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic, TopicStatus

logger = logging.getLogger("cheesex.review.pr_poll")

# A poller against a network misses sometimes: a DNS blip, the connection owner
# restarting mid-release, a TLS handshake that never finished. The first miss is
# not news — the next tick is a minute away and usually fixes it — and reporting
# each one as an error made 39 of the alert channel's first 600 messages, none
# of which anybody acted on. What IS news is that the retries are not working,
# so a card has to miss this many ticks in a row before it is reported as an
# error. Everything else still fails loudly on the first occurrence: a bug in
# the poller is not something a later tick repairs.
TRANSIENT_MISSES_BEFORE_ERROR = 3

#: Consecutive ticks each card has lost to the network, so that a blip and an
#: outage do not read the same. Module level because the clock that drives this
#: is process-wide: one backend, one count per card.
_transient_misses: dict[uuid.UUID, int] = {}

# Keep the one-minute sweep within the forge API quota while covering old tasks.
UNCARDED_TASKS_PER_TICK = 10
_uncarded_task_cursor: uuid.UUID | None = None

#: 「最近进展」只往回看 14 天（`project_progress.feed.WINDOW`）；合并人补录跟着
#: 同一个窗口走，更早的卡已经不在那一屏上，不必再花 GitHub 配额。review 在
#: 下面一层，读不到那个常量，所以这里重写一遍。
MERGE_ACTOR_BACKFILL_WINDOW = timedelta(days=14)

#: 一次补录最多补几张：上线时的那一次后台读，配额要留给还在走的卡。
MERGE_ACTORS_PER_BACKFILL = 20

#: The three words `_checks_state` folds a PR's check runs into.
CHECKS_RUNNING = "running"
CHECKS_GREEN = "green"
CHECKS_RED = "red"


def _checks_state(runs: Sequence[dict]) -> str:
    """这个提交上一批 check run 的净状态：``running`` / ``green`` / ``red``。

    一个还在跑的检查意味着结论还不存在，所以「有在跑的」压过其余两条——包括
    一条已经红的，重跑会把它变绿。

    ``cancelled`` / ``stale`` 同样按「还没有结论」算，不折成绿：它们不是裁决。
    被新推送顶掉的那次运行正是以 ``cancelled`` 收场的，把它读成全绿就会对着一个
    还没人跑完的 head 说「可以递卡了」；`superseded_runs` 和卡面那句「已取消」
    也是同一套词表（``_NO_VERDICT_CONCLUSIONS``）。``skipped`` 仍然是绿：路径过滤
    不跑的那几个 job 在这套 CI 里是常态，把它们算成没结论会让这句话永远等不到。

    全部跑完、没有一条是 :data:`~app.domain.review.merge_state.RED_CONCLUSIONS`
    里的词、也没有一条是「没有裁决」的词，才算全绿。

    一份 run 都没读到按 ``running`` 算：推送后到 workflow 起跑之间正是这个
    空窗，那个时刻说「全绿」是错的；一直不报检查的仓库只是永远等不到这句话，
    代价比误报小。
    """
    if not runs:
        return CHECKS_RUNNING
    if any(run.get("status") != "completed" for run in runs):
        return CHECKS_RUNNING
    if any(run.get("conclusion") in RED_CONCLUSIONS for run in runs):
        return CHECKS_RED
    if any(run.get("conclusion") in _NO_VERDICT_CONCLUSIONS for run in runs):
        # 没有裁决的 run 既不是过也不是没过：等它被重跑/被顶替后的那次结论。
        return CHECKS_RUNNING
    return CHECKS_GREEN


# How many of a tick's PRs are carried at once. No card or task PR depends on
# another — each opens its own session and talks to the forge on its own — so
# walking them in file order made the tick cost the sum of every round trip,
# every minute, while the clock that runs it never moved. Bounded rather than
# unbounded because each one in flight holds a database connection out of the
# pool the rest of the process shares.
PRS_AT_ONCE = 6


async def poll_open_prs(
    chat: Chat,
    announce: Announce,
    project_id: uuid.UUID | None = None,
    *,
    settle_card: SettleCard,
    record_instruction: RecordInstruction,
    task_teammate: TaskTeammate,
) -> dict:
    """Reconcile returned batches and advance pending PR cards (#718) one step —
    mirror its merge state, send the events the 「谁的活」 table names, and merge
    an armed auto-merge card whose rules are satisfied
    (`AcceptService.advance_pr_card`). One DB transaction per card so one card's
    failure can't roll back another's progress, and `PRS_AT_ONCE` of them
    carried together so the tick is not the sum of every card's round trips."""
    # deferred-import: tests replace this name on app.domain.review.services
    from app.domain.review.services import AcceptService

    sessions = chat.session_factory
    # The runner comes off the chat service — the one the composition root
    # assembled — instead of being imported from `app.api.deps`, which is a
    # domain importing up into the API layer (`.importlinter`, C1).
    runner = chat.work_runner
    async with sessions() as session:
        # 孤儿卡修复的第二道锁，换到关掉的活上：一条活已经结束，它名下的卡
        # 不再有人跟。关任务的那条路当场收；这里兜住别的关法，和修复之前就停
        # 在 pending 上的那些行。
        await archive.close_cards_for_closed_tasks(
            session, settle_card=settle_card, project_id=project_id
        )
        await session.commit()
        # 孤儿卡修复 (2026-08-10): cards on ARCHIVED topics are deliberately NOT
        # in this list — driving them means using the approver's GitHub token on
        # work nobody tracks any more. 那条判据留在 review 领域里
        # （open_pr_card_ids），这里只管拿 id。
        card_ids = await AcceptService(session).open_pr_card_ids(project_id)
    slots = asyncio.Semaphore(PRS_AT_ONCE)

    async def advance(card_id: uuid.UUID) -> str | None:
        """One card in its own session; returns the failure to report, if any."""
        async with slots:
            async with sessions() as session:
                try:
                    await AcceptService(
                        session,
                        announce=announce,
                        settle_card=settle_card,
                        record_instruction=record_instruction,
                        task_teammate=task_teammate,
                    ).advance_pr_card(card_id, chat_service=chat, runner=runner)
                    await session.commit()
                    _transient_misses.pop(card_id, None)
                    return None
                except Exception as exc:  # one card must not stop the rest
                    await session.rollback()
                    _report_card_failure(card_id, exc)
                    await _note_card_poll_crashed(
                        chat,
                        announce,
                        card_id,
                        exc,
                        settle_card=settle_card,
                        record_instruction=record_instruction,
                        task_teammate=task_teammate,
                    )
                    return f"{card_id}: {exc}"

    # `gather` hands back one outcome per card in the order the cards were
    # picked up, so the errors read the way they did when this was a loop.
    failures = await asyncio.gather(*(advance(card_id) for card_id in card_ids))
    return {
        "cards_checked": sum(1 for failure in failures if failure is None),
        "errors": [failure for failure in failures if failure is not None],
    }


async def _merged_by_name(session, status) -> str | None:
    """Who to name as the person who merged: their platform handle when the
    GitHub account is linked to one, else the login GitHub reported. None
    when the read carries no merger (the batch metadata never does)."""
    github_id = getattr(status, "merged_by_github_id", None)
    if not github_id:
        return None
    linked = await handles_for_github_ids(session, [github_id])
    return linked.get(github_id) or getattr(status, "merged_by_login", None)


@dataclass(frozen=True)
class _TaskPrCandidate:
    task_id: uuid.UUID
    project_id: uuid.UUID
    number: int


async def poll_uncarded_task_prs(
    chat: Chat,
    announce: Announce,
    project_id: uuid.UUID | None = None,
    *,
    record_instruction: RecordInstruction,
    task_teammate: TaskTeammate,
) -> dict:
    """Record task PRs merged on the forge without an accept card."""

    # deferred-import: tests replace this name on app.domain.review.forge_clients
    from app.domain.review.forge_clients import proposal_client

    global _uncarded_task_cursor
    sessions = chat.session_factory
    query = (
        select(Task.id, Task.project_id, Task.pr_number)
        .join(Topic, Topic.id == Task.room_id)
        .where(
            Task.pr_number.is_not(None),
            # A delivery that landed and closed the task is done with, and so
            # is one whose task is being written up before it closes; an open
            # task's earlier steps landed on PRs it no longer holds.
            or_(Task.delivered_head.is_(None), Task.status == TaskStatus.open),
            Task.closing_since.is_(None),
            Topic.status != TopicStatus.archived,
            # A card that is still waiting for its reviewer keeps the task in
            # this poll: its checks turning green is news to the line too.
            ~select(AcceptCard.id)
            .where(
                AcceptCard.task_id == Task.id,
                AcceptCard.pr_number == Task.pr_number,
                AcceptCard.status != AcceptStatus.pending,
            )
            .exists(),
        )
        .order_by(Task.id)
        .limit(UNCARDED_TASKS_PER_TICK)
    )
    if project_id is not None:
        query = query.where(Task.project_id == project_id)
    cursor = _uncarded_task_cursor if project_id is None else None
    async with sessions() as session:
        rows = list(
            (
                await session.execute(
                    query.where(Task.id > cursor) if cursor is not None else query
                )
            ).all()
        )
        if cursor is not None and len(rows) < UNCARDED_TASKS_PER_TICK:
            rows.extend(
                (
                    await session.execute(
                        query.where(Task.id <= cursor).limit(
                            UNCARDED_TASKS_PER_TICK - len(rows)
                        )
                    )
                ).all()
            )
        candidates = [
            _TaskPrCandidate(row.id, row.project_id, row.pr_number)
            for row in rows
            if row.pr_number is not None
        ]
    if project_id is None and candidates:
        _uncarded_task_cursor = candidates[-1].task_id

    slots = asyncio.Semaphore(PRS_AT_ONCE)

    async def advance(
        candidate: _TaskPrCandidate,
        client,
        quota,
        metadata: dict[int, TaskPrMetadataResult] | None,
    ) -> tuple[bool, bool, str | None]:
        task_id = candidate.task_id
        """One task PR; returns (checked, merged, the failure to report)."""
        checked = merged = False
        try:
            async with slots:
                async with sessions() as session:
                    task = await session.get(Task, task_id)
                    if (
                        task is None
                        or task.pr_number != candidate.number
                        or task.project_id != candidate.project_id
                    ):
                        return checked, merged, None
                    number, project = candidate.number, candidate.project_id
                    room_id, pr_url = task.room_id, task.pr_url
                    previous = (task.pr_checks_head, task.pr_checks_state)
                if metadata is None:
                    # A provider without the batch operation keeps its own read.
                    if not await quota_serves_background(quota):
                        return checked, merged, None
                    status = await client.pr_status(number)
                else:
                    value = metadata.get(number)
                    if not isinstance(value, TaskPrMetadata) or value.number != number:
                        reason = (
                            value.reason
                            if isinstance(value, TaskPrMetadataFailure)
                            else "missing_metadata"
                        )
                        logger.warning("unreadable task PR metadata: %s", reason)
                        raise GitHubPRError(say("upstreamUnavailable"))
                    status = value
                checked = True
                if not status.merged:
                    # The metadata read may have spent the remaining quota;
                    # fresh merged facts need no further forge read to settle.
                    if (
                        metadata is not None
                        and status.state == "open"
                        and not await quota_serves_background(quota)
                    ):
                        return checked, merged, None
                    await _wake_when_checks_pass(
                        task_id,
                        number,
                        status,
                        client,
                        room_id=room_id,
                        project_id=project,
                        pr_url=pr_url,
                        previous=previous,
                        announce=announce,
                        record_instruction=record_instruction,
                        sessions=sessions,
                    )
                    return checked, merged, None
                if metadata is not None and await quota_serves_background(quota):
                    # 批量元数据只说合没合，不说谁合的。总览那一行要显示
                    # 合并人，所以在这个 PR 落地的那一拍多读一次；读不到就
                    # 照旧没有经手人，不影响这次落地本身。
                    try:
                        fresh = await client.pr_status(number)
                        # 只借它说合并人；落地用的是刚才那份元数据的事实。
                        if fresh.merged:
                            status = fresh
                    except Exception as exc:
                        logger.warning(
                            "task %s: cannot read who merged PR #%s: %s",
                            task_id,
                            number,
                            exc,
                        )
                async with sessions() as session:
                    task = await lock_task_for_change(session, task_id)
                    if (
                        task is None
                        or task.pr_number != number
                        or task.project_id != project
                        or (task.status != TaskStatus.open and task.delivered_head)
                        or task.closing_since is not None
                    ):
                        return checked, merged, None
                    if await session.scalar(
                        select(AcceptCard.id)
                        .where(
                            AcceptCard.task_id == task_id,
                            AcceptCard.pr_number == number,
                        )
                        .limit(1)
                    ):
                        return checked, merged, None
                    topic = await session.get(Topic, task.room_id)
                    if topic is None or topic.status == TopicStatus.archived:
                        return checked, merged, None
                    # Merged on the forge without a delivery: nothing said it was
                    # a step, so the task is done.
                    await delivery_landed(
                        session,
                        task,
                        by=await _merged_by_name(session, status),
                        head=status.head_sha,
                        pr_number=number,
                        completes=True,
                        handed_over=None,
                        record_instruction=record_instruction,
                        task_teammate=task_teammate,
                    )
                    await announce(
                        session,
                        place_id=task.room_id,
                        task_id=task.id,
                        content=say("prMergedDelivered", pr=number),
                        meta=notice(
                            EVENT_ACCEPT_DONE,
                            severity=SEVERITY_INFO,
                            who=WHO_PLATFORM,
                            detail=task.pr_url or "",
                        ),
                    )
                    await session.commit()
                    merged = True
        except Exception as exc:  # one PR must not block the rest
            logger.warning(
                "uncarded task PR poll failed for %s", task_id, exc_info=True
            )
            return checked, merged, f"{task_id}: {exc}"
        return checked, merged, None

    projects: dict[uuid.UUID, list[_TaskPrCandidate]] = {}
    for candidate in candidates:
        projects.setdefault(candidate.project_id, []).append(candidate)

    async def poll_project(project: uuid.UUID, picked: list[_TaskPrCandidate]):
        try:
            async with slots:
                async with sessions() as session:
                    quota = await background_quota(project, session)
                    client = await proposal_client(project, session)
                if client is None:
                    return {item.task_id: (False, False, None) for item in picked}
                reader = getattr(client, "task_pr_metadata", None)
                metadata = None
                if reader is not None:
                    if not await quota_serves_background(quota):
                        return {item.task_id: (False, False, None) for item in picked}
                    metadata = await reader([item.number for item in picked])
                    if not isinstance(metadata, dict):
                        raise GitHubPRError(say("upstreamUnavailable"))
        except Exception as exc:
            logger.warning(
                "task PR metadata batch failed for %s", project, exc_info=True
            )
            return {
                item.task_id: (False, False, f"{item.task_id}: {exc}")
                for item in picked
            }
        # Each provider advances after its own metadata, not after all GitHub
        # queries; a Forgejo project still runs while a GitHub batch waits.
        values = await asyncio.gather(
            *(advance(item, client, quota, metadata) for item in picked)
        )
        return {item.task_id: value for item, value in zip(picked, values, strict=True)}

    project_results = await asyncio.gather(
        *(poll_project(project, picked) for project, picked in projects.items())
    )
    by_task = {key: value for group in project_results for key, value in group.items()}
    outcomes = [by_task[item.task_id] for item in candidates]
    return {
        "tasks_checked": sum(1 for checked, _, _ in outcomes if checked),
        "tasks_merged": sum(1 for _, merged, _ in outcomes if merged),
        "errors": [failure for _, _, failure in outcomes if failure is not None],
    }


async def backfill_missing_merge_actors(chat: Chat) -> dict:
    """Backfill who merged the cards that landed on GitHub before the poller
    recorded it (`AcceptCard.merged_by`).

    Every path but one now records the merger as it watches a merge; this runs
    ONCE per process at takeover, never on a clock (the owner's call on
    2026-10-21: checking every minute for a fact that stops changing is
    waste). What it fills is the window the project overview still shows, so a
    redeploy after the first one finds almost nothing. One GitHub read per
    card, one card per transaction — a slow or failing forge read must not
    hold another card's row or stop the rest."""
    # deferred-import: tests replace this name on app.domain.review.services
    from app.domain.review.services import AcceptService

    sessions = chat.session_factory
    since = datetime.now(UTC) - MERGE_ACTOR_BACKFILL_WINDOW
    async with sessions() as session:
        card_ids = await AcceptService(session).missing_merge_actor_card_ids(
            since=since, limit=MERGE_ACTORS_PER_BACKFILL
        )
    slots = asyncio.Semaphore(PRS_AT_ONCE)

    async def fill(card_id: uuid.UUID) -> bool:
        async with slots:
            async with sessions() as session:
                try:
                    filled = await AcceptService(session).fill_merged_by(card_id)
                    await session.commit()
                    return filled
                except Exception as exc:  # one card must not stop the rest
                    await session.rollback()
                    logger.warning(
                        "merger backfill failed for card %s: %s", card_id, exc
                    )
                    return False

    outcomes = await asyncio.gather(*(fill(card_id) for card_id in card_ids))
    return {
        "cards_checked": len(card_ids),
        "filled": sum(1 for filled in outcomes if filled),
    }


async def _wake_when_checks_pass(
    task_id: uuid.UUID,
    number: int,
    status: PullRequestStatus | TaskPrMetadata,
    client,
    *,
    room_id: uuid.UUID,
    project_id: uuid.UUID,
    pr_url: str | None,
    previous: tuple[str | None, str | None],
    announce: Announce,
    record_instruction: RecordInstruction,
    sessions,
) -> None:
    """Say 「检查全绿，可以递卡了」 once, on the tick the state turns green.

    `poll_open_prs` walks cards, so a task with a PR but no card is watched by
    nobody: the line learns its checks passed whenever its own turn comes up,
    which on a busy seat can be tens of minutes after CI finished (2026-10-18,
    thirteen lines waited 97 minutes between green and filing their card,
    measured over two weeks). This is that one word, said in the task's room
    and recorded for its seat.

    Silent unless this is a transition: the same head was ``running`` last tick
    and this tick is ``green``. Nothing is said for a head never seen before
    (the first tick of a process, a push), for a PR that is not open, for a
    task that is no longer open, or on a forge whose client has no `check_runs`
    (Forgejo's does, but the seam is optional and the GitHub shape is what this
    reads).

    The open-task re-read is the whole point of the sentence: it tells a line
    to file its card, and this poll's query deliberately keeps closed tasks
    whose PR is still open (`or_(delivered_head IS NULL, status == open)` —
    that arm is what settles a PR somebody merged on the forge for a task they
    had already closed). A close whose PR-close call failed, or a room archived
    and unarchived, leaves exactly that state with the PR still open, and
    without this check the closed task would be told to file a card nobody
    will ever file — and `record_instruction` would write a Delivery row that
    the dispatcher refuses with 「任务已不再开放」. The row is read again
    rather than carried from the caller for the same reason the merged branch
    re-reads it: the answer that matters is the one at the write.

    GitHub answers a repeated read with 304 (`forge_etags`), which is not
    charged to the installation's quota, so asking every tick costs nothing in
    the steady state.
    """
    check_runs = getattr(client, "check_runs", None)
    if check_runs is None or status.state != "open":
        return
    state = _checks_state(await check_runs(status.head_sha))
    head = status.head_sha
    if previous != (head, CHECKS_RUNNING) or state != CHECKS_GREEN:
        if previous != (head, state):
            await _remember_checks(
                sessions, task_id, head, state, project_id=project_id, number=number
            )
        return
    async with sessions() as session:
        task = await lock_task_for_change(session, task_id)
        if task is None or task.project_id != project_id or task.pr_number != number:
            return
        if task.status is not TaskStatus.open or task.closing_since is not None:
            if task is not None:
                _set_checks(task, head, state)
                await session.commit()
            return
        # With a card waiting, the line has nothing to file: it waits for the
        # reviewer, so the sentence says that instead.
        waiting = await session.scalar(
            select(AcceptCard.id)
            .where(
                AcceptCard.task_id == task_id,
                AcceptCard.pr_number == number,
                AcceptCard.status == AcceptStatus.pending,
            )
            .limit(1)
        )
        content = say(
            "checksPassedAwaitingReview" if waiting else "checksPassed", pr=number
        )
        await record_instruction(
            session,
            DeliveryEvent(
                id=uuid.uuid4(),
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(project_id),
                    "topicId": str(room_id),
                },
                occurred_at=datetime.now(UTC),
            ),
            task_id=task_id,
            content=content,
        )
        await announce(
            session,
            place_id=room_id,
            task_id=task_id,
            content=content,
            meta=notice(
                EVENT_ACCEPT_READY,
                severity=SEVERITY_INFO,
                who=WHO_CHEESE,
                detail=pr_url or "",
            ),
        )
        # Written with the sentence in one commit: a failed wake leaves the
        # stored state at «running», so the next tick says it again.
        _set_checks(task, head, state)
        await session.commit()


def _set_checks(task: Task, head: str, state: str) -> None:
    task.pr_checks_head = head
    task.pr_checks_state = state


async def _remember_checks(
    sessions,
    task_id: uuid.UUID,
    head: str,
    state: str,
    *,
    project_id: uuid.UUID,
    number: int,
) -> None:
    async with sessions() as session:
        task = await lock_task_for_change(session, task_id)
        if (
            task is not None
            and task.project_id == project_id
            and task.pr_number == number
        ):
            _set_checks(task, head, state)
            await session.commit()


async def sweep_closings(
    chat: Chat,
    announce: Announce,
    *,
    record_instruction: RecordInstruction,
    task_teammate: TaskTeammate,
    machines: SweepCheckouts | None = None,
) -> dict:
    """The tasks being written up: one whose AI teammate has gone on working
    goes on to its next step (`task_landing.go_on_while_working`), and one
    past its write-up deadline is closed and its room told what was left
    undone (`room_task.closing.close_overdue`). ``machines`` removes a closed
    task's checkouts on the machines holding it."""
    sessions = chat.session_factory
    gone_on = await go_on_while_working(
        sessions,
        record_instruction=record_instruction,
        task_teammate=task_teammate,
    )
    async with sessions() as session:
        closed = await close_overdue(session, chat, machines=machines)
        for overdue in closed:
            await tell_room_undelivered(session, overdue, announce=announce)
        await session.commit()
    return {"gone_on": len(gone_on), "closed": len(closed)}


def _report_card_failure(card_id: uuid.UUID, exc: BaseException) -> None:
    """Loudly, unless the network is the only thing that went wrong and the
    retries have not yet run out of excuses."""
    if not isinstance(exc, httpx.TransportError):
        _transient_misses.pop(card_id, None)
        logger.exception("poll_open_prs failed for card %s", card_id)
        return
    misses = _transient_misses.get(card_id, 0) + 1
    _transient_misses[card_id] = misses
    if misses < TRANSIENT_MISSES_BEFORE_ERROR:
        logger.warning(
            "poll_open_prs could not reach the network for card %s "
            "(%d in a row, reporting at %d): %r",
            card_id,
            misses,
            TRANSIENT_MISSES_BEFORE_ERROR,
            exc,
        )
        return
    logger.exception(
        "poll_open_prs has been unable to reach the network for card %s "
        "for %d ticks in a row",
        card_id,
        misses,
    )


async def _note_card_poll_crashed(
    chat: Chat,
    announce: Announce,
    card_id: uuid.UUID,
    exc: BaseException,
    *,
    settle_card: SettleCard,
    record_instruction: RecordInstruction,
    task_teammate: TaskTeammate,
) -> None:
    """Leave the crash on the card, in its own transaction.

    The rollback above throws away everything the failed tick wrote — which is
    right for the state machine and wrong for the reader: the card keeps showing
    whatever it said before, usually 「等 CI」, while every tick dies the same
    way. A person watching a green PR that never merges has no way to tell that
    apart from slow checks. So the explanation is written by a SEPARATE session
    that the rollback cannot take with it.

    Best-effort by construction: if even this write fails, the log line above is
    still there and the poll loop keeps going.
    """
    # deferred-import: tests replace this name on app.domain.review.services
    from app.domain.review.services import AcceptService

    try:
        async with chat.session_factory() as session:
            await AcceptService(
                session,
                announce=announce,
                settle_card=settle_card,
                record_instruction=record_instruction,
                task_teammate=task_teammate,
            ).note_poll_crashed(card_id, exc)
            await session.commit()
    except Exception:  # never let the explanation kill the loop
        logger.exception("could not record poll failure on card %s", card_id)


async def open_draft_prs(
    chat: Chat,
    announce: Announce,
    *,
    dispatch_pending: DispatchPending,
    record_instruction: RecordInstruction,
    quiet_window_s: int | None = None,
) -> dict:
    """有东西就有 PR (#718 拍板①): give every batch with commits a draft PR,
    without waiting for anyone to file a card.

    The observation and every reason it is an observation rather than a hook
    live in `pr_publish.sweep_draft_prs`; this is only the clock. The
    periodic one passes ``quiet_window_s`` to keep the tick cheap; a catch-up
    call (a reconnect, a lost event) leaves it ``None`` so every open batch is
    looked at.
    """

    result = dict(
        await pr_publish.sweep_draft_prs(
            chat.session_factory,
            announce=announce,
            record_instruction=record_instruction,
            quiet_window_s=quiet_window_s,
        )
    )
    await deliver_dependency_notices(chat, dispatch_pending=dispatch_pending)
    return result


async def deliver_dependency_notices(
    chat: Chat, *, dispatch_pending: DispatchPending
) -> None:
    """Dispatch committed task-parent intent through the delivery ledger.

    ``dispatch_pending`` 是包好了会话工厂、房间服务和跑机器的那个零参调用
    （`app.domain.review.ports.DispatchPending`）—— 这里只决定「现在放」。
    """
    await dispatch_pending()


async def forge_repository_changed(
    chat: Chat,
    kind: str,
    repo: str,
    project_id: str | None = None,
    *,
    announce: Announce,
    settle_card: SettleCard,
    record_instruction: RecordInstruction,
    dispatch_pending: DispatchPending,
    task_teammate: TaskTeammate,
) -> None:
    """One repository moved — from a relayed forge event, or from a reconnect."""

    sessions = chat.session_factory
    query = select(ProjectForge.project_id).where(
        ProjectForge.kind == kind, ProjectForge.repo == repo
    )
    if project_id is not None:
        query = query.where(ProjectForge.project_id == uuid.UUID(project_id))
    async with sessions() as session:
        projects = list(await session.scalars(query))
    for changed_project_id in projects:
        await poll_open_prs(
            chat,
            announce,
            changed_project_id,
            settle_card=settle_card,
            record_instruction=record_instruction,
            task_teammate=task_teammate,
        )
        await pr_publish.sweep_draft_prs(
            sessions,
            changed_project_id,
            announce=announce,
            record_instruction=record_instruction,
        )
    await deliver_dependency_notices(chat, dispatch_pending=dispatch_pending)
