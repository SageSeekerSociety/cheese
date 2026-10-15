"""平台说的一句话提交了，开着那段对话的页面当场看见它。

`announce` 落下一行时把它记在会话的 `session.info` 里（`SHOW_ONCE_COMMITTED`），
这里在会话提交之后把它们作为 `event_block` 发到各自那段对话的频道上；回滚了就扔掉。
每一层 savepoint 各是一桶（`core.live_frames`）：释放不当作提交，回滚只丢自己那一桶。
排进这个槽的不只是平台说的话：任务变了（`room_task/live`）和验收卡变了
（`review/live`）的 `state` 帧也从这里出去 —— 它们各自都在这个包下面一层，说不了
broker。

发送放在这里而不在 `announce` 里：broker 在 `runtime`，而 `runtime` 本身会间接载入
`announce`，两边互相 import 就成了一个环。这个模块由 `app.api.deps`（接 broker 的
那一层）载入，监听器随之装上。
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.background import spawn
from app.core.live_frames import (
    SHOW_ONCE_COMMITTED,
    drop_rolled_back,
    take_committed,
)
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.agent.realtime.broker import get_broker


def _each_frame_once(staged: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """同样的帧排进两个 savepoint，也还是只发一次。

    `stage` 只在一个桶里去重（`core.live_frames`），而一帧在内层 savepoint 排过、
    释放之后外层再排一次，就是两个桶里各一份。发两遍是让页面白读一遍。
    """
    seen: set[tuple[str, str]] = set()
    each: list[tuple[str, dict]] = []
    for channel, frame in staged:
        key = (channel, json.dumps(frame, sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            each.append((channel, frame))
    return each


@event.listens_for(Session, "after_commit")
def _show_committed_notices(session: Session) -> None:
    rows: set[uuid.UUID] = set()
    staged = take_committed(session, SHOW_ONCE_COMMITTED)
    for channel, frame in _each_frame_once(staged):
        spawn(get_broker().publish(channel, frame), name="notice live")
        # A room's row changed: the project's channel list shows it too, once
        # however many of the room's conversations were told.
        if frame.get("type") == "state" and frame.get("resource") == TOPICS:
            rows.add(uuid.UUID(str(frame.get("id") or channel)))
    for room in rows:
        spawn(tell_project(room, TOPICS), name="project feed")


@event.listens_for(Session, "after_rollback")
def _drop_rolled_back_notices(session: Session) -> None:
    drop_rolled_back(session, SHOW_ONCE_COMMITTED)
