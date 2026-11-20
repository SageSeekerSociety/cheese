"""What a task's delivery landing does to the task.

A task may deliver several times, and no delivery says by itself that it is
the last: a card does not carry it (the old ``AcceptCard.completes_task`` is
kept on old rows and read by nothing), and a person accepting one only says
this step is good. So every merge that lands leaves the task open and moves it
onto a branch cut from the project's latest code. Who then judges whether the
task is done depends on who coordinates it:

* A task the channel's main line opened (`main_line_opener`): the main line.
  It is told the delivery was adopted and the task is still open
  (`task_landed_opener_prompt`) — in the reply of the adoption it made itself,
  when the merge landed inside that call, or woken in the main line otherwise
  (`wake_opener`) — and closes the task with a conclusion, tells it the next
  step, or asks the member. The task's own teammate is not woken: it is told
  on its next input that the delivery landed and the main line decides
  (`heard.inform`), and it does not close the task (the close route refuses).
* Any other task — made by a person, turned from a message or a 支线 — its AI
  teammate, told to judge against the task's goal, what the task document
  still has open, its checklist, and what the owner said, and either carry on
  or write the task up and close it (`task_landed_prompt`). Whether that close
  is allowed is the close route's to check (`close_grounds`): the platform
  does not read the task document, it checks facts.

Either way the discussion the task came from hears what landed: from the
main line in its own words when it adopted the delivery on the reviewer's
word, from the platform's line otherwise (`delivery_landed`).

A merge nobody filed a card for — a PR merged on the forge by hand — leaves
the task open as well; its AI teammate is not woken, and the owner is told in
the task that it goes on (`pr_poll`).

In a project whose deploy reports back (`deploy_scope`), a close after the
task's last merge waits, open, until a deploy containing that merge is live
(`Task.deploying_since`, `deploy_wait.ship`): the teammate's close is followed
by its write-up then; the main line's close keeps its conclusion
(`Task.held_conclusion`) and the task closes with it then (`deploy_landed`).
Steps before it do not wait. Nothing else is watched after the merge: the
repository's CI and deploy on its default branch are its own.

When the task's own AI teammate's session is at work on a message it read
that was sent after the delivery was handed over, or a message to it there is
still waiting to be read — someone asked for more after the card went up, and
accepted it anyway — it is told the merge landed while it works
(`task_step_landed_while_working_prompt`), not asked to judge the task done in
the middle of that work. The same holds for a task already being written up
when such work starts in it (`go_on_while_working`). Only the task's own
teammate's work counts, and only what a person or the teammate itself asked
for: an instruction the platform gave — another seat woken in the task, a
conflict or CI fix — is not more work asked of it
(`room_task.closing.unfinished`).

Every path that sees a delivery merge comes through here — an accept click,
the merge queue, the poller, a PR merged by hand — so none of them can close a
task on a merge, and none of them can land one merge on a task twice.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_frames import show_once_committed
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.delivery_events import DeliveryEvent, event_id_for
from app.domain.identity.actor import acted_by
from app.domain.identity.handles import AGENT_HANDLE_PREFIX, agent_instance_handle
from app.domain.notice_meta import (
    EVENT_TASK_ENDED,
    EVENT_TASK_FILED,
    EVENT_TASK_LANDED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.notification.models import NotificationType
from app.domain.project.models import Project
from app.domain.prompts import piece
from app.domain.review.deploy_scope import waits_for_deploy
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.ports import (
    Announce,
    RecordInstruction,
    RecordSeatInstruction,
    TaskTeammate,
)
from app.domain.review.task_pr import close_pr_after_commit
from app.domain.room_task import heard
from app.domain.room_task.closing import CLOSES_TASK, close_task, unfinished
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService, said_title
from app.domain.thread.services import open_thread, thread_of
from app.domain.topic.models import Topic, TopicStatus
from app.domain.user.services import names_by_handles

logger = logging.getLogger(__name__)


def handed_in(task_teammate: TaskTeammate | None) -> TaskTeammate:
    """把 `review.ports.TaskTeammate` 要回来；没递进来当场炸掉，不走静默。

    少递这一样不会有别的反应：这个队友自己干的活没人认（任务该接着开却关了），
    或者它该署名的那些话没人署名 —— 两样都看着像什么都没发生，比报错难查得多
    （`app.domain.review.ports`）。所以缺了就地报错。"""
    if task_teammate is None:  # pragma: no cover - 接线漏了才会走到
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError("task_teammate was not handed in")
    return task_teammate


@dataclass(frozen=True, slots=True)
class Landed:
    """A merge landed on its task (`delivery_landed`). ``for_adopter``: what
    the channel's main line that adopted the delivery in the very call that
    landed it reads in that call's reply — told there instead of being woken
    for it (`accept_coordinated_task`). None when there is nothing for it to
    read, or it is woken instead."""

    for_adopter: str | None = None


async def delivery_landed(
    session: AsyncSession,
    task: Task,
    *,
    by: str | None,
    head: str | None,
    pr_number: int | None,
    handed_over: datetime | None,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    announce: Announce | None = None,
    card_id: uuid.UUID | None = None,
    record_seat: RecordSeatInstruction | None = None,
    adopting_seat: str | None = None,
) -> Landed | None:
    """``task``'s delivery merged. ``handed_over``: when its card went up; None
    for a merge no card asked for, which wakes nobody (module docstring).

    A delivery someone adopted is recorded where the task came from as one
    platform line naming who adopted it (`_step_landed`, ``announce``), not
    said by the teammate: the reviewer's decision is something that happened,
    not anyone's words. Unless the channel's main line that opened the task
    adopted it on the reviewer's word (`_adopted_by_opener`): it says so
    itself, and the platform records nothing there. A merge nobody adopted on
    the platform is recorded the same way, naming nobody.

    Whether the task is done is then judged by the channel's main line that
    opened it, when one did, and by the task's own teammate otherwise (module
    docstring). ``card_id`` names the card whose delivery landed, so the main
    line is told of it once and told which version it was and whose
    (`task_landed_opener_prompt`); ``record_seat`` tells it
    (`ports.RecordSeatInstruction`). ``adopting_seat``: the main line seat
    adopting this delivery in the call that lands it, which reads what it is
    told in that call's reply (`Landed.for_adopter`).

    Says whether the merge landed on the task this time (None when not): one
    that has already landed there does not land twice (`_already_landed`)."""
    if _already_landed(task, head):
        return None
    card = await session.get(AcceptCard, card_id) if card_id is not None else None
    now = datetime.now(UTC)
    task.accepted_at = now
    task.accepted_by = by or task.accepted_by
    if head:
        task.delivered_head = head[:64]
    # A task being written up, or waiting for its deploy, already had its
    # teammate's word that it is done; a merge landing on it now (one merged
    # by hand, say) is recorded and moves nothing — the deploy it waits for
    # is read off `accepted_at`, which this merge just moved.
    goes_on = (
        task.status == TaskStatus.open
        and task.closing_since is None
        and task.deploying_since is None
    )
    for_adopter = None
    if goes_on and handed_over is None:
        await TaskService(session).next_step(task)
    elif goes_on and await _asked_for_more(
        session, task, since=handed_over, task_teammate=task_teammate
    ):
        await _go_on(session, task, record_instruction=record_instruction)
    elif goes_on and (opener := await main_line_opener(session, task)) is not None:
        await TaskService(session).next_step(task)
        # Its teammate is not woken: whether the task is done is the main
        # line's call. It reads on its next input where the task stands.
        await heard.inform(
            session,
            task,
            task_landed_coordinated_prompt(
                title=task.title,
                task_id=task.id,
                has_checkout=task.branch_name is not None,
            ),
        )
        told = await task_landed_opener_prompt(session, task, by=by, card=card)
        if adopting_seat == opener:
            for_adopter = told
        else:
            await wake_opener(
                session,
                task,
                key=f"{EVENT_TASK_LANDED}:{card_id or head or now.isoformat()}",
                event_type=EVENT_TASK_LANDED,
                content=told,
                record_seat=record_seat,
            )
    elif goes_on:
        await TaskService(session).next_step(task)
        await tell_agent(
            session,
            task,
            task_landed_prompt(
                title=task.title,
                task_id=task.id,
                has_checkout=task.branch_name is not None,
                waits_for_deploy=(
                    head is not None
                    and pr_number is not None
                    and await waits_for_deploy(session, task.project_id)
                ),
            ),
            record_instruction=record_instruction,
        )
    await session.flush()
    adopted_by = by if handed_over is not None else None
    if goes_on and await _adopted_by_opener(session, task, adopted_by):
        # The main line adopted it on the reviewer's word, and says so in its
        # own reply; the task's own conversation keeps the card's record.
        pass
    elif goes_on:
        await record_at_origin(
            session,
            task,
            EVENT_TASK_LANDED,
            await _step_landed(session, task, adopted_by, card=card),
            announce=announced(announce),
        )
    return Landed(for_adopter=for_adopter)


async def wait_for_deploy(
    session: AsyncSession, task: Task, *, held: str | None = None
) -> bool:
    """``task`` is closed as done: in a project whose deploy reports back,
    whose last delivery was a merge, the task waits open for a deploy
    containing that merge (`deploy_landed`). Says whether it waits: one
    already waiting goes on waiting, and closing it again does not skip its
    deploy. A task being written up already waited, or never had to; one with
    no merge delivered has nothing to deploy.

    ``held``: the conclusion the channel's main line that opened the task
    closed it with (empty when it gave none), which the task closes with once
    the merge is live; None for its own teammate's close, which is followed by
    its write-up then. Closing again while it waits replaces it."""
    if task.status == TaskStatus.open and task.deploying_since is not None:
        if held is not None:
            task.held_conclusion = held
            await session.flush()
        return True
    if (
        task.status != TaskStatus.open
        or task.closing_since is not None
        or task.delivered_head is None
        or not await waits_for_deploy(session, task.project_id)
    ):
        return False
    task.deploying_since = datetime.now(UTC)
    task.deploy_blocked_url = None
    task.held_conclusion = held
    await session.flush()
    return True


async def _write_up(
    session: AsyncSession,
    task: Task,
    *,
    record_instruction: RecordInstruction | None,
    deployed: bool = False,
) -> None:
    """``task`` is done and, where it waits for its deploy, live: its AI
    teammate writes it up and closes it (`room_task.closing`). Never for a
    task the channel's main line coordinates: the main line closes that one
    (`deploy_landed`)."""
    task.closing_since = datetime.now(UTC)
    await tell_agent(
        session,
        task,
        task_summary_prompt(
            title=task.title,
            has_checkout=task.branch_name is not None,
            deployed=deployed,
        ),
        record_instruction=record_instruction,
        closes=True,
    )


async def deploy_landed(
    session: AsyncSession,
    task: Task,
    *,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    announce: Announce | None = None,
    record_seat: RecordSeatInstruction | None = None,
) -> bool:
    """A deploy containing the merge ``task`` waits for is live: the task
    stops waiting. One the channel's main line closed closes now with the
    conclusion it gave (`Task.held_conclusion`), and the main line is woken
    to tell the end in its own words (`tell_origin_ended`) — it did not cause
    this moment, so its close's reply could not have told it; the task's AI
    teammate is not woken to write it up. Otherwise its AI teammate —
    who already judged it done (`wait_for_deploy`) — writes it up and closes
    it. Unless it was asked for more while it waited: then that work goes on as
    the next step, as it does for a merge that lands while such work runs
    (module docstring), and a held conclusion is dropped — the task is not
    over, and the main line that gave it is told so.

    Says whether the task was waiting; one that is not is left alone, so a
    second report of the same deploy changes nothing. The caller holds the
    task's row (`room_task.lock_order`)."""
    if task.deploying_since is None:
        return False
    waited_since = task.deploying_since
    task.deploying_since = None
    task.deploy_blocked_url = None
    held, task.held_conclusion = task.held_conclusion, None
    if task.status != TaskStatus.open:
        await session.flush()
        return True
    if await _asked_for_more(
        session,
        task,
        since=await _handed_over(session, task),
        task_teammate=task_teammate,
    ):
        await _go_on(session, task, record_instruction=record_instruction)
        await record_at_origin(
            session,
            task,
            EVENT_TASK_LANDED,
            await _step_landed(session, task, None),
            announce=announced(announce),
        )
        if held is not None:
            # The main line closed it and was told it would close once live:
            # it hears that it did not, and why.
            await wake_opener(
                session,
                task,
                key=f"task_went_on:{task.id}:{waited_since.isoformat()}",
                event_type=EVENT_TASK_LANDED,
                content=piece("main_line/report_went_on", task=task_named(task)),
                record_seat=record_seat,
            )
    elif held is not None or await main_line_opener(session, task) is not None:
        await _close_as_concluded(
            session,
            task,
            conclusion=held or None,
            by_opener=held is not None,
            announce=announce,
            record_seat=record_seat,
        )
    else:
        await _write_up(
            session, task, record_instruction=record_instruction, deployed=True
        )
    await session.flush()
    return True


