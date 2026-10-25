"""Reading a session's transcript from the machine its tools do not run on.

Claude Code writes a session's transcript on the session host. The machine a
session's Bash and file tools run on is a different one, and it does not have
that file — so after a compaction, the path the continuation message names
reads as "No such file" there. The executor's own read-only view of that path
(`forwarded_transcript`) pulls its bytes from here: one chunk per call, from
this session's own transcript.

The caller is the executor, with the session's own execution credential
(``cxss_…``). The session id in that credential is what picks the transcript —
never a path from the caller — so nothing but this session's own file can be
reached through this route.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_session_host
from app.api.request_deps import DbSession
from app.api.response import ok
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sandbox_auth import scoped_token_claims
from app.domain.agent.session_host.host import SessionHost
from app.domain.device import owner_reads

router = APIRouter(prefix="/topics", tags=["topics"])

Host = Annotated[SessionHost, Depends(get_session_host)]


@router.get("/{topic_id}/session-transcript", response_model=None)
async def session_transcript(
    topic_id: uuid.UUID,
    request: Request,
    db: DbSession,
    host: Host,
    offset: int = Query(0, ge=0),
    size: int = Query(384 * 1024, ge=1, le=4 * 1024 * 1024),
    path: bool = Query(False),
) -> dict:
    """One chunk of the calling session's own transcript, or that it is not
    there yet.

    The credential says which session and which conversation; a credential
    that names no session, or one of another conversation, is refused. The
    transcript is read on the session host, where the session's runner answers
    `transcript_chunk` — this route is only the relay, because the executor
    and the session host do not reach each other directly.

    ``path=true`` asks where the file is instead of for its bytes: the answer
    carries the session host's own spelling of the path, which is the name the
    executor mounts its read-only view at.
    """
    claims = scoped_token_claims(request.headers.get("x-cheese-token", ""))
    if not claims or claims.get("t") != str(topic_id):
        raise AuthenticationRequiredError("A credential for this room is required")
    session_claim = claims.get("session")
    try:
        session_id = uuid.UUID(str(session_claim))
    except (TypeError, ValueError):
        raise AuthenticationRequiredError("An execution session is required") from None
    owned = await owner_reads.session_execution(db, session_id)
    if owned is None:
        raise NotFoundError.for_resource("session", str(session_id))
    if owned.conversation_id != topic_id:
        raise ForbiddenError("This credential works another conversation")
    location = owned.runtime_location or {}
    device_id = location.get("device_id")
    state = (location.get("runtime") or {}).get("state")
    if not device_id or not state:
        raise NotFoundError.for_resource("session", str(session_id))
    answer = await host.hub.call_executor(
        device_id,
        state,
        "transcript_chunk",
        {"offset": 0 if path else offset, "size": 0 if path else size},
        timeout=60,
    )
    return ok(answer)
