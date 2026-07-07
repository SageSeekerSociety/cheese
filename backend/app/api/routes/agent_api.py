"""The agent tool door — the tiny API an agent's ``cheese api`` calls.

Mounted as its own FastAPI sub-app (its own ``/openapi.json``) so ``cheese api`` sees
a clean, tiny surface. **Auth is the device token + the screen token — no per-agent JWT
is injected** (a JWT would expire while an agent runs for days). The screen is launched
with ``CHEESE_API`` pointing here and ``CHEESE_SCREEN`` = its screen token (a 128-bit
``uuid4`` secret); ``cheese api`` sends the device's durable token as ``Authorization:
Bearer`` (its config fallback) and the screen token as ``X-Cheese-Screen``. The server
resolves that pair to the screen's agent user (``resolve_actor``) — a call from inside a
screen acts as that agent, exactly how a human's REST call is authorized, per actor, and
it never expires.

``post-note`` is how the agent speaks into a **thread**: it writes a ``block`` into the
target thread (either the explicit ``thread_id`` or the agent's most-recently-@'d
thread — the cheeselet's ``say`` prompt carries a ``[thread:<id>]`` marker).
"""

from fastapi import FastAPI, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.attribution import resolve_actor
from app.agent.hub import DeviceHub
from app.agent.orchestrator import AgentService
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    BaseError,
    NotFoundError,
    base_error_handler,
)
from app.domain.block.repositories import BlockRepository
from app.domain.device.service import DeviceService
from app.domain.thread.repositories import ThreadRepository


class NoteBody(BaseModel):
    text: str
    thread_id: int | None = None


class FinishTriageBody(BaseModel):
    thread_id: int | None = None


def build_agent_api(
    session_factory: async_sessionmaker[AsyncSession],
    agent_service: AgentService,
    hub: DeviceHub,
    device_service: DeviceService,
    public_base: str | None = None,
) -> FastAPI:
    async def _agent_user(authorization: str | None, screen_token: str | None) -> int:
        """The calling agent's user id, from the device token (bearer) + screen token
        (X-Cheese-Screen). Must come from inside a live screen — a bare device call is
        the human owner, which the tool door rejects."""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise AuthenticationRequiredError("missing device token")
        attribution = await resolve_actor(
            device_service, hub, device_token=authorization[7:], screen_token=screen_token
        )
        if attribution is None:
            raise AuthenticationRequiredError("unknown device token")
        if not attribution.inside_screen:
            raise AuthenticationRequiredError("this call must come from inside an agent screen")
        return attribution.actor_user_id

    # `cheese api` (restish) resolves request URLs against the spec's server URL, not
    # against CHEESE_API. A mounted sub-app otherwise advertises a host-absolute
    # `/agent-api`, which drops the edge's `/api` prefix (agents then POST to
    # <origin>/agent-api/notes → 404). So advertise the full external base
    # (CHEESE_API, e.g. <origin>/api/agent-api) as the server URL.
    servers = [{"url": public_base}] if public_base else None
    # root_path_in_servers=False stops the mount's `/agent-api` from being injected as
    # the first server (which restish would use, dropping the `/api` edge prefix).
    api = FastAPI(
        title="cheese agent API",
        version="1.0.0",
        servers=servers,
        root_path_in_servers=False,
    )
    api.add_exception_handler(BaseError, base_error_handler)  # type: ignore[arg-type]

    @api.post("/notes", operation_id="post-note", summary="Post a message into a thread.")
    async def post_note(
        body: NoteBody,
        authorization: str | None = Header(default=None),
        x_cheese_screen: str | None = Header(default=None, alias="X-Cheese-Screen"),
    ) -> dict[str, object]:
        user_id = await _agent_user(authorization, x_cheese_screen)
        thread_id = body.thread_id or agent_service.last_thread_for_agent(user_id)
        if thread_id is None:
            raise BadRequestError("no target thread; specify thread_id")
        async with session_factory() as session:
            thread = await ThreadRepository(session).get(thread_id)
            if thread is None:
                raise NotFoundError("Unknown thread")
            block = await BlockRepository(session).add_message(
                thread_id=thread_id,
                project_id=thread.project_id,
                author_id=user_id,
                text=body.text,
            )
            await ThreadRepository(session).touch(thread_id)
            block_id = block.id
            await session.commit()
        return {"ok": True, "id": block_id, "thread_id": thread_id}

    @api.post(
        "/finish-triage",
        operation_id="finish-triage",
        summary="Release the group's message lock so other agents get the message.",
    )
    async def finish_triage(
        body: FinishTriageBody,
        authorization: str | None = Header(default=None),
        x_cheese_screen: str | None = Header(default=None, alias="X-Cheese-Screen"),
    ) -> dict[str, object]:
        user_id = await _agent_user(authorization, x_cheese_screen)
        thread_id = body.thread_id or agent_service.last_thread_for_agent(user_id)
        if thread_id is None:
            raise BadRequestError("no target thread; specify thread_id")
        released = await agent_service.finish_triage(thread_id)
        return {"ok": True, "thread_id": thread_id, "released": released}

    @api.get("/whoami", operation_id="whoami", summary="Report who you are and where.")
    async def whoami(
        authorization: str | None = Header(default=None),
        x_cheese_screen: str | None = Header(default=None, alias="X-Cheese-Screen"),
    ) -> dict[str, object]:
        user_id = await _agent_user(authorization, x_cheese_screen)
        screen = hub.screen_by_token(x_cheese_screen or "")
        return {
            "agent_user_id": user_id,
            "project_id": screen.project_id if screen else None,
            "screen": screen.sid if screen else None,
        }

    return api