async def _close_as_concluded(
    session: AsyncSession,
    task: Task,
    *,
    conclusion: str | None,
    by_opener: bool,
    announce: Announce | None,
    record_seat: RecordSeatInstruction | None,
) -> None:
    """Close ``task`` with the conclusion the channel's main line gave it,
    once the merge it waited for is live. Its conversation records how it
    ended; the discussion it came from hears it from the main line, told to
    say it, when the main line closed it (``by_opener``), and from the
    platform otherwise (`tell_origin_ended`). A session
    still at work in it is stopped by the session's holder once the task has
    been closed a while (`room_task.closing.close_task`): nothing here holds a
    running session."""
    if announce is None:  # pragma: no cover - 接线漏了才会走到
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError("announce was not handed in")
    await close_task(session, task, None, conclusion=conclusion)
    close_pr_after_commit(session, task)
    ended = (
        say("taskCompleted", title=said_title(task), conclusion=task.conclusion)
        if task.conclusion
        else say("taskClosed", title=said_title(task))
    )
    await announce(
        session,
        place_id=task.room_id,
        task_id=task.id,
        content=ended,
        meta=heard.informing(
            {"platform": True, "action": "task_closed", "task_id": str(task.id)},
            heard.task_closed_deployed(conclusion=task.conclusion),
        ),
    )
    await tell_origin_ended(
        session,
        task,
        announce=announce,
        record_seat=record_seat,
        concluded_by_opener=by_opener,
    )


