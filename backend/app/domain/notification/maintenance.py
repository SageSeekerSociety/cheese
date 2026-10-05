"""Committed email intents are consumed outside the producer transaction."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.email import get_email_sender, is_placeholder_email
from app.domain.notification.entity_resolvers import (
    TeamEntityResolver,
    UserEntityResolver,
)
from app.domain.notification.letter import letter_for, render_html, render_text
from app.domain.notification.repositories import NotificationRepository
from app.domain.notification.services import NotificationQueryService
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import TeamService
from app.domain.user.models import User
from app.domain.user.repositories import UserProfileRepository
from app.domain.user.services import UserService

logger = logging.getLogger(__name__)


async def _names(session: AsyncSession, payload: Any) -> dict[str, str]:
    """`payload` 顶层那些用户、团队引用的显示名，按键索引。

    和站内通知用同一套解析（`NotificationQueryService` 加两个 resolver），所以邮件
    里的「张三」和收件箱里的是同一个名字。
    """
    if not isinstance(payload, dict):
        return {}
    avatar_url = settings.avatar_base_url
    resolved = await NotificationQueryService(
        NotificationRepository(session),
        resolvers=[
            TeamEntityResolver(TeamService(TeamRepository(session)), avatar_url),
            UserEntityResolver(UserService(UserProfileRepository(session)), avatar_url),
        ],
    ).resolve_entities_from_metadata([payload])
    return {path: info.name for path, info in resolved.items() if info and info.name}


async def send_email(sessions, item):
    async with sessions() as session:
        email = await session.scalar(
            select(User.email).where(User.id == item["recipientId"])
        )
        names = await _names(session, item.get("payload"))
    if not email or is_placeholder_email(email):
        raise ValueError("Email recipient has no address")
    letter = letter_for(item, names)
    if not await get_email_sender().send(
        to=email,
        subject=letter.subject,
        body_html=render_html(letter),
        body_text=render_text(letter),
    ):
        raise RuntimeError("SMTP delivery returned false")


async def drain_email_queue(sessions: SessionFactory) -> dict[str, int]:
    from app.domain.notification.legacy_queue import import_legacy_queue
    from app.domain.notification.outbox import drain_channel

    try:
        await import_legacy_queue(
            sessions, channel="email", batch_size=settings.notification_email_batch_size
        )
    except Exception:
        # Redis migration failure must not stall new SQL-backed notifications.
        logger.exception("Legacy email queue migration will retry next tick")

    result = await drain_channel(
        sessions,
        channel="email",
        batch_size=settings.notification_email_batch_size,
        max_attempts=max(1, settings.notification_email_max_retries),
    )
    result.pop("expired")
    return result
