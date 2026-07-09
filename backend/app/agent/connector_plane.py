"""Assembles the connector plane and mounts it into the app (Act 2, production wiring).

This is the single place the frozen-``link.Msg`` connector is wired for the real
backend: one process-global ``DeviceService`` + ``DeviceHub``, the device-flow /
device-``/agent`` / 现场-viewer routes, and the concrete authorization adapters —

* browser auth (`_resolve_user`): a viewer WebSocket cannot send an Authorization
  header, so the human's JWT access token is passed as ``?token=`` and validated
  with the app's own ``decode_token`` (enforcing ``type == "access"``);
* membership (`_is_member`): a viewer must be a member — or the leader — of the
  project the watched device is bound to.

Device state is persisted in PostgreSQL via ``SqlDeviceRepository`` (a short session per
op from ``AsyncSessionLocal``), so enrollments survive restarts and are shared across
workers; routes, hub and authz are unaffected by the storage backend.
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI, WebSocket

from app.agent.hub import DeviceHub
from app.agent.orchestrator import AgentService
from app.agent.viewer_authz import project_member_authorizer
from app.api.routes.agent_api import build_agent_api, build_agent_tool_router
from app.api.routes.connector_agent import build_agent_router
from app.api.routes.connector_device import (
    build_agent_list_router,
    build_agent_open_router,
    build_device_admin_router,
    build_device_flow_router,
    build_device_update_router,
    build_project_members_router,
)
from app.api.routes.connector_documents import build_document_connector_router
from app.api.routes.connector_myagent import build_myagent_router
from app.api.routes.connector_presence import build_presence_router
from app.api.routes.connector_viewer import build_viewer_router
from app.api.routes.threads import build_thread_router
from app.common.auth import decode_token
from app.core.config import settings
from app.core.errors import BaseError
from app.db.session import AsyncSessionLocal
from app.domain.device import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.project.repositories import ProjectMembershipRepository, ProjectRepository
from app.domain.thread.repositories import ThreadRepository
from app.domain.thread.services import ThreadService

# Process-global connector plane. Kept module-level so the device-flow HTTP routes,
# the device control WebSocket, and the viewer WebSocket all share one live hub and
# one device registry within a worker. Device state is persisted in PostgreSQL (the
# repository opens a short session per op from AsyncSessionLocal), so enrollments
# survive restarts and are shared across workers.
_device_service = DeviceService(SqlDeviceRepository(AsyncSessionLocal))
_hub = DeviceHub()
_CHEESELET = (Path(__file__).parent / "cheeselets" / "claude.js").read_text()
# The backend base reachable from the client machine (defaults to this host for the
# local dev client). New screens point `cheese api` (CHEESE_API) at this root, so it
# lists the WHOLE server API — agents are first-class API clients (一个 agent 就是一个
# user), authorized per-actor by the device+screen token (see app.common.auth). The
# tool-door routes (post-note/…) are reached under `/agent-tool` in the main app.
_CONNECTOR_BASE = (settings.connector_origin or "http://127.0.0.1:8080").rstrip("/")
# Legacy sub-app base — kept so already-running screens (CHEESE_API=<base>/agent-api)
# keep their exact endpoint after a restart.
_AGENT_API_BASE = _CONNECTOR_BASE + "/agent-api"
_agent_service = AgentService(
    AsyncSessionLocal, _hub, _device_service, _CHEESELET, agent_api_base=_CONNECTOR_BASE
)
_thread_service = ThreadService(AsyncSessionLocal, _hub, _agent_service)


def agent_api_app() -> FastAPI:
    """The agent tool-door sub-app; ``main.py`` mounts it at ``/agent-api`` (legacy
    surface for already-running screens)."""
    return build_agent_api(
        AsyncSessionLocal, _agent_service, _hub, _device_service, public_base=_AGENT_API_BASE
    )


def agent_tool_router() -> APIRouter:
    """The agent tool-door routes for the main app's OpenAPI, included at ``/agent-tool``
    so a screen pointed at the site root sees ``post-note`` alongside the full API."""
    return build_agent_tool_router(
        AsyncSessionLocal, _agent_service, _hub, _device_service, _thread_service
    )


def device_service() -> DeviceService:
    return _device_service


def hub() -> DeviceHub:
    return _hub


async def _resolve_user(websocket: WebSocket) -> int | None:
    """Authenticate a viewer WebSocket from its ``?token=`` JWT. Any failure →
    ``None`` (denied); never raises into the handshake."""
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = decode_token(token)
    except BaseError:
        return None
    if payload.get("type") != "access":
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


async def _is_member(project_id: int, user_id: int) -> bool:
    """A member — or the leader — of the project may watch its devices' screens."""
    async with AsyncSessionLocal() as session:
        if await ProjectMembershipRepository(session).get_relation(project_id, user_id) is not None:
            return True
        project = await ProjectRepository(session).get_by_id(project_id)
        return project is not None and project.leader_id == user_id


async def _is_owner(device_id: str, user_id: int) -> bool:
    """The device's owner may watch its screens — used for project-less agents."""
    device = await _device_service.get_device(device_id)
    return device is not None and device.owner_user_id == user_id


async def _shares_thread(agent_user_id: int, user_id: int) -> bool:
    """Whether the viewer is in any chat group with the agent — the group is the unit of
    shared 现场 access, so any co-member may watch and operate it."""
    async with AsyncSessionLocal() as session:
        repo = ThreadRepository(session)
        agent_threads = {t.id for t in await repo.threads_for_user(agent_user_id)}
        if not agent_threads:
            return False
        viewer_threads = {t.id for t in await repo.threads_for_user(user_id)}
        return not agent_threads.isdisjoint(viewer_threads)


async def _can_operate_agent(device_id: str, sid: str, user_id: int) -> bool:
    """Who may drive an agent's 现场 (say / compact / close): the device owner, anyone
    who shares a chat group with the agent, or (if it has one) a project member. Same
    rule as the 现场 viewer — the group is the unit of shared access, project or not."""
    screen = _agent_service.screen(device_id, sid)
    if screen is None:
        return False
    if await _is_owner(device_id, user_id):
        return True
    if await _shares_thread(screen.agent_user_id, user_id):
        return True
    return screen.project_id is not None and await _is_member(screen.project_id, user_id)


def build_connector_routers() -> list[APIRouter]:
    """The connector routers to mount, in a fixed order. ``main.py`` includes each."""
    authorizer = project_member_authorizer(_resolve_user, _is_member, _is_owner, _shares_thread)
    return [
        build_device_flow_router(_device_service),
        build_device_admin_router(_device_service, _is_member),
        build_device_update_router(_device_service, _hub, _is_member),
        build_agent_open_router(_agent_service, _is_member, _can_operate_agent),
        build_agent_list_router(_agent_service, _is_member, _device_service, _hub, AsyncSessionLocal),
        build_project_members_router(_hub, AsyncSessionLocal, _is_member),
        build_document_connector_router(AsyncSessionLocal),
        build_thread_router(_thread_service),
        build_presence_router(_hub, AsyncSessionLocal),
        build_myagent_router(_agent_service, _device_service, _hub, AsyncSessionLocal),
        build_agent_router(_device_service, _hub, _agent_service.readopt_device_screens),
        build_viewer_router(_device_service, _hub, authorizer),
    ]