async def _step_landed(
    session: AsyncSession,
    task: Task,
    by: str | None,
    *,
    card: AcceptCard | None = None,
) -> str:
    """The origin's line for a step that landed with the task going on: who
    adopted it (``by``, named by `_adopted_by`), as one line with no PR number
    in it. A member who handed the version in and adopted it in the same go
    (「提交并采纳」) did one thing, and the line names the version they handed
    in: its hand-in recorded nothing there (`machine.hand_in`). With nobody to
    name — a PR merged on the forge by hand — it says only that it landed."""
    if (
        card is not None
        and by is not None
        and card.handed_in_by == by
        and card.auto_merge_adopts
    ):
        return say(
            "taskHandedInAndAdopted",
            who=f"<@{by}>",
            title=card.change_subject or said_title(task),
        )
    who = await _adopted_by(session, task, by)
    if who is not None:
        return say("taskAdopted", who=who, title=said_title(task))
    return say("taskStepLandedNoPr", title=said_title(task))


def announced(announce: Announce | None) -> Announce:
    """把 `review.ports.Announce` 要回来；没递进来当场炸掉（同 `handed_in`）：少了
    它，任务出自的那段对话就少一行记录，看着像什么都没发生。"""
    if announce is None:  # pragma: no cover - 接线漏了才会走到
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError("announce was not handed in")
    return announce


async def tell_origin_ended(
    session: AsyncSession,
    task: Task,
    *,
    announce: Announce,
    record_seat: RecordSeatInstruction | None,
    closed_by: str | None = None,
    concluded_by_opener: bool = False,
) -> None:
    """Tell the conversation ``task`` came from how it ended, once it is
    closed: its write-up when it has one; that it was accepted, when its last
    delivery landed and nobody wrote it up (the write-up was given up on, or
    ran out of time); otherwise that it was closed. Every path that closes a
    task calls this, so the conversation the task came from always hears the
    end.

    An end the AI teammate that opened the task from the channel's main line
    brought about is said by it, in its own words, and the platform records
    nothing there: its own close (``closed_by`` is its seat), which it read in
    the close's reply and is not woken about; and the close with the
    conclusion it gave once the merge it waited for is live
    (``concluded_by_opener``), which it is woken to tell — the platform's line
    is recorded only if it could not be. Any other end is the platform's line
    (`record_at_origin`). A person who closed it by hand said so by clicking:
    the opener, if any, is not woken to say it again, and reads who closed it
    and how it ended in its next input, on that line. An end nobody brought
    about by hand — the task's own teammate's close, one the platform made —
    wakes the opener with the same facts to say the result (`wake_opener`)."""
    line, facts = _how_it_ended(task)
    opener = await main_line_opener(session, task)
    if closed_by is not None and closed_by == opener:
        return
    if (
        opener is not None
        and closed_by is not None
        and not closed_by.startswith(AGENT_HANDLE_PREFIX)
    ):
        names = await names_by_handles(session, [closed_by])
        await record_at_origin(
            session,
            task,
            EVENT_TASK_ENDED,
            line,
            announce=announce,
            informs=piece(
                "main_line/report_closed_by",
                task=task_named(task),
                by=_member(closed_by, names),
                facts=facts,
            ),
        )
        return
    names = await names_by_handles(session, _askers(task))
    closed_at = task.closed_at.astimezone(UTC).isoformat() if task.closed_at else ""
    told = await wake_opener(
        session,
        task,
        # A task reopened and closed again ends again: its close time tells
        # the two ends apart, and the same end reported twice is told once.
        key=f"{EVENT_TASK_ENDED}:{task.id}:{closed_at}",
        event_type=EVENT_TASK_ENDED,
        content=task_ended_opener_prompt(task, facts=facts, names=names),
        record_seat=record_seat,
    )
    if not (concluded_by_opener and told):
        await record_at_origin(session, task, EVENT_TASK_ENDED, line, announce=announce)


