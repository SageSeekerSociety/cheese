"""运行记录，给做这个平台本身的项目里的 AI 队友读：和管理后台「运行记录」同一份
（`app.domain.run_record.admin`），从它所在的频道读。

为什么不是管理后台那两条：`/admin/*` 不让带 agent 绑定的身份进（管理面的每个
endpoint 都是管理动作）。这里只读，而且只在平台自己的项目里读得到——报错里有别的
项目的名字和对话，只该给在修这个平台的人看。
"""

import time
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from app.api.auth import ActorResolverDep
from app.api.deps import get_session_host, get_work_runner
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.session_host.host import SessionHost
from app.domain.feedback.claims import is_platform_project
from app.domain.room_task.place import place_or_404
from app.domain.run_record import admin, turns
from app.domain.run_record.turns import TurnDetailOut, TurnListOut

#: The two in-process sources of a turn's last frame: the sessions this
#: process reads, and the work runner's own recent records. Both are looked up
#: by full turn id, and neither costs a call to the session's machine.
Host = Annotated[SessionHost, Depends(get_session_host)]
Runner = Annotated[AgentWorkRunner, Depends(get_work_runner)]

router = APIRouter(prefix="/run-records", tags=["run-records"])

Window = Literal["24h", "7d", "30d"]


async def _from_platform_room(
    db: DbSession, resolver: ActorResolverDep, topic: uuid.UUID
) -> None:
    """The caller is in this room, and the room's project works on the platform."""
    place = await place_or_404(db, topic)
    who = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        who, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if not await is_platform_project(db, place.project_id):
        raise ForbiddenError(say("runRecordsPlatformRoomsOnly"))


def _add_frames(payload: dict[str, Any], *, host: Host, runner: Runner) -> None:
    """Say how recently each still-open turn's session last produced a frame.

    Only a turn whose row is still open (`stopped_at` empty) is filled: a
    closed turn's frame is history, not a sign of life. Two in-process sources
    are consulted, both matched by the FULL turn id — the sessions this
    process reads (`host.frame_snapshots`) and the work runner's own recent
    records (`recent_work`) — and neither is asked anything over the network.
    A turn that neither knows keeps `None` ("unknown"): after a restart there
    is honestly no frame, and that is not age 0.
    """
    turns = payload.get("turns") or [payload]
    open_turns = [turn for turn in turns if turn.get("stopped_at") is None]
    if not open_turns:
        return
    wanted = {str(turn["turn_id"]) for turn in open_turns}
    frames = host.frame_snapshots([uuid.UUID(value) for value in wanted])
    recent = {
        record.get("turn_id"): record
        for record in runner.recent_work()
        if record.get("turn_id") in wanted
    }
    for turn in open_turns:
        # Written explicitly for every open turn, so an unknown frame is a
        # stated `None` rather than a missing field a reader could mistake.
        turn["last_frame_kind"] = None
        turn["last_frame_age_s"] = None
        frame = frames.get(uuid.UUID(str(turn["turn_id"])))
        if frame is not None:
            turn["last_frame_kind"] = frame.get("last_frame_kind")
            turn["last_frame_age_s"] = frame.get("last_frame_age_s")
            continue
        record = recent.get(str(turn["turn_id"]))
        if record is None or record.get("last_frame_at") is None:
            continue
        # The record's monotonic reading and this clock are the same process's.
        # Its `status` is deliberately not consulted: it says what the runner
        # thought of an older moment, not whether a live session is there now.
        turn["last_frame_kind"] = record.get("last_frame_kind")
        turn["last_frame_age_s"] = round(
            time.monotonic() - float(record["last_frame_at"]), 2
        )


@router.get("")
async def run_records_overview(
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID,
    window: Window = "24h",
    group: Literal["all", "errors", "recovered"] = "all",
    q: str | None = Query(None, max_length=200),
) -> dict:
    await _from_platform_room(db, resolver, topic)
    return ok(await admin.overview(db, window=window, group=group, query=q))


@router.get("/turns", **typed_response(TurnListOut))
async def run_records_turns(
    db: DbSession,
    resolver: ActorResolverDep,
    host: Host,
    runner: Runner,
    topic: uuid.UUID,
    window: Window = "24h",
    limit: int = Query(turns.DEFAULT_LIMIT, ge=1, le=turns.MAX_LIMIT),
) -> dict:
    """一轮一行，按窗口。运行记录答不了「分母」，这一条答的就是分母 —— 口径写在
    `app.domain.run_record.turns` 的模块 docstring 上。"""
    await _from_platform_room(db, resolver, topic)
    data = await turns.in_window(db, window=window, limit=limit)
    _add_frames(data, host=host, runner=runner)
    return ok(data)


@router.get("/turns/{turn_id}", **typed_response(TurnDetailOut))
async def run_records_turn(
    db: DbSession,
    resolver: ActorResolverDep,
    host: Host,
    runner: Runner,
    turn_id: uuid.UUID,
    topic: uuid.UUID,
) -> dict:
    """一轮的下钻：它怎么了，和它落到运行记录里的那几件事。

    `turn_id` 是完整的一轮号：路径参数按 `uuid.UUID` 收，短前缀进不来（本仓的
    校验错误统一是 400）。找不到这一轮是 404 —— 行是关掉不是删掉，所以「没有」
    只会是号不对。
    """
    await _from_platform_room(db, resolver, topic)
    found = await turns.of_turn(db, turn_id)
    if found is None:
        raise NotFoundError("No such turn")
    _add_frames(found, host=host, runner=runner)
    return ok(found)


@router.get("/detail")
async def run_records_detail(
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID,
    key: str = Query(..., max_length=2000),
    kind: str = Query(..., max_length=48),
    window: Window = "24h",
) -> dict:
    await _from_platform_room(db, resolver, topic)
    found = await admin.detail(db, key=key, kind=kind, window=window)
    if found is None:
        raise NotFoundError("No such run records in this window")
    return ok(found)
