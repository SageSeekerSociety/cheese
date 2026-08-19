"""一个房间一条分支：把子话题的提交并进母话题那一个 PR 的**判读**部分。

`workspace.service.merge_subtopic_into_room` does the git; this decides what its
answer MEANS — whether the job is finished, whether it has to be retried, and
whether a person needs to hear about it. Kept beside the conclusion card because
采信 is what triggers the merge, and kept out of `services.py` because that file
is about 默认采信 and nothing else.

The retry queue is **derived, not stored**: "an accepted card whose commits are
not on the room's branch yet" is a question git can already answer, so nothing
here needs a column (and no migration). What lives on disk is only a memo of the
last outcome per card — it makes the sweep skip finished cards without asking
git, and it keeps a stuck merge from announcing itself every single sweep. Lose
it and the sweep re-derives everything from git: slower, never wrong.
"""

import contextlib
import logging
import os
import uuid
from pathlib import Path

from app.core.config import settings
from app.domain.agent.platform_notices import (
    EVENT_ROOM_MERGE,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    WHO_CHEESE,
    WHO_PLATFORM,
    notice,
)

logger = logging.getLogger("cheesex.conclusion.room_branch")

#: 干完了 —— 提交在母话题分支上，母话题工作区也跟上了。扫描不再看这张卡。
DONE = "done"
#: 排队 —— 现在不能合（母话题在等 CI / 工作区有人在改），条件解除后自动再试。
DEFERRED = "deferred"
#: 冲突 —— 两条活改了同一处，要人解。扫描照样重试（解完就能过），但只播报一次。
CONFLICT = "conflict"
#: 合了但母话题工作区没跟上 —— 下一次快照会把分支带回去，所以还没完，继续重试。
STALE = "stale"

_NOTICES_DIRNAME = ".room-merges"


def _notices_dir() -> Path:
    return Path(settings.workspace_root) / _NOTICES_DIRNAME


def read_state(card_id: uuid.UUID) -> str | None:
    """This card's last known outcome, or None when it has never been tried."""
    try:
        return (_notices_dir() / card_id.hex).read_text().strip() or None
    except OSError:
        return None


def write_state(card_id: uuid.UUID, state: str) -> None:
    path = _notices_dir() / card_id.hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(state)
        tmp.replace(path)
    except OSError:
        logger.warning("could not record room-merge state for %s", card_id)


def forget_state(card_id: uuid.UUID) -> None:
    with contextlib.suppress(OSError):
        (_notices_dir() / card_id.hex).unlink()


def state_of(result: dict) -> str:
    """Read `merge_subtopic_into_room`'s answer as one of the four states.

    `workspace_stale` outranks everything, including a clean merge: the commits
    ARE on the branch, but the room's workspace did not follow, and the next
    snapshot taken there would set the bookmark back off them. Calling that
    finished is how the merge would get silently undone.
    """
    if result.get("workspace_stale"):
        return STALE
    if result.get("merged") or result.get("noop"):
        return DONE
    if result.get("deferred"):
        return DEFERRED
    return CONFLICT


def merged_notice(*, title: str, result: dict) -> tuple[str, dict]:
    commits = result.get("commits") or 0
    return (
        f"子话题《{title}》的 {commits} 个提交已并入本房间的分支",
        notice(
            EVENT_ROOM_MERGE,
            severity=SEVERITY_INFO,
            who=WHO_PLATFORM,
            detail=(
                f"并进了 `{result.get('into')}`，跟着本房间的验收卡一起交付，"
                "不再单开一个 PR。"
            ),
            detail_label="并到哪了",
        ),
    )


def deferred_notice(*, title: str, reason: str) -> tuple[str, dict]:
    return (
        f"子话题《{title}》的提交先排队",
        notice(
            EVENT_ROOM_MERGE,
            severity=SEVERITY_INFO,
            who=WHO_PLATFORM,
            detail=(
                f"暂时不并进本房间的分支：{reason}。"
                "条件解除后平台会自动再合一次，不需要谁来催。"
            ),
            detail_label="为什么排队",
        ),
    )


def conflict_notice(*, title: str, result: dict) -> tuple[str, dict]:
    conflicts = result.get("conflicts") or []
    where = "、".join(conflicts[:20]) if conflicts else "（见原因）"
    return (
        f"子话题《{title}》的提交并进本房间分支时冲突",
        notice(
            EVENT_ROOM_MERGE,
            severity=SEVERITY_ERROR,
            who=WHO_CHEESE,
            detail=(
                f"冲突在：{where}\n"
                f"原因：{result.get('reason', '')}\n"
                "两条活改到了同一处。在本房间的工作区里解掉冲突并提交，平台下一轮"
                "会自动重试；这比等到两个 PR 之间才发现要早得多。"
            ),
            detail_label="冲突详情",
        ),
    )