async def _adopted_by(
    session: AsyncSession, task: Task, by: str | None
) -> object | None:
    """How a room line names ``by``, who adopted ``task``'s delivery, from what
    the card recorded: the person (``<@handle>``); 「<teammate>代 <person>」
    when a project AI teammate adopted it on their word (`decided_by_seat`);
    the existing 「芝士代 <person>」 (`acted_by`) when their own personal
    assistant did, and on cards from before the seat was kept. None when
    nobody adopted it."""
    if by is None:
        return None
    person = f"<@{by}>"
    card = await session.scalar(
        select(AcceptCard)
        .where(AcceptCard.task_id == task.id, AcceptCard.decided_by == by)
        .order_by(AcceptCard.created_at.desc())
        .limit(1)
    )
    if card is None or not card.decided_by_assistant:
        return person
    if card.decided_by_seat is not None:
        return say("actedByTeammate", agent=f"<@{card.decided_by_seat}>", person=person)
    return acted_by(by, by_assistant=True)


async def _adopted_by_opener(session: AsyncSession, task: Task, by: str | None) -> bool:
    """Whether ``task``'s delivery that ``by`` adopted was adopted for them by
    the AI teammate that opened the task from the channel's main line, on
    their word (the card's `decided_by_seat`), whether it lands in that call
    or later."""
    if by is None:
        return False
    opener = await main_line_opener(session, task)
    if opener is None:
        return False
    card = await session.scalar(
        select(AcceptCard)
        .where(AcceptCard.task_id == task.id, AcceptCard.decided_by == by)
        .order_by(AcceptCard.created_at.desc())
        .limit(1)
    )
    return (
        card is not None
        and card.decided_by_assistant
        and card.decided_by_seat == opener
    )


def _how_it_ended(task: Task) -> tuple[str, str]:
    """How ``task`` ended: the line its origin records, and the same facts as
    its opener is told them. One reading of the task's row for both, so the
    two never say different things. Who adopted what landed was said when it
    landed (`_step_landed`); the end says how the task ended, with no PR
    number in the sentence."""
    accepted = (
        f"交付已采纳（PR #{task.pr_number}）"
        if task.pr_number is not None
        else "交付已采纳"
    )
    if task.conclusion:
        line = say("taskCompleted", title=said_title(task), conclusion=task.conclusion)
        facts = (
            f"{accepted}，结论是「{task.conclusion}」"
            if task.accepted_at is not None
            else f"没有哪一版交付被采纳，关闭时的结论是「{task.conclusion}」"
        )
    elif task.accepted_at is not None:
        line = say("taskLandedNoPr", title=said_title(task))
        facts = f"{accepted}，任务总结没有写成"
    else:
        line = say("taskClosed", title=said_title(task))
        facts = "没有哪一版交付被采纳，关闭时也没有写结论"
    return line, facts


async def tell_opener_filed(
    session: AsyncSession,
    task: Task,
    card: AcceptCard,
    *,
    version: int,
    reviewers: list[str],
    record_seat: RecordSeatInstruction | None,
) -> bool:
    """``task`` handed ``card`` in as its ``version``-th version: the AI
    teammate that opened it from the channel's main line is told what was
    handed in and who reviews it, so it can say so there (`wake_opener`).
    Each card is one version, so each is told once. Says whether it was told:
    when it was, it says the hand-in itself, and the platform records no line
    for it there."""
    names = await names_by_handles(session, [*reviewers, *_askers(task)])
    return await wake_opener(
        session,
        task,
        key=f"{EVENT_TASK_FILED}:{card.id}",
        event_type=EVENT_TASK_FILED,
        content=task_filed_opener_prompt(
            task,
            version=version,
            reviewers=reviewers,
            names=names,
            subject=card.change_subject,
            body=card.change_body,
            focus=card.focus,
        ),
        record_seat=record_seat,
    )


