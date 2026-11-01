"""这件事怎么在房间里说一遍 —— 卡上留一行、房间里说一句、要点名就点一个人。

`AcceptService` 做完一件事之后，剩下的都是「告诉谁」：房间里那一行、卡上那条
note、以及要不要把某个人叫起来。它们原先都是 `services.py` 上的方法，形状一样 ——
只读自己那几个参数，唯一碰的服务状态是那次请求的 session。

所以它们是模块函数，`session` 走第一个位置参数，写法与 `services.py` 里既有的
`_read_deliverable` 一致：谁调用谁给 session。`AcceptService` 上留一行同名委托，
调用点与测试的写法一格没动。

一处例外是 `_notify_merge_result`：「合了」这件事已经在 GitHub 上发生过，回滚带不
走它，所以它不跟调用方共用那个事务 —— 它拿**调用方交给它的那个 session 工厂**自己
开一条，重试到说完为止（`webhook_service.post_with_retries`）。工厂是参数而不是模块
级的那个单例，和这一片其余函数拿 session 的方式是同一条规矩：谁调用谁给。

说什么、说到什么程度、以及「同一件事不重复说」不在这一层：note 的码在
`notes.py`，提示统一契约（meta / detail / severity）在 `notice_meta`，几句人话在
`agent/platform_notices.py`，
内容签名在 `pr_signals.py`。这里只把已经定好的事实说出去。

## 卡的结局写在最后一句里，等的话在决定时一起结掉

一张卡等待期间的话有好几句（递卡、检查全绿、机器推不动），每一句都投给验收人，
也都在他的收件箱里说着「等你」。卡一有决定，这几句就都不成立了 —— 而它们还各
自躺着，人只能自己去点「已读」。`_settle_the_card` 就是那次决定带来的收尾，由
让卡离开验收人手上的那几处调用（采纳、驳回、作废、归档）。

这是两半合成的一件事，各自在一边：**说**的时候（`_announce_filed`、
`_tell_the_reviewer` 里等谁动手的那几句）把卡号捎上（`about_card`），**决定**的
时候按卡号收尾。这几句话各自投出去、各自躺在收件箱里，卡号是它们之间唯一的共同
点（`delivery/ledger.settle_card`）。

说**结局**的那几句不带卡号：`_notify_merge_result` 的合了/作废了，以及
`_tell_the_reviewer` 里说 PR 被关的那条 —— 它们是这个决定**说完之后**才要说的话，
这一刻把「合了」标成已读，等于它刚发出去就消失。写调用点时看一句话是「等你」还
是「结局」。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.live_frames import show_once_committed
from app.core.sentences import say
from app.domain import happenings
from app.domain.addressing import Event
from app.domain.block.questions import settle_decision
from app.domain.block.schemas import BlockOut
from app.domain.deliverables import artifacts
from app.domain.delivery_events import SettleCard
from app.domain.notice_meta import (
    EVENT_ACCEPT_READY,
    EVENT_ARTIFACT_DECLARED,
    EVENT_CARD_FILED,
    EVENT_MERGE_WITHHELD,
    EVENT_TURN_TIMEOUT,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.review import notes, pr_signals
from app.domain.review.models import AcceptCard
from app.domain.review.ports import Announce
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.closing import Overdue
from app.domain.room_task.models import Task
from app.domain.room_task.services import said_title
from app.domain.topic.models import Topic
from app.domain.webhook import service as webhook_service

logger = logging.getLogger("cheesex.review")


def _notify_merge_result(
    session_factory,
    topic: Topic,
    content: str,
    *,
    task_id: uuid.UUID | None,
    meta: dict | None = None,
    announce: Announce,
) -> None:
    """merge 后结果回房间: post the accept's merge outcome into the topic
    timeline via the webhook primitive's internal function (卡1) — no HTTP
    hop, no token check, this call is trusted by construction. Uses its
    own session (async_session_factory), independent of the caller's session, so
    the notice lands even when the accept itself is about to be rolled
    back by a raised ValidationError.

    `content` is the one line the room shows; everything else — why, what
    to do about it, the service's own words — goes in `meta`'s detail and
    is opened only by whoever wants it (notice_meta.notice).

    Fire-and-forget, but through `spawn`, which holds a strong reference:
    asyncio keeps only a weak one, and this coroutine sleeps up to 35s across
    its retries — a wide window in which an unreferenced task can be
    collected mid-await. Losing it means the room never learns the accept's
    outcome at all. The accepter's HTTP response still doesn't wait on the
    notification succeeding — only on the merge itself.

    房间里留一句，不点名任何人。要人动手、而且**说的时候没有人在看**的那几
    条走 `_tell_the_reviewer`：判据不是「这句话说给谁听」，而是「说的时候谁
    在」。轮询每 60 秒替没人看着的卡看一眼，它发现的事除了通知没有别的路能
    到人手上。

    留在这里的是另外三类：不要人动手的结局（合了、芝士去改）；采纳当场的那几
    条 —— 停下的，或者人工放行成功的 —— 点的人正读着自己那次请求的回应，再投
    一条通知给他，说的是他刚刚已经读到的那句话；以及递卡当场的提醒（递卡本身
    已经通知过验收人，他来了就看见它）。"""
    spawn(
        webhook_service.post_with_retries(
            session_factory,
            poster=announce,
            topic_id=topic.id,
            task_id=task_id,
            content=content,
            source="accept",
            meta=meta,
        ),
        name=f"accept notice topic={topic.id}",
    )


async def _tell_the_reviewer(
    session: AsyncSession,
    card: AcceptCard,
    topic: Topic,
    content: str,
    *,
    meta: dict,
    announce: Announce,
    also: Sequence[str] = (),
    about_card: uuid.UUID | None = None,
) -> None:
    """房间里说这一句，并通知这张卡的验收人（`also` 再加几个人）。

    收件人是卡点了名的验收人；判据是**这件事由后台发现**。采纳当场停下的那几
    条不走这里 —— 点采纳的人正看着自己那次请求的回应，再投一条通知给他，说的
    是他刚刚已经读到的那句话。轮询不一样：它每 60 秒替没人看着的卡看一眼，它
    发现的事除了通知没有别的路能到人手上。

    `also` 是那句话点到名、却不是验收人的人：批准票被作废时，被作废的是投票
    的人的判断，而重新投一次这件事只有他能做。一条点了某人名字的提示不该绕开
    他 —— 同一个人在两处都出现只收一条（`announce` 按人去重）。

    走调用方的 session，不是 `_notify_merge_result` 的新 session：这几处都在
    轮询那一拍的事务里，房间里那一行、卡上的码、去重的账、通知一起提交。整拍
    回滚就当这一拍没发生过，下一拍重新发现同一件事会再说一次，所以什么都不会
    丢。那几条**已经在 GitHub 上发生了**的结局（合了、被拒了）是另一回事：它
    们的事实不会重来，所以继续走新 session 的重试。

    `about_card` 填一句话是不是「等你动手」那一类：是就填卡的 id，卡一有决定它
    就从收件箱里消失（`_settle_the_card`）；说结局的那几句不填。
    """
    reviewers = await AcceptCardRepository(session).reviewer_handles(card.id)
    await announce(
        session,
        place_id=topic.id,
        task_id=card.task_id,
        content=content,
        meta={"source": "accept", **meta},
        author="accept",
        points_at=Event(reviewers=(*reviewers, *also)),
        about_card=about_card,
    )


#: 卡的结局对应问题卡上的哪个按钮（`agent.ask.ask_to_decide`）：采纳就是「接受」，
#: 任务关掉就是「关闭任务」；退回、作废、归档不对应哪一个。
_DECIDED_AS = {"accepted": "accept", "closed": "close"}


async def _settle_the_card(
    session: AsyncSession,
    card: AcceptCard,
    outcome: str,
    *,
    settle_card: SettleCard | None,
) -> None:
    """卡有了决定：等它的那几句话一起结掉，结局记在它们身上。

    `outcome` 说的是这次决定是什么（`accepted` / `rejected` / `voided` /
    `archived`）—— 并进 payload，通知那一侧据此说现在的话：一条说过「等你」
    的提醒，办完之后不该还这么说。结的是**已经投出去**的那几条（`settle_card`
    按卡号找，见模块说明），所以调用点不必先问「卡上有没有积着话」，没有就是
    空转。

    由让卡离开验收人手上的那几处调用：采纳、驳回、作废、归档。别的地方改
    `card.status` 不代表卡不再等人 —— 改验收人就不是，卡只是换到别人手上。

    ``settle_card`` 没递进来是接线漏了一处（第 4 层的 review 够不着第 5 层的
    投递账本，得由门递）：当场炸掉，不比静默留下一条说着「等你」的提醒好。
    """
    if settle_card is None:  # pragma: no cover - 接线漏了才会走到
        # i18n-exempt: developer miswiring, not a refusal a reader can act on
        raise RuntimeError("settle_card was not handed in")
    await settle_card(session, card.id, {"outcome": outcome})
    if card.task_id is not None:
        # 对话里问「要接受还是关掉」的那几道题，按钮决定的就是这张卡：卡一有决定，
        # 它们一起收起来，免得留着两个点了也没用的按钮。
        for question in await settle_decision(
            session,
            conversation_id=card.task_id,
            card_id=card.id,
            chosen=_DECIDED_AS.get(outcome),
            by=card.decided_by or "",
        ):
            show_once_committed(
                session,
                question.conversation_id,
                {
                    "type": "block_updated",
                    "block": BlockOut.model_validate(question).model_dump(mode="json"),
                },
            )
    if outcome in ("accepted", "rejected") and card.task_id is not None:
        task = await session.get(Task, card.task_id)
        if task is not None:
            await _happened(
                session, happenings.TASK_DECIDED, card, task, outcome=outcome
            )


async def _happened(
    session: AsyncSession, kind: str, card: AcceptCard, task: Task, **more: str
) -> None:
    """说给等着这件事的规则听（`happenings`）：哪个任务、谁提的、谁审。"""
    reviewers = await AcceptCardRepository(session).reviewer_handles(card.id)
    await happenings.happened(
        session,
        kind,
        task_id=task.id,
        project_id=task.project_id,
        payload={
            "title": task.title,
            "number": task.number,
            "topicId": str(task.room_id),
            "reporter": task.reporter_handle or "",
            "createdBy": task.created_by or "",
            "reviewers": list(reviewers),
            **more,
        },
    )


async def _announce_filed(
    session: AsyncSession,
    topic: Topic,
    card: AcceptCard,
    task: Task,
    *,
    announce: Announce,
) -> None:
    """递卡说一声，并通知等着这件事的两个人。

    卡的每一种结局在房间里都有一行 —— 驳回、作废、改描述、合了、卡住了 ——
    唯独等待的**开始**没有。而验收卡本身钉在对话末尾，不随时间线往上滚，所以
    翻历史也找不到它是什么时候递上来的：一张卡递出去，房间里此前没有任何痕迹。

    通知发给两个人：验收人（这件事现在在他手上）和提需求的人（他等的东西有
    结果了）。同一个人只收一条。GitHub 项目上另有一条 `EVENT_ACCEPT_READY`，
    说的是另一件事 —— 那是 PR 的检查全绿、可以当场合并；这一条说的是卡递到
    了，两件事之间可能隔着一次 CI。

    房间里那一行用第三人称：一屋子人都看得见它，而「待你验收」只对其中一个人
    成立。改动主题（最长 72 字）进 `detail`，房间里那一行保持一行。

    那一行点的是任务的名字，不是产物的：交合并时产物就是项目的仓库，名字是项目
    名，一个频道里每张卡都会念同一个名字，读的人分不出递上来的是哪一件。

    这一句是「等你」那一类的开头：卡号随它一起投出去，卡一有决定它就一并被结掉
    （`about_card`，见模块说明）。
    """
    detail = "\n\n".join(
        part
        for part in (card.change_subject or "", (card.change_body or "").strip())
        if part
    )
    reviewers = await AcceptCardRepository(session).reviewer_handles(card.id)
    await announce(
        session,
        place_id=topic.id,
        task_id=task.id,
        content=say(
            "cardFiled",
            task=task.title,
            reviewer="、".join(f"<@{h}>" for h in reviewers),
        ),
        meta=notice(
            EVENT_CARD_FILED,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=detail or None,
            detail_label=say("labelChanges"),
        ),
        points_at=Event(
            reviewers=tuple(reviewers),
            reporter=task.reporter_handle,
        ),
        about_card=card.id,
    )
    await _happened(session, happenings.TASK_FILED, card, task)


async def _announce_new_artifact(
    session: AsyncSession,
    topic: Topic,
    name: str,
    *,
    task_id: uuid.UUID,
    announce: Announce,
) -> None:
    """清单上多出一项 —— 在房间里说一声 (#1085 结论三)。

    新建产物是少见动作：一个项目交出去的东西就那么几样，往后每一次交付都沿用
    同一个名字。而它错起来是无声的 —— 把《报告》写成《结题报告》不会报错，只
    会在清单上多一项看着像重复的东西，然后这份清单进了每个新房间的开场，错的
    那一项从此在每一轮里重复一遍。所以这一下当场说出来，就在声明它的那一轮
    里，那时人还认得出这是不是他要的名字。

    展开区里摆的是清单现在的全部内容 —— 判断「这是不是刚才那一项换了个说法」
    要的正是把两个名字放在一起看，而这一行本身只说得出新的那一个。
    """
    listed = await artifacts.list_for_project(
        session,
        topic.project_id,
        hidden=await artifacts.hidden_from_room(session, topic.id),
    )
    await announce(
        session,
        place_id=topic.id,
        # Said in the task that declared it, beside its card.
        task_id=task_id,
        content=say("artifactDeclared", name=name),
        meta=notice(
            EVENT_ARTIFACT_DECLARED,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say(
                "lines",
                items=[
                    say("artifactVersion", name=a.name, version=a.version)
                    if a.version
                    else say("artifactUndelivered", name=a.name)
                    for a in listed
                ],
            ),
            detail_label=say("labelArtifactList"),
        ),
    )


async def _notify_ready(
    session: AsyncSession,
    card: AcceptCard,
    topic: Topic,
    *,
    announce: Announce,
) -> None:
    """CLEAN → 通知验收人，按 (head, clean) 经账本去重 —— 一个 head 只说
    一次「可以采纳了」，重跑的检查、反复的轮询都不重复。"""
    ledger = pr_signals.NudgeLedger.load(card.nudge_state)
    signature = pr_signals.signature("ready", card.pr_head_sha or "")
    if ledger.already_sent(pr_signals.NudgeKind.ready, signature):
        return
    reviewers = await AcceptCardRepository(session).reviewer_handles(card.id)
    await _tell_the_reviewer(
        session,
        card,
        topic,
        say(
            "acceptReady",
            pr=card.pr_number,
            reviewer="、".join(f"<@{h}>" for h in reviewers),
        ),
        meta=notice(
            EVENT_ACCEPT_READY,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=say("acceptReadyDetail", url=card.pr_url or ""),
            detail_label=say("labelNextStep"),
        ),
        announce=announce,
        about_card=card.id,
    )
    ledger.record(pr_signals.NudgeKind.ready, signature)
    card.nudge_state = ledger.dump()


async def _note_needs_human(
    session: AsyncSession,
    *,
    card: AcceptCard,
    topic: Topic,
    reason: str,
    announce: Announce,
    explain: str | None = None,
) -> None:
    """The machine ran out of moves on this card: say so on the card and
    in the room, and stop — never merge.

    `explain` is the room message's middle sentence — WHY the machine is
    stopping. Callers must say their own (a required check that never
    reported, a rebase cap, short votes), or the room gets told a
    confident falsehood about what happened.

    Dedup by exact text rather than by code: the poll runs every 60s, and
    the reason can legitimately change while the card itself hasn't
    moved."""
    note = (
        f"PR #{card.pr_number} 不会自动合并：{reason}。"
        "可以由有权限的人人工放行，在 GitHub 上处理，或者作废这次审阅。"
    )
    if card.note == note:
        return  # already said once — the 60s poll must not repeat it
    notes.record(card, notes.NoteCode.merge_withheld, note)
    logger.warning("card %s: poller stopped — %s", card.id, reason)
    why = explain or reason
    await _tell_the_reviewer(
        session,
        card,
        topic,
        say("mergeWithheld", pr=card.pr_number),
        meta=notice(
            EVENT_MERGE_WITHHELD,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say("mergeWithheldDetail", reason=reason, why=why, url=card.pr_url),
            detail_label=say("labelReason"),
        ),
        announce=announce,
        about_card=card.id,
    )


def _note_poll_failed(card: AcceptCard, exc: BaseException) -> None:
    """Record that this tick could not read GitHub, without burying a note
    that describes something worse.

    Only overwrites a note this same failure wrote, or an ordinary 「等 CI」
    line. A 检查未通过 / 拒绝合并 / 需要人来看 note names a live condition the
    reader has to act on; a transient poll error must not push it off the
    card. The next successful poll re-derives the real state and replaces
    this line, so it is self-healing the same way `poll_paused` is.
    """
    if card.note_code not in (
        None,
        notes.NoteCode.waiting_checks,
        notes.NoteCode.poll_failed,
    ):
        return
    detail = " ".join(str(exc).split())[:200] or exc.__class__.__name__
    notes.record(
        card,
        notes.NoteCode.poll_failed,
        f"无法读取 PR 状态，稍后自动重试：{detail}",
    )


async def _note_outside_accept_txn(
    session: AsyncSession, card_id: uuid.UUID, code: notes.NoteCode, note: str
) -> None:
    """Persist a card note through its own session + commit, so it survives
    the rollback of the accept transaction it accompanies (the caller is
    about to raise). Sessions are minted off the request session's own
    engine — NOT the module-level `async_session_factory`, which the test
    harness binds to a different database than the request session.
    Best-effort: the raise this note accompanies must fire regardless."""
    try:
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        async with factory() as session:
            fresh = await AcceptCardRepository(session).get(card_id)
            if fresh is None:
                return
            notes.record(fresh, code, note)
            await session.commit()
    except Exception:
        logger.exception("could not record the PR-open failure on card %s", card_id)


async def tell_room_undelivered(
    session: AsyncSession, closed: Overdue, *, announce: Announce
) -> None:
    """``closed`` was closed past its deadline with work still going on or
    asked for in it (`room_task.closing.STOP_WORK_AFTER`): its room hears that
    the task closed, that work was stopped, and what it was. One line for the
    room, however many turns were stopped; each of those turns keeps a run
    record under its own number in `closing` (where the close is written).
    Best effort, like `task_landing.tell_origin`."""
    if not closed.undelivered:
        return
    try:
        async with session.begin_nested():
            await announce(
                session,
                place_id=closed.task.room_id,
                content=say("taskClosedUndelivered", title=said_title(closed.task)),
                meta=notice(
                    EVENT_TURN_TIMEOUT,
                    severity=SEVERITY_WARN,
                    who=WHO_HUMAN,
                    detail=say(
                        "taskClosedUndeliveredDetail",
                        items=[work.gist for work in closed.undelivered],
                    ),
                    detail_label=say("labelDetails"),
                ),
            )
    except Exception:  # a courtesy line never undoes a close
        logger.warning(
            "could not tell task %s's room what it left", closed.task.id, exc_info=True
        )
