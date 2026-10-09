"""平台说的一句话提交了，开着那段对话的页面当场看见它。

`announce` 落下一行时把它记在会话的 `session.info` 里（`SHOW_ONCE_COMMITTED`），
这里在会话提交之后把它们作为 `event_block` 发到各自那段对话的频道上；回滚了就扔掉。

发送放在这里而不在 `announce` 里：broker 在 `runtime`，而 `runtime` 本身会间接载入
`announce`，两边互相 import 就成了一个环。这个模块由 `app.api.deps`（接 broker 的
那一层）载入，监听器随之装上。
"""

from __future__ import annotations

import uuid

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.background import spawn
from app.core.live_frames import SHOW_ONCE_COMMITTED
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.agent.realtime.broker import get_broker


@event.listens_for(Session, "after_commit")
def _show_committed_notices(session: Session) -> None:
    rows: set[uuid.UUID] = set()
    for channel, frame in session.info.pop(SHOW_ONCE_COMMITTED, ()):
        spawn(get_broker().publish(channel, frame), name="notice live")
        # A room's row changed: the project's channel list shows it too, once
        # however many of the room's conversations were told.
        if frame.get("type") == "state" and frame.get("resource") == TOPICS:
            rows.add(uuid.UUID(str(frame.get("id") or channel)))
    for room in rows:
        spawn(tell_project(room, TOPICS), name="project feed")


@event.listens_for(Session, "after_rollback")
def _drop_rolled_back_notices(session: Session) -> None:
    session.info.pop(SHOW_ONCE_COMMITTED, None)