async def wake_opener(
    session: AsyncSession,
    task: Task,
    *,
    key: str,
    event_type: str,
    content: str,
    record_seat: RecordSeatInstruction | None,
) -> bool:
    """Tell ``content`` to the AI teammate that opened ``task`` from something
    said in its channel's main line, in that main line, as a platform
    instruction it reads on its next turn (or in the work it is doing).

    Only that one: a task a person made, or turned from a message, wakes
    nobody, and nor does one that came from a 支线 or from nowhere — the
    origin's line records what happened there either way. The opener is
    ``created_by``, which a teammate's own `cheese_task(action=open)` writes as
    its seat; a seat this project no longer has is told nothing.

    ``key`` names the happening; the instruction's identity is derived from
    it, so the same happening is told once however often it is reported.

    Says whether it was told: a caller that leaves the saying to it records
    the platform's line only when nobody was. Best effort, as
    `record_at_origin`: telling never undoes what it tells. ``record_seat``
    missing where someone is to be told is a door that did not hand it in
    (`review.ports.RecordSeatInstruction`), and fails loudly."""
    opener = await main_line_opener(session, task)
    if opener is None:
        return False
    if record_seat is None:  # pragma: no cover - 接线漏了才会走到
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError("record_seat was not handed in")
    try:
        async with session.begin_nested():
            return await record_seat(
                session,
                DeliveryEvent(
                    id=event_id_for(NotificationType.ROOM_NOTICE, key),
                    type=NotificationType.ROOM_NOTICE,
                    payload={
                        "projectId": str(task.project_id),
                        "topicId": str(task.room_id),
                        "taskId": str(task.id),
                        "eventType": event_type,
                        "severity": SEVERITY_INFO,
                    },
                    occurred_at=datetime.now(UTC),
                ),
                project_id=task.project_id,
                conversation_id=task.room_id,
                seat=opener,
                content=content,
            )
    except Exception:  # telling never undoes what it tells
        logger.warning("could not tell task %s's opener", task.id, exc_info=True)
        return False


async def go_on_while_working(
    sessions,
    *,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    announce: Announce,
) -> list[uuid.UUID]:
    """Every task being written up whose AI teammate has started work in it
    since its last delivery went up, or has a message waiting there, stops
    being written up and goes on, like a last step that landed while that
    work was running (module docstring). Returns the ids of those that did.

    Each task is looked at in transactions of its own: one whose forge cannot
    be reached is tried again on the next tick and holds up neither the other
    tasks nor the rest of the watch. Its next branch is read from the forge
    before its row is locked, and a row someone else holds is left for the
    next tick."""
    async with sessions() as session:
        being_written_up = list(
            await session.scalars(
                select(Task.id).where(Task.closing_since.is_not(None))
            )
        )
    gone_on = []
    for task_id in being_written_up:
        try:
            if await _go_on_if_asked(
                sessions,
                task_id,
                record_instruction=record_instruction,
                task_teammate=task_teammate,
                announce=announce,
            ):
                gone_on.append(task_id)
        except Exception:  # one task must not stop the rest
            logger.warning("could not take task %s on", task_id, exc_info=True)
    return gone_on


async def _go_on_if_asked(
    sessions,
    task_id: uuid.UUID,
    *,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    announce: Announce,
) -> bool:
    # deferred-import: tests replace this name on app.domain.project.forge
    from app.domain.project.forge import default_branch

    async with sessions() as session:
        task = await session.get(Task, task_id)
        if task is None or task.closing_since is None:
            return False
        if not await _asked_for_more(
            session,
            task,
            since=await _handed_over(session, task),
            task_teammate=task_teammate,
        ):
            return False
        base = (
            await default_branch(task.project_id, session)
            if task.branch_name is not None
            else None
        )
        await session.commit()
    async with sessions() as session:
        task = await session.scalar(
            select(Task)
            .where(Task.id == task_id, Task.closing_since.is_not(None))
            # FOR NO KEY UPDATE, after its numbering: the task lock order
            # (`room_task.lock_order`).
            .with_for_update(skip_locked=True, key_share=True)
        )
        if task is None:
            return False
        task.closing_since = None
        await _go_on(
            session, task, base_branch=base, record_instruction=record_instruction
        )
        # Its landing said nothing at the origin, as a last step's does; it is
        # a step now, and the origin hears it as one.
        await record_at_origin(
            session,
            task,
            EVENT_TASK_LANDED,
            await _step_landed(session, task, None),
            announce=announce,
        )
        await session.commit()
    return True


async def _asked_for_more(
    session: AsyncSession,
    task: Task,
    *,
    since: datetime | None,
    task_teammate: TaskTeammate | None,
) -> list[str]:
    """What ``task``'s own AI teammate is doing in it, begun after ``since``,
    or has waiting there for it (`room_task.closing.unfinished`)."""
    teammate = await handed_in(task_teammate)(
        session, task.project_id, task.room_id, task.agent_handle
    )
    if teammate is None:
        # 项目和房间都不在了：这个任务无处落脚，也就没有队友的活要认。
        return []
    instance_id, handle = teammate
    return await unfinished(
        session,
        task.id,
        since=since,
        by=handle,
        seat=agent_instance_handle(instance_id),
    )


async def _handed_over(session: AsyncSession, task: Task) -> datetime | None:
    """When the card of the delivery ``task`` landed last went up: its latest
    accepted card. None when no card of it was ever accepted."""
    return await session.scalar(
        select(AcceptCard.created_at)
        .where(
            AcceptCard.task_id == task.id,
            AcceptCard.status == AcceptStatus.accepted,
        )
        .order_by(AcceptCard.created_at.desc())
        .limit(1)
    )


async def _go_on(
    session: AsyncSession,
    task: Task,
    *,
    record_instruction: RecordInstruction | None,
    base_branch: str | None = None,
) -> None:
    """``task``'s last step landed while its AI teammate was at work on more:
    it goes on to the next step, and its AI teammate hears so. The write-up
    instruction, if one was given, is not sent
    (`delivery.agent.dispatch_pending`)."""
    await TaskService(session).next_step(task, base_branch=base_branch)
    await tell_agent(
        session,
        task,
        task_step_landed_while_working_prompt(
            title=task.title,
            task_id=task.id,
            has_checkout=task.branch_name is not None,
            coordinated=await main_line_opener(session, task) is not None,
        ),
        record_instruction=record_instruction,
    )


