"""Standalone demo FastAPI app for ``app.connector`` -- runs without touching
``app.main`` at all.

Run it directly:

    uvicorn app.connector.demo:app --host 127.0.0.1 --port 8099

Then, e.g.:

    curl http://127.0.0.1:8099/connector/tools/schema
    curl -X POST 'http://127.0.0.1:8099/connector/tools/call?tool=chat' \\
        -H 'X-Cheese-Session: demo-token' -H 'Content-Type: application/json' \\
        -d '{"message": "hello from the agent"}'

To mount the SAME seams inside the real app, add ONE line to
``app/main.py``'s ``create_app()`` (do not import this ``demo`` module from
there -- it seeds a fixed demo token meant for local/manual testing only)::

    from app.connector.router import build_router
    from app.connector.wiring import build_default_graph

    app.include_router(build_router(build_default_graph()))
"""

import asyncio

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.connector.context import ActorContext
from app.connector.router import build_router
from app.connector.wiring import ConnectorGraph, build_default_graph
from app.core import errors as core_errors

DEMO_SESSION_TOKEN = "demo-token"
DEMO_ACTOR_ID = "claude-code-agent"


async def _seed_demo_session(graph: ConnectorGraph) -> None:
    actor = ActorContext(
        actor_id=DEMO_ACTOR_ID,
        scopes=frozenset({"tool:*"}),
        session_token=DEMO_SESSION_TOKEN,
    )
    await graph.sessions.create(actor)


def create_demo_app() -> FastAPI:
    graph = build_default_graph()
    asyncio.run(_seed_demo_session(graph))

    demo_app = FastAPI(title="Cheese Connector (demo)")
    demo_app.add_exception_handler(core_errors.BaseError, core_errors.base_error_handler)  # type: ignore[arg-type]
    demo_app.add_exception_handler(
        StarletteHTTPException,
        core_errors.http_exception_handler,  # type: ignore[arg-type]
    )
    demo_app.add_exception_handler(
        RequestValidationError,
        core_errors.validation_exception_handler,  # type: ignore[arg-type]
    )
    demo_app.include_router(build_router(graph))
    demo_app.state.connector_graph = graph  # introspection hook for tests/tools
    return demo_app


app = create_demo_app()
