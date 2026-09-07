"""Project environment settings and explicit room preparation controls."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service
from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import environment_status
from app.domain.agent.harness.claude_code.hooks_substrate import (
    drop_topic_subscriptions,
)
from app.domain.agent.models import AgentTurn
from app.domain.device.wiring import sql_device_service
from app.domain.membership.repositories import MemberRepository
from app.domain.project.environment import EnvironmentConfig, project_environment
from app.domain.project.models import Project, ProjectRole
from app.domain.topic.models import Topic, TopicStatus
from app.domain.user.repositories import UserRepository

router = APIRouter(prefix="/projects/{project_id}/environment", tags=["environment"])
Db = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[AuthUserInfo, Depends(require_auth_user)]
Chat = Annotated[ChatService, Depends(get_chat_service)]


async def access(
    db: AsyncSession,
    project_id: uuid.UUID,
    auth_user: AuthUserInfo,
    *,
    write: bool = False,
) -> tuple[Project, bool]:
    project = await db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    user = await UserRepository(db).get_by_id(auth_user.user_id)
    member = await MemberRepository(db).get(
        project_id=project_id, user_handle=user.username if user else ""
    )
    steward = bool(
        user
        and (
            project.owner_handle == user.username
            or member
            and member.role == ProjectRole.lead
        )
    )
    if not steward and (write or member is None):
        raise ForbiddenError("只有项目成员能查看环境，owner / lead 能修改环境")
    return project, steward


async def room(
    db: AsyncSession, project_id: uuid.UUID, topic_id: uuid.UUID, *, lock: bool = False
) -> Topic:
    statement = select(Topic).where(
        Topic.id == topic_id,
        Topic.project_id == project_id,
        Topic.is_private.is_(False),
    )
    if lock:
        statement = statement.with_for_update()
    topic = await db.scalar(statement)
    if topic is None:
        raise NotFoundError("Room not found")
    return topic


@router.get("")
async def get_environment(project_id: uuid.UUID, db: Db, user: User) -> dict:
    project, can_edit = await access(db, project_id, user)
    topics = (
        await db.scalars(
            select(Topic)
            .where(
                Topic.project_id == project_id,
                Topic.is_private.is_(False),
                Topic.status != TopicStatus.archived,
            )
            .order_by(Topic.created_at)
        )
    ).all()
    return ok(
        {
            "config": project_environment(project.settings),
            "can_edit": can_edit,
            "rooms": [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "revision": (t.environment or {}).get("revision"),
                }
                for t in topics
            ],
        }
    )


@router.put("")
async def save_environment(
    project_id: uuid.UUID, body: EnvironmentConfig, db: Db, user: User
) -> dict:
    await access(db, project_id, user, write=True)
    project = await db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    config = body.snapshot()
    if project is None:
        raise NotFoundError("Project not found")
    project.settings = {**(project.settings or {}), "environment": config}
    await db.flush()
    return ok(config)


@router.get("/rooms/{topic_id}")
async def get_room_environment(
    project_id: uuid.UUID, topic_id: uuid.UUID, db: Db, user: User
) -> dict:
    await access(db, project_id, user)
    topic = await room(db, project_id, topic_id)
    binding = await sql_device_service(db).topic_binding(topic_id)
    if binding is None:
        state = {"state": "pending"}
    elif not device_hub.is_online(binding.device_id):
        state = {"state": "offline"}
    else:
        state = await environment_status(
            device_hub, binding.device_id, project_id, topic_id
        )
    return ok({**state, "pinned_revision": (topic.environment or {}).get("revision")})


class ApplyEnvironment(BaseModel):
    latest: bool = False


@router.post("/rooms/{topic_id}/apply")
async def apply_environment(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    body: ApplyEnvironment,
    db: Db,
    user: User,
    chat: Chat,
) -> dict:
    project, _ = await access(db, project_id, user, write=True)
    async with chat.edit_environment(topic_id):
        topic = await room(db, project_id, topic_id, lock=True)
        active = await db.scalar(
            select(AgentTurn.id)
            .where(AgentTurn.topic_id == topic_id, AgentTurn.stopped_at.is_(None))
            .limit(1)
        )
        if active is not None:
            raise ValidationError("房间仍有未结束的工作，不能修改正在使用的环境")
        binding = await sql_device_service(db).topic_binding(topic_id)
        if binding is not None:
            if not device_hub.is_online(binding.device_id):
                raise ValidationError("机器离线，无法确认旧会话已停止，请连接后重试")
            await environment_status(
                device_hub, binding.device_id, project_id, topic_id, action="reset"
            )
            await drop_topic_subscriptions(topic_id)
            for screen in device_hub.screens_for_topic(topic_id):
                await device_hub.close_screen(screen.device_id, screen.sid)
        if body.latest or topic.environment is None:
            topic.environment = project_environment(project.settings)
        # Commit while holding the prompt lock, before another turn can start.
        await db.commit()
    return ok({"state": "pending", "revision": topic.environment["revision"]})
