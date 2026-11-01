"""What a task's delivery landing does to the task.

A task may deliver several times. The card says whether this delivery is the
last step (``AcceptCard.completes_task``). Any step but the last leaves the
task open and moves it onto a branch cut from the project's latest code, for
its AI teammate to carry on from. The last one has the AI teammate write the
task up and close it (`room_task.closing`). Either
way the discussion the task came from hears what landed. Nothing is watched
after the merge: the repository's CI and deploy on its default branch are
its own.

A last step lands as a step after all when the task's own AI teammate's
session is at work on a message it read that was sent after the delivery was
handed over, or a message to it there is still waiting to be read — someone
asked for more after the card went up, and accepted it anyway. What it is doing is not in the
delivery, and closing the task would stop it half done and leave it with
nowhere to be delivered. So the task stays open on the next step's branch,
and the AI teammate delivers that work as the next step. The same holds for a
task already being written up when such work starts in it
(`go_on_while_working`). Only the task's own teammate's work counts, and only
what a person or the teammate itself asked for: an instruction the platform
gave — another seat woken in the task, a conflict or CI fix — is not more work
asked of it (`room_task.closing.unfinished`).

Every path that sees a delivery merge comes through here — an accept click,
the merge queue, the poller, a PR merged by hand — so none of them can keep
closing a task the card said goes on, and none of them can land one merge on
a task twice.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_frames import show_once_committed
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.block.schemas import BlockOut
from app.domain.delivery_events import DeliveryEvent
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.ports import RecordInstruction, TaskTeammate
from app.domain.room_task.closing import CLOSES_TASK, unfinished
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService, said_title
from app.domain.thread.services import open_thread
from app.domain.topic.models import Topic, TopicStatus

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


async def delivery_landed(
    session: AsyncSession,
    task: Task,
    *,
    by: str | None,
    head: str | None,
    pr_number: int | None,
    completes: bool,
    handed_over: datetime | None,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
) -> bool:
    """``task``'s delivery merged. ``completes``: it was the task's last step.
    ``handed_over``: when its card went up; None for a merge no card asked
    for, where any turn running in the task is work that merge does not hold.

    Says whether the merge landed on the task this time: one that has already
    landed there does not land twice (`_already_landed`)."""
    if _already_landed(task, head):
        return False
    now = datetime.now(UTC)
    task.accepted_at = now
    task.accepted_by = by or task.accepted_by
    if head:
        task.delivered_head = head[:64]
    still_working = (
        completes
        and task.status == TaskStatus.open
        and bool(
            await _asked_for_more(
                session, task, since=handed_over, task_teammate=task_teammate
            )
        )
    )
    goes_on = task.status == TaskStatus.open and (not completes or still_working)
    if still_working:
        await _go_on(session, task, record_instruction=record_instruction)
    elif goes_on:
        await TaskService(session).next_step(task)
        await tell_agent(
            session,
            task,
            task_next_step_prompt(title=task.title, task_id=task.id),
            record_instruction=record_instruction,
        )
    elif task.status == TaskStatus.open and task.closing_since is None:
        task.closing_since = now
        await tell_agent(
            session,
            task,
            task_summary_prompt(title=task.title),
            record_instruction=record_instruction,
            closes=True,
        )
    await session.flush()
    await tell_origin(
        session,
        task,
        say("taskStepLanded", title=said_title(task), pr=pr_number)
        if goes_on and pr_number is not None
        else say("taskStepLandedNoPr", title=said_title(task))
        if goes_on
        else say("taskLanded", title=said_title(task), pr=pr_number)
        if pr_number is not None
        else say("taskLandedNoPr", title=said_title(task)),
        task_teammate=task_teammate,
    )
    return True


async def go_on_while_working(
    sessions,
    *,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
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
        base = await default_branch(task.project_id, session)
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
        task_step_landed_while_working_prompt(title=task.title, task_id=task.id),
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


async def tell_origin(
    session: AsyncSession,
    task: Task,
    content: str,
    *,
    task_teammate: TaskTeammate | None,
) -> None:
    """Say ``content`` in the 支线 under the message ``task`` was made from,
    opening it if nobody has replied there yet. It is said by the task's AI
    teammate, as a reply: whoever took part in the discussion hears it, and the
    line under the message shows it. A task made on its own, or from something
    that is not a message in a live channel, has nowhere to say it; nor does a
    task that is that 支线, turned into the task in place.

    Best effort: what landed has landed, and a line that cannot be said must
    not take the task's own state back with it."""
    try:
        async with session.begin_nested():
            await _tell_origin(session, task, content, task_teammate=task_teammate)
    except Exception:  # a courtesy line never undoes a landing
        logger.warning("could not tell task %s's discussion", task.id, exc_info=True)


