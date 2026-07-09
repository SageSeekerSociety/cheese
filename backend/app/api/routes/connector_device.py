"""The device-flow HTTP endpoints ``cheese auth login`` speaks (Act 2 step 3, HTTP half).

These wrap ``DeviceService`` one-to-one and match the frozen web-claude contract:

  POST /connector/auth/device/start   {device_name?}   -> {device_code, approve_url, interval}
  POST /connector/auth/device/poll    {device_code}    -> {status[, token, device_id, device_name]}
  POST /connector/auth/device/rename  {device_name} + Bearer -> {ok, device_name}

They are decision-free (no ambiguous authz): ``start`` / ``poll`` are unauthenticated
by construction (the flow's secret is the opaque code), ``rename`` authenticates with
the device's own durable token. The human approval itself is ``POST /auth/device/approve``
below: it runs behind a real login (``get_current_user_id``) and binds the pending device
to the approver as its owner. Project + agent binding is a later, separate step.

``approve_url`` points at the frontend ``/connect`` page on whatever public origin the
request came in on (derived from the request, not a configured URL), where that logged-in
human approves — matching CLAUDE.md.
"""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.hub import DeviceHub
from app.agent.identity import build_member_dicts
from app.agent.orchestrator import AgentService
from app.common.auth import get_current_user_id
from app.common.origin import public_origin
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.device.service import DeviceService
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.user.models import User, UserProfile

MembershipChecker = Callable[[int, int], Awaitable[bool]]  # (project_id, user_id) -> bool
OperateChecker = Callable[[str, str, int], Awaitable[bool]]  # (device_id, sid, user_id) -> bool


class DeviceStartBody(BaseModel):
    device_name: str | None = None


class DevicePollBody(BaseModel):
    device_code: str


class DeviceRenameBody(BaseModel):
    device_name: str


class DeviceApproveBody(BaseModel):
    code: str


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationRequiredError("missing bearer device token")
    return authorization[7:]


