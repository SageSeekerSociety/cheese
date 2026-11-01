"""Direct machine wake and browse-activity keepalive for a previewed app.

Two pieces that keep a ``cheese serve`` preview responsive without spending
model turns:

1. ``touch_preview_activity`` moves the serving session's ``SandboxHome.active_at``
   forward when a reader's browser loads or interacts with the preview, at most
   once per ``ACTIVE_STEP`` (1 minute). While someone is actively browsing the
   preview, the idle sweeper does not reclaim the sandbox under them; once they
   leave, the idle clock runs out as usual.

2. ``wake_preview_on_machine`` brings a sleeping app back up directly on the
   session's machine — allocating a fresh sandbox via ``session_work.ensure``
   when the previous one was reclaimed, then running ``cheese serve --restore``
   with the launch spec recorded in ``Block.meta["serve"]`` — and falls back to
   starting an agent turn only when no launch spec was recorded or the direct
   restart did not bring the port back up.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import shlex
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import get_redis_client
from app.core.sandbox_auth import bind_resource_token, scoped_token_claims
from app.domain.agent import execution
from app.domain.agent.device_hub import device_hub
from app.domain.agent.harness.channel import mint_session_token
from app.domain.agent.preview_hub import preview_hub
from app.domain.agent.preview_owner import inspect_owner
from app.domain.agent_session.models import LOST_KEY, AgentSession
from app.domain.agent_session.services import AgentSessionService
from app.domain.machine import lifecycle, session_work
from app.domain.machine.models import SandboxHome
from app.domain.machine.services import ACTIVE_STEP
from app.domain.machine.session_work import _session_author
from app.domain.project.environment import EnvironmentConfig, pin_environment
from app.domain.project.services import ProjectService
from app.domain.room_task.place import Place
from app.domain.topic.models import TopicKind
from app.domain.topic.services import TopicService

logger = logging.getLogger("cheese.machine.preview_wake")

#: Minimum interval between DB updates of ``SandboxHome.active_at`` per
#: (conversation, seat) when a reader browses a preview.
PREVIEW_TOUCH_STEP_S = ACTIVE_STEP.total_seconds()

#: How long a direct restore waits for the preview tunnel and port to answer
#: once ``cheese serve --restore`` returns.
RESTORE_ATTACH_WAIT_S = 8.0

#: How long ``session_work.ensure`` may wait for a sandbox to be prepared during
#: a preview wake.
WAKE_ENSURE_WAIT_S = 120.0

#: How long the restore command on the executor may run.
RESTORE_COMMAND_TIMEOUT_MS = 120_000

_preview_touched: dict[tuple[uuid.UUID, str], float] = {}
_waking_conversations: set[uuid.UUID] = set()
_WAKE_TASKS: set[asyncio.Task[None]] = set()


def clear_preview_touch_cache() -> None:
    """Clear the in-memory throttle table (used by tests)."""
    _preview_touched.clear()


def is_preview_waking(conversation_id: uuid.UUID) -> bool:
    """Whether a direct machine wake is currently running for ``conversation_id``."""
    return conversation_id in _waking_conversations


async def _never_gone() -> bool:
    return False


async def find_seat_session(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    conversation_id: uuid.UUID,
    seat: str,
) -> AgentSession | None:
    """The most recent ``AgentSession`` in ``conversation_id`` acting as ``seat``."""
    project = await ProjectService(db).get(project_id)
    if project is None:
        return None
    rows = await db.scalars(
        select(AgentSession)
        .where(AgentSession.conversation_id == conversation_id)
        .order_by(AgentSession.updated_at.desc())
    )
    for row in rows:
        if row.agent_handle == seat:
            return row
        if await _session_author(db, project, row.agent_handle) == seat:
            return row
    return None


async def touch_preview_activity(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    seat: str,
    *,
    project_id: uuid.UUID | None = None,
) -> None:
    """Refresh ``SandboxHome.active_at`` when a reader browses an app preview.

    Throttled in-memory and in SQL to ``ACTIVE_STEP`` (1 minute) so bursts of
    asset requests from a dev server cost at most one row update per minute.
    Best-effort: a transient database failure is logged and never interrupts
    preview serving.
    """
    key = (conversation_id, seat)
    now_mono = time.monotonic()
    if now_mono - _preview_touched.get(key, -math.inf) < PREVIEW_TOUCH_STEP_S:
        return
    if len(_preview_touched) >= 256:
        stale_keys = [
            k
            for k, ts in _preview_touched.items()
            if now_mono - ts >= PREVIEW_TOUCH_STEP_S
        ]
        for k in stale_keys:
            _preview_touched.pop(k, None)
    _preview_touched[key] = now_mono
    try:
        session_row = (
            await find_seat_session(
                db,
                project_id=project_id,
                conversation_id=conversation_id,
                seat=seat,
            )
            if project_id is not None
            else await db.scalar(
                select(AgentSession)
                .where(
                    AgentSession.conversation_id == conversation_id,
                    AgentSession.agent_handle == seat,
                )
                .order_by(AgentSession.updated_at.desc())
            )
        )
        if session_row is None:
            return
        now = datetime.now(UTC)
        cutoff = now - ACTIVE_STEP
        updated = list(
            await db.scalars(
                update(SandboxHome)
                .where(
                    SandboxHome.session_id == session_row.id,
                    SandboxHome.stopped_at.is_(None),
                    or_(
                        SandboxHome.active_at.is_(None),
                        SandboxHome.active_at <= cutoff,
                    ),
                )
                .values(active_at=now)
                .returning(SandboxHome.id)
            )
        )
        for home_id in updated:
            lifecycle._asked.pop(home_id, None)
        if updated:
            await db.commit()
    except Exception:
        logger.warning(
            "touching preview activity for conversation %s failed",
            conversation_id,
            exc_info=True,
        )
        with contextlib.suppress(Exception):
            await db.rollback()


def can_wake_on_machine(art_meta: dict | None, session: AgentSession | None) -> bool:
    """Whether ``art_meta`` and ``session`` carry enough to wake without a model turn."""
    if not isinstance(art_meta, dict) or session is None:
        return False
    serve = art_meta.get("serve")
    if not isinstance(serve, dict):
        return False
    port = serve.get("port")
    command = serve.get("command")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        return False
    if not isinstance(command, str) or not command.strip():
        return False
    return bool(session.runtime_location or session.work_lease)


async def restore_preview_on_machine(
    bind,
    *,
    place: Place,
    session_id: uuid.UUID,
    seat: str,
    serve: dict,
    hub=None,
) -> bool:
    """Acquire the session's sandbox and run ``cheese serve --restore`` there.

    Returns True when the command succeeded and the app answers through the
    preview tunnel; False on any setup, command, or probe failure so the caller
    can fall back to waking the agent for a turn.
    """
    hub = hub or device_hub
    try:
        async with AsyncSession(bind, expire_on_commit=False) as db:
            row = await AgentSessionService(db).by_id(session_id)
            if row is None:
                return False
            topic = await TopicService(db).get_or_404(place.room_id)
            project = await ProjectService(db).get_or_404(place.project_id)
            author = await _session_author(db, project, row.agent_handle)
            resource = str(topic.resource_id or topic.id)
            environment = (
                EnvironmentConfig().snapshot()
                if topic.kind == TopicKind.root
                else await pin_environment(db, project.id, topic.id)
            )
            env = {"CHEESE_ENVIRONMENT": json.dumps(environment)}
            target: dict | None = None
            if row.runtime_location is not None:
                raw_token = mint_session_token(project.id, row.conversation_id, author)
                token = bind_resource_token(
                    raw_token,
                    resource,
                    session_id=str(row.id),
                )
                claims = scoped_token_claims(token) or {}
                outcome = await session_work.ensure(
                    db,
                    topic_id=place.room_id,
                    session_id=row.id,
                    claims=claims,
                    token=token,
                    env=env,
                    wait_s=WAKE_ENSURE_WAIT_S,
                    gone=_never_gone,
                    hub=hub,
                    tells_agent=False,
                )
                if isinstance(outcome, dict) and isinstance(
                    outcome.get("target"), dict
                ):
                    target = outcome["target"]
            elif row.work_lease and row.work_lease.get("device_id"):
                lease = dict(row.work_lease)
                start = await session_work.restart_executor(db, row, lease)
                await db.commit()
                info = await start()
                target = {**lease, "state": info["state"]}
            if target is None or target.get("status", "ready") != "ready":
                return False

        port = int(serve["port"])
        spec_json = json.dumps(serve, ensure_ascii=False)
        restore_cmd = f"cheese serve {port} --restore {shlex.quote(spec_json)}"
        result = await execution.call(
            target,
            "invoke",
            {
                "id": f"preview-wake-{uuid.uuid4().hex}",
                "tool": "Bash",
                "args": {
                    "command": restore_cmd,
                    "timeout": RESTORE_COMMAND_TIMEOUT_MS,
                },
                "platform": True,
            },
            hub=hub,
            timeout=RESTORE_COMMAND_TIMEOUT_MS / 1000.0 + 15.0,
        )
        value = result.get("value") if isinstance(result, dict) else None
        stdout = (value.get("stdout") if isinstance(value, dict) else "") or ""
        if (
            not isinstance(result, dict)
            or result.get("error")
            or stdout.startswith("Exit code")
        ):
            return False
        inspection = None
        if settings.preview_connection_mode == "owner":
            inspection = await inspect_owner(
                place.conversation_id,
                seat,
                wait_ms=min(8000, int(RESTORE_ATTACH_WAIT_S * 1000)),
                probe=True,
            )
        tunnel_up = (
            inspection.tunnel_up
            if inspection is not None
            else await preview_hub.wait_online(
                place.conversation_id, seat, RESTORE_ATTACH_WAIT_S
            )
        )
        if not tunnel_up:
            return False
        alive = (
            inspection.alive
            if inspection is not None
            else await preview_hub.probe(place.conversation_id, seat)
        )
        if not alive:
            return False
        if serve.get("task_id"):
            async with AsyncSession(bind, expire_on_commit=False) as db:
                row = await AgentSessionService(db).by_id(session_id, lock=True)
                if row is not None and (row.execution_request or {}).get(LOST_KEY):
                    row.execution_request = {
                        k: v
                        for k, v in (row.execution_request or {}).items()
                        if k != LOST_KEY
                    }
                    await db.commit()
        return True
    except Exception:
        logger.warning(
            "direct preview wake for conversation %s failed; falling back to agent turn",
            place.conversation_id,
            exc_info=True,
        )
        return False


def schedule_direct_preview_wake(
    bind,
    *,
    place: Place,
    session_id: uuid.UUID,
    seat: str,
    serve: dict,
    fallback: Callable[[], None],
    dedup_key: str = "",
    hub=None,
) -> asyncio.Task[None]:
    """Run ``restore_preview_on_machine`` in the background; call ``fallback`` if it fails."""
    conversation_id = place.conversation_id
    _waking_conversations.add(conversation_id)

    async def _run() -> None:
        try:
            restored = await restore_preview_on_machine(
                bind,
                place=place,
                session_id=session_id,
                seat=seat,
                serve=serve,
                hub=hub,
            )
            if not restored:
                try:
                    fallback()
                except Exception:
                    logger.warning(
                        "fallback turn submission for preview wake in %s failed",
                        conversation_id,
                        exc_info=True,
                    )
                    redis = get_redis_client()
                    if redis is not None and dedup_key:
                        with contextlib.suppress(Exception):
                            await redis.delete(dedup_key)
        finally:
            _waking_conversations.discard(conversation_id)

    task = asyncio.create_task(_run())
    _WAKE_TASKS.add(task)
    task.add_done_callback(_WAKE_TASKS.discard)
    return task