async def _tell_origin(
    session: AsyncSession,
    task: Task,
    content: str,
    *,
    task_teammate: TaskTeammate | None,
) -> None:

    if task.upgraded_from_block_id is None:
        return
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
        return
    teammate = await handed_in(task_teammate)(
        session, task.project_id, task.room_id, task.agent_handle
    )
    if teammate is None:
        return
    instance_id, _handle = teammate
    thread = await open_thread(
        session, origin.id, by=task.owner_handle or origin.author
    )
    if thread.id == task.id:
        # The 支线 became this task: the discussion is the task's own
        # conversation, which the landing already speaks in.
        return
    said = Block(
        id=uuid.uuid4(),
        project_id=task.project_id,
        conversation_id=thread.id,
        author=agent_instance_handle(instance_id),
        author_type=AuthorType.participant,
        kind=BlockKind.message,
        content=str(content),
        meta={"task_result": str(task.id)},
    )
    session.add(said)
    await session.flush()
    show_once_committed(
        session, said.conversation_id, {"type": "user_block", "block": _out(said)}
    )
    show_once_committed(session, task.room_id, {"type": "state", "resource": "threads"})


def _out(block) -> dict:

    return BlockOut.model_validate(block).model_dump(mode="json")


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


# 下面这三段是「这次交付落地之后，任务本身怎么走」说给那个 AI 队友听的话。原先
# 和 `agent` 的其它提示词一起放在 `agent/harness/prompt.py`，可那个包里没有一处
# 用它们 —— 说它们的只有这里，`delivery_landed` 和 `_go_on`。第 9 步把 review 够
# 不着 `agent` 的那几条调用收进端口时，纯字符串没有理由走端口，就搬回说它的地方。


def task_next_step_prompt(*, title: str, task_id) -> str:
    """What a task's agent is told when one of its deliveries lands and the
    task goes on."""
    return (
        f"任务「{title}」的这次交付已经采纳并合并，任务还没完成。"
        f'下一步从项目最新的代码开始：先执行 cd "$(cheese worktree {task_id})"，'
        "工作目录会换到一条新分支上，没合并的提交和改动会一起带过去。"
        "对照实况文档接着做下一步，做完再用 cheese_card(action=request) 递一次交付；"
        "这一步做完任务目标就全部达成时 completes_task 填 true，"
        "还有没做完的就填 false，并在交付说明里写下一步做什么。"
    )


def task_step_landed_while_working_prompt(*, title: str, task_id) -> str:
    """What a task's agent is told when the delivery it called the last lands
    while it is working on something begun after that delivery went up: the
    delivery counts as a step, and the work goes on as the next one."""
    return (
        f"任务「{title}」你标成最后一步的那次交付已经采纳并合并，"
        "但交付之后你又开始了新的工作，它不在这次合并里，所以任务没有关闭，接着开着。"
        f'手上这件做完后，先执行 cd "$(cheese worktree {task_id})"，'
        "工作目录会换到一条从最新代码切出的新分支上，没合并的提交和改动会一起带过去；"
        "再用 cheese_card(action=request) 把它作为下一步递交，"
        "做完任务目标就全部达成时 completes_task 填 true。"
        "新做的东西如果不需要交付，就用 cheese_task(action=close) 关闭任务，结论里写明。"
    )


def task_summary_prompt(*, title: str) -> str:
    """What a task's agent is told when its last delivery lands: write the task
    up, then close it."""
    return (
        f"任务「{title}」的最后一次交付已经采纳并合并，写完总结就关闭任务。"
        "只做三件事：\n"
        "1. 用 cheese_doc(action=get) 读实况文档，用 cheese_doc(action=edit) 改成任务完成时的样子："
        "最上面写最终做成了什么；和任务目标无关、这次没有处理的发现，有就在下面交代，"
        "没有就不写；过程中被推翻的计划删掉。\n"
        "2. 用 chat_send 在任务里发一条总结：结果一两句，文档改了什么，"
        "没有处理的发现逐条列出；工作目录里还有这次交付之外的提交或改动的，"
        "写明是什么、没有交付。"
        "没什么可补的，只写一句结果。\n"
        "3. 用 cheese_task(action=close) 关闭任务，结论写一句结果。\n"
        "不要改动文件，不要提交审阅。"
    )
