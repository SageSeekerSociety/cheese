"""The real 现场 viewer authorization policy (architecture §5, §6).

Only a **logged-in human who is a member of the screen's project** may watch that
screen. A screen carries its own ``project_id`` (the project the agent runs in), so
this composes the ``authorize_viewer`` seam of ``connector_viewer.py`` from two
injected adapters — the policy is unit-testable and the concrete integrations wire
once at assembly:

* ``resolve_user(websocket) -> user_id | None`` — authenticate the browser (in
  practice: validate the JWT passed as ``?token=`` since a browser cannot set an
  Authorization header on a WebSocket). ``None`` means not logged in → denied.
* ``is_member(project_id, user_id) -> bool`` — the project-membership check.
"""

from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from app.agent.hub import HubScreen
from app.api.routes.connector_viewer import ViewerAuthorizer

UserResolver = Callable[[WebSocket], Awaitable[int | None]]
MembershipChecker = Callable[[int, int], Awaitable[bool]]  # (project_id, user_id) -> bool
OwnershipChecker = Callable[[str, int], Awaitable[bool]]  # (device_id, user_id) -> bool
ThreadPeerChecker = Callable[[int, int], Awaitable[bool]]  # (agent_user_id, user_id) -> bool


def project_member_authorizer(
    resolve_user: UserResolver,
    is_member: MembershipChecker,
    is_owner: OwnershipChecker,
    shares_thread: ThreadPeerChecker,
) -> ViewerAuthorizer:
    async def authorize(screen: HubScreen, websocket: WebSocket) -> bool:
        user_id = await resolve_user(websocket)
        if user_id is None:
            return False
        # Primary rule: anyone in a chat group with the agent may watch AND operate its
        # 现场 — the group is the unit of shared access.
        if await shares_thread(screen.agent_user_id, user_id):
            return True
        # Otherwise: the device owner (project-less agent), or a project member.
        if screen.project_id is None:
            return await is_owner(screen.device_id, user_id)
        return await is_member(screen.project_id, user_id)

    return authorize
