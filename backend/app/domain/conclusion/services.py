"""结论卡 business logic — 默认采信 (accept-by-default) is the whole point.

The card exists so a sub-topic's conclusion gets a receipt and a status; it does
NOT exist to add a review step. Every mechanism here is bent toward "nothing
happens → 采信":

1. the parent's turn ends, card still open → auto-accept (`settle_turn_cards`);
2. 30 minutes pass with nobody running a turn at all → auto-accepted (`sweep_expired`);
3. 打回 costs a whole turn (it wakes the sub-topic) while 采信 wakes nobody;
4. 打回 is capped at ``MAX_RETURNS`` and must anchor to a blocking uncertainty.

Stage one is PURELY ADDITIVE: `TopicService.return_conclusion` keeps all
three of its original side effects (message into the parent, a section appended
to the parent's living doc, a change-alert) and merely opens a card beside them.

采信 is also what moves a sub-topic's COMMITS: they are folded into the room's
branch (`fold_into_room`), so a room's accept card ships everything its tasks
produced instead of every task opening a PR of its own. That fold can be refused
(the room is waiting on CI, somebody is editing in its workspace) or can
conflict — none of which may fail the settlement, so the card settles either way
and `sweep_room_merges` is the exit from the queue.

采信即归档有**一个**例外，and it is not a softening of 默认采信: a sub-topic
holding an undecided ACCEPT card (验收卡) is not archived yet — archiving would
revoke that card (`review/archive.py`), and the platform tells 分身 to both
`conclude` early AND file an accept card when done, so 默认采信 would routinely
destroy a card its reviewer never got to see. The conclusion still settles and
still flows to the parent; only the archive is owed, and
`sweep_deferred_archives` pays it back the moment nobody is waiting on a card.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.text import markdown_preview
from app.domain.agent.platform_notices import (
    EVENT_ARCHIVE_DEFERRED,
    EVENT_CONCLUSION_SETTLED,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.alert.models import AlertKind, AlertLevel
from app.domain.alert.services import AlertService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.conclusion import room_branch
from app.domain.conclusion.models import (
    ARCHIVE_DEFERRED,
    ARCHIVE_DEFERRED_DONE,
    ARCHIVE_REFILE_GRACE_MINUTES,
    DIGEST_TIMEOUT_MINUTES,
    HARD_MAX_RETURNS,
    MAX_RETURNS,
    ConclusionCard,
    ConclusionStatus,
)
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.topic.models import Topic, TopicKind, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.conclusion")

#: 平台自己结算时记的 handle（轮结束自动采信 / 超时采信 / 被 supersede）。
SYSTEM_ACTOR = "system"

#: 一轮扫描最多看多少张采信卡。护栏而不是策略：磁盘备忘让已经合完的卡连 git 都
#: 不用问，所以真正会被检查的只有还欠着的那几张。撞到上限会记 warning——「悄悄
#: 少扫了一批」比慢一点危险得多。
_ROOM_MERGE_SWEEP_LIMIT = 500

#: 实际允许的打回次数。策略值是 1；2 是硬上限，任何调参都不许越过它
#: (设计 §二 机制③：用完只剩采信或升级)。
RETURN_BUDGET = min(MAX_RETURNS, HARD_MAX_RETURNS)


def need_evidence_prompt(card: ConclusionCard) -> str:
    """The sub-topic's wake-up instruction when its conclusion is sent back.

    Prompt-only, and the reason is copied verbatim — nothing is derived from it.
    """
    return (
        "父话题看了你回流的结论，要你**补一条证据**再重新回流"
        "（这不是驳回：结论本身没被否掉，缺的是支撑它的那一条证据）。\n\n"
        f"父话题要补的是：\n{card.settle_reason}\n\n"
        "你的容器、会话和读过的代码都还在——去把这条证据补上"
        "（跑一次、读一遍、或者说明为什么补不了），然后再 `cheese conclude` 一次，"
        "新的结论会接着这张卡走。"
    )


class ConclusionCardService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = ConclusionCardRepository(session)
        self._topics = TopicRepository(session)
        self._blocks = BlockRepository(session)

    # ---- 开卡 ---------------------------------------------------------

    async def open_for_conclusion(
        self, *, sub: Topic, parent: Topic, conclusion: str
    ) -> ConclusionCard:
        """Open (or re-open) the card for a sub-topic's conclusion.

        Three cases, and they are what gives `conclude` idempotency:
        - no live card → a fresh open card;
        - a ``returned`` card → re-opened with the new text (the ONLY way back
          to open), keeping the return counter so the cap still bites;
        - a still-``open`` card → superseded, and a fresh one takes its place.
        """
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=DIGEST_TIMEOUT_MINUTES)
        live = await self._repo.live_for_topic(sub.id)
        if live is not None and live.status == ConclusionStatus.returned:
            live.conclusion = conclusion
            live.status = ConclusionStatus.open
            live.settled_by = None
            live.settled_at = None
            live.settle_reason = ""
            live.blocking_ref = None
            live.digest_deadline_at = deadline
            await self._session.flush()
            return live
        if live is not None:
            await self._settle(
                live,
                status=ConclusionStatus.superseded,
                by=SYSTEM_ACTOR,
                reason="子话题在结算前重新回流了结论，这张卡作废",
                announce=False,
            )
        return await self._repo.add(
            project_id=sub.project_id,
            topic_id=sub.id,
            receiver_topic_id=parent.id,
            conclusion=conclusion,
            digest_deadline_at=deadline,
        )

    # ---- 结算 ---------------------------------------------------------

    async def get_for_receiver(
        self, *, receiver_topic_id: uuid.UUID, card_id: uuid.UUID
    ) -> ConclusionCard:
        """Fetch a card, refusing one that is not addressed to this parent.

        The route is mounted under the PARENT precisely so the per-turn token's
        scope check lines up (a parent operating on a card mounted under the
        child would 401); this guard keeps the body honest with the URL.
        """
        card = await self._repo.get(card_id)
        if card is None or card.receiver_topic_id != receiver_topic_id:
            raise NotFoundError("结论卡不存在")
        return card

    async def accept(self, card: ConclusionCard, *, by: str) -> ConclusionCard:
        """采信 —— the default. Settles the card and archives the sub-topic.

        采信 is also when the sub-topic's COMMITS join the room's branch
        (`fold_into_room`) — 一个房间一条分支一个 PR. It cannot fail the
        settlement: a merge that has to wait (or that conflicts) leaves the card
        accepted and gets picked up by `sweep_room_merges`.

        归档是这里唯一可能欠下的动作：子话题还挂着一张等人的验收卡时，采信照做、
        回流照做，只把归档推迟到卡有结果之后（`_archive_subtopic`）。
        """
        self._require_open(card)
        await self._settle(
            card, status=ConclusionStatus.accepted, by=by, reason="", announce=True
        )
        try:
            await self.fold_into_room(card)
        except Exception:  # noqa: BLE001 — 采信 must not depend on git
            logger.exception("folding sub-topic %s into its room failed", card.topic_id)
        await self._archive_subtopic(card, by=by)
        return card

    # ---- 提交进母话题那一个 PR ------------------------------------------

    async def fold_into_room(self, card: ConclusionCard) -> dict:
        """把子话题分支上的提交并进母话题的分支 —— 一个房间一条分支一个 PR。

        子话题不是「更小的房间」，是房间里看得见的一条 subagent 线：上下文和
        session 跟它走，房间、容器、PR 跟母话题走。所以它的产出不该自己开一个
        PR，而该落进母话题手上那一个。

        三种情况不合，各有各的说法，**没有一种是默默算了**：母话题在等 CI
        （`pr_open`，合了就把 CI 打回起点）、母话题工作区有人在改（人的未提交
        编辑绝不能被机器扫掉）、两条活改到了同一处（冲突）。前两种排队等下一轮
        扫描，第三种要人解——三种都会在房间里说一句。
        """
        sub = await self._topics.get(card.topic_id)
        room = await self._topics.get(card.receiver_topic_id)
        if sub is None or room is None:
            return {"skipped": "话题不存在"}
        # 只有「房间里的一件活」共用房间的分支。房间自己（root 的儿子）照旧从
        # 基线分支长出来、照旧靠采纳并进 main —— 把它并进根话题是没有意义的。
        #
        # 记成 DONE 而不是直接返回：这个判断的答案永远不会变，不记的话每一轮扫描
        # 都要把它重新问一遍，而它的数量是「项目里所有房间级采信卡」，只增不减。
        if sub.kind not in (TopicKind.task, TopicKind.subtopic):
            room_branch.write_state(card.id, room_branch.DONE)
            return {"skipped": "不是房间里的一件活"}
        from app.domain.review.services import AcceptService  # 局部 import：避免成环

        if await AcceptService(self._session).pr_is_in_flight(room.id):
            return await self._record_fold(
                card,
                sub,
                room,
                {
                    "merged": False,
                    "deferred": True,
                    "reason": "母话题的验收卡正在等 CI（PR 已开），"
                    "现在并进去会让整条 CI 队列从头重排",
                },
            )
        result = await asyncio.to_thread(
            ws.merge_subtopic_into_room, card.project_id, sub.id, room.id
        )
        return await self._record_fold(card, sub, room, result)

    async def _record_fold(
        self, card: ConclusionCard, sub: Topic, room: Topic, result: dict
    ) -> dict:
        """Remember what happened, and say it in the room if it is news.

        Only a CHANGE is announced: the sweep retries a queued or conflicted
        merge every round, and a room that repeated "还在排队" every few minutes
        would be worse than silent.
        """
        state = room_branch.state_of(result)
        previously = room_branch.read_state(card.id)
        room_branch.write_state(card.id, state)
        if state == previously or room.status == TopicStatus.archived:
            return result
        if result.get("merged"):
            await self._say_in_room(
                room, *room_branch.merged_notice(title=sub.title, result=result)
            )
        elif state == room_branch.DEFERRED:
            await self._say_in_room(
                room,
                *room_branch.deferred_notice(
                    title=sub.title, reason=result.get("reason", "")
                ),
            )
        elif state == room_branch.CONFLICT:
            await self._say_in_room(
                room, *room_branch.conflict_notice(title=sub.title, result=result)
            )
            await AlertService(self._session).create(
                project_id=card.project_id,
                level=AlertLevel.strong,
                kind=AlertKind.decision_request,
                title=f"子话题「{sub.title}」的提交并不进本房间的分支：冲突",
                body=markdown_preview(str(result.get("reason", "")), 200),
                topic_id=room.id,
            )
        return result

    async def _say_in_room(self, room: Topic, text: str, meta: dict) -> None:
        await self._blocks.add(
            project_id=room.project_id,
            topic_id=room.id,
            author=SYSTEM_ACTOR,
            author_type=AuthorType.system,
            content=text,
            kind=BlockKind.event,
            meta={"platform": True, "room_merge": True, **meta},
        )

    async def sweep_room_merges(self) -> list[uuid.UUID]:
        """还没并进母话题分支的那些采信卡，再试一次 —— 排队的出口就是这里。

        队列是**推导出来的**，不是存下来的：「这张采信卡的提交在不在母话题分支
        上」git 自己答得出来，所以既不需要新列也不需要迁移。磁盘上那张备忘只是
        让扫描跳过已经干完的卡（丢了就多问几次 git，不会答错）。
        """
        folded: list[uuid.UUID] = []
        cards = await self._repo.list_accepted(limit=_ROOM_MERGE_SWEEP_LIMIT)
        if len(cards) == _ROOM_MERGE_SWEEP_LIMIT:
            logger.warning(
                "room-merge sweep hit its %d-card ceiling; older accepted cards "
                "were not examined this round",
                _ROOM_MERGE_SWEEP_LIMIT,
            )
        for card in cards:
            if room_branch.read_state(card.id) == room_branch.DONE:
                continue
            result = await self.fold_into_room(card)
            if result.get("merged"):
                folded.append(card.topic_id)
        return folded

    async def need_evidence(
        self,
        card: ConclusionCard,
        *,
        by: str,
        reason: str,
        blocking_ref: str | None = None,
    ) -> ConclusionCard:
        """补证据 —— deliberately NOT called 驳回/rejected.

        Its point is reusing the sub-topic's still-warm context (container,
        session, the code it just read), not quality gatekeeping. Costs a whole
        turn, which is exactly why it is capped.
        """
        self._require_open(card)
        if not reason.strip():
            raise ValidationError("补证据必须说明要补什么")
        if card.returned_count >= RETURN_BUDGET:
            raise ValidationError(
                f"这张卡已经打回过 {card.returned_count} 次（上限 {RETURN_BUDGET}）——"
                "只剩采信或升级两条路"
            )
        self._check_evidence_anchor(card, blocking_ref)
        sub = await self._topics.get(card.topic_id)
        if sub is None:
            raise NotFoundError("子话题不存在")
        if sub.status == TopicStatus.archived:
            raise ValidationError("子话题已归档，补不了证据了——只能采信或升级")
        card.returned_count += 1
        card.blocking_ref = blocking_ref
        await self._settle(
            card,
            status=ConclusionStatus.returned,
            by=by,
            reason=reason,
            announce=True,
        )
        return card

    async def escalate(
        self, card: ConclusionCard, *, by: str, reason: str
    ) -> ConclusionCard:
        """升级 —— this conclusion needs somebody's authority, which is outside
        what a conclusion card can grant. The sub-topic is NOT archived: the
        follow-up (a 人向卡 or a 决策请求) still has work to hang off."""
        self._require_open(card)
        if not reason.strip():
            raise ValidationError("升级必须说明要谁拍什么板")
        await self._settle(
            card,
            status=ConclusionStatus.escalated,
            by=by,
            reason=reason,
            announce=True,
        )
        sub = await self._topics.get(card.topic_id)
        await AlertService(self._session).create(
            project_id=card.project_id,
            level=AlertLevel.strong,
            kind=AlertKind.decision_request,
            title=f"子话题「{sub.title if sub else '?'}」的结论需要人拍板",
            body=markdown_preview(reason, 200),
            topic_id=card.receiver_topic_id,
        )
        return card

    # ---- 默认采信的两条兜底 ----------------------------------------------

    async def settle_open_for_turn(
        self, *, receiver_topic_id: uuid.UUID, turn_started_at: datetime
    ) -> list[ConclusionCard]:
        """机制①: the parent's turn ended without settling — 采信.

        Only cards that already existed when the turn started count: a card born
        mid-turn was never visible to it, and its own digest turn is still queued.
        """
        cards = await self._repo.list_open_for_receiver(
            receiver_topic_id, created_before=turn_started_at
        )
        accepted: list[ConclusionCard] = []
        for card in cards:
            # 同 sweep_expired：级联结算可能已经把本批里的另一张卡关掉了。
            if card.status != ConclusionStatus.open:
                continue
            await self.accept(card, by=SYSTEM_ACTOR)
            accepted.append(card)
        return accepted

    async def sweep_expired(self, *, now: datetime | None = None) -> list[uuid.UUID]:
        """机制①bis: the 30-minute absolute timeout.

        Covers the case the turn-end hook cannot: the parent's digest turn never
        ran at all (queued behind a wedged turn, refused on credits, crashed).
        """
        cards = await self._repo.list_expired(now or datetime.now(UTC))
        settled: list[uuid.UUID] = []
        for card in cards:
            # 采信一张卡会级联结算它下级的卡，而那张下级卡可能就在本批里——走到它
            # 时它已经不是 open 了。必须跳过而不是抛：一抛整批 sweep 回滚，这批卡
            # 永远扫不掉，30 分钟兜底就成了摆设。
            if card.status != ConclusionStatus.open:
                continue
            await self.accept(card, by=SYSTEM_ACTOR)
            settled.append(card.id)
        return settled

    # ---- 归档 ---------------------------------------------------------

    async def settle_descendants_before_archive(
        self, topic_id: uuid.UUID, *, by: str
    ) -> list[ConclusionCard]:
        """归档是级联的：直接 archive 一个还挂着未结算下级结论卡的话题，会把孙子
        连人带卡一起冻住（归档后实况文档定格，卡再也没人能结算）。规则是「有未结算
        下级结论卡时先结算再归档」——这里就是那个「先结算」。"""
        descendants = await self._descendant_ids(topic_id)
        cards = await self._repo.list_live_under(descendants)
        # 打回中的卡也一起收：子话题马上要被归档，没人会再来补证据了。
        for card in cards:
            await self._settle(
                card,
                status=ConclusionStatus.accepted,
                by=by,
                reason="上级话题归档前自动结算（默认采信）",
                announce=True,
            )
        return cards

    async def _descendant_ids(self, topic_id: uuid.UUID) -> list[uuid.UUID]:
        out: list[uuid.UUID] = []
        frontier = [topic_id]
        while frontier:
            current = frontier.pop()
            for child in await self._topics.list_children(current):
                out.append(child.id)
                frontier.append(child.id)
        return out

    async def _archive_subtopic(self, card: ConclusionCard, *, by: str) -> None:
        """采信即归档 —— 除非子话题还挂着一张等人的验收卡，那就先欠着。

        为什么要欠着：归档会把非终态的验收卡一并收敛掉
        (`review/archive.py`)，而默认采信是**平台自己**发起的（父话题那一轮
        结束 / 30 分钟超时），于是"分身做完 → conclude → 递卡"这个平台两头都在
        鼓励的组合，会在验收人还没看见卡的时候把卡作废掉，工作也就断在那里
        （归档话题递不出新卡）。真正错的不是归档收卡那条策略，而是这里：
        采信不该在还有人要拍板的时候动手归档。
        """
        sub = await self._topics.get(card.topic_id)
        if sub is None or sub.status == TopicStatus.archived:
            return
        if await self._somebody_is_still_deciding(sub.id):
            await self._defer_archive(card, sub)
            return
        await self._archive_now(sub, by=by)

    async def _archive_now(self, sub: Topic, *, by: str) -> None:
        """Import is local: TopicService opens cards, so a module-level import
        here would be circular."""
        from app.domain.topic.services import TopicService

        # 顺序: 先结算下级卡, 再归档 —— 反过来孙子的卡会随级联一起冻死。
        await self.settle_descendants_before_archive(sub.id, by=by)
        await TopicService(self._session).archive(sub.id, by=by)

    async def _somebody_is_still_deciding(self, topic_id: uuid.UUID) -> bool:
        """这个子话题、连同归档会一起带走的后代，还有没有一张卡等着人决议。

        后代一起算：`TopicService.archive` 是级联的，孙子话题的卡会在同一次归档
        里被收敛掉，所以孙子那张等人的卡同样构成"先别归档"的理由。判据本身问的是
        验收卡那个领域（`AcceptService.anybody_still_waiting`）——哪些状态算"还
        等着"是它的知识，这边自己去数状态迟早会跟归档收敛的那张表走散。
        """
        from app.domain.review.services import AcceptService  # 局部 import：避免成环

        scope = await self._archive_scope(topic_id)
        return await AcceptService(self._session).anybody_still_waiting(scope)

    async def _archive_scope(self, topic_id: uuid.UUID) -> list[uuid.UUID]:
        """归档这个话题会连带走的全部话题。"""
        return [topic_id, *await self._descendant_ids(topic_id)]

    async def _defer_archive(self, card: ConclusionCard, sub: Topic) -> None:
        """记下"归档欠着"，并在子话题里说明为什么它还活着。

        结论本身照常结算、照常回流——父话题读到的东西一个字都没少，欠下的只有
        归档这一个动作。
        """
        if card.settle_reason == ARCHIVE_DEFERRED:  # 幂等：别重复播报
            return
        card.settle_reason = ARCHIVE_DEFERRED
        await self._session.flush()
        await self._blocks.add(
            project_id=card.project_id,
            topic_id=sub.id,
            author=SYSTEM_ACTOR,
            author_type=AuthorType.system,
            content="结论已被父话题采信，这个话题暂不归档",
            kind=BlockKind.event,
            meta={
                "platform": True,
                "conclusion_card": str(card.id),
                **notice(
                    EVENT_ARCHIVE_DEFERRED,
                    severity=SEVERITY_INFO,
                    who=WHO_PLATFORM,
                    detail=(
                        "它还挂着一张等人拍板的验收卡。归档会等验收卡有结果之后"
                        "再落，在那之前卡照常有效，验收人照常能采纳。"
                    ),
                    detail_label="为什么还没归档",
                ),
            },
        )

    async def sweep_deferred_archives(
        self, *, now: datetime | None = None
    ) -> list[uuid.UUID]:
        """把欠下的归档补上 —— "不留僵尸子话题"那一条就落在这里。

        推迟不是取消。一张被推迟的卡带着 `ARCHIVE_DEFERRED` 这个哨兵，扫描每轮
        都来看一眼它欠的归档能不能落：

        - 验收卡被**采纳** → 采纳即归档，话题自己就归档了，这里只把哨兵消掉；
        - 验收卡被**驳回/作废**（或闸门判红）→ 话题还活着，给一个
          `ARCHIVE_REFILE_GRACE_MINUTES` 的窗口让它改完重新递卡；窗口内递出新卡
          就重新受保护，窗口过完还没有新卡，归档在这里落下；
        - 卡还在**等人** → 什么都不做，这正是本次修复要保住的状态。

        所以一个被推迟的子话题只可能停在两处：手上有一张活卡（有人正欠它一个
        决定），或者还在重新递卡的宽限里。两者都是有界的，没有第三种停法。
        """
        moment = now or datetime.now(UTC)
        archived: list[uuid.UUID] = []
        for card in await self._repo.list_archive_deferred():
            if await self._discharge_deferred_archive(card, now=moment):
                archived.append(card.topic_id)
        return archived

    async def _discharge_deferred_archive(
        self, card: ConclusionCard, *, now: datetime
    ) -> bool:
        """一张卡的归档待办能不能结清。True = 这一轮把话题归档了。"""
        sub = await self._topics.get(card.topic_id)
        if sub is None or sub.status != TopicStatus.active:
            # 多半是验收卡被采纳了（采纳即归档），待办自己消解了。消掉哨兵，
            # 这张卡从此退出扫描——包括人后来手动取消归档的情况：那是人的决定，
            # 平台不该拿一条早就结算完的结论把它再关一次。
            await self._settle_deferral(card)
            return False
        from app.domain.review.services import AcceptService  # 局部 import：避免成环

        accepts = AcceptService(self._session)
        scope = await self._archive_scope(sub.id)
        if await accepts.anybody_still_waiting(scope):
            return False
        decided_at = await accepts.latest_decision_at(scope)
        if decided_at is not None and decided_at > now - timedelta(
            minutes=ARCHIVE_REFILE_GRACE_MINUTES
        ):
            return False
        await self._settle_deferral(card)
        by = card.settled_by or SYSTEM_ACTOR
        await self._archive_now(sub, by=by)
        return True

    async def _settle_deferral(self, card: ConclusionCard) -> None:
        card.settle_reason = ARCHIVE_DEFERRED_DONE
        await self._session.flush()

    # ---- 内部 ---------------------------------------------------------

    def _require_open(self, card: ConclusionCard) -> None:
        if card.status != ConclusionStatus.open:
            raise ValidationError(f"结论卡已经结算过了（{card.status}）")

    def _check_evidence_anchor(
        self, card: ConclusionCard, blocking_ref: str | None
    ) -> None:
        """机制②: 打回必须引用卡面 uncertainties 里的某条 blocking 项，引用不到
        就 400 —— 父话题不能凭空提新要求。

        阶段一的卡只有 conclude 的原文，**没有结构化栏位可引用**，所以这条校验
        在阶段一是空转的：代码在这里就位，但对「无结构化栏位的卡」一律放行。
        等阶段二的 `--card ./conclusion.json` 落地、卡面有了 uncertainties，
        ``_blocking_refs`` 会返回真实的 id 列表，这个分支才开始咬人。
        """
        refs = self._blocking_refs(card)
        if not refs:
            return
        if blocking_ref not in refs:
            raise ValidationError(
                "打回必须引用卡面 uncertainties 里的一条 blocking 项："
                + "、".join(refs)
            )

    def _blocking_refs(self, card: ConclusionCard) -> list[str]:
        """阶段二才有内容 —— 卡面还没有结构化栏位，所以现在恒为空。"""
        return []

    async def _settle(
        self,
        card: ConclusionCard,
        *,
        status: ConclusionStatus,
        by: str,
        reason: str,
        announce: bool,
    ) -> None:
        card.status = status
        card.settled_by = by
        card.settled_at = datetime.now(UTC)
        card.settle_reason = reason
        await self._session.flush()
        if announce:
            await self._announce(card)

    async def _announce(self, card: ConclusionCard) -> None:
        """One line in the sub-topic's timeline so the verdict is visible where
        the work happened (采信 must still be *legible*, just not *expensive*)."""
        who = "平台" if card.settled_by == SYSTEM_ACTOR else f"<@{card.settled_by}>"
        why = card.settle_reason
        said = {
            ConclusionStatus.accepted: (
                f"结论已被父话题采信（{who}）",
                SEVERITY_INFO,
                WHO_PLATFORM,
                "",
            ),
            ConclusionStatus.returned: (
                f"父话题要补一条证据（{who}）",
                SEVERITY_WARN,
                WHO_CHEESE,
                why,
            ),
            ConclusionStatus.escalated: (
                f"结论已升级，等人拍板（{who}）",
                SEVERITY_WARN,
                WHO_HUMAN,
                why,
            ),
        }.get(card.status)
        if said is None:
            return
        text, severity, whose, detail = said
        await self._blocks.add(
            project_id=card.project_id,
            topic_id=card.topic_id,
            author=card.settled_by or SYSTEM_ACTOR,
            author_type=AuthorType.system,
            content=text,
            kind=BlockKind.event,
            meta={
                "platform": True,
                "conclusion_card": str(card.id),
                **notice(
                    EVENT_CONCLUSION_SETTLED,
                    severity=severity,
                    who=whose,
                    detail=detail or None,
                    detail_label="理由" if detail else None,
                ),
            },
        )


async def settle_turn_cards(
    session_factory, topic_id: uuid.UUID, *, turn_started_at: datetime
) -> int:
    """机制①, called from the turn runner when any turn on ``topic_id`` ends.

    Owns its own session/transaction: the turn's own session is long gone by
    then, and a failure here must not be able to roll anything else back.
    """
    async with session_factory() as session:
        service = ConclusionCardService(session)
        cards = await service.settle_open_for_turn(
            receiver_topic_id=topic_id, turn_started_at=turn_started_at
        )
        if cards:
            await session.commit()
        return len(cards)
