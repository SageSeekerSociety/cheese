"""Process-owner assembly; no credentials enter persisted AI request context."""

from app.api.deps import get_chat_service
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import ValidationError
from app.domain.doc_ai.completion import complete
from app.domain.doc_ai.worker import run_one


async def invoke(lease):
    base = (settings.llm_gateway_admin_base or "").rstrip("/")
    if not base:
        raise ValidationError("当前部署没有配置文档 completion 网关")
    key = await get_chat_service().project_gateway_key(lease.project_id)
    if not key:
        raise ValidationError("项目模型 key 不可用；没有改用全局凭据")
    return await complete(lease, base=base, key=key)


async def meter(lease):
    await get_chat_service()._drain_gateway_usage(
        lease.project_id,
        lease.room_id,
        lease.request_id,
    )


async def scan_document_ai():
    await run_one(async_session_factory, invoke, meter)
