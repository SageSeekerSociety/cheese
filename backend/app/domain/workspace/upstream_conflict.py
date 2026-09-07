"""同步上游冲突 → 派芝士解决 (spec §6.3).

`sync_upstream` aborting cleanly on conflict is the right thing for the shared
repo — it never half-merges. But on its own it was a DEAD END: the caller got
`{"synced": false, "reason": "合并冲突：..."}` and there was nothing anywhere to
act on it. Accepting a topic already had an exit (routes/accept.py materializes
the conflict in the topic's workspace and dispatches 芝士); syncing did not. A
project whose upstream had diverged simply could not pull, and every later sync
walked into the same wall.

This module is that missing exit. It puts the conflicted merge somewhere 芝士
can actually work — which has to be a `task`, because that is the node that
carries a branch, a workspace and an accept card — and summons it there.

WHERE the task goes: the 1:1 room between 芝士 and whoever pressed 同步上游.
That room already exists as a product concept (`get_or_create_private`), so
nothing new is invented, and the answer comes back to the person who asked. The
alternative — a platform-created public room — would be the first time the
platform ever creates a room on its own, which is a product decision this fix
does not need to make.
"""

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.chat import ChatService
from app.domain.agent.github_app import github_app_read_token_for_project
from app.domain.agent.platform_notices import (
    EVENT_UPSTREAM_CONFLICT,
    SEVERITY_WARN,
    WHO_CHEESE,
    notice,
)
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.upstream_conflict")

RESOLUTION_TITLE = "解决同步上游冲突"


def _prompt(files: list[str]) -> str:
    listing = "、".join(files[:15]) or "（见工作区冲突标记）"
    return (
        "同步上游时合并冲突了，同步已原样中止，没有合一半。平台已经把上游那一支合进"
        f"你的工作区，冲突标记就在这些文件里：{listing}。请打开这些文件解决所有 "
        "<<<<<<< 冲突标记（保留双方意图，语义化合并，不要机械二选一），跑相关测试"
        "确认没破坏，然后简短汇报解决思路，最后递一张验收卡——这件活被采纳时，上游"
        "历史会连同你的解决一起并进主分支，同步才算完成。没有验收卡就没有可采纳的"
        "东西，你解干净的这一支会停在工作区里出不去。"
    )


async def dispatch(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    requested_by: str,
    chat: ChatService,
    runner: AgentWorkRunner,
) -> dict | None:
    """Materialize the aborted upstream merge in a fresh task and summon 芝士.

    Returns the dispatch descriptor, or None when it could not be set up — the
    sync result stays truthful either way, so a failure here degrades to the old
    "reported but no exit" behaviour rather than lying about what happened.
    """
    topics = TopicService(db)
    try:
        room = await topics.get_or_create_private(
            project_id=project_id, user_handle=requested_by
        )
        # Pressing 同步上游 again while a resolution is already open must NOT
        # start a second one: re-materializing would overwrite whatever 芝士 has
        # resolved so far, and the room would fill with identical dead threads.
        # Point back at the live one and leave its turn alone.
        for open_one, _ in await TaskService(db).threads_for_room(room.id, limit=0):
            if (
                open_one.title == RESOLUTION_TITLE
                and open_one.status == TaskStatus.open
            ):
                return {"topic_id": str(open_one.id), "files": [], "reused": True}
        # A thread carries the branch, the workspace and the accept card, which
        # is exactly what resolving a conflict needs — and it does not cost the
        # private room a second room to hold it.
        task = await topics.dispatch_task(
            place_id=room.id,
            title=RESOLUTION_TITLE,
            created_by=requested_by,
        )
        await db.flush()
    except Exception:  # noqa: BLE001 — never turn a reported conflict into a 500
        logger.exception("upstream conflict dispatch failed for project %s", project_id)
        return None

    try:
        # A bound project's upstream is read as the App, here as in the sync
        # that found the conflict; an unbound one fetches with no credential.
        token = await github_app_read_token_for_project(project_id, db)
        files = await asyncio.to_thread(
            ws.prepare_upstream_conflict_resolution, project_id, task.id, token=token
        )
    except Exception:  # noqa: BLE001 — as above, plus: do not strand the task
        logger.exception(
            "materializing the upstream conflict failed for project %s", project_id
        )
        # The task exists only to hold this merge. With no merge in it, it is an
        # orphan in someone's private room that nothing will ever close, so it
        # goes back out with the failure rather than accumulating.
        await db.delete(task)
        await db.flush()
        return None

    runner.submit(
        chat,
        task.id,
        author="system",
        content=_prompt(files),
        summon=True,
        # 平台提示统一契约: 一行给房间，完整冲突文件清单进 meta.detail
        # （`_prompt` 里那份为了可读只列前 15 个）。给芝士的 content 一字未动。
        nudge_event=f"同步上游时合并冲突，{len(files)} 个文件",
        nudge_meta=notice(
            EVENT_UPSTREAM_CONFLICT,
            severity=SEVERITY_WARN,
            who=WHO_CHEESE,
            detail="\n".join(files) or "（见工作区冲突标记）",
            detail_label="冲突文件",
        ),
    )
    return {"topic_id": str(task.id), "files": files}
