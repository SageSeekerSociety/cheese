"""Project environment settings and explicit room preparation controls."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service
from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent.chat import ChatService
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_provider import environment_status
from app.domain.agent.models import AgentTurn
from app.domain.device.wiring import sql_device_service
from app.domain.membership.repositories import MemberRepository
from app.domain.project.environment import EnvironmentConfig, project_environment
from app.domain.project.environment_recovery import (
    close_recovery,
    latest_recovery,
    reconcile_recovery,
)
from app.domain.project.models import Project, ProjectRole
from app.domain.topic.models import Topic, TopicKind, TopicStatus
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
                Topic.kind != TopicKind.root,
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
            device_hub, binding.device_id, project_id, topic.resource_id or topic_id
        )
    recovery = await reconcile_recovery(db, topic_id)
    busy = await db.scalar(
        select(AgentTurn.id)
        .where(AgentTurn.topic_id == topic_id, AgentTurn.stopped_at.is_(None))
        .limit(1)
    )
    return ok(
        {
            **state,
            "pinned_revision": (topic.environment or {}).get("revision"),
            **({"busy": True} if busy is not None else {}),
            **(
                {"recovery_state": (recovery.meta or {}).get("state")}
                if recovery
                else {}
            ),
        }
    )


class ApplyEnvironment(BaseModel):
    latest: bool = False


async def reset_idle_room(db: AsyncSession, topic_id: uuid.UUID, project_id: uuid.UUID):
    topic = await room(db, project_id, topic_id, lock=True)
    if topic.archived_at is not None:
        raise ValidationError("请先取消归档，再修改房间环境")
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
            device_hub,
            binding.device_id,
            project_id,
            topic.resource_id or topic_id,
            action="reset",
        )
        # Closing each screen also releases its runtime subscription.
        for screen in device_hub.screens_for_topic(topic_id):
            await device_hub.close_screen(screen.device_id, screen.sid)


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
        if topic.kind == TopicKind.root:
            raise ValidationError("总览使用基础运行环境，不应用项目脚本")
        await reset_idle_room(db, topic_id, project_id)
        await close_recovery(db, topic_id)
        if body.latest or topic.environment is None:
            topic.environment = project_environment(project.settings)
        # Commit while holding the prompt lock, before another turn can start.
        await db.commit()
    return ok({"state": "pending", "revision": topic.environment["revision"]})


async def overview_access(request: Request, db: Db, project_id: uuid.UUID):
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    project = await db.get(Project, project_id)
    if (
        not claims
        or project is None
        or project.root_topic_id is None
        or claims.get("p") != str(project_id)
        or claims.get("t") != str(project.root_topic_id)
    ):
        raise ForbiddenError("只有本项目总览芝士能处理环境修复")
    return project


@router.get("/recovery/rooms/{topic_id}")
async def inspect_recovery(
    project_id: uuid.UUID, topic_id: uuid.UUID, request: Request, db: Db
):
    await overview_access(request, db, project_id)
    topic = await room(db, project_id, topic_id)
    incident = await latest_recovery(db, topic_id)
    if incident is None:
        raise NotFoundError("没有待处理的环境故障")
    binding = await sql_device_service(db).topic_binding(topic_id)
    status = (
        await environment_status(
            device_hub, binding.device_id, project_id, topic.resource_id or topic_id
        )
        if binding is not None and device_hub.is_online(binding.device_id)
        else {"state": "offline"}
    )
    return ok(
        {
            "incident_id": str(incident.id),
            "recovery": incident.meta,
            "config": topic.environment,
            "status": status,
        }
    )


class RepairEnvironment(BaseModel):
    incident_id: uuid.UUID
    expected_revision: str
    config: EnvironmentConfig | None = None
    reason: str = ""


@router.post("/recovery/rooms/{topic_id}")
async def repair_environment(
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    body: RepairEnvironment,
    request: Request,
    db: Db,
    chat: Chat,
):
    from app.api.deps import get_work_runner

    await overview_access(request, db, project_id)
    async with chat.edit_environment(topic_id):
        topic = await room(db, project_id, topic_id, lock=True)
        incident = await latest_recovery(db, topic_id)
        if (
            topic.kind == TopicKind.root
            or incident is None
            or incident.id != body.incident_id
            or (incident.meta or {}).get("state") != "requested"
            or (topic.environment or {}).get("revision") != body.expected_revision
        ):
            raise ValidationError("环境已变化或自动重试已使用，请重新查看状态")
        if body.config is None:
            if not body.reason.strip():
                raise ValidationError("请说明需要什么协助")
            incident.meta = {
                **incident.meta,
                "state": "needs_help",
                "reason": body.reason,
            }
            await db.commit()
            return ok({"state": "needs_help"})
        binding = await sql_device_service(db).topic_binding(topic_id)
        if binding is None or not device_hub.is_online(binding.device_id):
            raise ValidationError("机器离线，暂时无法修复")
        state = await environment_status(
            device_hub, binding.device_id, project_id, topic.resource_id or topic_id
        )
        if state.get("state") != "failed" or state.get("attempt") != incident.meta.get(
            "attempt"
        ):
            raise ValidationError("房间已不处于本次失败状态，请重新查看状态")
        await reset_idle_room(db, topic_id, project_id)
        topic.environment = body.config.snapshot()
        turn_id = uuid.uuid4()
        incident.meta = {
            **incident.meta,
            "state": "retrying",
            "dispatch_turn": str(turn_id),
            "dispatched_at": datetime.now(UTC).isoformat(),
        }
        await db.commit()
        get_work_runner().submit_kickoff(
            chat,
            topic_id,
            turn_id=turn_id,
            prompt="环境配置已修复，请继续处理此前尚未送达的用户消息。",
        )
    return ok({"state": "retrying", "turn_id": str(turn_id)})
