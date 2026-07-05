"""Cheese connector: FastAPI seams for the agent layer (cheesed <-> backend
<-> browser), self-contained and mountable.

See ``docs/design/cheese-agent-layer.md`` for the full contract. This package
never imports or modifies ``app.main`` -- ``wiring.py`` builds the object
graph and ``router.py`` exposes the ``APIRouter`` any host app can
``include_router()``. ``demo.py`` shows the standalone usage and documents the
one-line mount into the real app.

Module map (each one an independent, additively-extensible seam):
    context.py        ActorContext -- the identity injected into every tool call.
    sessions.py        SessionStore Protocol + in-memory implementation.
    authorization.py   Authorizer Protocol + permissive default implementation.
    services.py        Business-function Protocols the tools call (ChatService, ...).
    registry.py         @tool decorator + ToolRegistry (signature -> JSON-Schema projection).
    tools.py           Concrete tools registered via @tool.
    proxy.py           Takeover arbitration + terminal relay (SessionHub).
    router.py          APIRouter binding all of the above to HTTP/WS endpoints.
    wiring.py          Factory wiring the default implementations into one graph.
    demo.py            Standalone demo FastAPI app.
"""
