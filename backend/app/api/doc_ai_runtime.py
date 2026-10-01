"""Process-owner assembly; no credentials enter persisted AI request context."""

from app.api.auth import ActorResolver
from app.api.deps import get_chat_service
from app.api.doc_identity import human_operation_actor
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import ValidationError
from app.domain.doc_ai.completion import complete
from app.domain.doc_ai.routing import admit
from app.domain.doc_ai.services import DocAiService
from app.domain.doc_ai.worker import run_one
from app.domain.identity.actor import Actor
from app.domain.topic.services import TopicService
from app.domain.user.services import usernames_by_ids


async def authorize_work(session, lease):
    prefix, _, suffix = lease.actor.partition(":")
    if prefix != "user" or not suffix.isdecimal():
        raise ValidationError("文档请求没有已认证的人类身份")
    uid = int(suffix)
    handle = (await usernames_by_ids(session, [uid])).get(uid)
    if not handle:
        raise ValidationError("文档请求发起人的账号已不存在")
    actor = Actor(handle=handle, user_id=uid, via="token")
    await human_operation_actor(session, actor)
    topic = await TopicService(session).get_or_404(lease.room_id)
    if topic.project_id != lease.project_id or topic.archived_at is not None:
        raise ValidationError("文档请求所在房间已归档或发生变化")
    resolver = ActorResolver(session=session, bearer=None, cheese_token="", writes=True)
    await resolver.authorize_topic(
        actor, project_id=lease.project_id, topic_id=lease.room_id, enforce=True
    )


async def invoke(lease):
    base = (settings.llm_gateway_admin_base or "").rstrip("/")
    if not base:
        raise ValidationError("当前部署没有配置文档 completion 网关")
    key = await get_chat_service().project_gateway_key(lease.project_id)
    if not key:
        raise ValidationError("项目模型 key 不可用；没有改用全局凭据")
    # Key lookup can await a remote mint; recheck after that wait, before HTTP.
    async with async_session_factory() as session:
        await authorize_work(session, lease)
        if not await DocAiService(session).ready_to_invoke(lease):
            raise ValidationError("文档请求已取消或租约已过期")
        await admit(session, lease.project_id, lease.binding)
    return await complete(lease, base=base, key=key)


async def meter(lease):
    await get_chat_service()._drain_gateway_usage(
        lease.project_id,
        lease.room_id,
        lease.request_id,
    )


async def reconcile_usage(sessions, drain):
    async with sessions() as session:
        lease = await DocAiService(session).claim_meter()
        await session.commit()
    if lease is not None:
        await drain(lease)


async def scan_document_ai():
    await reconcile_usage(async_session_factory, meter)
    await run_one(async_session_factory, invoke, meter, authorize_work)