def _already_landed(task: Task, head: str | None) -> bool:
    """Whether this merge has already landed on ``task``: the commit it put on
    the branch is the one the task's latest delivery delivered.

    Landing a merge a second time would drag a task that has since gone on
    from the next step, or been reopened, back into a write-up window it has
    already been through. The task keeps `delivered_head` across both of those
    (`room_task.services.TaskService.next_step` and `reopen`), so the commit is
    what tells a second look at the same merge apart from the next delivery.

    A delivery is told the head it landed by every path that sees a merge; one
    that is not (a discussion with no change, an artifact, a URL) has no commit
    to compare and is not held back."""
    if head is None:
        return False
    return task.delivered_head is not None and task.delivered_head == head[:64]


async def record_at_origin(
    session: AsyncSession,
    task: Task,
    event_type: str,
    content: str,
    *,
    announce: Announce,
    refers: dict | None = None,
    informs: str | None = None,
) -> None:
    """Record in the conversation ``task`` was opened from that something
    happened to it: a version handed in for review, adopted or returned, a
    step that landed, the task ended.

    It is the platform's record, not anyone's words: a platform line
    (`kind=event`) under nobody's name that wakes nobody (``who`` is the
    platform's). A task opened from something said in the channel's main line
    is recorded there; one opened from a 支线 in the 支线 under the message it
    hangs under (opened if nobody has replied there yet). A task made on its
    own, or from something that is not a message in a live channel, has
    nowhere to record it; nor does a 支线 turned into the task in place, whose
    discussion is the task's own conversation. Whoever waits on the task is
    told by the task's own lines (`room_notices._announce_filed`). ``refers``
    is what else the line points at, besides the task: a hand-in names the
    card and the version it filed. ``informs``: what the AI teammates in that
    conversation read in their next input with it, waking nobody
    (`room_task.heard.informing`).

    Best effort: what happened has happened, and a line that cannot be
    recorded must not take the task's own state back with it."""
    try:
        async with session.begin_nested():
            told_in = await _origin_conversation(session, task)
            if told_in is None:
                return
            meta = {
                **notice(event_type, severity=SEVERITY_INFO, who=WHO_PLATFORM),
                "task_id": str(task.id),
                **(refers or {}),
            }
            await announce(
                session,
                place_id=task.room_id,
                task_id=None if told_in == task.room_id else told_in,
                content=content,
                meta=heard.informing(meta, informs) if informs else meta,
            )
            if told_in != task.room_id:
                show_once_committed(
                    session, task.room_id, {"type": "state", "resource": "threads"}
                )
    except Exception:  # a record never undoes what it records
        logger.warning("could not record at task %s's origin", task.id, exc_info=True)


async def _origin_conversation(session: AsyncSession, task: Task) -> uuid.UUID | None:
    """The conversation ``task`` was opened from, where it is told how it goes:
    the channel's main line, or the 支线 under the message it hangs under
    (opened if nobody has replied there yet). None when it has nowhere to be
    told (`record_at_origin`)."""
    origin = await _origin(session, task)
    if origin is None:
        return None
    block, in_main_line = origin
    if in_main_line:
        return task.room_id
    return (
        await open_thread(session, block.id, by=task.owner_handle or block.author)
    ).id


async def main_line_opener(session: AsyncSession, task: Task) -> str | None:
    """The seat of the AI teammate that opened ``task`` from something said in
    its channel's main line, which it reports to and is told of it; None for a
    task a person made, or one from a 支线 or from nowhere. ``created_by`` is
    what a teammate's own `cheese_task(action=open)` writes as its seat."""
    opener = task.created_by
    if (
        opener is None
        or task.created_by_assistant
        or not opener.startswith(AGENT_HANDLE_PREFIX)
    ):
        return None
    origin = await _origin(session, task)
    if origin is None or not origin[1]:
        return None
    return opener


async def _origin(session: AsyncSession, task: Task) -> tuple[Block, bool] | None:
    """The main-line message ``task`` hangs under, and whether what it came
    from was said in the main line itself rather than in that message's 支线.
    None when it has nowhere to be told (`record_at_origin`). Opens nothing."""
    if task.upgraded_from_block_id is None:
        return None
    origin = await session.get(Block, task.upgraded_from_block_id)
    room = await session.get(Topic, task.room_id)
    project = await session.get(Project, task.project_id)
    if (
        origin is None
        or origin.kind != BlockKind.message
        or origin.conversation_id != task.room_id
        or room is None
        or room.status == TopicStatus.archived
        or project is None
    ):
        return None
    if await thread_of(session, task.id) is not None:
        return None
    source = (
        await session.get(Block, task.source_block_id)
        if task.source_block_id is not None
        else None
    )
    return origin, source is not None and source.conversation_id == task.room_id


async def tell_agent(
    session: AsyncSession,
    task: Task,
    content: str,
    *,
    record_instruction: RecordInstruction | None,
    closes: bool = False,
) -> None:
    """The task's AI teammate hears ``content``. ``closes``: it is the
    instruction to write the finished task up and close it.

    ``record_instruction`` 是第 5 层那个 `delivery.agent.record_task_instruction`，
    由调用链上的门递下来（`app.domain.review.ports.RecordInstruction`）：这句话
    落在投递账本上，而本包够不着它。没递进来是接线漏了一处：当场炸掉，不静默
    让这个 AI 队友永远收不到这一句。"""
    if record_instruction is None:  # pragma: no cover - 接线漏了才会走到
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError("record_instruction was not handed in")

    payload: dict[str, object] = {
        "projectId": str(task.project_id),
        "topicId": str(task.room_id),
    }
    if closes:
        payload[CLOSES_TASK] = True
    await record_instruction(
        session,
        DeliveryEvent(
            id=uuid.uuid4(),
            type=NotificationType.ROOM_NOTICE,
            payload=payload,
            occurred_at=datetime.now(UTC),
        ),
        task_id=task.id,
        content=content,
    )


