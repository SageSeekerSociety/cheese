"""A running app in the preview, while its machine sleeps: its snapshot, and waking it.

The right-hand panel shows an app as a little browser. When the teammate's
machine goes to sleep (or the app exits) the page cannot be reached, so the panel
shows the last page that loaded — a snapshot the runtime bridge took in the
reader's browser (`preview_runtime.snapshotHtml`) — and offers to wake the app.

Three routes:

- ``PUT/GET /topics/{id}/preview/snapshot`` keep and read the latest snapshot of
  the app the room is previewing. One per app: the key is who serves it and what
  it was declared as, so declaring the same app again keeps its snapshot.
- ``POST /topics/{id}/preview/wake`` brings the app back. While the sandbox
  still exists, running or frozen, waking the machine is enough — the same wake
  its next tool call would do: a frozen sandbox is thawed and the app goes on
  running as it was. Once the sandbox has been destroyed the platform does not
  know how the app was started — the agent ran it as a background command — so
  restarting it is the agent's job: a turn is started for that agent with the
  instruction.
- ``GET /topics/{id}/preview/wake-status`` is what the panel polls while it shows
  the steps: machine, tunnel, the agent working on it, the app answering.
"""

import hashlib
import json
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_work_runner
from app.api.preview_app_state import app_state
from app.api.preview_runtime import seal_snapshot
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.api.topics_deps import BlockRepository, ProjectRepository, _actor_in_place
from app.api.write_access import Can
from app.core import blob_store
from app.core.errors import NotFoundError, ValidationError
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, addressed_to_agent
from app.domain.machine.preview_wake import (
    find_seat_session,
    has_sandbox,
    is_preview_waking,
    schedule_preview_wake,
)
from app.domain.machine.session_reports import conversation_sessions
from app.domain.project.room_files import ARTIFACT_MIME
from app.domain.project_permissions import Permission
from app.domain.room_task.place import Place, place_or_404
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])

#: A snapshot is a page's DOM with its styles inlined and readable images as
#: `data:` URLs; the bridge refuses anything bigger than this, and so do we.
SNAPSHOT_MAX_BYTES = 2_000_000
#: One wake per app this long, whoever presses it: the turn it starts takes a
#: while to show up, and a second press in that time would start another.
WAKE_DEDUP_SECONDS = 90

WAKE_INSTRUCTION = (
    "右栏预览里的应用「{app}」现在打不开（{state}），{actor} 点了「唤醒并重启应用」。"
    "请把这个应用重新跑起来（后台命令），再用 `cheese serve <端口>` 声明它；"
    "预览面板会自己跟上，不用在对话里回复。起不来的话，在对话里说清卡在哪。"
)


async def _current_app(db, place: Place):
    """The app the room is previewing, or a refusal when it previews something else."""
    art = await BlockRepository(db).latest_artifact(place.conversation_id)
    if art is None or art.mime_type != ARTIFACT_MIME["app"]:
        raise NotFoundError(say("previewNotAnApp"))
    return art


def _snapshot_key(place: Place, art) -> str:
    # Who serves it and what it was declared as: the same app declared again (a
    # new artifact row) keeps its snapshot; a different app does not inherit one.
    app = hashlib.sha256(f"{art.author}\0{art.content}".encode()).hexdigest()[:32]
    return f".preview-snapshots/{place.conversation_id}/{app}.json"


class SnapshotIn(BaseModel):
    artifact_id: uuid.UUID
    html: str
    href: str = ""
    title: str = ""


class SnapshotOut(BaseModel):
    html: str
    href: str
    title: str
    #: Unix seconds.
    taken_at: float


