"""Sandbox-facing endpoints for the tmux agent backend.

The interactive `claude` running inside a topic's container posts Claude Code
HTTP hooks here (settings.json `"type": "http"` hooks). This endpoint verifies a
per-topic scoped token (same auth as the cheese CLI — app.core.sandbox_auth) and
routes the hook payload into the topic's live screen subscription (HookRouter).

It lives OUTSIDE /api on purpose: the cheese_token_gate middleware only guards
/api write paths, so this route does its own token check.
"""

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.deps import get_chat_service
from app.core.sandbox_auth import is_valid_cheese_token, scoped_token_claims
from app.domain.agent import event_spool
from app.domain.agent.chat import ChatService
from app.domain.agent.hook_events import hook_router
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

# The `cheese` platform-action CLI source, shipped to enrolled devices (the local
# sandbox bakes it into its own image instead). Loaded LAZILY and defensively:
# this module is imported by route auto-discovery, which SWALLOWS import errors —
# a module-level read of a file the image may not carry silently unmounted the
# whole sandbox router (hooks included) on dev. Import must never depend on it.
_CLI_PATH = Path(__file__).resolve().parents[3] / "sandbox" / "cheese"


def _cheese_cli_source() -> str | None:
    try:
        return _CLI_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("cheese CLI source unavailable at %s", _CLI_PATH)
        return None


@router.get("/cli/cheese", response_model=None)
async def get_cheese_cli(
    x_cheese_token: str = Header(default=""),
) -> PlainTextResponse | JSONResponse:
    """Serve the `cheese` platform-action CLI to an enrolled device's screen launcher
    (a device has no baked-in image). Gated by any valid scoped
    token — it carries no data, just the script; the token still authorizes the
    ACTIONS the CLI performs."""
    if not scoped_token_claims(x_cheese_token):
        return JSONResponse(
            {"code": 401, "message": "invalid sandbox token", "data": None},
            status_code=401,
        )
    src = _cheese_cli_source()
    if src is None:
        return JSONResponse(
            {
                "code": 503,
                "message": "cheese CLI not shipped in this image",
                "data": None,
            },
            status_code=503,
        )
    return PlainTextResponse(src, media_type="text/x-python")


@router.post("/hooks/{topic_id}", response_model=None)
async def receive_hook(
    topic_id: str,
    request: Request,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    x_cheese_token: str = Header(default=""),
    x_cheese_event_id: str = Header(default=""),
) -> dict | JSONResponse:
    """Receive one Claude Code hook for `topic_id` and hand it to that topic's
    live screen. Responds fast (the container's hook call blocks on this): an
    empty 200 body = "no decision", so a PreToolUse hook proceeds normally."""
    if not is_valid_cheese_token(x_cheese_token, topic_id=topic_id):
        # Say so. A rejected hook used to vanish here with no trace at all, and
        # that silence is the whole reason a deaf sandbox took days to find: the
        # turn observes nothing, reports `first_output_s: null` / `tools: 0`, and
        # runs to its ceiling, which is indistinguishable from a model that never
        # spoke. The common cause is a box baked with a token signed by a
        # PREVIOUS backend process (`SANDBOX_TOKEN` unpinned → a fresh random
        # secret per restart), so the hook it just sent is not malicious traffic
        # to drop quietly — it is our own agent, locked out.
        logger.warning(
            "sandbox hook rejected: token does not verify for topic %s "
            "(box likely baked before a backend restart — see tmux_provider."
            "_hook_token_dead)",
            topic_id,
        )
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
    # Carry the forwarder's stable per-event id so the durable-spool reconcile can
    # dedup this live delivery against the same event's spooled copy (idempotency).
    if x_cheese_event_id:
        payload["_eid"] = x_cheese_event_id
    delivered = hook_router.push(topic_id, payload)
    if not delivered and x_cheese_event_id:
        # No live screen is subscribed. Park it in the
        # topic's server-side spool so a reconcile materializes it as HISTORY —
        # never dropped, and never replayed into a later live queue (a stale
        # Stop would end the wrong turn). Idempotent by event-id, so a
        # container-side spooled copy of the same event stays a no-op. Then
        # schedule the settle that drains it: an orphaned turn's claude keeps
        # working after a backend restart (#316), and with the sweep no longer
        # re-prompting it, "the next turn's reconcile" may otherwise be never —
        # its progress, and the Stop that finishes the turn, land via this.
        claims = scoped_token_claims(x_cheese_token)
        project = str(claims.get("p") or "") if claims else ""
        try:
            event_spool.append(
                ws.spool_dir(uuid.UUID(project), uuid.UUID(topic_id)),
                x_cheese_event_id,
                payload,
            )
            # Give a reconnecting screen first claim; settle remains the
            # backstop when the screen never returns.
            chat.schedule_spool_settle(uuid.UUID(topic_id), delay_s=10.0)
        except Exception:  # noqa: BLE001 — parking is best-effort, reply stays 200
            logger.warning("hook park failed for topic %s", topic_id, exc_info=True)
    # Still 200 either way so claude doesn't treat it as a hook failure.
    return {"code": 200, "message": "ok", "data": {"delivered": delivered}}
