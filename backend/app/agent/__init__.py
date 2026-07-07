"""Agent plane: the server side of the frozen ``cli/`` connector, wired to the real
backend. Not business logic — the actor is resolved at the trust boundary (a device
owner, or an agent user via its injected session token) and business calls hit the
*same* domain services a human REST call would (``docs/design/architecture.md`` §5,
"一套业务服务,三个前门").

Module map:
    hub.py            DeviceHub — the link.Msg protocol state machine (welcome, screen
                      open, screen.data fan-out, var/rpc, exec, viewer relay).
    attribution.py    device token + screen token → actor.
    viewer_authz.py   project-member 现场 viewer authorization policy.
    orchestrator.py   AgentService — an agent's lifecycle (an agent is a user): open
                      (create user + join project + inject CHEESE_TOKEN), say, close.
    connector_plane.py assembly: wires the routers/hub/orchestrator into the app.
    cheeselets/       the server-delivered driver (claude.js).
"""
