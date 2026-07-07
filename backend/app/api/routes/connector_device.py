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

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

from app.agent.orchestrator import AgentService
from app.common.auth import get_current_user_id
from app.common.origin import public_origin
from app.core.errors import AuthenticationRequiredError, ForbiddenError, NotFoundError
from app.domain.device.service import DeviceService

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


class OpenProjectAgentBody(BaseModel):
    nickname: str | None = None


def build_agent_list_router(agent_service: AgentService, is_member: MembershipChecker) -> APIRouter:
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

    @router.post("/{project_id}/agents")
    async def open_agent_in_project(
        project_id: int, body: OpenProjectAgentBody, user_id: int = Depends(get_current_user_id)
    ) -> dict[str, object]:
        if not await is_member(project_id, user_id):
            raise ForbiddenError("must be a member of the project to run an agent in it")
        opened = await agent_service.open_agent_in_project(
            project_id=project_id, nickname=body.nickname
        )
        return {
            "agent_user_id": opened.agent_user_id,
            "agent_username": opened.agent_username,
            "sid": opened.sid,
        }

    return router


class OpenAgentBody(BaseModel):
    project_id: int
    nickname: str | None = None


class SayBody(BaseModel):
    text: str


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
