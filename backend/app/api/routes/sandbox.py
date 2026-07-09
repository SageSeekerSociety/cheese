"""Sandbox-facing endpoints for the tmux agent backend.

The interactive `claude` running inside a topic's container posts Claude Code
HTTP hooks here (settings.json `"type": "http"` hooks). This endpoint verifies a
per-topic scoped token (same auth as the cheese CLI — app.core.sandbox_auth) and
routes the hook payload into the topic's active turn queue (HookRouter).

It lives OUTSIDE /api on purpose: the cheese_token_gate middleware only guards
/api write paths, so this route does its own token check.
"""

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.core.sandbox_auth import is_valid_cheese_token
from app.domain.agent.hook_events import hook_router

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.post("/hooks/{topic_id}", response_model=None)
async def receive_hook(
    topic_id: str,
    request: Request,
    x_cheese_token: str = Header(default=""),
) -> dict | JSONResponse:
    """Receive one Claude Code hook for `topic_id` and hand it to that topic's
    active turn. Responds fast (the container's hook call blocks on this): an
    empty 200 body = "no decision", so a PreToolUse hook proceeds normally."""
    if not is_valid_cheese_token(x_cheese_token, topic_id=topic_id):
        return JSONResponse(
            {"code": 401, "message": "invalid sandbox token", "data": None},
            status_code=401,
        )
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is a no-op, not a 500
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse(
            {"code": 400, "message": "hook body must be a JSON object", "data": None},
            status_code=400,
        )
    delivered = hook_router.push(topic_id, payload)
    # `delivered=False` = no turn is listening (hook outside a run_turn window);
    # still 200 so claude doesn't treat it as a hook failure.
    return {"code": 200, "message": "ok", "data": {"delivered": delivered}}
