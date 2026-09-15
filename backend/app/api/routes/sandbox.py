"""Claude Code's way in: the hook endpoint, and the CLI a device fetches.

Every screen running this harness — in a container here or on someone's enrolled
machine — posts its hooks to this route, which verifies a per-topic scoped token
(same auth as the cheese CLI — app.core.sandbox_auth), writes the event to that
topic's spool, and hands the payload to its live subscription (HookRouter).

This is the adapter's outward edge and it is meant to be one: a harness that
senses itself some other way brings its own ingress rather than being squeezed
through this one.

It lives OUTSIDE /api on purpose: the cheese_token_gate middleware only guards
/api write paths, so this route does its own token check.
"""

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_chat_service
from app.core.db import get_db
from app.core.errors import BaseError, UnauthorizedError
from app.core.sandbox_auth import is_valid_cheese_token, scoped_token_claims
from app.domain.agent.chat import ChatService
from app.domain.agent.event_spool import append as append_event
from app.domain.agent.harness.claude_code import hook_router
from app.domain.workspace import service as ws

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


@router.post("/storage-sweep")
async def trigger_storage_sweep(x_cheese_token: str = Header(default="")) -> dict:
    from app.core.background import spawn
    from app.core.db import async_session_factory
    from app.core.sandbox_auth import is_global_sandbox_token
    from app.domain.topic.retire import sweep_retired_storage

    if not is_global_sandbox_token(x_cheese_token):
        raise UnauthorizedError("Cleanup trigger requires the server credential")
    spawn(sweep_retired_storage(async_session_factory), name="archived-room cleanup")
    return {"code": 200, "data": {"scheduled": True}}


class TranscriptManifest(BaseModel):
    source: str = Field(max_length=1024)
    offset: int = Field(default=0, ge=0)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/transcripts/{topic_id}/{file_id}/confirm")
async def confirm_transcript(
    topic_id: uuid.UUID,
    file_id: uuid.UUID,
    body: TranscriptManifest,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_cheese_token: str = Header(default=""),
) -> dict:
    from app.domain.topic import transcript_stream

    if not is_valid_cheese_token(x_cheese_token, topic_id=str(topic_id)):
        raise UnauthorizedError("Invalid transcript token")
    claims = scoped_token_claims(x_cheese_token)
    try:
        project_id = uuid.UUID(str((claims or {}).get("p", "")))
    except ValueError as exc:
        raise UnauthorizedError("Transcript token needs a project") from exc
    receipt = await transcript_stream.confirm(
        db,
        project_id=project_id,
        topic_id=topic_id,
        file_id=file_id,
        source=body.source,
        offset=body.offset,
        size=body.size,
        sha256=body.sha256,
    )
    return {"code": 200, "data": receipt}


@router.put("/transcripts/{topic_id}/{file_id}")
async def receive_transcript(
    topic_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str = Query(max_length=1024),
    offset: int = Query(ge=0),
    x_cheese_token: str = Header(default=""),
) -> dict:
    from app.domain.topic import transcript_stream

    if not is_valid_cheese_token(x_cheese_token, topic_id=str(topic_id)):
        raise UnauthorizedError("Invalid transcript token")
    claims = scoped_token_claims(x_cheese_token)
    try:
        project_id = uuid.UUID(str((claims or {}).get("p", "")))
    except ValueError as exc:
        raise UnauthorizedError("Transcript token needs a project") from exc
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > transcript_stream.CHUNK_BYTES:
            raise BaseError(413, "Transcript chunk too large")
        content.extend(part)
    receipt = await transcript_stream.append(
        db,
        project_id=project_id,
        topic_id=topic_id,
        file_id=file_id,
        source=source,
        offset=offset,
        content=bytes(content),
    )
    return {"code": 200, "data": receipt}


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
    """Record one Claude Code hook for `topic_id`, then hand it to that topic's
    live screen.

    **200 means the event is on OUR disk.** The sender treats the ack as
    permission to delete its own copy — the device drainer does exactly that —
    and until this route has written the spool, the only copy in the world is on
    a machine we do not own (docs/device-self-hosting.md §0): it can go offline,
    be wiped, or be deleted by the person who owns it. An in-memory queue is not
    somewhere an event has been put; a process that dies between the ack and the
    consumer takes the only remaining copy with it, and a lost `Stop` leaves that
    turn showing as never finished. So the write comes first and its failure is
    said out loud, because a non-200 is what keeps the event where it still
    exists.

    Responds fast (the container's hook call blocks on this): an empty 200 body =
    "no decision", so a PreToolUse hook proceeds normally."""
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
            "(screen likely launched before a backend restart)",
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
    # What the event has to be filed under. The id is the sender's stable name
    # for it — the spool's filename, the dedup key between a live delivery and
    # its spooled twin, and what a redelivery is recognised by; the project comes
    # from the token because the spool is per topic under it. Without either
    # there is nowhere to put this and nothing to call it, so the answer is the
    # honest one rather than an ack for an event we cannot keep.
    claims = scoped_token_claims(x_cheese_token)
    project = str(claims.get("p") or "") if claims else ""
    spool = None
    if x_cheese_event_id and project:
        try:
            spool = ws.spool_dir(uuid.UUID(project), uuid.UUID(topic_id))
        except ValueError:
            spool = None
    if spool is None:
        logger.warning(
            "sandbox hook not recorded for topic %s: it arrived without an event "
            "id or without a project-scoped token, so there is nowhere to file it",
            topic_id,
        )
        return JSONResponse(
            {
                "code": 400,
                "message": "hook needs X-Cheese-Event-Id and a project-scoped token",
                "data": None,
            },
            status_code=400,
        )
    payload["_eid"] = x_cheese_event_id
    # Attribute late events to the authenticated sender, not the teammate now
    # selected in the room. The sender cannot override the token's identity.
    payload["_agent_handle"] = claims.get("a") if claims else None
    try:
        append_event(spool, x_cheese_event_id, payload)
    except OSError:
        # Nothing else holds this event yet, so refusing the ack is the whole
        # point: the sender keeps its copy and comes back with it.
        logger.warning("hook spool write failed for topic %s", topic_id, exc_info=True)
        return JSONResponse(
            {"code": 503, "message": "hook could not be recorded", "data": None},
            status_code=503,
        )
    delivered = hook_router.push(topic_id, payload)
    if not delivered:
        # No live screen is subscribed, so nothing will read what was just
        # written until something goes looking. Schedule that: an orphaned turn's
        # claude keeps working after a backend restart (#316), and with the sweep
        # no longer re-prompting it, "the next turn's reconcile" may otherwise be
        # never — its progress, and the Stop that finishes the turn, land via
        # this. Delayed, so a reconnecting screen gets first claim; settle is the
        # backstop for when it never returns.
        chat.schedule_spool_settle(uuid.UUID(topic_id), delay_s=10.0)
    return {"code": 200, "message": "ok", "data": {"delivered": delivered}}
