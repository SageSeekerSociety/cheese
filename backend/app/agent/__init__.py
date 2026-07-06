"""Agent plane: FastAPI seams for the connector (cheesed <-> backend <->
browser). Not business logic -- the actor is injected at the trust boundary
and every tool RPC is authorized against the actor's real permissions, then
dispatched to the *same* domain services a human REST call would hit
(``docs/design/architecture.md`` §5, "一套业务服务,三个前门").

This package never imports or modifies ``app.main``. ``wiring.py`` builds the
object graph; the HTTP/WS front door lives at ``app.api.routes.connector``
(the doc-specified location), which calls ``build_router(graph)``.

Module map (each one an independent, additively-extensible seam):
    context.py        ActorContext -- the identity injected into every tool call.
    sessions.py        SessionStore Protocol + in-memory implementation.
    authorization.py   Authorizer Protocol + permissive default implementation.
    services.py        Business-function Protocols the tools call (ChatService, ...).
    registry.py         @tool decorator + ToolRegistry (signature -> JSON-Schema projection).
    tools.py           Concrete tools registered via @tool.
    proxy.py           Takeover arbitration + terminal relay (SessionHub).
    wiring.py          Factory wiring the default implementations into one graph.

The front door (``build_router``, the 4 WS/HTTP endpoints) is in
``app.api.routes.connector``.
"""
