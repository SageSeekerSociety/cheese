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
`notes.py`，提示统一契约（meta / detail / severity）在 `agent/platform_notices.py`，
内容签名在 `pr_signals.py`。这里只把已经定好的事实说出去。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.sentences import say
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_READY,
    EVENT_ARTIFACT_DECLARED,
    EVENT_CARD_FILED,
    EVENT_MERGE_WITHHELD,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.delivery.addressing import Event
from app.domain.project import artifacts
from app.domain.review import notes, pr_signals
from app.domain.review.models import AcceptCard
from app.domain.review.repositories import AcceptCardRepository
from app.domain.room_task.models import Task, TaskStatus
from app.domain.topic.models import Topic
from app.domain.webhook import service as webhook_service

logger = logging.getLogger("cheesex.review")


def _notify_merge_result(
    session_factory, topic: Topic, content: str, *, meta: dict | None = None
) -> None:
    """merge 后结果回房间: post the accept's merge outcome into the topic
    timeline via the webhook primitive's internal function (卡1) — no HTTP
    hop, no token check, this call is trusted by construction. Uses its
    own session (async_session_factory), independent of the caller's session, so
    the notice lands even when the accept itself is about to be rolled
    back by a raised ValidationError.

    `content` is the one line the room shows; everything else — why, what
    to do about it, the service's own words — goes in `meta`'s detail and
    is opened only by whoever wants it (platform_notices.notice).

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
            topic_id=topic.id,
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
    also: Sequence[str] = (),
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
    """
    await announce(
        session,
        place_id=topic.id,
        task_id=card.task_id,
        content=content,
        meta={"source": "accept", **meta},
        author="accept",
        points_at=Event(reviewers=(card.reviewer_handle, *also)),
    )


async def _announce_filed(
    session: AsyncSession, topic: Topic, card: AcceptCard, task: Task, *, artifact: str
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
    """
    detail = "\n\n".join(
        part
        for part in (card.change_subject or "", (card.change_body or "").strip())
        if part
    )
    await announce(
        session,
        place_id=topic.id,
        task_id=task.id,
        content=say("cardFiled", artifact=artifact, reviewer=card.reviewer_handle),
        meta=notice(
            EVENT_CARD_FILED,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=detail or None,
            detail_label=say("labelChanges"),
        ),
        points_at=Event(
            reviewers=(card.reviewer_handle,),
            reporter=task.reporter_handle,
        ),
    )


async def _announce_new_artifact(
    session: AsyncSession, topic: Topic, name: str
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
    listed = await artifacts.list_for_project(session, topic.project_id)
    await announce(
        session,
        place_id=topic.id,
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


async def _notify_ready(session: AsyncSession, card: AcceptCard, topic: Topic) -> None:
    """CLEAN → 通知验收人，按 (head, clean) 经账本去重 —— 一个 head 只说
    一次「可以采纳了」，重跑的检查、反复的轮询都不重复。"""
    ledger = pr_signals.NudgeLedger.load(card.nudge_state)
    signature = pr_signals.signature("ready", card.pr_head_sha or "")
    if ledger.already_sent(pr_signals.NudgeKind.ready, signature):
        return
    await _tell_the_reviewer(
        session,
        card,
        topic,
        say("acceptReady", pr=card.pr_number, reviewer=card.reviewer_handle),
        meta=notice(
            EVENT_ACCEPT_READY,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=say("acceptReadyDetail", url=card.pr_url or ""),
            detail_label=say("labelNextStep"),
        ),
    )
    ledger.record(pr_signals.NudgeKind.ready, signature)
    card.nudge_state = ledger.dump()


async def _note_needs_human(
    session: AsyncSession,
    *,
    card: AcceptCard,
    topic: Topic,
    reason: str,
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


async def _record_task_nudge(
    session: AsyncSession, *, topic, task, content, headline, meta
):
    """Commit the task event and parent delivery with the source state."""
    from app.domain.delivery.agent import record_task_instruction
    from app.domain.delivery.ledger import DeliveryEvent
    from app.domain.notification.models import NotificationType

    block = await announce(
        session,
        place_id=topic.id,
        task_id=task.id if task is not None else None,
        content=headline,
        meta=meta,
    )
    if block is not None and task is not None and task.status == TaskStatus.open:
        await record_task_instruction(
            session,
            DeliveryEvent(
                id=block.id,
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(topic.project_id),
                    "topicId": str(topic.id),
                },
                occurred_at=block.created_at,
            ),
            task=task,
            content=content,
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
    except Exception:  # noqa: BLE001
        logger.exception("could not record the PR-open failure on card %s", card_id)
