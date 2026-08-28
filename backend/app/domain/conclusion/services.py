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

采信 no longer moves anything: a task writes to the tree it shares with its
batch, so by the time its conclusion settles the commits are already on the
branch the PR is open on. There is nothing to fold, nothing to queue, and no
conflict to report — 一棵树 = 一个分支 = 一个 PR = 一批活.

采信即归档有**一个**例外，and it is not a softening of 默认采信: a sub-topic
holding an undecided ACCEPT card (验收卡) is not archived yet — archiving would
revoke that card (`review/archive.py`), and the platform tells 分身 to both
`conclude` early AND file an accept card when done, so 默认采信 would routinely
destroy a card its reviewer never got to see. The conclusion still settles and
still flows to the parent; only the archive is owed, and
`sweep_deferred_archives` pays it back the moment nobody is waiting on a card.
"""

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
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic
from app.domain.topic.repositories import TopicRepository

logger = logging.getLogger("cheesex.conclusion")

#: 平台自己结算时记的 handle（轮结束自动采信 / 超时采信 / 被 supersede）。
SYSTEM_ACTOR = "system"

#: 一轮扫描最多看多少张采信卡。护栏而不是策略：磁盘备忘让已经合完的卡连 git 都
#: 不用问，所以真正会被检查的只有还欠着的那几张。撞到上限会记 warning——「悄悄
#: 少扫了一批」比慢一点危险得多。

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
        self, *, sub: Task, room: Topic, conclusion: str
    ) -> ConclusionCard:
        """Open (or re-open) the card for a thread's conclusion.

        Three cases, and they are what gives `conclude` idempotency:
        - no live card → a fresh open card;
        - a ``returned`` card → re-opened with the new text (the ONLY way back
          to open), keeping the return counter so the cap still bites;
        - a still-``open`` card → superseded, and a fresh one takes its place.
        """
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=DIGEST_TIMEOUT_MINUTES)
        live = await self._repo.live_for_task(sub.id)
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
                reason="这条支线在结算前重新回流了结论，这张卡作废",
                announce=False,
            )
        return await self._repo.add(
            project_id=sub.project_id,
            # Both ends are the same room now: the thread that concluded, and the
            # room it concluded TO. What used to be two topics is one place with
            # a thread key.
            topic_id=room.id,
            task_id=sub.id,
            receiver_topic_id=room.id,
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

        收起是这里唯一可能欠下的动作：那条支线还挂着一张等人的验收卡时，采信照做、
                回流照做，只把归档推迟到卡有结果之后（`_archive_subtopic`）。
        """
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
        sub = (
            None
            if card.task_id is None
            else await TaskService(self._session).get(card.task_id)
        )
        if sub is None:
            raise NotFoundError("这条支线不存在")
        if sub.status == TaskStatus.closed:
            raise ValidationError("这条支线已经收起，补不了证据了——只能采信或升级")
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
        sub = (
            None
            if card.task_id is None
            else await TaskService(self._session).get(card.task_id)
        )
        await AlertService(self._session).create(
            project_id=card.project_id,
            level=AlertLevel.strong,
            kind=AlertKind.decision_request,
            title=f"「{sub.title if sub else '?'}」的结论需要人拍板",
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

    # 「归档前先结算孙子的卡」那一整套没有了：工作不嵌套，一条支线底下不会再挂
    # 一条支线，所以级联归档要防的那个场面（孙子连人带卡一起被冻住）在结构上就不
    # 存在了。留着一个只会遍历空集合的遍历，读的人会以为它还在防什么。

    async def _archive_subtopic(self, card: ConclusionCard, *, by: str) -> None:
        """采信即收起这条支线 —— 除非它还挂着一张等人的验收卡，那就先欠着。

        为什么要欠着：收起会把非终态的验收卡一并收敛掉
        (`review/archive.py`)，而默认采信是**平台自己**发起的（房间那一轮
        结束 / 30 分钟超时），于是"分身做完 → conclude → 递卡"这个平台两头都在
        鼓励的组合，会在验收人还没看见卡的时候把卡作废掉，工作也就断在那里。
        真正错的不是收卡那条策略，而是这里：采信不该在还有人要拍板的时候动手。

        收起不是冻结：`status=closed` 只影响默认展开和排序，支线照常能追加对话。
        房间归档之后都还能说话，一条支线更没有理由做得比房间更死。
        """
        if card.task_id is None:
            return
        sub = await TaskService(self._session).get(card.task_id)
        if sub is None or sub.status == TaskStatus.closed:
            return
        if await self._somebody_is_still_deciding(sub.id):
            await self._defer_archive(card, sub)
            return
        await self._archive_now(sub, by=by)

    async def _archive_now(self, sub: Task, *, by: str) -> None:
        sub.status = TaskStatus.closed
        sub.closed_at = datetime.now(UTC)
        await self._session.flush()

    async def _somebody_is_still_deciding(self, task_id: uuid.UUID) -> bool:
        """这条支线还有没有一张卡等着人决议。

        判据本身问的是验收卡那个领域（`AcceptService.anybody_still_waiting`）——
        哪些状态算"还等着"是它的知识，这边自己去数状态迟早会跟收敛的那张表走散。

        以前这里还要把「归档会连带走的后代」算进来。不用了：工作不嵌套，一条支线
        没有后代。
        """
        from app.domain.review.services import AcceptService  # 局部 import：避免成环

        return await AcceptService(self._session).anybody_still_waiting([task_id])

    async def _defer_archive(self, card: ConclusionCard, sub: Task) -> None:
        """记下"收起欠着"，并在那条支线里说明为什么它还开着。

        结论本身照常结算、照常回流——父话题读到的东西一个字都没少，欠下的只有
        归档这一个动作。
        """
        if card.settle_reason == ARCHIVE_DEFERRED:  # 幂等：别重复播报
            return
        card.settle_reason = ARCHIVE_DEFERRED
        await self._session.flush()
        await self._blocks.add(
            project_id=card.project_id,
            topic_id=sub.room_id,
            task_id=sub.id,
            author=SYSTEM_ACTOR,
            author_type=AuthorType.system,
            content="结论已被房间采信，这条支线暂不收起",
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
        """把欠下的收起补上 —— "不留僵尸支线"那一条就落在这里。

        推迟不是取消。一张被推迟的卡带着 `ARCHIVE_DEFERRED` 这个哨兵，扫描每轮
        都来看一眼它欠的归档能不能落：

        - 验收卡被**采纳** → 采纳即归档，话题自己就归档了，这里只把哨兵消掉；
        - 验收卡被**驳回/作废**（或闸门判红）→ 话题还活着，给一个
          `ARCHIVE_REFILE_GRACE_MINUTES` 的窗口让它改完重新递卡；窗口内递出新卡
          就重新受保护，窗口过完还没有新卡，归档在这里落下；
        - 卡还在**等人** → 什么都不做，这正是本次修复要保住的状态。

        所以一条被推迟的支线只可能停在两处：手上有一张活卡（有人正欠它一个
        决定），或者还在重新递卡的宽限里。两者都是有界的，没有第三种停法。
        """
        moment = now or datetime.now(UTC)
        archived: list[uuid.UUID] = []
        for card in await self._repo.list_archive_deferred():
            if await self._discharge_deferred_archive(card, now=moment):
                archived.append(card.task_id or card.topic_id)
        return archived

    async def _discharge_deferred_archive(
        self, card: ConclusionCard, *, now: datetime
    ) -> bool:
        """一张卡的归档待办能不能结清。True = 这一轮把话题归档了。"""
        sub = (
            None
            if card.task_id is None
            else await TaskService(self._session).get(card.task_id)
        )
        if sub is None or sub.status != TaskStatus.open:
            # 多半是验收卡被采纳了（采纳即收起），待办自己消解了。消掉哨兵，
            # 这张卡从此退出扫描——包括人后来手动重开它的情况：那是人的决定，
            # 平台不该拿一条早就结算完的结论把它再关一次。
            await self._settle_deferral(card)
            return False
        from app.domain.review.services import AcceptService  # 局部 import：避免成环

        accepts = AcceptService(self._session)
        scope = [sub.id]
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
        # The verdict lands in the THREAD it is about — that is where the 分身
        # waiting on it is looking, and where a reader who opens the work later
        # finds out how it ended.
        await self._blocks.add(
            project_id=card.project_id,
            topic_id=card.topic_id,
            task_id=card.task_id,
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
