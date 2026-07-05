"""``APIRouter`` binding the connector seams to HTTP/WS endpoints (contract
§5, §6):

  WS  /connector/agent                          -- cheesed dials in.
  WS  /connector/session/{session_id}/screen     -- browser viewer.
  GET /connector/tools/schema                    -- tool catalog.
  POST /connector/tools/call?tool=<name>         -- tool dispatch.

``build_router(graph)`` is the only entry point; it never touches
``app.main`` -- the host app (real or demo, see ``demo.py``) calls
``app.include_router(build_router(graph))``.

Auth failures raise the existing ``app.core.errors`` classes
(``AuthenticationRequiredError`` / ``InvalidTokenError``). For the two HTTP
endpoints that just propagates to the app's registered exception handlers,
exactly like every other route in this codebase. For the two WS endpoints we
catch it ourselves and close the socket with policy-violation (1008) --
translating an HTTP-shaped ``JSONResponse`` onto an unaccepted WebSocket
connection is undefined behavior in ASGI, so we do not rely on the
HTTP exception-handler machinery there.
"""

import json
from typing import Any

from fastapi import APIRouter, Body, Header, Query, WebSocket, WebSocketDisconnect
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT
from starlette.websockets import WebSocketState

from app.connector.sessions import Session, SessionStore
from app.connector.wiring import ConnectorGraph
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    BaseError,
    InvalidTokenError,
)


class _WebSocketTransport:
    """Adapts a live ``fastapi.WebSocket`` to ``proxy.Transport``."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send_text(self, data: str) -> None:
        if self._websocket.client_state == WebSocketState.CONNECTED:
            await self._websocket.send_text(data)

    async def send_bytes(self, data: bytes) -> None:
        if self._websocket.client_state == WebSocketState.CONNECTED:
            await self._websocket.send_bytes(data)


async def _resolve_session(
    sessions: SessionStore, header_token: str | None, query_token: str | None
) -> Session:
    token = header_token or query_token
    if not token:
        raise AuthenticationRequiredError("X-Cheese-Session header or ?token= query param required")
    session = await sessions.get_by_token(token)
    if session is None:
        raise InvalidTokenError("Unknown or expired session token")
    return session


class ToolArgumentError(BaseError):
    """Agent-supplied tool arguments failed schema validation (missing
    required key / unknown key -- see ``ToolRegistry.validate_args``). Uses
    ``HTTP_422_UNPROCESSABLE_CONTENT``, the current non-deprecated starlette
    status name (``HTTP_422_UNPROCESSABLE_ENTITY`` triggers a
    ``StarletteDeprecationWarning``)."""

    def __init__(self, message: str, data: Any | None = None) -> None:
        super().__init__(HTTP_422_UNPROCESSABLE_CONTENT, message, data)


def build_router(graph: ConnectorGraph) -> APIRouter:
    """Bind every connector endpoint to one wired ``ConnectorGraph``."""

    router = APIRouter(prefix="/connector", tags=["connector"])

    @router.websocket("/agent")
    async def agent_socket(
        websocket: WebSocket,
        x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
        token: str | None = Query(default=None),
    ) -> None:
        try:
            session = await _resolve_session(graph.sessions, x_cheese_session, token)
        except BaseError as exc:
            await websocket.close(code=1008, reason=exc.args[0])
            return

        await websocket.accept()
        transport = _WebSocketTransport(websocket)
        await graph.hub.attach_agent(session.session_id, transport)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data_bytes = message.get("bytes")
                data_text = message.get("text")
                if data_bytes is not None:
                    await graph.hub.on_agent_binary(session.session_id, data_bytes)
                elif data_text is not None:
                    await graph.hub.on_agent_text(session.session_id, data_text)
        except WebSocketDisconnect:
            pass
        finally:
            await graph.hub.detach_agent(session.session_id, transport)

    @router.websocket("/session/{session_id}/screen")
    async def viewer_socket(
        websocket: WebSocket,
        session_id: str,
        x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
        token: str | None = Query(default=None),
    ) -> None:
        try:
            session = await _resolve_session(graph.sessions, x_cheese_session, token)
        except BaseError as exc:
            await websocket.close(code=1008, reason=exc.args[0])
            return
        if session.session_id != session_id:
            await websocket.close(code=1008, reason="session token does not match session_id")
            return

        await websocket.accept()
        viewer_id = f"{session.actor.actor_id}:{id(websocket)}"
        transport = _WebSocketTransport(websocket)
        await graph.hub.attach_viewer(session.session_id, viewer_id, transport)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data_bytes = message.get("bytes")
                data_text = message.get("text")
                if data_bytes is not None:
                    await graph.hub.on_viewer_binary(session.session_id, viewer_id, data_bytes)
                elif data_text is not None:
                    await _handle_viewer_text(graph, session.session_id, viewer_id, data_text)
        except WebSocketDisconnect:
            pass
        finally:
            await graph.hub.detach_viewer(session.session_id, viewer_id)

    @router.get("/tools/schema")
    async def tools_schema() -> dict[str, Any]:
        return {"tools": graph.registry.schema()}

    @router.post("/tools/call")
    async def tools_call(
        tool: str,
        args: dict[str, Any] = Body(default_factory=dict),
        x_cheese_session: str | None = Header(default=None, alias="X-Cheese-Session"),
    ) -> dict[str, Any]:
        session = await _resolve_session(graph.sessions, x_cheese_session, None)
        definition = graph.registry.get(tool)
        if definition is None:
            raise BadRequestError(f"Unknown tool: {tool}")
        await graph.authorizer.authorize(session.actor, tool, args)
        try:
            result = await graph.registry.invoke(tool, session.actor, args)
        except ValueError as exc:
            raise ToolArgumentError(str(exc)) from exc
        return {"ok": True, "result": result}

    return router


async def _handle_viewer_text(
    graph: ConnectorGraph, session_id: str, viewer_id: str, text: str
) -> None:
    try:
        payload = json.loads(text)
    except ValueError:
        return
    if isinstance(payload, dict) and payload.get("t") == "takeover":
        await graph.hub.request_takeover(session_id, viewer_id, bool(payload.get("on", False)))
