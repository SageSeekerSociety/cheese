"""一张验收卡变了，开着它的页面重新读一遍 —— 不论是哪条路改的。

卡会在很多地方变：芝士递卡、人退回、采纳、作废、改派，轮询发现 PR 合了，合并队列
排到了它。以前「告诉页面」是每个路由各自记得调一次 `announce_stale`，一共只记了两
处，而且发在房间上；任务页的 socket 听的是任务自己那段对话，于是递卡、退回、重递、
采纳，任务页一样都听不见，要刷新才看得到。

所以这件事不放在调用点上，放在卡这一行被写下去的那一刻：这个会话 flush 过一张卡，
提交之后就对卡所在的那段对话（有任务就是任务，没有就是房间）和它的房间各发
`state: accept` 和 `state: tasks`（卡决定任务走到哪一档）。回滚了就不发。

**排进 `SHOW_ONCE_COMMITTED`，不自己发**：提交之后真发出去的监听器在
`agent/live_notices`，broker 也在 `agent` 里，而本包在它下面一层
（`docs/manual/dev/architecture.md`）。`core/live_frames` 就是为这件事准备的 —— 它
自己的 docstring 写着「任何改了页面所见之物的领域都排到这里，谁都不需要拿到那个
broker」，隔壁 `room_task/live.py` 正是这么发的。积压按 savepoint 分桶也是那里的
规矩：释放一个 savepoint 不当作提交，别的 savepoint 排的帧留到会话真正提交时才发。
"""

from __future__ import annotations

import uuid

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.live_frames import SHOW_ONCE_COMMITTED, stage
from app.domain.review.models import AcceptCard

#: 一条卡变了，页面要重读的两样：卡自己，和它决定的任务档位（待审阅 / 已退回 /
#: 已采纳）—— 任务页的页头和房间的任务列表都读它。
_RESOURCES = ("accept", "tasks")


def _channels(card: AcceptCard) -> set[str]:
    places: set[uuid.UUID] = {card.topic_id}
    if card.task_id is not None:
        places.add(card.task_id)
    return {str(place) for place in places}


#: When the merge state was last looked at. Rewritten on every check, shown
#: nowhere: a check that found what the card already says changes only this.
_UNSHOWN = frozenset({"checked_at"})


def _shown(state: object) -> object:
    if isinstance(state, dict):
        return {k: v for k, v in state.items() if k not in _UNSHOWN}
    return state


def _changed_on_screen(card: AcceptCard) -> bool:
    """Whether this flush changed anything a page shows of the card.

    Reading a room's cards re-checks the merge state of a pending one that is
    a minute old, and every GitHub event on its pull request does too. Telling
    every open page each time made each of them read the cards again, which
    re-checked the next card due: a room with five pending cards was told every
    two seconds."""
    for attr in inspect(card).attrs:
        history = attr.history
        if not history.has_changes():
            continue
        if attr.key != "merge_state":
            return True
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if _shown(before) != _shown(after):
            return True
    return False


def _tell(session: Session, card: AcceptCard) -> None:
    for channel in _channels(card):
        for resource in _RESOURCES:
            stage(
                session,
                SHOW_ONCE_COMMITTED,
                (channel, {"type": "state", "resource": resource}),
            )


@event.listens_for(Session, "after_flush")
def _note_cards(session: Session, _context) -> None:
    # `new` / `dirty` still describe what this flush wrote while it runs.
    for row in session.new:
        if isinstance(row, AcceptCard):
            _tell(session, row)
    for row in session.dirty:
        if isinstance(row, AcceptCard) and _changed_on_screen(row):
            _tell(session, row)
