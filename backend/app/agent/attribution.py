"""The attribution seam: device token + screen token → actor (Act 2 step 5).

This is the join between the two connector cores. Every ``cheese api`` call and
every ``/agent`` connection carries a device token (``X-Cheese-Session`` / bearer);
a call made *from inside a screen* also carries that screen's token
(``X-Cheese-Screen``). A screen **is** an agent (一个 agent 是一个屏幕), so a call
from inside a screen acts as that screen's ``agent_user_id`` in its ``project_id``;
a bare device call (outside any screen) acts as the device's human ``owner_user_id``.
Either way the result is a ``user_id`` the shared domain services authorize against,
exactly like a human's.

Kept tiny and I/O-free (it only composes ``DeviceService`` + ``DeviceHub``) so the
attribution rule is unit-tested without WebSockets, DB or a real device.
"""

from dataclasses import dataclass

from app.agent.hub import DeviceHub, HubScreen
from app.domain.device.service import DeviceService


@dataclass
class Attribution:
    """Who a connector call acts as. ``actor_user_id`` is handed to the domain
    services: the screen's agent when inside one, else the device owner."""

    actor_user_id: int
    owner_user_id: int
    project_id: int | None  # set only when the call came from inside a screen
    device_id: str
    screen: HubScreen | None

    @property
    def inside_screen(self) -> bool:
        return self.screen is not None


async def resolve_actor(
    device_service: DeviceService,
    hub: DeviceHub,
    *,
    device_token: str,
    screen_token: str | None = None,
) -> Attribution | None:
    """Resolve a connector call to its actor, or ``None`` if the device token is
    unknown (the caller turns that into 401/1008). A screen token is honored only
    when it names a screen belonging to *this* device — a token from another
    device's screen is ignored, never trusted, so it can never escalate across
    devices."""
    device = await device_service.verify_token(device_token)
    if device is None:
        return None
    screen: HubScreen | None = None
    if screen_token:
        candidate = hub.screen_by_token(screen_token)
        if candidate is not None and candidate.device_id == device.device_id:
            screen = candidate
    if screen is not None:
        return Attribution(
            actor_user_id=screen.agent_user_id,
            owner_user_id=device.owner_user_id,
            project_id=screen.project_id,
            device_id=device.device_id,
            screen=screen,
        )
    return Attribution(
        actor_user_id=device.owner_user_id,
        owner_user_id=device.owner_user_id,
        project_id=None,
        device_id=device.device_id,
        screen=None,
    )