def build_device_flow_router(device_service: DeviceService) -> APIRouter:
    # The frozen cli derives this from its bare-origin `base` as `base + /auth/device/*`
    # (the installer strips the `/connector` segment), so these live at the origin root,
    # not under `/connector`. See docs/design/architecture.md §5.5.
    router = APIRouter(prefix="/auth/device", tags=["connector"])

    @router.post("/start")
    async def device_start(body: DeviceStartBody, request: Request) -> dict[str, object]:
        code = await device_service.start(body.device_name)
        # The approve page lives at the site root (`/connect`), on whatever origin the
        # request actually came in on — derived from the request, not a configured URL,
        # so the link is correct under any host/port/proxy. (The cli reaches /start via
        # its `<origin>/api` base, so the forwarded Host is that public origin.)
        approve_url = public_origin(request) + f"/connect?code={code}"
        return {"device_code": code, "approve_url": approve_url, "interval": 1}

    @router.post("/poll")
    async def device_poll(body: DevicePollBody) -> dict[str, str | None]:
        return await device_service.poll(body.device_code)

    @router.post("/approve")
    async def device_approve(
        body: DeviceApproveBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        """The human approval behind the frontend ``/connect`` page: a logged-in user
        binds the pending device to themselves as its **owner**. Idempotent; unknown
        or expired codes 404. Project assignment and agents come later, not here."""
        device = await device_service.approve(body.code, actor_user_id=user_id)
        return {"ok": True, "device_id": device.device_id, "device_name": device.name}

    @router.post("/rename")
    async def device_rename(
        body: DeviceRenameBody, authorization: str | None = Header(default=None)
    ) -> dict[str, object]:
        device = await device_service.rename(_bearer(authorization), body.device_name)
        return {"ok": True, "device_name": device.name}

    return router


class AssignProjectBody(BaseModel):
    project_id: int


def build_device_admin_router(
    device_service: DeviceService, is_member: MembershipChecker
) -> APIRouter:
    """Owner-facing device management: assign the device to the projects it may run
    agents in (many-to-many). Assigning requires the caller to both own the device
    (enforced by the service) and be a member of the target project (checked here)."""
    router = APIRouter(prefix="/connector/devices", tags=["connector"])

    @router.post("/{device_id}/projects")
    async def assign_project(
        device_id: str, body: AssignProjectBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(body.project_id, user_id):
            raise ForbiddenError("must be a member of the project to assign a device to it")
        await device_service.assign_to_project(device_id, body.project_id, actor_user_id=user_id)
        return {"ok": True}

    @router.delete("/{device_id}/projects/{project_id}")
    async def unassign_project(
        device_id: str, project_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        await device_service.unassign_from_project(device_id, project_id, actor_user_id=user_id)
        return {"ok": True}

    @router.get("/{device_id}/projects")
    async def list_projects(
        device_id: str, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        device = await device_service.get_device(device_id)
        if device is None:
            raise NotFoundError("Unknown device")
        if device.owner_user_id != user_id:
            raise ForbiddenError("Only the device owner may list its projects")
        return {"project_ids": await device_service.list_projects(device_id)}

    return router


def build_device_update_router(
    device_service: DeviceService, hub: DeviceHub, is_member: MembershipChecker
) -> APIRouter:
    """Push a forced client self-update to a device. Authorized like device admin:
    the device **owner**, or a member of any project the device is assigned to
    (mirrors ``build_device_admin_router``). The device downloads + verifies the
    latest binary and hands off in place, so its running screens survive."""
    router = APIRouter(prefix="/connector/devices", tags=["connector"])

    @router.post("/{device_id}/update")
    async def update_device(
        device_id: str, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        device = await device_service.get_device(device_id)
        if device is None:
            raise NotFoundError("Unknown device")
        allowed = device.owner_user_id == user_id
        if not allowed:
            for project_id in await device_service.list_projects(device_id):
                if await is_member(project_id, user_id):
                    allowed = True
                    break
        if not allowed:
            raise ForbiddenError("not allowed to update this device")
        if not hub.is_online(device_id):
            raise PreconditionFailedError("device is not connected")
        await hub.send_update(device_id)
        return {"ok": True}

    return router


class OpenProjectAgentBody(BaseModel):
    nickname: str | None = None
    # 「复制自」: fork an existing agent's Claude conversation onto the new one. The
    # source must be a member of this same project (checked below) — a project agent
    # gets the same template options its human owner has via /connector/my/agents.
    copy_from_agent_user_id: int | None = None
    target_cwd: str | None = None
    # 「接入已有会话」: resume a Claude session already on disk instead of a fresh one.
    attach_session_id: str | None = None
    attach_cwd: str | None = None


class RecreateInProjectBody(BaseModel):
    resume: bool = False  # True → continue the old Claude conversation (claude --resume)
    force: bool = False  # True → replace a still-live screen (caller warned the human)
    cwd: str | None = None


def build_agent_list_router(
    agent_service: AgentService,
    is_member: MembershipChecker,
    device_service: DeviceService | None = None,
    hub: DeviceHub | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> APIRouter:
    """List a project's live agents (for the workspace to show avatars + open 现场).
    Project members only."""
    router = APIRouter(prefix="/connector/projects", tags=["connector"])

    @router.get("/{project_id}/agents")
    async def list_agents(
        project_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to list its agents")
        return {"agents": agent_service.list_agents_in_project(project_id)}

    if device_service is not None and hub is not None and session_factory is not None:

        @router.get("/{project_id}/devices")
        async def list_project_devices(
            project_id: int, user_id: int = Depends(get_current_user_id)
        ) -> dict[str, object]:
            """Devices assigned to this project — for the workspace's device panel.
            Project members only; the device's own owner_user_id is included so the
            UI can tell who may rename/unassign it (only its owner)."""
            if not await is_member(project_id, user_id):
                raise ForbiddenError("must be a member of the project to list its devices")
            devices = await device_service.list_devices_for_project(project_id)
            out: list[dict[str, object]] = []
            for device in devices:
                agent_ids = agent_service.agent_user_ids_on_device(device.device_id)
                async with session_factory() as session:
                    agents = await build_member_dicts(session, hub, agent_ids)
                out.append(
                    {
                        "device_id": device.device_id,
                        "name": device.name,
                        "owner_user_id": device.owner_user_id,
                        "online": hub.is_online(device.device_id),
                        "agents": agents,
                    }
                )
            return {"devices": out}

    @router.post("/{project_id}/agents")
    async def open_agent_in_project(
        project_id: int, body: OpenProjectAgentBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to run an agent in it")
        if body.copy_from_agent_user_id is not None and session_factory is not None:
            async with session_factory() as session:
                relation = await ProjectMembershipRepository(session).get_relation(
                    project_id, body.copy_from_agent_user_id
                )
            if relation is None:
                raise ForbiddenError("template agent must be a member of this project")
        opened = await agent_service.open_agent_in_project(
            project_id=project_id,
            nickname=body.nickname,
            copy_from_agent_user_id=body.copy_from_agent_user_id,
            target_cwd=body.target_cwd,
            attach_session_id=body.attach_session_id,
            attach_cwd=body.attach_cwd,
        )
        return {
            "agent_user_id": opened.agent_user_id,
            "agent_username": opened.agent_username,
            "sid": opened.sid,
        }

    @router.post("/{project_id}/agents/{agent_user_id}/recreate")
    async def recreate_agent_in_project(
        project_id: int,
        agent_user_id: int,
        body: RecreateInProjectBody,
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        """Recreate a project agent, auto-picking a device (works when the agent is
        offline). ``resume`` continues its old Claude conversation; ``force`` replaces a
        still-live screen — the service raises 412 on live-and-not-forced so the UI warns."""
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to recreate its agent")
        opened = await agent_service.recreate_agent_in_project(
            project_id=project_id,
            agent_user_id=agent_user_id,
            resume=body.resume,
            force=body.force,
            cwd=body.cwd,
        )
        return {
            "agent_user_id": opened.agent_user_id,
            "agent_username": opened.agent_username,
            "sid": opened.sid,
            "resumed": body.resume,
        }

    return router


class OpenAgentBody(BaseModel):
    project_id: int
    nickname: str | None = None


class SayBody(BaseModel):
    text: str


class RecreateAgentBody(BaseModel):
    agent_user_id: int
    project_id: int
    resume: bool = False  # True → continue the old Claude conversation (claude --resume)
    force: bool = False  # True → replace a still-live screen (caller warned the human)
    cwd: str | None = None


def build_agent_open_router(
    agent_service: AgentService,
    is_member: MembershipChecker,
    can_operate: "OperateChecker",
) -> APIRouter:
    """Open an agent (a screen) on a device in a project. The human trigger must be a
    member of the project; the orchestrator enforces device online + assigned and
    mints the agent's identity."""
    router = APIRouter(prefix="/connector/devices", tags=["connector"])

    @router.post("/{device_id}/agents")
    async def open_agent(
        device_id: str, body: OpenAgentBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(body.project_id, user_id):
            raise ForbiddenError("must be a member of the project to run an agent in it")
        opened = await agent_service.open_agent(
            device_id=device_id, project_id=body.project_id, nickname=body.nickname
        )
        return {
            "agent_user_id": opened.agent_user_id,
            "agent_username": opened.agent_username,
            "sid": opened.sid,
        }

    @router.post("/{device_id}/agents/recreate")
    async def recreate_agent(
        device_id: str, body: RecreateAgentBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        """Recreate an agent reusing its user. ``resume`` continues the old Claude
        conversation; ``force`` replaces a still-live screen. On a live-and-not-forced
        agent the service raises 412 so the UI can warn and re-call with force=True."""
        if not await is_member(body.project_id, user_id):
            raise ForbiddenError("must be a member of the project to recreate its agent")
        opened = await agent_service.recreate_agent(
            device_id=device_id,
            agent_user_id=body.agent_user_id,
            resume=body.resume,
            force=body.force,
            project_id=body.project_id,
            cwd=body.cwd,
        )
        return {
            "agent_user_id": opened.agent_user_id,
            "agent_username": opened.agent_username,
            "sid": opened.sid,
            "resumed": body.resume,
        }

    @router.post("/{device_id}/agents/{sid}/say")
    async def say_to_agent(
        device_id: str, sid: str, body: SayBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if agent_service.screen(device_id, sid) is None:
            raise NotFoundError("Unknown agent screen")
        if not await can_operate(device_id, sid, user_id):
            raise ForbiddenError("not allowed to operate this agent")
        await agent_service.say(device_id=device_id, sid=sid, text=body.text)
        return {"ok": True}

    @router.post("/{device_id}/agents/{sid}/compact")
    async def compact_agent(
        device_id: str, sid: str, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if agent_service.screen(device_id, sid) is None:
            raise NotFoundError("Unknown agent screen")
        if not await can_operate(device_id, sid, user_id):
            raise ForbiddenError("not allowed to operate this agent")
        await agent_service.compact(device_id=device_id, sid=sid)
        return {"ok": True}

    @router.delete("/{device_id}/agents/{sid}")
    async def close_agent(
        device_id: str, sid: str, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if agent_service.screen(device_id, sid) is None:
            raise NotFoundError("Unknown agent screen")
        if not await can_operate(device_id, sid, user_id):
            raise ForbiddenError("not allowed to operate this agent")
        agent_user_id = await agent_service.close_agent(device_id=device_id, sid=sid)
        return {"ok": True, "agent_user_id": agent_user_id}

    return router


class AddProjectMemberBody(BaseModel):
    user_id: int


def build_project_members_router(
    hub: DeviceHub,
    session_factory: async_sessionmaker[AsyncSession],
    is_member: MembershipChecker,
) -> APIRouter:
    """项目成员管理（知是 2.0 工作区）。忽略角色/权限差异：任何项目成员都拥有项目的
    全部权限（扁平、无角色区分），因此列出 / 添加 / 移除成员、搜索候选人，都只要求调用者
    是本项目成员即可。成员列表通过 ``build_member_dicts`` 附带「是否为在线 agent」及其
    device/screen 现场信息，供前端渲染头像徽标并打开现场。"""
    router = APIRouter(prefix="/connector/projects", tags=["connector"])

    def _service(session: AsyncSession) -> ProjectService:
        return ProjectService(ProjectRepository(session), ProjectMembershipRepository(session))

    @router.get("/{project_id}/members")
    async def list_members(
        project_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to view its members")
        async with session_factory() as session:
            memberships, total = await ProjectMembershipRepository(session).list_members(
                project_id, limit=200, offset=0
            )
            roles = {m.user_id: m.role for m in memberships}
            members = await build_member_dicts(
                session, hub, [m.user_id for m in memberships], roles=roles
            )
        return {"members": members, "total": total}

    @router.post("/{project_id}/members")
    async def add_member(
        project_id: int, body: AddProjectMemberBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to add members")
        async with session_factory() as session:
            # 忽略角色/权限：所有新成员一律以 MEMBER 身份加入（扁平）。
            await _service(session).add_member(
                project_id=project_id, user_id=body.user_id, role="MEMBER"
            )
            members = await build_member_dicts(session, hub, [body.user_id])
            await session.commit()
        return {"member": members[0]}

    @router.delete("/{project_id}/members/{target_user_id}")
    async def remove_member(
        project_id: int, target_user_id: int, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to remove members")
        async with session_factory() as session:
            await _service(session).remove_member(
                project_id=project_id, user_id=target_user_id
            )
            await session.commit()
        return {"ok": True}

    @router.get("/{project_id}/member-candidates")
    async def member_candidates(
        project_id: int,
        q: str = Query(default=""),
        user_id: int = Depends(get_current_user_id),
    ) -> dict[str, object]:
        """搜索可加入项目的用户（按昵称或用户名模糊匹配），排除已是成员的人。"""
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to search candidates")
        needle = q.strip()
        if not needle:
            return {"candidates": []}
        async with session_factory() as session:
            member_ids = {
                m.user_id
                for m in (
                    await ProjectMembershipRepository(session).list_members(
                        project_id, limit=1000, offset=0
                    )
                )[0]
            }
            like = f"%{needle}%"
            rows = (
                await session.execute(
                    select(UserProfile.user_id)
                    .join(User, User.id == UserProfile.user_id)
                    .where(
                        or_(
                            UserProfile.nickname.ilike(like),
                            User.username.ilike(like),
                        ),
                        UserProfile.deleted_at.is_(None),
                        User.deleted_at.is_(None),
                    )
                    .limit(20)
                )
            ).scalars()
            candidate_ids = [uid for uid in rows if uid not in member_ids]
            candidates = await build_member_dicts(session, hub, candidate_ids)
        return {"candidates": candidates}

    return router