@router.put(
    "/{topic_id}/preview/snapshot",
    dependencies=[Can(Permission.edit_content, at="topic_id")],
    **typed_response(SnapshotOut),
)
async def put_preview_snapshot(
    topic_id: uuid.UUID, body: SnapshotIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Keep the latest snapshot of the app this room previews.

    Written by whoever may change this room's content (``edit_content``, this
    route's own declaration): the snapshot is what one reader just saw, but
    every reader of the room is shown it later, so putting one there is a write
    to shared content and takes the permission that says so. A mentor sees the
    preview without it, so their browser's PUT is refused and ``keep()`` drops
    it — the snapshot on the platform is then whatever an editor last saw.

    It is shown in a frame that runs no script and reaches no network
    (``sandbox=""``). That frame runs no script because the sandbox says so; it
    reaches no network because the CSP the platform stamps into the stored
    bytes says so — whoever wrote the snapshot does not get to decide that, or
    a crafted page would phone home from every reader's browser. Sealing
    happens here, on the way in, so a stored snapshot is already safe to
    render."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    art = await _current_app(db, place)
    if body.artifact_id != art.id:
        raise ValidationError(say("previewSnapshotStale"))
    raw = body.html.encode()
    # The cap is measured on what the browser produced, not on the sealed bytes:
    # the bridge applies the same number to the same html before uploading, and a
    # snapshot it accepted must be storable. Adding the CSP meta afterwards must
    # not turn an accepted snapshot into a refused one.
    if len(raw) > SNAPSHOT_MAX_BYTES:
        raise ValidationError(
            say("previewSnapshotTooLarge", mb=SNAPSHOT_MAX_BYTES // 1_000_000)
        )
    sealed = seal_snapshot(raw)
    if sealed is None:
        raise ValidationError(say("previewSnapshotUnsealed"))
    # body.html is a JSON string, so its bytes are UTF-8 and inserting ASCII keeps
    # them valid UTF-8; the decode is lossless.
    shot = {
        "html": sealed.decode(),
        "href": body.href[:2048],
        "title": body.title[:200],
        "taken_at": time.time(),
    }
    await blob_store.local().put(
        _snapshot_key(place, art), json.dumps(shot, ensure_ascii=False).encode()
    )
    return ok(shot)


@router.get("/{topic_id}/preview/snapshot", **typed_response(SnapshotOut))
async def get_preview_snapshot(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The latest snapshot of the app this room previews; null when none was kept."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    art = await _current_app(db, place)
    try:
        raw = await blob_store.local().get(_snapshot_key(place, art))
    except blob_store.BlobMissingError:
        return ok(None)
    return ok(json.loads(raw))


class WakeOut(BaseModel):
    started: bool
    #: Why nothing was started: ``working`` (the agent is mid-turn and will see
    #: the app itself), ``pending`` (someone pressed it moments ago), ``up``
    #: (the app answers already).
    reason: str | None = None


@router.post(
    "/{topic_id}/preview/wake",
    dependencies=[Can(Permission.ask_agent, at="topic_id")],
    **typed_response(WakeOut),
)
async def wake_preview_app(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """Bring the app back up: thaw its sandbox, or wake its teammate.

    A sandbox that still exists is woken the way its next tool call wakes it —
    frozen is thawed, running is left alone — and the app it was running keeps
    running; no model turn is spent on that. Only when the sandbox has been
    destroyed is the teammate asked to start the app again,
    which costs a model call: that is the same permission as asking the agent
    anything (``ask_agent``, the route's own declaration:
    ``Can(Permission.ask_agent)`` let the caller in and hands the place and the
    actor here), and it starts at most one: not while that conversation already
    has a turn running, and not twice within :data:`WAKE_DEDUP_SECONDS` for the
    same app."""
    place = await place_or_404(db, topic_id)
    actor = await _actor_in_place(resolver, place)
    art = await _current_app(db, place)
    seat = art.author
    if seat not in await TopicMemberService(db).agent_handles(place.room_id):
        raise ValidationError(say("previewWakeNoSeat"))
    state = await app_state(place.conversation_id, seat)
    if state.alive:
        return ok({"started": False, "reason": "up"})
    if is_preview_waking(place.conversation_id) or await chat.has_running_turn(
        place.conversation_id
    ):
        return ok({"started": False, "reason": "working"})
    session_row = await find_seat_session(
        db,
        project_id=place.project_id,
        conversation_id=place.conversation_id,
        seat=seat,
    )
    redis = get_redis_client()
    key = f"preview-wake:{_snapshot_key(place, art)}"
    if redis is not None:
        try:
            if not await redis.set(key, "1", nx=True, ex=WAKE_DEDUP_SECONDS):
                return ok({"started": False, "reason": "pending"})
        except Exception:  # an unreachable limiter does not block a person's wake
            key = ""

    def _submit_turn() -> None:
        runner.submit(
            chat,
            place.conversation_id,
            author="system",
            content=WAKE_INSTRUCTION.format(
                app=art.content,
                state="电脑没连上平台" if not state.tunnel_up else "应用没有应答",
                actor=f"<@{actor.handle}>",
            ),
            addressed=addressed_to_agent(seat),
            nudge_event=say(
                "previewWakeRequested", actor=f"<@{actor.handle}>", seat=f"<@{seat}>"
            ),
        )

    if session_row is not None and await has_sandbox(db, session_row.id):
        # The app's sandbox is still there: waking it is enough. A frozen one
        # thaws with its processes as they were, and the app goes on running.
        schedule_preview_wake(
            db.bind,
            place=place,
            session_id=session_row.id,
            fallback=_submit_turn,
            dedup_key=key,
        )
        return ok({"started": True})
    try:
        _submit_turn()
    except Exception:
        # 这一轮没起成：把刚才占下的坑放掉，再按一次还能按。留着的话接下来 90 秒都只会
        # 报「刚有人按过」，而实际上没有任何东西在跑。
        if redis is not None and key:
            try:
                await redis.delete(key)
            except Exception:
                pass
        raise
    return ok({"started": True})


class WakeStatusOut(BaseModel):
    #: The agent's machine lease here: ``preparing``, ``ready``, or None when it
    #: holds none.
    machine: str | None
    tunnel_up: bool
    #: The conversation has a turn running (the agent restarting the app).
    agent_working: bool
    #: The app answers through the tunnel.
    app_up: bool


@router.get("/{topic_id}/preview/wake-status", **typed_response(WakeStatusOut))
async def preview_wake_status(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """Where waking the app has got to, in the steps the panel shows."""
    place = await place_or_404(db, topic_id)
    await _actor_in_place(resolver, place)
    art = await _current_app(db, place)
    state = await app_state(place.conversation_id, art.author)
    machine = None
    project = await ProjectRepository(db).get(place.project_id)
    if project is not None:
        for session in await conversation_sessions(db, project, place):
            if art.author in (session["seat_handle"], session["agent_handle"]):
                machine = session["status"]
                break
    return ok(
        {
            "machine": machine,
            "tunnel_up": state.tunnel_up,
            "agent_working": is_preview_waking(place.conversation_id)
            or await chat.has_running_turn(place.conversation_id),
            "app_up": state.alive,
        }
    )
