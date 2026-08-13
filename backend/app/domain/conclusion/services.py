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
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.text import markdown_preview
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.conclusion.models import (
    DIGEST_TIMEOUT_MINUTES,
    HARD_MAX_RETURNS,
    MAX_RETURNS,
    ConclusionCard,
    ConclusionStatus,
)
from app.domain.conclusion.repositories import ConclusionCardRepository
from app.domain.cx_notification.models import NotifKind, NotifLevel
from app.domain.cx_notification.services import NotificationService
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository

#: 平台自己结算时记的 handle（轮结束自动采信 / 超时采信 / 被 supersede）。
SYSTEM_ACTOR = "system"

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
        """采信 —— the default. Settles the card and archives the sub-topic."""
        self._require_open(card)
        await self._settle(
            card, status=ConclusionStatus.accepted, by=by, reason="", announce=True
        )
        await self._archive_subtopic(card, by=by)
        return card

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
        await NotificationService(self._session).create(
            project_id=card.project_id,
            level=NotifLevel.strong,
            kind=NotifKind.decision_request,
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
        连人带卡一起冻住（归档后活文档定格，卡再也没人能结算）。规则是「有未结算
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
        """采信即归档。Import is local: TopicService opens cards, so a module-level
        import here would be circular."""
        from app.domain.topic.services import TopicService

        sub = await self._topics.get(card.topic_id)
        if sub is None or sub.status == TopicStatus.archived:
            return
        service = TopicService(self._session)
        # 顺序: 先结算下级卡, 再归档 —— 反过来孙子的卡会随级联一起冻死。
        await self.settle_descendants_before_archive(sub.id, by=by)
        await service.archive(sub.id, by=by)

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
        text = {
            ConclusionStatus.accepted: f"✅ 结论已被父话题采信（{who}）",
            ConclusionStatus.returned: f"↩️ 父话题要补一条证据（{who}）：{why}",
            ConclusionStatus.escalated: f"⬆️ 结论已升级，等人拍板（{who}）：{why}",
        }.get(card.status)
        if text is None:
            return
        await self._blocks.add(
            project_id=card.project_id,
            topic_id=card.topic_id,
            author=card.settled_by or SYSTEM_ACTOR,
            author_type=AuthorType.system,
            content=text,
            kind=BlockKind.event,
            meta={"platform": True, "conclusion_card": str(card.id)},
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
