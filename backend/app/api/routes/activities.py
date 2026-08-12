"""线下接入 / 定期巡检 / 一页纸总结 — **parked, no route is mounted**.

The three turns this module used to expose (`ingest_activity`, `run_heartbeat`,
`summarize_project`) were built to satisfy evals E1/E3/F2/G1. Each of them has
the platform perform something 芝士 already does the moment you ask it to, so
each grew its own turn, its own session and its own failure mode — see
`docs/agent-principles.md` §12 ("不要把一段对话升格成机械"), where they are the
worked examples.

The most expensive one was concrete: activity ingestion ran in a *fresh* session
and then wrote that session's id back onto the topic, so the topic's real
conversation could never be resumed again. Cold-started sessions were 27% of the
live ones.

**The需求 behind them is real** — offline material has to reach the room, someone
has to be nudged when work stalls, and a project's state should be legible at a
glance. What is wrong is the shape, and the redesign has not happened yet. So
the trigger paths are removed rather than the ideas: `ChatService` still carries
the implementations (marked parked), git history carries the routes, and nothing
can invoke them until there is a design that starts from the principles doc.

Do not re-mount these without that design.
"""

from fastapi import APIRouter

# Deliberately empty: discovered by app.main._discover_routers, mounts nothing.
# Keeping the module (rather than deleting the file) is what makes the reason
# above discoverable at the place someone would go to add the route back.
router = APIRouter(prefix="/api/projects", tags=["activities"])
