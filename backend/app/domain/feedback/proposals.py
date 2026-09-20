"""agent 提案：芝士举手，人决定。

规则在这里，因为它们是**三条彼此独立**的限流，每一条都在挡一种不同的刷屏：

1. **配额** —— 每个话题每天最多 `settings.feedback_proposals_per_topic_per_day` 张。
   形状照 `alerts` 那套分级限流（`docs/product-impl.md` §3.7）。提案卡和决策请求
   的区别就在这一条上：决策请求「有人在等」，丢一张就是把人卡住；提案卡没有人在等，
   它可以被丢。
2. **指纹去重** —— 同一个问题换一种说法提上来，人不想看第二遍。指纹算
   「发生了什么 + 怎么复现」归一化之后的哈希。
3. **拒绝要有记忆** —— 这一条最重要。原型的「不用」只把组件状态置成 `dismissed`，
   刷新就回来；服务端不落一行的话，同一个问题每轮都会再问一遍。这是刷屏最主要的
   来源，而且它会让人对整张卡产生免疫。

提案本身**不落这张表**：它就是话题里的一条消息块（`Block.meta.feedback_proposal`），
也就是人看到的那张卡。落表的只有「不用」，因为那是唯一一件卡消失之后还必须记得的事。

**agent 不能自己把它变成正式反馈**（§5.2）。提交那一下必须是人：`POST /feedback`
带 `proposal_block_id` 时，作者从提案里取（agent），`submitted_by_handle` 取
验证过的调用者。agent 直接调 `POST /feedback` 会被拒 —— 没有这条，一个跑歪的 agent
可以往公开列表里灌东西。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, PreconditionFailedError
from app.domain.block.models import Block
from app.domain.feedback.models import FeedbackProposalDismissal
from app.domain.feedback.schemas import FeedbackProposalIn


@dataclass(frozen=True)
class AcceptedProposal:
    """A proposal card, plus who wrote it — the two things `POST /feedback`
    needs and must not take from the request body.

    `author_handle` comes off the block (`Block.author`), so the agent that
    proposed it is the author and the person who pressed send is only the
    submitter. A client cannot name either one.
    """

    payload: dict[str, Any]
    author_handle: str


#: 「发生了什么」和「怎么复现」两段进指纹。其余字段都是措辞，措辞会变，问题不会。
FINGERPRINT_FIELDS = ("what_happened", "repro")

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w一-鿿]+")


def proposal_fingerprint(*, what_happened: str | None, repro: str | None) -> str:
    """Stable hash of the two fields that describe the problem itself.

    Normalised — whitespace collapsed, punctuation dropped, case folded — so the
    same words typed twice land together even if the model reflowed them.

    **What this does not do**: catch a genuinely rephrased report. Different
    words hash differently, so "按钮点了没反应" and "点击按钮之后界面没有变化" are
    two fingerprints and two cards. Catching that needs embeddings, which is a
    much larger thing than this limit is worth — the daily cap is the backstop
    for the cases the fingerprint misses. Said plainly here so nobody reads the
    dedup as stronger than it is.
    """
    raw = "\n".join((what_happened or "", repro or ""))
    normalised = _PUNCTUATION.sub(" ", _WHITESPACE.sub(" ", raw).strip().lower())
    normalised = " ".join(normalised.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]


class ProposalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _proposals_since(
        self, topic_id: uuid.UUID, since: datetime
    ) -> list[dict[str, Any]]:
        """The proposal cards this topic produced since `since`, oldest first.

        Counted off the message blocks themselves rather than a counter column:
        the blocks *are* the cards, and a counter is a second copy of a number
        that a deleted block silently invalidates.

        One query, then the payload filter in Python. A JSON path predicate
        would be dialect-specific for no gain: this reads at most one day of one
        topic's blocks — bounded by the day, and the day is the cap's horizon.
        """
        stmt = (
            select(Block)
            .where(
                Block.topic_id == topic_id,
                Block.created_at >= since,
                Block.meta.is_not(None),
            )
            .order_by(Block.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        payloads = (proposal_from_meta(row.meta or {}) for row in rows)
        return [payload for payload in payloads if payload is not None]

    async def _is_dismissed(self, topic_id: uuid.UUID, fingerprint: str) -> bool:
        stmt = select(FeedbackProposalDismissal.id).where(
            FeedbackProposalDismissal.topic_id == topic_id,
            FeedbackProposalDismissal.fingerprint == fingerprint,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def _dismissed(self, topic_id: uuid.UUID) -> set[str]:
        stmt = select(FeedbackProposalDismissal.fingerprint).where(
            FeedbackProposalDismissal.topic_id == topic_id
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def live_cards(self, topic_id: uuid.UUID) -> list[dict[str, Any]]:
        """The proposal cards still worth showing, newest first.

        A card stays in the topic either way (it is a message, and history is
        history) — what this filters is which ones the chat column still offers
        to send. Dismissed ones drop out by fingerprint: a reflowed re-proposal
        of a refused problem stays out, while a rephrased one gets a new
        fingerprint and does come back. That is the honest reach of a hash —
        see `proposal_fingerprint`.

        One card per fingerprint even if it was proposed several times, newest
        kept — the card that shows is the one a person can still act on.
        """
        dismissed = await self._dismissed(topic_id)
        stmt = (
            select(Block)
            .where(Block.topic_id == topic_id, Block.meta.is_not(None))
            .order_by(Block.created_at.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        seen: set[str] = set()
        cards: list[dict[str, Any]] = []
        for row in rows:
            payload = proposal_from_meta(row.meta or {})
            if payload is None:
                continue
            fingerprint = payload.get("fingerprint", "")
            if fingerprint in dismissed or fingerprint in seen:
                continue
            seen.add(fingerprint)
            cards.append(
                {
                    "block_id": str(row.id),
                    "author_handle": row.author,
                    "authored_at": row.created_at.isoformat(),
                    "payload": payload,
                }
            )
        return cards

    async def check(self, topic_id: uuid.UUID, body: FeedbackProposalIn) -> str:
        """Return the fingerprint, or refuse with the reason.

        Three refusals, one status: **412 for all three**. They differ in wording
        and in which limit they hit, but the client's correct reaction is the same
        for every one — stop trying, do not retry later. 409 or 429 would tell a
        naive caller "try again", which is the wrong instruction for all of them.

        Ordering is oldest-limit-first on purpose. "You already refused this" is
        the most specific and the most permanent, so it is answered before "you
        are out of quota today" — otherwise a refused problem, once the topic is
        out of quota, would come back as a temporary-looking refusal and get
        proposed again tomorrow.

        「一天」是滚动的 24 小时，不是自然日：自然日会在午夜清零，于是紧挨着
        午夜的两分钟里可以提两条一样的。
        """
        fingerprint = proposal_fingerprint(
            what_happened=body.what_happened, repro=body.repro
        )
        if fingerprint in await self._dismissed(topic_id):
            raise PreconditionFailedError("这个提案已经被「不用」过了，不要重复提")
        # One read serves both remaining limits: the duplicate check and the cap
        # are the same list of cards, counted two ways.
        cards = await self._proposals_since(
            topic_id, datetime.now(UTC) - timedelta(days=1)
        )
        if any(card.get("fingerprint") == fingerprint for card in cards):
            raise PreconditionFailedError("这个提案刚提过，不要重复提")
        if len(cards) >= settings.feedback_proposals_per_topic_per_day:
            raise PreconditionFailedError("今天这个话题的反馈提案已经够了，明天再说")
        return fingerprint

    async def dismiss(
        self, topic_id: uuid.UUID, fingerprint: str, *, handle: str
    ) -> None:
        """Record 「不用」. Idempotent: a second press is not a second row."""
        if fingerprint in await self._dismissed(topic_id):
            return
        self._session.add(
            FeedbackProposalDismissal(
                topic_id=topic_id,
                fingerprint=fingerprint,
                dismissed_by_handle=handle,
            )
        )
        await self._session.flush()


def proposal_meta(body: FeedbackProposalIn, fingerprint: str) -> dict[str, Any]:
    """The card's payload, stored in the block's `meta`.

    `fingerprint` travels with the card so the dismiss endpoint never has to
    recompute it from text that the frontend may have re-rendered.
    """
    return {
        "kind": body.kind.value,
        "title": body.title,
        "summary": body.summary or body.title,
        "problem": body.problem,
        "visibility": body.visibility.value,
        "why": body.why,
        "expectation": body.expectation,
        "what_happened": body.what_happened,
        "repro": body.repro,
        "evidence": body.evidence,
        "logs": body.logs,
        "session_id": body.session_id,
        "environment": body.environment,
        "tags": list(body.tags),
        "user_said": body.user_said,
        "fingerprint": fingerprint,
    }


def proposal_from_meta(meta: dict[str, Any]) -> dict[str, Any] | None:
    """Read a card's payload back out of a block, or None if it is not one."""
    payload = (meta or {}).get("feedback_proposal")
    if not isinstance(payload, dict):
        return None
    return payload


def proposal_block_or_404(block: Block | None) -> dict[str, Any]:
    if block is None:
        raise NotFoundError("提案不存在")
    payload = proposal_from_meta(block.meta or {})
    if payload is None:
        # A real block id that is not a proposal: 404 rather than 400 — the
        # caller asked for a proposal and there is none at this id.
        raise NotFoundError("提案不存在")
    return payload
