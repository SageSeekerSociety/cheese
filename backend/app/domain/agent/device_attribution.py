"""The attribution seam: device token + screen token → actor (P3, fusion-design §2/§4).

Every ``cheese`` call and every ``/connector/agent`` connection carries a device
token (``X-Cheese-Session`` / bearer); a call made *from inside a screen* also carries
that screen's token (``X-Cheese-Screen``). A screen **is** an agent (一个 agent 是一个
屏幕), so a call from inside a screen acts as that screen's ``agent_user_id`` (handle
``agent_handle``) in its ``project_id``; a bare device call acts as the device's human
owner. Either way the result resolves to the same ``Actor`` shape the shared domain
services authorize against — exactly like a human (our ``identity.resolve_actor``).

Kept tiny + I/O-free (it composes ``DeviceService`` + ``DeviceHub``) so the rule is
unit-tested without WebSockets, DB, or a real device.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.domain.agent.device_hub import DeviceHub, HubScreen
from app.domain.device.service import DeviceService

# handle-resolver adapter injected at the trust boundary (user_id → handle).
OwnerHandleResolver = Callable[[int], Awaitable[str]]


@dataclass(frozen=True)
class DeviceAttribution:
    """Who a connector call acts as. ``actor_handle`` is the authorship key handed to
    the domain services (the screen's agent when inside one, else the device owner)."""

    actor_user_id: int
    actor_handle: str
    owner_user_id: int
    project_id: uuid.UUID | None  # set only when the call came from inside a screen
    device_id: str
    screen: HubScreen | None

    @property
    def inside_screen(self) -> bool:
        return self.screen is not None


async def resolve_device_actor(
    device_service: DeviceService,
    hub: DeviceHub,
    *,
    device_token: str,
    owner_handle_of: OwnerHandleResolver,
    screen_token: str | None = None,
) -> DeviceAttribution | None:
    """Resolve a connector call to its actor, or ``None`` if the device token is
    unknown (the caller turns that into 401/1008). A screen token is honored only when
    it names a screen belonging to *this* device — a token from another device's screen
    is ignored, never trusted, so it can never escalate across devices."""
    device = await device_service.verify_token(device_token)
    if device is None:
        return None
    screen: HubScreen | None = None
    if screen_token:
        candidate = hub.screen_by_token(screen_token)
        if candidate is not None and candidate.device_id == device.device_id:
            screen = candidate
    if screen is not None:
        return DeviceAttribution(
            actor_user_id=screen.agent_user_id,
            actor_handle=screen.agent_handle,
            owner_user_id=device.owner_user_id,
            project_id=screen.project_id,
            device_id=device.device_id,
            screen=screen,
        )
    owner_handle = await owner_handle_of(device.owner_user_id)
    return DeviceAttribution(
        actor_user_id=device.owner_user_id,
        actor_handle=owner_handle,
        owner_user_id=device.owner_user_id,
        project_id=None,
        device_id=device.device_id,
        screen=None,
    )


def resolve_screen_actor(hub: DeviceHub, screen_token: str) -> HubScreen | None:
    """Resolve a *screen-scoped* call to the screen it came from, by its token alone.

    A screen token is an unguessable per-screen secret minted server-side and injected
    into the screen process (``CHEESE_SCREEN`` → ``X-Cheese-Screen`` header). Possession
    proves the call originates inside that screen, so it authorizes acting as the
    screen's ``agent_user_id`` (handle ``agent_handle``) in its ``project_id`` — 一个
    agent 是一个屏幕. Returns ``None`` for an unknown token (the caller ignores it and
    falls back to the request's other credentials).

    This is the token-only fast path used by the shared cheese-API actor resolver; the
    device-token-anchored ``resolve_device_actor`` above is the fuller path (owner
    attribution + cross-device safety) used when a bare device token is also present.
    """
    if not screen_token:
        return None
    return hub.screen_by_token(screen_token)