# 下面这几段是「这次交付落地之后，任务本身怎么走」说给那个 AI 队友听的话。原先
# 和 `agent` 的其它提示词放在一起，可那里没有一处
# 用它们 —— 说它们的只有这里，`delivery_landed` 和 `_go_on`。第 9 步把 review 够
# 不着 `agent` 的那几条调用收进端口时，纯字符串没有理由走端口，就搬回说它的地方。


def _judge(*, deploys: bool) -> str:
    """How a task's AI teammate tells whether the task is done, and what it
    does either way, once a merge has landed."""
    done = (
        "都做完了：步骤清单全部标成 completed，有不做的项就在关闭时用 unchecked_reason 写明理由；"
        "直接用 cheese_task(action=close) 关闭，结论写一句结果。"
        "这个项目要等部署：平台会先等这次交付上线，上线后再叫你更新实况文档、发总结，"
        "现在不用写。"
        if deploys
        else "都做完了：步骤清单全部标成 completed，有不做的项就在关闭时用 unchecked_reason 写明理由；"
        "用 cheese_doc(action=edit) 把实况文档改成任务完成时的样子，用 chat_send 在任务里发一条总结，"
        "再用 cheese_task(action=close) 关闭，结论写一句结果。"
    )
    return (
        "由你判断任务做没做完：对照任务目标、实况文档里还没做完的、步骤清单，"
        "和负责人在任务里说过的话（他说过还要接着做的，就没做完）。\n"
        "- 还有没做完的：在步骤清单里勾掉刚采纳的这一步，接着做下一步，"
        "做完再用 cheese_card(action=request) 递一次交付。\n"
        f"- {done}\n"
        "拿不准就用 cheese_ask 问负责人，等他回话，不要先关。"
        "关闭要有依据：最后一次交付已采纳且清单勾完（或写明不做的理由），"
        "或者负责人在任务里说过结束（关闭时用 owner_message 带上那条消息的 id）；"
        "两样都没有，平台会拒绝。"
    )


def task_landed_prompt(
    *, title: str, task_id, has_checkout: bool, waits_for_deploy: bool = False
) -> str:
    """What a task's agent is told when one of its deliveries lands: the task
    is still open, and whether it is done is the agent's to judge."""
    # 没有工作目录的任务（项目没有仓库）交的是文件或地址：没有合并，也没有新分支。
    start = (
        f"任务「{title}」的这次交付已经采纳并合并，任务还开着。"
        f'要接着改文件，先执行 cd "$(cheese worktree {task_id})"：'
        "工作目录会换到一条从项目最新代码切出的新分支上，没合并的提交和改动会一起带过去。"
        if has_checkout
        else f"任务「{title}」的这次交付已经采纳，任务还开着。"
    )
    return start + _judge(deploys=waits_for_deploy)


def task_step_landed_while_working_prompt(
    *, title: str, task_id, has_checkout: bool, coordinated: bool = False
) -> str:
    """What a task's agent is told when one of its deliveries lands while it
    is working on something begun after that delivery went up: that work is
    not in the merge, and the task goes on. ``coordinated``: the channel's
    main line opened the task, and judges whether it is done."""
    start = (
        f"任务「{title}」的这次交付已经采纳并合并，"
        "但交付之后你又开始了新的工作，它不在这次合并里，任务没有关闭，接着开着。"
        f'手上这件做完后，先执行 cd "$(cheese worktree {task_id})"，'
        "工作目录会换到一条从最新代码切出的新分支上，没合并的提交和改动会一起带过去；"
        "再用 cheese_card(action=request) 把它作为下一步递交。"
        if has_checkout
        else f"任务「{title}」的这次交付已经采纳，"
        "但交付之后你又开始了新的工作，任务没有关闭，接着开着。"
        "手上这件做完后，用 cheese_card(action=request) 把它作为下一步递交。"
    )
    if coordinated:
        return start + piece("opening/task_coordinated")
    return (
        start
        + "新做的东西不需要交付的，做完后照常判断任务做没做完。"
        + _judge(deploys=False)
    )


def task_landed_coordinated_prompt(*, title: str, task_id, has_checkout: bool) -> str:
    """What the AI teammate of a task the channel's main line coordinates
    reads on its next input once a delivery of it lands: the task is still
    open, the main line judges whether it is done, and where to go on from."""
    if has_checkout:
        return piece("task_landing/coordinated_checkout", title=title, task_id=task_id)
    return piece("task_landing/coordinated_plain", title=title)


def task_summary_prompt(
    *, title: str, has_checkout: bool, deployed: bool = False
) -> str:
    """What a task's agent is told once a task it judged done is live, in a
    project that waits for its deploy (`deploy_landed`): write the task up,
    then close it."""
    landed = "已经采纳" + ("并合并" if has_checkout else "")
    if deployed:
        landed += "，部署也已上线"
    return (
        f"任务「{title}」的最后一次交付{landed}，写完总结就关闭任务。"
        "只做三件事：\n"
        "1. 用 cheese_doc(action=get) 读实况文档，用 cheese_doc(action=edit) 改成任务完成时的样子："
        "最上面写最终做成了什么；和任务目标无关、这次没有处理的发现，有就在下面交代，"
        "没有就不写；过程中被推翻的计划删掉。\n"
        "2. 用 chat_send 在任务里发一条总结：结果一两句，文档改了什么，"
        "没有处理的发现逐条列出；"
        + (
            "工作目录里还有这次交付之外的提交或改动的，写明是什么、没有交付。"
            if has_checkout
            else ""
        )
        + "没什么可补的，只写一句结果。\n"
        "3. 用 cheese_task(action=close) 关闭任务，结论写一句结果。\n"
        "不要改动文件，不要提交审阅。"
    )


