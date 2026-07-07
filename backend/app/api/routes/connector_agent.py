"""The device control-channel WebSocket route (Act 2 step 3, device half).

Binds the frozen ``/agent`` link.Msg channel to ``DeviceService`` (token auth) and
``DeviceHub`` (the protocol state machine). This is the decision-free half of the
port: the device authenticates with its durable device token
(``X-Cheese-Session`` header, or ``?token=`` for browsers that cannot set headers),
and every inbound ``link.Msg`` is dispatched to the hub, which also drives outbound
messages (welcome on connect, ``session.create`` / ``screen.*`` / ``exec`` as the
orchestrator acts).

Mounted via ``build_agent_router(device_service, hub)`` so tests and the app inject
the same wired pair. The *viewer* channel (`/connector/session/{sid}/screen`) and its
authorization live in ``connector_viewer.py`` + ``viewer_authz.py`` (see
``docs/design/architecture.md`` §5.4).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Header, Query, WebSocket, WebSocketDisconnect

from app.agent.hub import DeviceHub
from app.domain.device.service import DeviceService


class _WebSocketDeviceTransport:
    """Adapts a live ``fastapi.WebSocket`` to the hub's ``DeviceTransport``."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_json(self, msg: dict[str, Any]) -> None:
        await self._websocket.send_json(msg)


def build_agent_router(
    device_service: DeviceService,
    hub: DeviceHub,
    on_attach: Callable[[str], Awaitable[int]] | None = None,
) -> APIRouter:
    # The frozen cli dials its control channel at `base + /agent` with a bare-origin
    # base, so the device WebSocket lives at the origin root `/agent`, not `/connector`.
    router = APIRouter(tags=["connector"])

    @router.websocket("/agent")
    async def agent_socket(
        websocket: WebSocket,
        x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
        token: str | None = Query(default=None),
    ) -> None:
        device = await device_service.verify_token(x_cheese_session or token or "")
        if device is None:
            # Reject the handshake with policy-violation — a token is necessary here
            # (though never sufficient; screen-scoped calls are authorized per-actor).
            await websocket.close(code=1008, reason="unknown or missing device token")
            return

        await websocket.accept()
        transport = _WebSocketDeviceTransport(websocket)
        await hub.attach_device(device.device_id, transport)  # sends welcome{v}
        if on_attach is not None:
            # After a server restart the cli reconnects with its screens still
            # running; re-adopt them so agents survive.
            await on_attach(device.device_id)
        try:
            while True:
                message = await websocket.receive_json()
                await hub.on_device_message(device.device_id, message)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.detach_device(device.device_id, transport)

    return router
