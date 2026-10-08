"""一张验收卡变了，开着它的页面重新读一遍 —— 不论是哪条路改的。

卡会在很多地方变：芝士递卡、人退回、采纳、作废、改派，轮询发现 PR 合了，合并队列
排到了它。以前「告诉页面」是每个路由各自记得调一次 `announce_stale`，一共只记了两
处，而且发在房间上；任务页的 socket 听的是任务自己那段对话，于是递卡、退回、重递、
采纳，任务页一样都听不见，要刷新才看得到。

所以这件事不放在调用点上，放在卡这一行被写下去的那一刻：这个会话 flush 过一张卡，
提交之后就对卡所在的那段对话（有任务就是任务，没有就是房间）和它的房间各发
`state: accept` 和 `state: tasks`（卡决定任务走到哪一档）。回滚了就不发。
"""

from __future__ import annotations

import uuid
from itertools import chain

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.domain.agent.realtime.broker import get_broker
from app.domain.review.models import AcceptCard

_PENDING = "accept_cards_changed"


def _channels(card: AcceptCard) -> set[str]:
    places: set[uuid.UUID] = {card.topic_id}
    if card.task_id is not None:
        places.add(card.task_id)
    return {str(place) for place in places}


@event.listens_for(Session, "after_flush")
def _note_cards(session: Session, _context) -> None:
    # `new` / `dirty` still describe what this flush wrote while it runs.
    for row in chain(session.new, session.dirty):
        if isinstance(row, AcceptCard):
            session.info.setdefault(_PENDING, set()).update(_channels(row))


@event.listens_for(Session, "after_commit")
def _tell_pages(session: Session) -> None:
    channels = session.info.pop(_PENDING, None)
    if not channels:
        return
    from app.core.background import spawn

    # The card itself, and the task's progress (待审阅 / 已退回 / 已采纳), which the
    # card decides: the task page's header and the room's task list read it.
    for channel in channels:
        for resource in ("accept", "tasks"):
            spawn(
                get_broker().publish(channel, {"type": "state", "resource": resource}),
                name="accept card changed",
            )


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop(_PENDING, None)