# 下面这几段是平台报给频道主线上开这个任务的那位 AI 队友的事实（`wake_opener`）：它
# 看不到任务里的东西，所以这件事的事实都写在话里；拿到后怎么做，写在主线的规矩里
# （`prompts/pieces/main_line/rules.md` 的「任务的报告」）。


def _member(handle: str, names: dict[str, str]) -> str:
    return f"{names.get(handle) or handle}（handle: {handle}）"


def task_named(task: Task) -> str:
    """A task as a report names it: its number and title."""
    title = f"「{said_title(task)}」"
    return f"#{task.number}{title}" if task.number is not None else title


def _askers(task: Task) -> list[str]:
    """Everyone who asked for what the task does: who proposed it, then each
    member a request was passed on for (`cheese_task(action=tell)`'s asker)."""
    first = task.reporter_handle or task.owner_handle
    return list(dict.fromkeys([h for h in (first, *(task.asker_handles or ())) if h]))


def task_filed_opener_prompt(
    task: Task,
    *,
    version: int,
    reviewers: list[str],
    names: dict[str, str],
    subject: str | None,
    body: str | None,
    focus: str | None,
) -> str:
    """What the AI teammate that opened a task from the channel's main line is
    told when the task hands a version in for review: which version, whom it
    waits on, the card's change title and description, what the reviewer is
    asked to look at, and everyone who asked for it (``names``: handle to
    roster name). A sentence with nothing to say is left out."""
    waiting = (
        piece(
            "context/state_review",
            who="、".join(_member(h, names) for h in reviewers),
        )
        if reviewers
        else "待审阅"
    )
    lines = [
        piece(
            "main_line/report_filed",
            task=task_named(task),
            version=version,
            waiting=waiting,
        )
    ]
    said = "\n".join(part.strip() for part in (subject, body) if part and part.strip())
    if said:
        lines.append(piece("main_line/report_said", text=said))
    if focus and focus.strip():
        lines.append(piece("main_line/report_focus", text=focus.strip()))
    askers = _askers(task)
    if askers:
        lines.append(
            piece(
                "main_line/report_askers",
                names="、".join(_member(h, names) for h in askers),
            )
        )
    return "\n".join(lines)


def _said(card: AcceptCard) -> list[str]:
    """A version's title and 说明 as handed in, as the main line reads them."""
    said = "\n".join(
        part.strip() for part in (card.change_subject, card.change_body) if part
    ).strip()
    return [piece("main_line/report_said", text=said)] if said else []


def _version_lines(card: AcceptCard, names: dict[str, str]) -> list[str]:
    """What the main line is told of one version of a task's delivery: its
    title and 说明 as handed in, and — when a member handed it in themselves
    rather than the task's AI teammate — whose it is: nothing the task
    reported describes it. A sentence with nothing to say is left out."""
    lines = _said(card)
    if card.handed_in_by:
        lines.append(
            piece("main_line/report_by_member", who=_member(card.handed_in_by, names))
        )
    return lines


async def task_landed_opener_prompt(
    session: AsyncSession,
    task: Task,
    *,
    by: str | None,
    card: AcceptCard | None = None,
) -> str:
    """What the AI teammate that opened a task from the channel's main line is
    told when a delivery of it is adopted and lands: which task, who adopted
    it (``by``; None for a merge nobody adopted on the platform), which
    version it was and whose (``card``, `_version_lines`), where its content
    now is, that the task is still open, and that whether it is done is its to
    judge — with everyone who asked for it."""
    handed_in_by = card.handed_in_by if card is not None else None
    names = await names_by_handles(
        session,
        [
            *((by,) if by else ()),
            *((handed_in_by,) if handed_in_by else ()),
            *_askers(task),
        ],
    )
    lines = [
        piece("main_line/report_landed", task=task_named(task), by=_member(by, names))
        if by
        else piece("main_line/report_landed_merged", task=task_named(task))
    ]
    if card is not None:
        lines.extend(_version_lines(card, names))
        if handed_in_by and task.branch_name is not None:
            lines.append(piece("main_line/report_in_trunk"))
    lines.append(piece("main_line/judge_done"))
    askers = _askers(task)
    if askers:
        lines.append(
            piece(
                "main_line/report_askers",
                names="、".join(_member(h, names) for h in askers),
            )
        )
    return "\n".join(lines)


async def task_handed_in_opener_note(
    session: AsyncSession, task: Task, card: AcceptCard, *, reviewers: list[str]
) -> str:
    """What the AI teammate that opened a task from the channel's main line
    reads in its next input when a member hands a version of it in
    themselves (`machine.hand_in`): who, which version, and whom it waits
    on. It is not woken: the member did it where people see it."""
    who = card.handed_in_by or ""
    names = await names_by_handles(session, [who, *reviewers])
    waiting = (
        piece(
            "context/state_review",
            who="、".join(_member(h, names) for h in reviewers),
        )
        if reviewers
        else "待审阅"
    )
    return "\n".join(
        [
            piece(
                "main_line/report_handed_in",
                who=_member(who, names),
                task=task_named(task),
                waiting=waiting,
            ),
            *_said(card),
        ]
    )


def task_ended_opener_prompt(task: Task, *, facts: str, names: dict[str, str]) -> str:
    """What the AI teammate that opened a task from the channel's main line is
    told when the task closes: how it ended (`_how_it_ended`) and everyone who
    asked for it."""
    lines = [piece("main_line/report_ended", task=task_named(task), facts=facts)]
    askers = _askers(task)
    if askers:
        lines.append(
            piece(
                "main_line/report_askers",
                names="、".join(_member(h, names) for h in askers),
            )
        )
    return "\n".join(lines)
