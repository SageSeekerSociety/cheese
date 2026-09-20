"""提案卡：芝士举手的那条路 —— 话题范围的 `/topics/{topic_id}/feedback-proposals`。

为什么是话题范围而不是 `/feedback/proposals`：提案是一句**在某个话题里**说的话，
配额、去重、拒绝的记忆全都挂在这个话题上；而「这个人能不能在这个话题里提」这个问题
已经有现成的答案（`resolver.authorize_topic`，`topics.py` 的 `_actor_in_place`
用的就是它）。绕开它自己搭一套的话，第一个漏挂门禁的端点不会报错，只会安静地放人进去。

卡本身是一条 `Block`（`meta.feedback_proposal`），不另开表 —— 它就是人看到的那张
东西，而 Block 已经会渲染、已经会广播、已经会跟着话题一起消失。落表的只有「不用」。

**发送**放在这里而不是 `POST /feedback`：发送要读卡上的作者、要按话题鉴权，
两个前提都只有在这个前缀下面才拿得到。`POST /feedback` 因此保持「人给自己提一条」
这一个含义，agent 走它会被拒。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, NotFoundError
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.feedback import proposals as proposal_rules
from app.domain.feedback.schemas import (
    FeedbackCreate,
    FeedbackProposalIn,
    FeedbackProposalResult,
)
from app.domain.feedback.services import FeedbackService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["feedback"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_proposal_service(db: DbSession) -> proposal_rules.ProposalService:
    return proposal_rules.ProposalService(db)


ProposalServiceDep = Annotated[
    proposal_rules.ProposalService, Depends(get_proposal_service)
]


async def _actor_in_topic(
    db: AsyncSession, resolver: ActorResolver, topic_id: uuid.UUID
):
    """The verified caller, authorized for this topic, as `(place, actor)`.

    Same two steps as `topics.py::_actor_in_place` — resolve **with** the topic
    context (that is what lets a topic-scoped credential resolve at all), then
    authorize. Copying the order matters: authorize-then-resolve would authorize
    a caller who has not been resolved against this topic yet.
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    if not actor.authenticated or not actor.handle:
        raise AuthenticationRequiredError("需要登录")
    return place, actor


async def _require_proposal_block(
    db: AsyncSession, topic_id: uuid.UUID, block_id: uuid.UUID
):
    block = await BlockRepository(db).get(block_id)
    if block is None or block.topic_id != topic_id:
        # A block in another topic, or none at all: 404. The id the client sent
        # is the problem, not a secret — but answering 400 would confirm which
        # ids are real.
        raise NotFoundError("提案不存在")
    return block


@router.get("/{topic_id}/feedback-proposals")
async def list_feedback_proposals(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """这个话题里还活着的提案卡，最新的一张在前。

    排除已经「不用」过的：那张卡不该再出现，而它是否出现过由服务端的指纹决定，
    不由前端的组件状态决定 —— 原型的「不用」只活在内存里，刷新就回来。
    """
    await _actor_in_topic(db, resolver, topic_id)
    cards = await proposal_rules.ProposalService(db).live_cards(topic_id)
    return ok(cards)


@router.post("/{topic_id}/feedback-proposals")
async def propose_feedback(
    topic_id: uuid.UUID,
    body: FeedbackProposalIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """落一张提案卡（`cheese feedback propose` 的落点）。

    三道限流都在 `proposals.ProposalService.check` 里，各自回不同的状态码：拒绝过的
    回 412（别重试，别再提这个）、今天提过同样的回 412、超过今天的话题配额回 412。
    412 而不是 429：客户端的正确反应是**别做这件事**，不是等一会儿再做。
    """
    place, actor = await _actor_in_topic(db, resolver, topic_id)
    handle, is_agent = actor.handle, actor.is_agent
    service = proposal_rules.ProposalService(db)
    fingerprint = await service.check(topic_id, body)
    block = await BlockRepository(db).add(
        project_id=place.project_id,
        topic_id=topic_id,
        author=handle,
        author_type=AuthorType.ai if is_agent else AuthorType.human,
        content=body.title,
        kind=BlockKind.message,
        meta={"feedback_proposal": proposal_rules.proposal_meta(body, fingerprint)},
    )
    await db.commit()
    result = FeedbackProposalResult(block_id=block.id, fingerprint=fingerprint)
    return ok(result.model_dump(mode="json"))


@router.post("/{topic_id}/feedback-proposals/{block_id}/dismiss")
async def dismiss_feedback_proposal(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """「不用」。落一行。

    不删除那张卡：历史留在话题里是应该的（原型的「不用」也不是删，草稿还在队列里），
    而**它被拒绝过**这件事才是必须记住的 —— 所以删的是「以后别再问」，留的是「问过」。
    """
    _, actor = await _actor_in_topic(db, resolver, topic_id)
    block = await _require_proposal_block(db, topic_id, block_id)
    payload = proposal_rules.proposal_block_or_404(block)
    await proposal_rules.ProposalService(db).dismiss(
        topic_id, payload["fingerprint"], handle=actor.handle
    )
    await db.commit()
    return ok({"dismissed": True})


@router.post("/{topic_id}/feedback-proposals/{block_id}/accept")
async def accept_feedback_proposal(
    topic_id: uuid.UUID,
    block_id: uuid.UUID,
    body: FeedbackCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """发送：把卡变成一条正式反馈。

    作者从**卡上**取（提案那个 agent），提交者取验证过的调用者 —— 两个字段，不是一个，
    所以「芝士提的反馈里有多少真的被人发出去了」答得出来。两边都不是客户端说了算：
    这就是为什么发送走这里，而不是让客户端往 `POST /feedback` 里塞一个作者名。

    正文取请求体而不是卡上的原文：抽屉是预填的，人可以改完再发（原型的流程就是
    「卡 → 提交反馈 → 抽屉 → 提交」），而按下发送的人为自己发出去的东西负责。

    `actor.is_agent` 如实往下传，不写死 False：agent 自己按发送和直接发布是同一件
    事，拒绝在 `FeedbackService.create` 里，这里不替它开例外。
    """
    place, actor = await _actor_in_topic(db, resolver, topic_id)
    block = await _require_proposal_block(db, topic_id, block_id)
    payload = proposal_rules.proposal_block_or_404(block)
    service = FeedbackService(db)
    # The topic and project come from the URL, not from the body: this endpoint
    # is defined as "send the card in THIS topic", and letting the body name a
    # different one would make the quota and the authorization describe two
    # different places.
    row = await service.create(
        body.model_copy(
            update={"topic_id": place.room_id, "project_id": place.project_id}
        ),
        actor_handle=actor.handle,
        actor_user_id=actor.user_id,
        actor_is_agent=actor.is_agent,
        proposal=proposal_rules.AcceptedProposal(
            payload=payload, author_handle=block.author
        ),
    )
    await db.commit()
    view = await service.detail_of(
        row, handle=actor.handle, is_admin=service.is_admin(actor.handle)
    )
    return ok(view.model_dump(mode="json"))
