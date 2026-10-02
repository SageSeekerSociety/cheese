"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

from __future__ import annotations

import asyncio as asyncio
import logging as logging
import time as time
import uuid as uuid
from collections.abc import Awaitable as Awaitable
from collections.abc import Callable as Callable
from collections.abc import Sequence as Sequence
from dataclasses import dataclass as dataclass
from datetime import UTC as UTC
from datetime import datetime as datetime
from pathlib import PurePosixPath as PurePosixPath
from typing import TYPE_CHECKING as TYPE_CHECKING
from typing import Final as Final
from typing import NoReturn as NoReturn

from sqlalchemy import select as select
from sqlalchemy.ext.asyncio import AsyncSession as AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as async_sessionmaker

from app.core.config import settings as settings
from app.core.db import async_session_factory as async_session_factory
from app.core.errors import ForbiddenError as ForbiddenError
from app.core.errors import NotFoundError as NotFoundError
from app.core.errors import ValidationError as ValidationError
from app.domain.block.about import EventAbout as EventAbout
from app.domain.block.about import landing as landing
from app.domain.block.models import AuthorType as AuthorType
from app.domain.block.models import BlockKind as BlockKind
from app.domain.block.notice_text import NoticeText as NoticeText
from app.domain.block.notice_text import say as say
from app.domain.identity.handles import (
    looks_like_agent_handle as looks_like_agent_handle,
)
from app.domain.library import service as library
from app.domain.membership.services import MemberService as MemberService
from app.domain.project import artifacts as artifacts
from app.domain.project.models import AiMode as AiMode
from app.domain.project.models import Project as Project
from app.domain.repository import identity as identity
from app.domain.review import (
    archive as archive,
)
from app.domain.review import (
    commit_message as commit_message,
)
from app.domain.review import forge as forge_mod
from app.domain.review import (
    merge_state as merge_state,
)
from app.domain.review import (
    notes as notes,
)
from app.domain.review import (
    pr_publish as pr_publish,
)
from app.domain.review import (
    pr_signals as pr_signals,
)
from app.domain.review import (
    pr_text as pr_text,
)
from app.domain.review.merge_state import MergeVerdict as MergeVerdict
from app.domain.review.merge_state import Who as Who
from app.domain.review.merge_state import whose_move as whose_move
from app.domain.review.models import (
    AcceptCard as AcceptCard,
)
from app.domain.review.models import (
    AcceptStatus as AcceptStatus,
)
from app.domain.review.models import (
    DeliverableKind as DeliverableKind,
)
from app.domain.review.models import (
    GateOutcome as GateOutcome,
)
from app.domain.review.nudges import _NUDGE_TAIL_LIMIT as _NUDGE_TAIL_LIMIT

# 兼容门面：PR 上的三件事怎么说（CI 挂了、有人评审、和目标分支冲突）搬去了
# `nudges.py`。这里重新导出，`app.domain.review.services` 仍是既有调用点与测试的
# 导入路径；`AcceptService` 上留一行同名委托，调用方一格没动。`_NUDGE_TAIL_LIMIT`
# 是给 `tests/integration/test_platform_notices.py` 取 detail 上界的，所以留在原
# 路径上（`as` 是 ruff 认的那种「这是再导出，不是没用上」的写法）；两段措辞辅助
# `_ci_log_howto` / `_nudge_note_prefix` 随三条待发一起搬走，没有别的取用者。
from app.domain.review.nudges import (
    _ci_nudge as _ci_nudge,
)
from app.domain.review.nudges import (
    _conflict_nudge as _conflict_nudge,
)
from app.domain.review.nudges import (
    _review_nudge as _review_nudge,
)
from app.domain.review.repositories import AcceptCardRepository as AcceptCardRepository

# 兼容门面：一件事做完之后「怎么说出去」（房间里那一行、卡上那条 note、要不要点
# 名）搬去了 `room_notices.py`。这里重新导出，`app.domain.review.services` 仍是既
# 有调用点与测试的导入路径；`AcceptService` 上每个各留一行同名委托，调用方一格没
# 动。`test_review_nudge_dedup.py` 就是靠 `service._record_task_nudge = AsyncMock()`
# 拦下一次待发的，所以那条委托必须还是一个真方法、走 `self.` 调用。
#
# `_notify_merge_result` 的那条委托多给一个参数：房间通知自己开一条 session 写库，
# 工厂由调用方给（就是上面的 `async_session_factory`），而不是被搬过去的那段代码
# 伸手去拿模块级单例——集成测试正是在 `services` 这个名字上换掉它
# （`test_accept_pr.py`、`forgejo/test_live.py`），放在这里读，换法照旧有效。
from app.domain.review.room_notices import (
    _announce_filed as _announce_filed,
)
from app.domain.review.room_notices import (
    _announce_new_artifact as _announce_new_artifact,
)
from app.domain.review.room_notices import (
    _note_needs_human as _note_needs_human,
)
from app.domain.review.room_notices import (
    _note_outside_accept_txn as _note_outside_accept_txn,
)
from app.domain.review.room_notices import (
    _note_poll_failed as _note_poll_failed,
)
from app.domain.review.room_notices import (
    _notify_merge_result as _notify_merge_result,
)
from app.domain.review.room_notices import (
    _notify_ready as _notify_ready,
)
from app.domain.review.room_notices import (
    _record_task_nudge as _record_task_nudge,
)
from app.domain.review.room_notices import (
    _tell_the_reviewer as _tell_the_reviewer,
)
from app.domain.review.schemas import AcceptCardOut as AcceptCardOut
from app.domain.room_task.checkouts import after_close as after_close
from app.domain.room_task.models import Task as Task
from app.domain.room_task.models import TaskStatus as TaskStatus
from app.domain.room_task.place import PlaceResolver as PlaceResolver
from app.domain.room_task.services import TaskService as TaskService
from app.domain.topic.models import Topic as Topic
from app.domain.topic.models import TopicStatus as TopicStatus

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.project.protection import BranchProtection
    from app.domain.review.github_pr import PullRequestStatus

from app.domain.review.services._shared import (
    _ACCEPT_NO_BRANCH_PREFIX as _ACCEPT_NO_BRANCH_PREFIX,
)
from app.domain.review.services._shared import (
    _ACCEPT_PR_OPEN_FAILED_PREFIX as _ACCEPT_PR_OPEN_FAILED_PREFIX,
)
from app.domain.review.services._shared import (
    _ACCEPT_PR_STALLED_PREFIX as _ACCEPT_PR_STALLED_PREFIX,
)
from app.domain.review.services._shared import (
    _ALEMBIC_VERSIONS_DIR as _ALEMBIC_VERSIONS_DIR,
)
from app.domain.review.services._shared import (
    _ARTIFACT_ACTION_BOTH as _ARTIFACT_ACTION_BOTH,
)
from app.domain.review.services._shared import (
    _ARTIFACT_ACTION_MISSING as _ARTIFACT_ACTION_MISSING,
)
from app.domain.review.services._shared import (
    _ARTIFACT_ACTION_UNWANTED as _ARTIFACT_ACTION_UNWANTED,
)
from app.domain.review.services._shared import (
    _BLOCKED_BY_CARD_MESSAGES as _BLOCKED_BY_CARD_MESSAGES,
)
from app.domain.review.services._shared import (
    _CARD_BLOCKS_NEW_CARD as _CARD_BLOCKS_NEW_CARD,
)
from app.domain.review.services._shared import (
    _DELIVERABLE_BOTH as _DELIVERABLE_BOTH,
)
from app.domain.review.services._shared import (
    _DELIVERABLE_MAX_BYTES as _DELIVERABLE_MAX_BYTES,
)
from app.domain.review.services._shared import (
    _FORCE_MERGE_VERDICTS as _FORCE_MERGE_VERDICTS,
)
from app.domain.review.services._shared import (
    _GITHUB_ENFORCES_TTL_S as _GITHUB_ENFORCES_TTL_S,
)
from app.domain.review.services._shared import (
    _MERGE_FAILED_MESSAGE as _MERGE_FAILED_MESSAGE,
)
from app.domain.review.services._shared import (
    _MISSING_SUBJECT as _MISSING_SUBJECT,
)
from app.domain.review.services._shared import (
    _NOT_THIS_ROOMS_WORK as _NOT_THIS_ROOMS_WORK,
)
from app.domain.review.services._shared import (
    _NOTHING_TO_DELIVER as _NOTHING_TO_DELIVER,
)
from app.domain.review.services._shared import (
    _REQUIRED_CHECK_GRACE_MINUTES as _REQUIRED_CHECK_GRACE_MINUTES,
)
from app.domain.review.services._shared import (
    EVENT_ACCEPT_DISMISSED as EVENT_ACCEPT_DISMISSED,
)
from app.domain.review.services._shared import (
    EVENT_ACCEPT_DONE as EVENT_ACCEPT_DONE,
)
from app.domain.review.services._shared import (
    EVENT_ACCEPT_STOPPED as EVENT_ACCEPT_STOPPED,
)
from app.domain.review.services._shared import (
    EVENT_CARD_REDESCRIBED as EVENT_CARD_REDESCRIBED,
)
from app.domain.review.services._shared import (
    EVENT_CARD_VOIDED as EVENT_CARD_VOIDED,
)
from app.domain.review.services._shared import (
    EVENT_FORCE_MERGED as EVENT_FORCE_MERGED,
)
from app.domain.review.services._shared import (
    EVENT_MERGE_REFUSED as EVENT_MERGE_REFUSED,
)
from app.domain.review.services._shared import (
    EVENT_MIGRATION_COLLISION as EVENT_MIGRATION_COLLISION,
)
from app.domain.review.services._shared import (
    EVENT_PR_CLOSED as EVENT_PR_CLOSED,
)
from app.domain.review.services._shared import (
    FORCE_MERGED_PREFIX as FORCE_MERGED_PREFIX,
)
from app.domain.review.services._shared import (
    GATE_ABANDONED_PREFIX as GATE_ABANDONED_PREFIX,
)
from app.domain.review.services._shared import (
    SEVERITY_ERROR as SEVERITY_ERROR,
)
from app.domain.review.services._shared import (
    SEVERITY_INFO as SEVERITY_INFO,
)
from app.domain.review.services._shared import (
    SEVERITY_WARN as SEVERITY_WARN,
)
from app.domain.review.services._shared import (
    VOIDED_PREFIX as VOIDED_PREFIX,
)
from app.domain.review.services._shared import (
    WHO_CHEESE as WHO_CHEESE,
)
from app.domain.review.services._shared import (
    WHO_HUMAN as WHO_HUMAN,
)
from app.domain.review.services._shared import (
    WHO_PLATFORM as WHO_PLATFORM,
)
from app.domain.review.services._shared import (
    BlockRepository as BlockRepository,
)
from app.domain.review.services._shared import (
    ProjectRepository as ProjectRepository,
)
from app.domain.review.services._shared import (
    ReviewerAdmission as ReviewerAdmission,
)
from app.domain.review.services._shared import (
    TopicRepository as TopicRepository,
)
from app.domain.review.services._shared import (
    _capped as _capped,
)
from app.domain.review.services._shared import (
    _force_merge_verdict as _force_merge_verdict,
)
from app.domain.review.services._shared import (
    _github_enforces as _github_enforces,
)
from app.domain.review.services._shared import (
    _github_enforces_cache as _github_enforces_cache,
)
from app.domain.review.services._shared import (
    _GitHubCredentials as _GitHubCredentials,
)
from app.domain.review.services._shared import (
    _never_shown_message as _never_shown_message,
)
from app.domain.review.services._shared import (
    _no_artifact_action as _no_artifact_action,
)
from app.domain.review.services._shared import (
    _one_artifact_action as _one_artifact_action,
)
from app.domain.review.services._shared import (
    _one_deliverable as _one_deliverable,
)
from app.domain.review.services._shared import (
    _read_deliverable as _read_deliverable,
)
from app.domain.review.services._shared import (
    _stale_view_message as _stale_view_message,
)
from app.domain.review.services._shared import (
    approvals_required_of as approvals_required_of,
)
from app.domain.review.services._shared import (
    logger as logger,
)
from app.domain.review.services._shared import (
    notice as notice,
)


class AcceptService:
    def __init__(self, session: AsyncSession):

        self._session = session
        self._repo = AcceptCardRepository(session)
        self._topics = TopicRepository(session)
        self._projects = ProjectRepository(session)

    async def _topic_or_404(self, topic_id: uuid.UUID) -> Topic:
        """The ROOM a place id names — a card is read in a room either way.

        `topic_id` here is a place id and is usually a thread's: a card is what a
        piece of work ends in. Everything this service does with the answer —
        rendering, notifying, the branch it pushes — belongs to the room, so the
        room is what it returns; which thread the card is FOR is on the card.
        """
        place = await PlaceResolver(self._session).resolve(topic_id)
        if place is None:
            raise NotFoundError("Topic not found")
        return place.room

    async def _stamp_delivery(
        self, card: AcceptCard, topic: Topic, *, by: str | None, at: datetime | None
    ) -> None:
        """Mark what was delivered — the THREAD when the card is a thread's.

        交付完成 ≠ 这个地方结束 (#442 decision 1): this is the delivery marker and
        nothing else; `status` is untouched and putting a place away stays a
        person's decision.

        Which row carries it matters: a room accumulates work forever, so
        stamping the room would say "this room was delivered" every time any one
        piece of work in it was, and the next reader cannot tell which. Passing
        `by=None` clears it (撤回采纳).
        """
        target: Topic | Task = topic
        if card.task_id is not None:
            thread = await TaskService(self._session).get(card.task_id)
            if thread is not None:
                target = thread
        target.accepted_by = by
        target.accepted_at = at

    async def _card_or_404(self, card_id: uuid.UUID) -> AcceptCard:
        card = await self._repo.get(card_id)
        if card is None:
            raise NotFoundError("Accept card not found")
        return card

    async def _reviewer_or_project_default(
        self,
        project: Project | None,
        reviewer_handle: str | None,
        *,
        from_work: list[Task] | None = None,
    ) -> str:
        """谁验收：显式指定 > 这批活派出去时定的人 > 项目默认验收人 (#718 设置表).

        显式指定优先 —— the person filing knows something neither the work nor
        the setting can: which change THIS is, and who understands that part of
        the code. A default that overrode them would make the setting a ceiling
        instead of a floor.

        Then the work's own reviewer, because that is who this work was HANDED
        TO when it was dispatched (`Task.reviewer_handle`, resolved from the
        same setting at that moment). Reading the project setting again instead
        would silently re-route work dispatched under an older policy.

        Work that disagrees is refused rather than resolved. Two threads handed
        to two different people, delivered in one batch, is a real question
        about who gets to say this may land, and any answer this code invented —
        the first, the newest, the most common — would route somebody's review
        to somebody else and look correct doing it.

        Nothing anywhere is an error rather than a guess, for the same reason:
        routing to the project owner, the room's owner, or whoever accepted last
        would each hand a real delivery to someone who never agreed to look at
        it, and the card would sit there looking correctly routed.
        """
        from app.domain.project.protection import branch_protection_of

        explicit = (reviewer_handle or "").strip()
        if explicit:
            return explicit
        handed_to = sorted(
            {t.reviewer_handle for t in (from_work or []) if t.reviewer_handle}
        )
        if len(handed_to) > 1:
            raise ValidationError(
                "这批活派出去时定的验收人不是同一个人（"
                + "、".join(handed_to)
                + "），平台不替你选。递卡时点名一个。"
            )
        if handed_to:
            return handed_to[0]
        default = branch_protection_of(project).default_reviewer
        if default:
            return default
        raise ValidationError(
            "没说验收卡递给谁，项目也没有设默认验收人。"
            "点名一个人（`cheese_members` 查准确 handle），"
            "或者在项目设置的「分支保护 → 任务默认 reviewer」里填一个。"
        )

    async def _require_reviewer_in_room(
        self, topic: Topic, handle: str, admits: ReviewerAdmission
    ) -> None:
        """一张卡只递给这道门会放进来的人。

        递卡是「这次改动交给谁看」的一次指派，而采纳那张卡的门问的是同一句话
        （`accept.py` 的 `_card_actor` → `resolver.authorize_topic`）。两个问题各答
        各的，结果就是卡递得出去、却谁也采纳不了：`/reassign` 把请求体里的人直接
        写进 `reviewer_handle`，点名一个不在这个话题里的人，就造出一张死卡 —— 卡面
        看着一切正常，走到采纳那一步才 403。

        判据不另造：`admits` 是路由注入的那道门自己（`ActorResolver
        .topic_admits_handle`，与 `authorize_topic` 同一份规则、同一个读点），这里
        判的是**目标人**而不是调用者。默认路由选出来的人一样要过这一关：项目的默认
        验收人是一个设置，它记的是「谁验收」，不是「谁是成员」，而派活时按它写下的
        `Task.reviewer_handle` 也照抄自同一个设置 —— 三条来源都从这里过。

        不进这一域的理由：这道判据要问房间是不是私聊，而「谁在什么情况下问这个
        布尔」在一个仓库里只该有一份声明（`tests/unit/test_is_private_read_points.py`
        是那道棘轮）。在这里照抄一遍读法，就是同一件事的第二份声明。

        挂的是同一只开关（`authz_enforce_topic_access`）：开关关掉时采纳那道门本来
        就不问成员资格，此时按房间名册拦下递卡只会让一个配置里合法的人递不出去。
        """
        if not settings.authz_enforce_topic_access:
            return
        if await admits(topic, handle):
            return
        raise ForbiddenError(say("reviewerNotInTopic", handle=handle))

    async def create_card(
        self,
        *,
        topic_id: uuid.UUID,
        task_id: uuid.UUID,
        reviewer_handle: str | None = None,
        routing_reason: str = "",
        change_subject: str | None = None,
        change_body: str | None = None,
        artifact: str | None = None,
        new_artifact: str | None = None,
        about: str | None = None,
        deliver: str | None = None,
        deliver_url: str | None = None,
        admits_reviewer: ReviewerAdmission,
    ) -> AcceptCard:
        topic = await self._topic_or_404(topic_id)
        task = await TaskService(self._session).require_in_room(topic_id, task_id)
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能再提交验收")
        if task.status != TaskStatus.open or not task.branch_name:
            raise ValidationError("这条任务已结束或没有工作分支")
        subject = (change_subject or "").strip()
        if not subject:
            raise ValidationError(_MISSING_SUBJECT)
        try:
            subject = commit_message.check_subject(subject)
        except commit_message.InvalidSubject as exc:
            raise ValidationError(str(exc)) from exc
        # 这次交付更新了哪一项产物 (#1085 结论三)。先验参数、后落行：一张递不上
        # 去的卡（分支没提交、已经有一张未决的卡）不该在清单上留下一项。
        #
        # 交出去的是什么，决定了产物还要不要声明 —— 所以先问这一句。合并交出去的
        # 是项目那个仓库，一个项目只有一个，平台自己认得出；只有交文件、交地址才
        # 真的有得选。
        _one_deliverable(deliver, deliver_url)
        hands_over_repository = (
            not (deliver or "").strip() and not (deliver_url or "").strip()
        )
        if hands_over_repository:
            _no_artifact_action(artifact, new_artifact, about)
        else:
            _one_artifact_action(artifact, new_artifact)
        about = artifacts.clean_about(about, subject=subject)
        existing = await self._repo.list_for_task(task.id)
        blocking = next(
            (c for c in existing if c.status in _CARD_BLOCKS_NEW_CARD), None
        )
        if blocking is not None:
            raise ValidationError(_BLOCKED_BY_CARD_MESSAGES[blocking.status])
        from app.domain.repository.forge_files import ProjectFiles

        comparison = await ProjectFiles(
            self._session, task.project_id, task.id
        ).comparison()
        if not comparison or not comparison.get("total_commits"):
            raise ValidationError(f"任务分支 {task.branch_name} 没有可交付的提交")
        reviewer_handle = await self._reviewer_or_project_default(
            await self._projects.get(topic.project_id),
            reviewer_handle,
            from_work=[task],
        )
        await self._require_reviewer_in_room(topic, reviewer_handle, admits_reviewer)
        # 这一版交出去的那一份，在它还存在的时候读下来 (#1085 结论五)。构建产物只
        # 活在这一轮的工作目录里，采纳时那个目录可能已经不在了 —— 建卡是唯一抓得
        # 住它的时刻。读在声明之前：路径写错这张卡递不上去，而一张递不上去的卡不该
        # 在清单上留下一项。
        handed_over = (deliver or "").strip()
        snapshot = (
            await _read_deliverable(
                self._session, task.project_id, task.id, handed_over
            )
            if handed_over
            else None
        )
        is_new = bool((new_artifact or "").strip())
        if hands_over_repository:
            project = await self._projects.get(topic.project_id)
            declared = await artifacts.for_repository(
                self._session,
                project_id=topic.project_id,
                project_name=project.name if project else "项目",
            )
        elif is_new:
            declared = await artifacts.claim(
                self._session,
                project_id=topic.project_id,
                name=new_artifact or "",
                about=about,
            )
        else:
            declared = await artifacts.reuse(
                self._session, project_id=topic.project_id, artifact_id=artifact or ""
            )
            await artifacts.describe(self._session, declared, about=about)
        card = await self._repo.add(
            topic_id=topic_id,
            task_id=task.id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=AcceptStatus.pending,
            change_subject=subject,
            change_body=change_body or None,
            delivered_task_ids=[task.id],
            artifact_id=declared.id,
            # 和上面那个「要不要声明产物」问的是同一句话，所以答案从同一个地方来：
            # 两处各算一次的话，有一天它们会对不上，而对不上的那一天没有任何报错。
            deliverable_kind=(
                DeliverableKind.merge
                if hands_over_repository
                else DeliverableKind.file
                if snapshot is not None
                else DeliverableKind.link
            ),
            deliverable_name=snapshot[0] if snapshot else None,
            deliverable_url=(deliver_url or "").strip() or None,
        )
        if snapshot is not None:
            await asyncio.to_thread(
                library.write_artifact_snapshot,
                task.project_id,
                card.id,
                snapshot[0],
                snapshot[1],
            )
        card.pr_number, card.pr_url = task.pr_number, task.pr_url
        await self._announce_filed(topic, card, task, artifact=declared.name)
        if is_new:
            await self._announce_new_artifact(topic, declared.name)
        await self._warn_about_a_second_pending_migration(topic, task.id)
        return card

    async def _announce_filed(
        self, topic: Topic, card: AcceptCard, task: Task, *, artifact: str
    ) -> None:
        return await _announce_filed(
            self._session, topic, card, task, artifact=artifact
        )

    async def _announce_new_artifact(self, topic: Topic, name: str) -> None:
        return await _announce_new_artifact(self._session, topic, name)

    async def _warn_about_a_second_pending_migration(
        self, topic: Topic, task_id: uuid.UUID
    ) -> None:
        """两张未决卡各带一个新迁移 → 在房间里说一声 (#314).

        The narrow, clean half of "two rooms doing the same work". Two branches
        editing the same existing file is ordinary — parent and child legitimately
        touch one file each. Two branches each CREATING an alembic revision is
        not: at best it forks the chain the moment both land (#312), at worst the
        two are the same feature implemented twice, which is what happened on
        2026-08-11 — `topics.progress` (a column) and `topic_progress` (a table),
        two incompatible data models, each with a green card.

        Deliberately a notice, not a block. The judgement "these two are the same
        work" needs a human; what a machine can contribute is making sure the
        human is looking at the moment there is something to look at. Both cards
        being individually green is exactly the state that hides this.

        Best-effort throughout: a git read that fails, or a room that won't take
        the message, must never stop someone filing a card.
        """
        from app.domain.repository.forge_files import ProjectFiles

        async def migrations(task_id: uuid.UUID) -> list[str]:
            try:
                comparison = await ProjectFiles(
                    self._session, topic.project_id, task_id
                ).comparison()
                added = [
                    entry["filename"]
                    for entry in (comparison or {}).get("files", [])
                    if entry.get("status") == "added"
                ]
            except Exception:  # noqa: BLE001 — a diagnostic must not break 递卡
                return []
            return [p for p in added if _ALEMBIC_VERSIONS_DIR in p]

        mine = await migrations(task_id)
        if not mine:
            return
        others = await self._repo.list_live_in_project(
            topic.project_id, statuses=_CARD_BLOCKS_NEW_CARD
        )
        collisions = [
            other
            for other in others
            if other.task_id is not None
            and other.task_id != task_id
            and await migrations(other.task_id)
        ]
        if not collisions:
            return
        rooms = []
        for other in collisions:
            sibling = await self._topics.get(other.topic_id)
            rooms.append(f"「{sibling.title}」" if sibling else str(other.topic_id))
        self._notify_merge_result(
            topic,
            say("migrationCollision"),
            meta=notice(
                EVENT_MIGRATION_COLLISION,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=say("migrationCollisionDetail", rooms="、".join(rooms)),
                detail_label=say("labelReason"),
            ),
        )

    async def project_id_for_topic(self, topic_id: uuid.UUID) -> uuid.UUID:
        """The topic's project id — what the PR-publish dispatch needs to resolve
        the App installation and upstream (采纳即合并 #296)."""
        topic = await self._topic_or_404(topic_id)
        return topic.project_id

    async def mark_gate_started(self, *, card_id: uuid.UUID) -> AcceptCard:
        """闸门开跑打点 (孤儿卡, 2026-08-11). Idempotent-ish and deliberately
        forgiving: if the card already left `pending_gate` (swept as abandoned,
        or voided by a human) this is a no-op rather than an error — a
        diagnostic timestamp must never resurrect a closed card, and it must
        never be the thing that fails a check that is about to run anyway."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending_gate:
            return card
        card.gate_started_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def finish_gate(
        self, *, card_id: uuid.UUID, outcome: GateOutcome, output_tail: str
    ) -> AcceptCard:
        """Settle a pending_gate card: 绿 → pending (卡片这才递到验收人手上),
        红 → gate_failed (卡片作废，芝士被 nudge 去修), 没跑成 → gate_blocked
        (同样不递出去，但检查对代码没有结论，别说成"未通过")."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending_gate:
            raise ValidationError("只有等待检查的验收卡能记录检查结果")
        card.gate_output = output_tail
        if outcome == GateOutcome.passed:
            card.status = AcceptStatus.pending
            card.gate_passed_at = datetime.now(UTC)
        elif outcome == GateOutcome.blocked:
            card.status = AcceptStatus.gate_blocked
        else:
            card.status = AcceptStatus.gate_failed
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def list_for_topic(self, topic_id: uuid.UUID) -> tuple[list[AcceptCard], int]:
        cards = await self._repo.list_for_topic(topic_id)
        return cards, len(cards)

    async def open_pr_card_ids(
        self, project_id: uuid.UUID | None = None
    ) -> list[uuid.UUID]:
        """Pending PR cards and returned batches awaiting external merge.

        只回 id 不回对象：调度器一张卡一个事务，跨事务复用 ORM 对象拿到的是过期状态。
        「哪张卡算在等」是本领域的知识，所以判断留在这里，而不是让调度器自己去查
        ``AcceptCardRepository``。

        孤儿卡修复 (2026-08-10) 的那条判据也在这里面：已归档话题上的卡不算——
        跟进它们等于拿 GitHub 凭据去动没人跟的活儿。
        """
        cards = await self._repo.list_awaiting_merge_on_active_topics(project_id)
        return [c.id for c in cards]

    async def anybody_still_waiting(self, place_ids: list[uuid.UUID]) -> bool:
        """这些地点里，还有没有一张卡等着人决议 —— 收起/归档前必须问的那一句。

        归档会把非终态的卡当场收敛掉（`review/archive.py`），所以任何**平台自己
        发起**的归档（结论卡默认采信就是）都得先问这一句，否则会把一张验收人还
        没看见的卡作废掉。判据（哪些状态算"还等着"）留在本领域，调用方不该自己
        去数状态——这正是 `close_cards_for_archived_topic` 收敛的那一张表。

        一组而不是一个：归档一个房间会把它里面的活一起收起，那些活的卡同样会
        被收掉，所以它们同样构成「先别动手」的理由。
        """
        return bool(
            await self._repo.list_live_for_places(
                place_ids, statuses=archive.OPEN_CARD_STATUSES
            )
        )

    async def latest_decision_at(self, place_ids: list[uuid.UUID]) -> datetime | None:
        """这些地点上最后一张卡是什么时候有结果的 —— None = 从来没有过卡。

        给"卡决议之后留一个重新递卡的窗口"用：驳回的意思是回去改了再来，而归档
        话题递不出新卡，所以窗口从这一刻起算。
        """
        return await self._repo.latest_decision_at(place_ids)

    async def reviewer_topic_ids(
        self, topic_ids: list[uuid.UUID], reviewer_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{话题: 这上面还有没有一张卡在等这个人} —— 只有点过名给他的话题会出现。

        给话题列表的「与我的相关性」用，一次查完：**在不在 key 里**是「这话题
        点过我的名」（采纳完也还算我的事），**value** 是「现在就等我动手」。
        """
        return await self._repo.reviewer_topic_ids(topic_ids, reviewer_handle)

    async def describe(self, card: AcceptCard) -> dict:
        """AcceptCardOut payload enriched with the vote state (approvals live in
        their own table; the requirement is a project setting)."""
        data = AcceptCardOut.model_validate(card).model_dump(mode="json")
        # 「这条 note 有多严重」是它的状态码算出来的（domain/review/notes.py），
        # 随卡下发。浏览器过去自己按 emoji 开头猜，而那份硬编码列表漏掉了后来加
        # 的 `🌿` 和 `🚪`——两条都是「停住了」，却和「还在等」画成同一个颜色。
        level = notes.note_level(card.note_code, card.note)
        data["note_level"] = level.value if level else None
        # 这次交付更新的是哪一项产物。卡面上要有它：验收的人正在决定这一版要不要
        # 成为《报告》的当前版本，而卡上别的字段一个都没说出这件事。
        declared = (
            await artifacts.summary(self._session, card.artifact_id)
            if card.artifact_id is not None
            else None
        )
        data["artifact"] = (
            None
            if declared is None
            else {
                "id": str(declared.id),
                "name": declared.name,
                # 这张卡自己是第几版。`ArtifactSummary.version` 数的是已采纳的卡
                # （`artifacts._claims`），所以还没采纳的这一张要自己加上一版：人
                # 正在决定的是「这一版要不要成为《报告》的当前版本」，卡上写着前
                # 一版的号码等于把他要定的那件事写错。
                "version": declared.version
                + (0 if card.status is AcceptStatus.accepted else 1),
            }
        )
        # 这一版交出去的是什么。卡面上要有它，因为验收的人要审的正是这一份：文件
        # 在递卡那一刻就落下来了，所以他能在点采纳之前打开它。
        data["deliverable"] = (
            None
            if card.deliverable_kind is None
            else {
                "kind": card.deliverable_kind.value,
                "filename": card.deliverable_name,
                "url": card.deliverable_url,
            }
        )
        data["approvals"] = await self._repo.list_approver_handles(card.id)
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        try:
            forge: forge_mod.Forge | None = await self._resolve_forge(
                topic.project_id, card=card
            )
        except ValidationError:
            # 读一张卡不是挑一条车道。采纳那一侧照旧 fail-closed（#362）：读不出
            # 事实就拒绝，绝不摸黑合一次。但这条读路径上同样的失败过去会把整个
            # 卡列表端点打成 422 —— 一个项目的 git 出问题，所有项目的卡都看不
            # 了。这里改成在卡面上如实说「暂时读不出」，失败一点没被盖住（I19），
            # 只是不再连累别的卡。
            forge = None
        caps = forge.capabilities if forge is not None else None
        # 托管方身份在卡生成的那一刻就在卡上（I23）：这一份是唯一的一份，卡片渲染
        # 「托管方是谁」只从这里取，人点完采纳之后不再补写任何一条 note。
        #
        # 「项目有没有绑外部仓库」不在这里（不变量 I21②）。它是一个能力位，由
        # `PlatformForge` 读去决定卡上说哪句话，说完就已经在 `declaration` 里；
        # 再发一遍，就是把那个布尔摆到产品面前请它自己分叉，而这正是按能力分派
        # 要取消的那件事。
        data["forge"] = (
            {
                "kind": forge.kind.value,
                "reports_checks": caps.reports_checks,
                "hosts_proposals": caps.hosts_proposals,
                "can_write_remote": caps.can_write_remote,
                "pushes_to_external_remote": caps.pushes_to_external_remote,
                "identity": caps.identity.value,
                "declaration": forge.declaration,
            }
            if forge is not None and caps is not None
            else {
                "kind": forge_mod.FORGE_KIND_UNKNOWN,
                # 一位都不敢说是，因为一位都没读出来。界面上每一处「这个托管方能
                # 做什么」的判断因此都收敛到最保守的那一边。
                "reports_checks": False,
                "hosts_proposals": False,
                "can_write_remote": False,
                "pushes_to_external_remote": False,
                "identity": forge_mod.ForgeIdentity.platform.value,
                "declaration": forge_mod.FORGE_UNKNOWN_DECLARATION,
            }
        )
        data["approvals_required"] = approvals_required_of(project)
        # External checks stay unknown until the PR has a mirrored state.
        # Local acceptance has no checks; only a recorded merge conflict blocks it.
        if caps is None:
            # 按钮灰着，理由就写在卡上：后端这会儿真去采纳也会拒（同一个失败），
            # 所以闸门和采纳还是同一条线。
            data["merge_state"] = {
                "state": "unknown",
                "who": "platform",
                "reasons": [
                    {
                        "kind": "no_signal",
                        "checks": [],
                        "detail": forge_mod.FORGE_UNKNOWN_DECLARATION,
                    }
                ],
                "head_sha": None,
                "checked_at": None,
                "since": None,
            }
        elif caps.reports_checks:
            mirror = card.merge_state if isinstance(card.merge_state, dict) else None
            data["merge_state"] = mirror or {
                "state": "unknown",
                "who": "platform",
                "reasons": [
                    {
                        "kind": "no_signal",
                        "checks": [],
                        "detail": (
                            "平台还没看过这个 PR 的合并态"
                            if card.pr_number is not None
                            else "PR 尚未创建，检查状态未知"
                        ),
                    }
                ],
                "head_sha": card.pr_head_sha,
                "checked_at": None,
                "since": None,
            }
        else:
            local = merge_state.local_merge_state(
                conflicts_with_trunk=(card.status == AcceptStatus.conflict)
            )
            data["merge_state"] = {
                "state": local.state,
                "who": "human",
                "reasons": [
                    {"kind": r.kind, "checks": list(r.checks), "detail": r.detail}
                    for r in local.reasons
                ],
                "head_sha": None,
                "checked_at": None,
                "since": None,
            }
        # 绿了自动合 (#718)：开了 auto_merge_allowed 的项目，验收人可以在
        # BLOCKED / BEHIND 时布防。
        from app.domain.project.protection import branch_protection_of

        data["auto_merge"] = {
            "allowed": (
                caps is not None
                and branch_protection_of(project).auto_merge_allowed
                and caps.reports_checks
                and card.pr_number is not None
            ),
            "armed_by": card.auto_merge_armed_by,
            "armed_at": (
                card.auto_merge_armed_at.isoformat()
                if card.auto_merge_armed_at
                else None
            ),
        }
        # 快检说了什么。Gates nothing — the PR's real CI decides (#296) — but a
        # red one has to be in front of the person about to accept. A check
        # whose result goes nowhere is a check nobody runs.
        tree = (
            await TaskService(self._session).get(card.task_id)
            if card.task_id is not None
            else None
        )
        data["quick_check"] = (
            None
            if tree is None or tree.last_check_at is None
            else {
                "ok": tree.last_check_ok,
                "at": tree.last_check_at.isoformat(),
                "detail": tree.last_check_detail,
            }
        )
        return data

    async def _enforce_protocol(self, topic: Topic, decided_by: str) -> None:
        """机构协议 (spec §4.2/§4.4): a 赛题 may require a mentor to accept a
        particular topic, and a project created from that 赛题 accepted the terms.

        Reads them from the 赛题's 项目集 (with the 赛题's own override — #370
        option (c)), reached through `project.external_task_id`. That is the link
        the 赛题 page's 「从这道赛题创建项目」 button writes; the cheesex
        `project_task_links` chain it replaced pointed at a 题目 hierarchy that
        had no way to be created.
        """
        from app.domain.task.models import Task
        from app.domain.task.teaching import protocol_for_task

        project = await self._projects.get(topic.project_id)
        task_id = getattr(project, "external_task_id", None) if project else None
        if not task_id:
            return
        task = await self._session.get(Task, task_id)
        if task is None:
            return
        protocol = await protocol_for_task(self._session, task)
        if not protocol.mentor_required_for(topic.title):
            return
        # The condition asks for someone from outside the team to sign off: an
        # external member of this project.
        from app.domain.membership.roster import roster

        outside = {
            m.handle
            for m in await roster(self._session, topic.project_id)
            if m.source == "external"
        }
        if decided_by not in outside:
            raise ValidationError(say("protocolExternalAccept"))

    async def reassign(
        self,
        *,
        card_id: uuid.UUID,
        reviewer_handle: str | None = None,
        reason: str = "",
        admits_reviewer: ReviewerAdmission,
    ) -> AcceptCard:
        """改验收人 (spec §4.4): anyone can re-route a pending accept card to a
        different reviewer — or, naming nobody, back to the project's default
        one, which is the same ladder 递卡 climbs."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError(say("reviewEndedNoReassign"))
        topic = await self._topic_or_404(card.topic_id)
        reviewer = await self._reviewer_or_project_default(
            await self._projects.get(topic.project_id), reviewer_handle
        )
        await self._require_reviewer_in_room(topic, reviewer, admits_reviewer)
        card.reviewer_handle = reviewer
        if reason:
            card.routing_reason = reason
        await self._session.flush()
        await self._session.refresh(card)
        return card

    def _forbid_ai(self, project: Project | None, handle: str, action: str) -> None:
        """Hard rule (spec §4.4): in collaborative mode AI cannot accept (or
        vote for) its own work — a human must. Autonomous mode allows it.

        Matches the whole 芝士 handle namespace, not the bare ``cheese`` string:
        每个话题的分身 acts under its own ``cheese-<topic hex>`` handle, and an
        exact-string rule would have let any 分身 walk straight through this."""
        if (
            project is not None
            and project.ai_mode == AiMode.collaborative
            and looks_like_agent_handle(handle)
        ):
            raise ValidationError(f"AI 不能{action}自己的改动，需要由人操作")

    async def approve(self, *, card_id: uuid.UUID, approver_handle: str) -> AcceptCard:
        """主分支保护 (spec §4.4): record one vote toward this card's accept.
        Idempotent per (card, approver); AI cannot vote in collaborative mode."""
        card = await self._card_or_404(card_id)
        # Votable while the card is still live (incl. behind the gate / in a
        # merge-conflict retry); decided or gate-failed cards are closed.
        if card.status not in (
            AcceptStatus.pending,
            AcceptStatus.pending_gate,
            AcceptStatus.conflict,
        ):
            raise ValidationError(say("reviewEndedNoApprove"))
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, approver_handle, "批准")
        await self._repo.add_approval(card_id, approver_handle)
        return card

    async def arm_auto_merge(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        enabled: bool,
        head_sha: str | None = None,
    ) -> AcceptCard:
        """绿了自动合 (#718)，GitHub auto-merge 的对应物。

        布防不是决议：卡留在 pending，规则满足时轮询器以布防人的名义合并（布防
        人的那票算进批准数）。新提交作废采纳（dismiss_stale）同样解除布防 ——
        机器合的永远是布防人看过的那份，或者不合。

        谁能布防：这张卡的验收人（跟采纳同一个人 —— 布防就是「提前采纳」）。
        项目要先开 `auto_merge_allowed`。解除给同一个人加布防人自己。

        布防等于提前采纳，所以它跟采纳一样要声明「我看的是哪一版」
        （`_seen_head_or_refresh`）：屏幕上那版已经过时、或者卡面还没显示过任何
        版本的话，布防就是替一段没人看过的代码预先按下同意。
        **这跟合并态是不是 blocked 无关**——这个开关本来就只在
        BLOCKED / BEHIND 出现，规则没满足正是布防的前提，拒的理由只有「旧 SHA」
        一个。解除布防不需要看过任何版本：撤销自己的同意什么都不会合并。
        """
        from app.domain.project.protection import branch_protection_of

        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending or card.pr_number is None:
            raise ValidationError(say("autoMergeNeedsOpenPr"))
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "设置自动合并")
        if enabled and not branch_protection_of(project).auto_merge_allowed:
            raise ValidationError(say("autoMergeNotEnabled"))
        allowed = {card.reviewer_handle}
        if card.auto_merge_armed_by:
            allowed.add(card.auto_merge_armed_by)
        if decided_by not in allowed:
            raise ForbiddenError(say("autoMergeReviewerOnly"))
        if enabled:
            await self._seen_head_or_refresh(
                card, topic, head_sha, say("verbArmAutoMerge")
            )
            card.auto_merge_armed_by = decided_by
            card.auto_merge_armed_at = datetime.now(UTC)
        else:
            card.auto_merge_armed_by = None
            card.auto_merge_armed_at = None
        await self._session.flush()
        await self._session.refresh(card)
        return card

    def _notify_merge_result(
        self, topic: Topic, content: str, *, meta: dict | None = None
    ) -> None:
        return _notify_merge_result(async_session_factory, topic, content, meta=meta)

    @staticmethod
    def _seen_head(card: AcceptCard, head_sha: str | None, action: str) -> str | None:
        """合的是**人看到的**那个 commit：核对请求声明的 head，并把它交回去用。

        `head_sha` 是前端渲染这张卡时卡面上的 head（`merge_state.head_sha`）。
        它必须仍然是卡当前的 `pr_head_sha`——不一致意味着轮询器在渲染与点击之间
        把卡刷到了新 commit，而屏幕上那份还是旧的：点下去合的会是一段**没有人
        看过**的代码 (`advance_pr_card` 每 60s 跑一次，这个窗口天天都在)。
        `dismiss_stale` 保护不了它，那条只清批准票，而采纳本身就是一票。

        None 与 None 相等只在**没有 GitHub PR 的那条 lane 上**成立：卡面显示的
        就是「没有 sha」，合的是 diff 视图展示的那条分支本身，没有哪一版可以过
        时。骑着 PR 的卡不是这样——那种情况下「卡上没有 sha」意味着还不知道要合
        哪个 commit，`_seen_head_or_refresh` 在进这里之前就把它拦下了。反过来，
        卡上有 head 而请求什么都不带（老客户端）就是不相等，照样拒——不带 sha
        不是绕过这道闸的方式。

        返回值是**请求带的**那个 sha，调用方拿它去调合并 API：GitHub 的 sha
        参数会在点击瞬间再拦一次漂移（409）。
        """
        seen = (head_sha or "").strip() or None
        if seen != (card.pr_head_sha or None):
            raise ValidationError(_stale_view_message(card.pr_number, action))
        return seen

    async def _seen_head_or_refresh(
        self, card: AcceptCard, topic: Topic, head_sha: str | None, action: str
    ) -> str | None:
        """`_seen_head`，外加「PR 上的卡必须先有过一个展示出来的 head」这道闸。

        骑着 PR 的卡在 `pr_head_sha` 还是空的时候（刚递上来、轮询器 60s 才跑一
        次），卡面从未写出过任何 sha。此时放行等于让下游拿**现读 GitHub** 的
        head 去合，而那个 commit 从来没有在任何界面上显示过——采纳、人工放行、
        自动合布防三个入口都会走到那里，是同一个洞。

        所以这不是「没有版本可以过时」，是「还不知道要合什么」：把当前 head 镜像
        到卡上，让人重新看一眼，看过的那一版才谈得上被采纳。没有 PR 的本地 lane
        不进这道闸——那条路合的就是 diff 视图展示的分支本身。
        """
        if card.pr_number is not None and not (card.pr_head_sha or "").strip():
            await self._refresh_never_shown_card(card, topic, action)
        return self._seen_head(card, head_sha, action)

    async def _refresh_never_shown_card(
        self, card: AcceptCard, topic: Topic, action: str
    ) -> NoReturn:
        forge = await self._resolve_forge(topic.project_id, card=card)
        await forge.refresh_unseen_head(self, card, topic, action)

    async def _refresh_github_unseen_head(
        self, card: AcceptCard, topic: Topic, action: str
    ) -> NoReturn:
        """把 PR 当前的 head 镜像到一张从没显示过 sha 的卡上，然后要求重看。

        读 head 是尽力而为：读不到就刷新一张没有 head 的卡（轮询器下一跳会补
        上），但**绝不**因此放行——放行的前提是人看过某一版，读不到 head 恰恰
        说明没有任何一版可看。"""

        number = card.pr_number
        assert number is not None  # PR lane only; the caller checked
        live = ""
        creds, why = await self._app_credentials(topic)
        if creds is None:
            logger.warning("card %s: no credentials to read PR head (%s)", card.id, why)
        else:
            try:
                owner, repo = await self._pr_repo_of(card, topic)
                client = await self._status_client(topic.project_id)
                live = await client.pull_request_head_sha(
                    owner=owner, repo=repo, number=number, token=creds.read
                )
            except Exception as exc:  # noqa: BLE001 — refuse anyway; refresh with what we have
                logger.warning("card %s: first-look head read failed: %s", card.id, exc)
        await self._refresh_stale_card(
            card,
            topic,
            live_head=live,
            action=action,
            headline=(f"PR #{number} 还没有显示过任何版本，已刷新到最新版本"),
        )
        raise ValidationError(_never_shown_message(number, action))

    async def accept(
        self, *, card_id: uuid.UUID, decided_by: str, head_sha: str | None = None
    ) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # 机器闸门 (eval C2): the card isn't in the reviewer's hands yet / died.
        if card.status == AcceptStatus.pending_gate:
            raise ValidationError(say("checksStillRunning"))
        if card.status == AcceptStatus.gate_failed:
            raise ValidationError(say("checksFailedResubmit"))
        if card.status == AcceptStatus.gate_blocked:
            raise ValidationError(say("checksDidNotRun"))
        # pending → first attempt; conflict → retry after 芝士 resolved.
        if card.status not in (AcceptStatus.pending, AcceptStatus.conflict):
            raise ValidationError(say("reviewEndedNoAccept"))
        # 递给某个具体的人 (spec §4.4): only the routed reviewer may accept —
        # decided_by is the caller's verified actor handle, never body-trusted.
        if decided_by != card.reviewer_handle:
            raise ForbiddenError(say("acceptReviewerOnly"))
        topic = await self._topic_or_404(card.topic_id)
        # 合的是人看到的那个 commit：屏幕上那一版还在，才谈得上采纳它。
        seen_head = await self._seen_head_or_refresh(
            card, topic, head_sha, say("verbAccept")
        )

        # 归档会连带终结这个话题上还没决议的卡 (review/archive.py)，所以这里通常
        # 走不到；留着是为了兜住"归档与采纳同时发生"的竞态。重复采纳本身由上面的
        # 卡状态闸门挡（一张卡只能 accepted 一次），不再依赖话题被归档。
        if topic.status == TopicStatus.archived:
            raise ValidationError(say("topicArchivedNoAccept"))
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "采纳")

        # Institution protocol from linked Task Templates (spec §4.2).
        await self._enforce_protocol(topic, decided_by)

        # 主分支保护 (spec §4.4): the accept itself counts as the accepter's
        # vote (default requirement of 1 ⇒ 现行为不变); short of votes the whole
        # transaction rolls back and nothing merges.
        approvers = await self._repo.list_approver_handles(card_id)
        # Count this decision as a vote without persisting it yet. A merge can
        # fail outside SQLAlchemy; delaying the write keeps even callers that
        # catch ValidationError from accidentally committing a failed accept.
        votes = len(set(approvers) | {decided_by})
        required = approvals_required_of(project)
        if votes < required:
            raise ValidationError(
                say("approvalsShort", left=required - votes, n=votes, of=required)
            )

        forge = await self._resolve_forge(topic.project_id, card=card)
        return await forge.accept(self, card, topic, decided_by, seen_head=seen_head)

    async def _accept_github(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str | None,
    ) -> AcceptCard:
        """GitHub proposals never fall back to a local merge after a failure."""
        if card.pr_number is None:
            await self._publish_pr_for_accept(card, topic)
            if card.pr_number is not None:
                # PR 是这一秒才开出来的：卡面在此之前没有、现在也还没有一个
                # 被展示过的 head。「人看的是同一条分支」不等于「同一个
                # commit」—— 浏览器从来没有声明过它渲染的 diff 是哪个 sha，
                # 而分身边干边推是常态。所以这次不合，PR 留着（开 PR 是有价
                # 值的副作用，下次采纳就有 head 可比），head 镜像上卡，人重
                # 新看过再点。
                await self._refresh_never_shown_card(card, topic, say("verbAccept"))
        if card.pr_number is not None:
            assert seen_head is not None  # the guard above rules None out
            return await self._merge_pr_for_accept(
                card, topic, decided_by, seen_head=seen_head
            )
        work = (
            await TaskService(self._session).get(card.task_id) if card.task_id else None
        )
        if (card.change_subject or "").strip() or (work and work.branch_name):
            await self._stop_accept_no_branch(card, topic)
        # A legacy discussion without a branch has no change to merge.
        return await self._accept_discussion(card, topic, decided_by)

    async def _accept_discussion(
        self, card: AcceptCard, topic: Topic, decided_by: str
    ) -> AcceptCard:
        """Record acceptance of a discussion with no code delivery."""
        await self._repo.add_approval(card.id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now
        notes.clear(card)
        await self._stamp_delivery(card, topic, by=decided_by, at=now)
        await self._mark_task_merged(card)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def _pr_repo_of(self, card: AcceptCard, topic: Topic) -> tuple[str, str]:
        """(owner, repo) the card's PR lives in — from the card when recorded,
        else resolved from the project's upstream and backfilled onto the card
        (pr_publish records only pr_number/pr_url at filing time)."""
        from urllib.parse import urlsplit

        from app.domain.project.forge import binding_for_project

        binding = await binding_for_project(topic.project_id, self._session)
        if binding is None:
            raise ValidationError(say("reviewNoRepository"))
        if card.pr_repo and card.pr_repo != binding.repo:
            raise ValidationError(say("reviewRepositoryChanged"))
        if card.pr_url and urlsplit(card.pr_url).netloc != urlsplit(binding.url).netloc:
            raise ValidationError(say("reviewForgeMismatch"))
        card.pr_repo = binding.repo
        owner, repo = binding.repo.split("/", 1)
        return owner, repo

    async def _status_client(self, project_id: uuid.UUID):
        from app.domain.project.forge import status_client

        return await status_client(project_id, self._session)

    async def _dependency_block_reason(
        self, card: AcceptCard, status: PullRequestStatus
    ) -> str | None:
        if card.task_id is None:
            return None
        task = await TaskService(self._session).get(card.task_id)
        if task is None or task.base_task_id is None:
            return None
        parent = await TaskService(self._session).get(task.base_task_id)
        if parent is None or not status.base_ref:
            return say("dependencyUnconfirmed")
        if status.base_ref == parent.branch_name:
            return say("dependencyAwaitsParent", title=parent.title)
        if status.base_ref != task.base_branch:
            return say("dependencyBaseChanged")
        return None

    async def _sync_dependency_target(
        self,
        card: AcceptCard,
        status: PullRequestStatus,
    ) -> bool:
        """Record a native PR retarget and invalidate reviews of its previous diff."""
        if card.task_id is None or not status.base_ref:
            return False
        task = await TaskService(self._session).get(card.task_id)
        return await self._sync_task_dependency_target(task, status)

    async def _sync_task_dependency_target(
        self, task, status: PullRequestStatus, *, drop_dependency: bool = False
    ) -> bool:
        from app.domain.project.forge import default_branch

        if (
            task is None
            or task.base_task_id is None
            or (task.base_branch == status.base_ref and not drop_dependency)
        ):
            return False
        if status.base_ref != await default_branch(task.project_id, self._session):
            return False
        task.base_branch = status.base_ref
        # The agent explicitly chose a different target on the forge. A later
        # parent-close sweep must not overwrite that decision.
        task.base_task_id = None
        for related in await self._repo.list_for_task(task.id):
            if related.status in (AcceptStatus.pending, AcceptStatus.conflict):
                await self._repo.clear_approvals(related.id)
                related.auto_merge_armed_by = None
                related.auto_merge_armed_at = None
                related.pr_head_sha = None
                related.merge_state = None
        await self._session.flush()
        return True

    async def _pr_verdict(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status: PullRequestStatus,
        ref: str,
    ) -> tuple[MergeVerdict, Who, BranchProtection, bool, list[merge_state.CheckRun]]:
        """The one merge-state computation (#718): gather this PR's raw
        signals and hand them to `merge_state.compute_merge_state`. Click-time
        and poll-time both come through here — the judgment exists once.

        Returns (verdict, whose move it is, the project's protection policy,
        whether GitHub itself is enforcing protection, the raw check runs —
        the poller's event table reads facts the verdict may have folded away:
        a conflicted PR is `dirty` no matter what its checks say, but a red
        check on it is still 芝士's to fix and must still reach it)."""
        from app.domain.project.forge import default_branch
        from app.domain.project.protection import branch_protection_of

        project = await self._projects.get(topic.project_id)
        protection = branch_protection_of(project)
        from app.domain.project.forge import binding_for_project

        binding = await binding_for_project(topic.project_id, self._session)
        enforces = (
            await _github_enforces(f"{owner}/{repo}", creds.read)
            if binding is not None and binding.kind == "github_app"
            else False
        )

        raw_runs = await client.list_check_runs(
            owner=owner, repo=repo, ref=ref, token=creds.read
        )
        runs = [
            merge_state.CheckRun(
                name=str(r.get("name") or ""),
                status=str(r.get("status") or ""),
                conclusion=(
                    str(r["conclusion"]) if r.get("conclusion") is not None else None
                ),
            )
            for r in raw_runs
        ]
        required = tuple(
            merge_state.RequiredCheck(name=rc.name, paths=rc.paths)
            for rc in protection.required_checks
        )

        # The two compare reads cost API calls, so each happens only when a
        # rule actually consumes it: ancestry for strict, the file list for a
        # path-scoped required check. Failures degrade to None — the verdict's
        # documented conservative fallbacks take over (scope unknown = the
        # check stays required; ancestry unknown = strict does not block).
        work = (
            await TaskService(self._session).get(card.task_id) if card.task_id else None
        )
        base = (work.base_branch if work else None) or await default_branch(
            topic.project_id, self._session
        )
        ancestry: str | None = None
        if protection.strict:
            try:
                ancestry = await client.compare_status(
                    owner=owner, repo=repo, base=base, head=ref, token=creds.read
                )
            except Exception:  # noqa: BLE001 — ancestry unreadable ≠ blocked
                logger.warning("card %s: compare_status failed", card.id, exc_info=True)
        changed_paths: list[str] | None = None
        if any(rc.paths for rc in required):
            try:
                files = await client.compare_files(
                    owner=owner, repo=repo, base=base, head=ref, token=creds.read
                )
            except Exception:  # noqa: BLE001 — scope unknown handled conservatively
                logger.warning("card %s: compare_files failed", card.id, exc_info=True)
                files = None
            changed_paths = None if files is None else [path for _, path in files]

        verdict = merge_state.compute_merge_state(
            github_mergeable_state=status.mergeable_state,
            github_mergeable=status.mergeable,
            check_runs=runs,
            changed_paths=changed_paths,
            required_checks=required,
            strict=protection.strict,
            base_ancestry=ancestry,
            github_enforces=enforces,
            draft=status.draft,
        )
        dependency = await self._dependency_block_reason(card, status)
        if dependency:
            verdict = MergeVerdict(
                state="blocked",
                reasons=(
                    merge_state.MergeReason(kind="dependency", detail=dependency),
                ),
            )
            return verdict, "agent", protection, False, runs
        return verdict, whose_move(verdict), protection, enforces, runs

    async def _mirror_pr_verdict(
        self,
        card: AcceptCard,
        *,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status: PullRequestStatus,
        ref: str,
    ) -> tuple[MergeVerdict, Who, BranchProtection, bool, list[merge_state.CheckRun]]:
        """问一次那个唯一的判官，把它的答案写到卡上 —— 中间的每一步都不许有人
        自己再判一遍（`_pr_verdict` 的 docstring）。轮询器、点击、以及读卡时补陈旧
        快照，三条路都从这一个门口过。"""
        verdict, who, protection, enforces, runs = await self._pr_verdict(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=ref,
        )
        self._write_merge_mirror(card, verdict, who, ref)
        return verdict, who, protection, enforces, runs

    def _write_merge_mirror(
        self, card: AcceptCard, verdict: MergeVerdict, who: Who, head_sha: str
    ) -> None:
        """Mirror the verdict onto the card — what the card UI shows (#718).

        `since` is when this (state, head) pair started holding, carried over
        from the previous mirror when unchanged: the required-check grace
        clock reads it."""
        now_iso = datetime.now(UTC).isoformat()
        prev = card.merge_state if isinstance(card.merge_state, dict) else {}
        since = (
            prev.get("since") or now_iso
            if prev.get("state") == verdict.state and prev.get("head_sha") == head_sha
            else now_iso
        )
        card.merge_state = {
            "state": verdict.state,
            "who": who,
            "reasons": [
                {"kind": r.kind, "checks": list(r.checks), "detail": r.detail}
                for r in verdict.reasons
            ],
            "head_sha": head_sha,
            "checked_at": now_iso,
            "since": since,
        }

    async def _merge_pr_for_accept(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str,
    ) -> AcceptCard:
        """App forge: 采纳 = 当场调合并 API，合的是人看到的那个 commit (#718).

        The gate keeping "只在绿的时候合" is the same merge-state computation
        the poller mirrors onto the card — evaluated fresh right here, because
        the mirror may be up to a poll interval stale. GitHub 自己开了保护的
        项目直接调 API（405 就是被拦住，平台一个字不重算）；其余项目 clean /
        unstable（红的不在必跑名单）才合，非绿拒绝采纳并把状态和原因写进响应。

        The merge call carries the head the human saw — `seen_head`, the sha
        the BROWSER rendered, already checked against the card by
        `_seen_head_or_refresh`, and never anything else: there is no "just
        read the live head" fallback, because a live head is by definition one
        no screen has shown. Any push that landed after their look — before the
        click (live head differs) or during it (GitHub answers 409) — refreshes
        the card instead of merging: head updated, approvals cleared when the
        project dismisses stale accepts, and the human asked to look again.
        #422's whole authorize-then-drift apparatus is replaced by this one API
        parameter plus dismiss-stale.
        """

        number = card.pr_number
        assert number is not None  # caller checked; keeps the type checker honest

        creds, why = await self._app_credentials(topic)
        if creds is None:
            await self._stop_accept_pr_unavailable(
                card, topic, say("acceptStoppedNoCredentials", why=why)
            )
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except ValidationError as exc:
            await self._stop_accept_pr_unavailable(card, topic, str(exc))

        client = await self._status_client(topic.project_id)
        try:
            status = await client.pull_request_status(
                owner=owner, repo=repo, number=number, token=creds.read
            )
        except Exception as exc:  # noqa: BLE001 — stop visibly; never local-merge
            logger.warning(
                "cannot read PR #%s for card %s at accept time: %s",
                number,
                card.id,
                exc,
            )
            await self._stop_accept_pr_unavailable(
                card,
                topic,
                _capped("acceptStoppedStatusUnread", pr=number, error=str(exc)),
            )

        if status.merged:
            # 有人已经在 GitHub 上合了这个 PR —— 同一件事，照单收下。
            card.pr_merged_at = status.merged_at or datetime.now(UTC)
            await self._mark_task_merged(card, delivered_head=status.head_sha)
            if status.merge_commit_sha:
                card.pr_head_sha = status.merge_commit_sha
            return await self._conclude_pr_accept(
                card, topic, decided_by, merged_externally=True
            )
        if status.state == "closed":
            await self._stop_accept_pr_unavailable(
                card, topic, say("acceptStoppedPrClosed", pr=number)
            )

        # 合的是人看到的那个 commit：浏览器渲染时卡面上的 head，一个字都不兜底。
        # GitHub 上的合并永远不用「现取的 head」——那种 commit 没有在任何界面上
        # 出现过（`_seen_head_or_refresh` 是这条规矩的入口闸）。
        seen = seen_head
        if status.head_sha != seen:
            await self._refresh_stale_card(card, topic, live_head=status.head_sha)
            raise ValidationError(say("prNewCommitsAfterView", number=number))

        try:
            verdict, who, protection, enforces, _runs = await self._pr_verdict(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                status=status,
                ref=seen,
            )
        except Exception as exc:  # noqa: BLE001 — stop visibly; never guess green
            logger.warning(
                "cannot compute merge state for PR #%s at accept time: %s",
                number,
                exc,
            )
            await self._stop_accept_pr_unavailable(
                card,
                topic,
                _capped("acceptStoppedMergeStateUnread", pr=number, error=str(exc)),
            )
        self._write_merge_mirror(card, verdict, who, seen)
        # GitHub enforcing → its merge API is the gate (405 = blocked, 如实转
        # 译). Platform enforcing → only clean, or unstable whose reds are not
        # on the required roster, may merge; everything else is refused with
        # the state and its reasons — the UI shouldn't have lit the button,
        # this is the defensive twin of that rule.
        if not enforces and verdict.state not in ("clean", "unstable"):
            detail = "；".join(r.detail for r in verdict.reasons if r.detail)
            raise ValidationError(
                say("acceptBlocked", state=verdict.state, detail=detail or "规则未满足")
            )

        attribution = await identity.attribution(
            self._session, topic, card=card, decided_by=decided_by
        )
        from app.domain.project.forge import ensure_author_email

        if attribution.author:
            await ensure_author_email(
                topic.project_id, self._session, attribution.author.email
            )
        try:
            result = await client.merge_pull_request(
                owner=owner,
                repo=repo,
                number=number,
                token=creds.write,
                commit_title=pr_text.merge_commit_title(card, topic, number),
                commit_message=pr_text.merge_commit_message(
                    topic, decided_by, card, attribution
                ),
                sha=seen,
            )
        except Exception:  # noqa: BLE001 — the remote may have merged before disconnecting
            logger.exception("merge request failed for card %s", card.id)
            await self._stop_accept_pr_unavailable(
                card,
                topic,
                say("acceptStoppedMergeUnconfirmed", pr=number),
            )
        if result.stale_head:
            # 芝士在点击和合并之间又推了 —— GitHub 拦下了那个没人看过的 commit。
            live = ""
            try:
                live = await client.pull_request_head_sha(
                    owner=owner, repo=repo, number=number, token=creds.read
                )
            except Exception:  # noqa: BLE001 — refresh with what we know
                logger.warning("card %s: post-409 head read failed", card.id)
            await self._refresh_stale_card(card, topic, live_head=live)
            raise ValidationError(say("prNewCommitsDuringAccept", number=number))
        if result.queued:
            await self._record_queue_entry(card, decided_by)
            return card
        if result.sha is None:
            # A faithful 405: GitHub (or its enforced protection) said no.
            reason = result.blocked_reason or say("reasonUnstated")
            self._notify_merge_result(
                topic,
                say("mergeRefusedOnAccept", pr=number),
                meta=notice(
                    EVENT_MERGE_REFUSED,
                    severity=SEVERITY_ERROR,
                    who=WHO_HUMAN,
                    detail=say(
                        "mergeRefusedDetail", reason=reason, url=card.pr_url or ""
                    ),
                    detail_label=say("labelGithubReply"),
                ),
            )
            raise ValidationError(say("githubMergeRefused", pr=number, reason=reason))

        card.pr_merged_at = datetime.now(UTC)
        await self._mark_task_merged(card, delivered_head=seen)
        card.pr_head_sha = result.sha  # the merge commit, for the record
        return await self._conclude_pr_accept(card, topic, decided_by)

    async def _record_queue_entry(self, card: AcceptCard, decided_by: str) -> None:
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        await self._repo.add_approval(card.id, decided_by)
        self._write_merge_mirror(
            card,
            MergeVerdict(
                state="blocked",
                reasons=(
                    merge_state.MergeReason(
                        kind="ci_running", detail="等待合并队列检查"
                    ),
                ),
            ),
            "ci",
            card.pr_head_sha or "",
        )
        notes.record(
            card,
            notes.NoteCode.waiting_merge_queue,
            f"PR #{card.pr_number} 已进入合并队列，等待队列检查和实际合并。",
        )
        await self._session.flush()
        await self._session.refresh(card)

    async def _conclude_pr_accept(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        merged_externally: bool = False,
    ) -> AcceptCard:
        """Shared tail of a click-time PR accept: record the decision and vote,
        then the common merged-PR bookkeeping (`_finish_pr_accept`)."""
        await self._repo.add_approval(card.id, decided_by)
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        await self._finish_pr_accept(
            card=card, topic=topic, merged_externally=merged_externally
        )
        await self._session.refresh(card)
        return card

    async def _refresh_stale_card(
        self,
        card: AcceptCard,
        topic: Topic,
        *,
        live_head: str,
        action: str = "采纳",
        headline: str | None = None,
    ) -> None:
        """新提交作废已有的采纳 (#718, dismiss_stale — GitHub 的「Dismiss stale
        pull request approvals」，这里默认开): the head moved out from under
        the reviewer, so the card refreshes — new head, stale mirror dropped,
        approvals cleared (when the project dismisses stale accepts), and the
        auto-merge arm disarmed.

        Written OUTSIDE the accept transaction (its caller is about to raise).
        **Rolls the request transaction back first**, for the same reason as
        `_stop_accept_pr_unavailable`: this request may already hold a row
        lock on the very card the fresh session is about to write, and two
        connections on one row with one waiting on the other is a hang, not a
        refresh. Every attribute needed later is read before the rollback
        (expired attributes reload with sync IO an AsyncSession cannot do)."""
        from app.domain.project.protection import branch_protection_of

        card_id = card.id
        number = card.pr_number
        project_id = topic.project_id
        await self._session.rollback()
        try:
            factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
            async with factory() as session:
                project = await ProjectRepository(session).get(project_id)
                dismiss = branch_protection_of(project).dismiss_stale
                repo = AcceptCardRepository(session)
                fresh = await repo.get(card_id)
                if fresh is None:
                    return
                if live_head:
                    fresh.pr_head_sha = live_head
                fresh.merge_state = None  # mirrored for the old head — stale
                if dismiss:
                    await repo.clear_approvals(card_id)
                    fresh.auto_merge_armed_by = None
                    fresh.auto_merge_armed_at = None
                notes.record(
                    fresh,
                    None,
                    (headline or f"PR #{number} 有新提交，之前看到的版本已过时")
                    + ("；已有的批准一并作废" if dismiss else "")
                    + f"，请重新查看后再{action}",
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — the raise this accompanies must fire
            logger.exception("could not refresh stale card %s", card_id)

    async def _pr_poll_credentials(
        self, card: AcceptCard, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        """The App's GitHub credentials for this card's PR — the only lane
        left (#718 deleted the personal-token one). Two mints, not one: see
        `_GitHubCredentials` for why reading checks with the write token is a
        403 that presents as a card frozen forever. Never raises — a poll
        tick degrades to "pause and retry"."""
        del card  # one lane now; the signature stays call-site-stable
        return await self._app_credentials(topic)

    async def _app_credentials(
        self, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        """The platform App's installation tokens for this project's repo."""
        from app.domain.project.forge import tokens_for_project

        tokens = await tokens_for_project(topic.project_id, self._session)
        if tokens is None:
            return None, "这个项目的代码托管凭据不可用"
        try:
            write, _ = await tokens.write_token()
            read, _ = await tokens.installation_token()
        except Exception as exc:  # noqa: BLE001 — pause this tick, don't crash
            return None, f"获取代码托管凭据失败（{type(exc).__name__}）"
        return _GitHubCredentials(write=write, read=read), ""

    async def advance_pr_card(
        self, card_id: uuid.UUID, *, chat_service, runner
    ) -> None:
        """Observe external merges and advance pending PR cards (#718). The
        poller does three things and nothing else: mirror the merge state
        onto the card, send the events the 「谁的活」 table names (deduped
        through the nudge ledger), and merge a card whose auto-merge is armed
        once the rules are satisfied. Called by
        `review/pr_poll.py::poll_open_prs`; never raises for a transient GitHub
        hiccup — the next poll just retries."""
        # A webhook and the reconciliation clock may observe the same card.
        # Only one transaction may advance it or emit its notifications.
        card = await self._session.scalar(
            select(AcceptCard)
            .where(AcceptCard.id == card_id)
            .with_for_update(skip_locked=True)
        )
        if card is None:
            return
        if (
            card.status not in (AcceptStatus.pending, AcceptStatus.rejected)
            or card.pr_number is None
        ):
            return
        if card.status == AcceptStatus.rejected:
            tree = (
                await TaskService(self._session).get(card.task_id)
                if card.task_id is not None
                else None
            )
            if (
                card.pr_merged_at is not None
                or tree is None
                or tree.status != TaskStatus.open
            ):
                return
        topic = await self._topic_or_404(card.topic_id)
        forge = await self._resolve_forge(topic.project_id, card=card)
        await forge.poll(self, card, topic, chat_service=chat_service, runner=runner)

    async def refresh_stale_pr_snapshots(self, cards: Sequence[AcceptCard]) -> None:
        """读卡这条路，把卡面上过期的合并态补上。

        卡上的 `merge_state` 是一份**快照**，而前端那颗「采纳」按钮按它亮不亮
        (`TopicAcceptCard.vue` 的 `acceptBlockedTitle`)。快照平时只有两个时候
        重算：轮询器 (`accept_pr_poll_interval_s`，默认 300s)，和点下采纳的那一
        刻。夹在两者之间来读的人只会读到陈旧的一份 —— 实测 CI 在 11:28Z 就全绿
        了，卡到 11:49Z 才写进 `clean`，中间二十来分钟界面上是「检查全绿、按钮
        点不动」；更短的一次是 12:06Z 卡上被写成 `unknown`（GitHub 那一刻还没算
        完 mergeable），而此刻 `/pr-checks` 早已 `mergeable: true`，快照又冻了五
        分钟。界面每 15s 来读一次，读到的却是同一份旧快照，**靠前端轮询自己收敛
        不了**。

        所以给读这条路一个它自己的保证：快照比 `accept_pr_snapshot_floor_s` 还
        旧，就重算一次。地板是必须的 —— 读卡是热点，不能每个读者都触发一次外呼。

        只做一件事：把当前判定重算一遍写回去（具体交给托管方那一侧的能力，
        `Forge.refresh_snapshot`）。轮询器做的另外两件**不做** —— 不发事件、
        不合并 (那是它的职责，`advance_pr_card` 的 docstring)。任何失败都吞掉：
        读卡不能因为 GitHub 抖一下就 500，快照下一轮重试就是。
        """
        for card in cards:
            try:
                await self._refresh_stale_pr_snapshot(card)
            except Exception:  # noqa: BLE001 — a read never fails over a poll
                logger.warning(
                    "card %s: refreshing a stale merge snapshot failed",
                    card.id,
                    exc_info=True,
                )

    def _merge_snapshot_age_s(self, card: AcceptCard) -> float | None:
        """这份快照是多久以前算的；没算过或读不懂就是 None（当作最陈旧）。"""
        state = card.merge_state if isinstance(card.merge_state, dict) else None
        raw = (state or {}).get("checked_at")
        if not raw:
            return None
        try:
            when = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return (datetime.now(UTC) - when).total_seconds()

    async def _refresh_stale_pr_snapshot(self, card: AcceptCard) -> None:
        if card.status != AcceptStatus.pending or card.pr_number is None:
            return
        age = self._merge_snapshot_age_s(card)
        if age is not None and age < settings.accept_pr_snapshot_floor_s:
            return
        topic = await self._topic_or_404(card.topic_id)
        if topic.status == TopicStatus.archived:
            # 归档里的卡不会再被采纳，没必要为它花一次外呼。
            return
        try:
            forge = await self._resolve_forge(topic.project_id, card=card)
        except ValidationError:
            # 托管绑定暂时读不出来。`describe` 那一侧照样把卡发出去（在卡面上
            # 如实说「暂时读不出」），这里也照做：不补快照，轮询器下一跳再试。
            return
        await forge.refresh_snapshot(self, card, topic)

    async def _refresh_github_snapshot(self, card: AcceptCard, topic: Topic) -> None:
        """GitHub 那一侧的重算：读一眼现在的状态，写回卡面。"""
        number = card.pr_number
        assert number is not None  # PR lane only; the caller checked
        owner, repo = await self._pr_repo_of(card, topic)
        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            logger.info("card %s: stale snapshot left alone (%s)", card.id, reason)
            return
        client = await self._status_client(topic.project_id)
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=number, token=creds.read
        )
        live = status.head_sha
        if card.pr_head_sha != live:
            # head 动了 —— 那属于「新提交作废已有的采纳」(`dismiss_stale_accept`
            # 会撤掉别人给的批准并发通知) 这个状态迁移，是 `_poll_pr_card` 的事。
            # 浏览器每 15s 读一次卡，读的人可能根本不在乎这张卡：读不替它迁移。
            return
        await self._mirror_pr_verdict(
            card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=live,
        )

    async def _advance_github_card(
        self,
        card: AcceptCard,
        topic: Topic,
        *,
        chat_service,
        runner,
    ) -> None:
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except Exception as exc:  # noqa: BLE001 — pause this tick, retry next
            logger.warning("card %s: cannot resolve PR repo: %s", card.id, exc)
            return

        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            logger.warning(
                "card %s has no usable GitHub token anymore (%s); "
                "skipping this poll (will retry next tick)",
                card.id,
                reason,
            )
            # Without this the card just sits there forever and looks
            # identical to "CI still running" — no signal anyone's token died.
            if (
                card.status == AcceptStatus.pending
                and card.note_code is not notes.NoteCode.poll_paused
            ):
                notes.record(
                    card,
                    notes.NoteCode.poll_paused,
                    f"轮询暂停，下一轮还会重试：{reason}",
                )
                await self._session.flush()
            return

        # Token is usable again → the pause note is stale. Clearing it here is
        # what makes the pause self-healing: it stops describing a condition
        # that no longer holds, AND it can no longer sit in front of a real CI
        # failure (which is how "轮询暂停" used to swallow CI 失败 notifications
        # — see the 2026-08-10 note in `_ci_nudge`). Only this exact prefix is
        # cleared; 重推失败 /
        # 拒绝合并 / 检查未通过 notes describe live conditions and stay put.
        if card.status == AcceptStatus.pending and card.note_code in (
            notes.NoteCode.poll_paused,
            notes.NoteCode.poll_failed,
        ):
            notes.clear(card)
            await self._session.flush()

        from app.domain.review import github_pr

        client = await self._status_client(topic.project_id)
        try:
            await self._poll_pr_card(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                chat_service=chat_service,
                runner=runner,
            )
        except github_pr.GitHubPrError as exc:
            logger.warning(
                "GitHub API hiccup polling card %s: %s — retrying next tick",
                card.id,
                exc,
            )
            # And say it on the CARD. A log line is only readable by whoever has
            # a shell on the host, and the person waiting is looking at a card
            # whose note still says 「等 CI」 — so a poll that fails every tick
            # forever is indistinguishable from checks that are simply slow.
            # That is how #575/#582 sat green-but-unmerged with nothing on
            # screen to explain it. Same treatment the credential branch above
            # already gets, for the same reason.
            if card.status == AcceptStatus.pending:
                self._note_poll_failed(card, exc)
            await self._session.flush()

    async def _mark_task_merged(
        self, card: AcceptCard, *, delivered_head: str | None = None
    ) -> None:
        if card.task_id is None:
            return
        task = await TaskService(self._session).require_in_room(
            card.topic_id, card.task_id
        )
        task.status = TaskStatus.closed
        task.closed_at = task.closed_at or datetime.now(UTC)
        after_close(self._session, task.room_id)
        task.accepted_at = task.accepted_at or datetime.now(UTC)
        task.accepted_by = task.accepted_by or card.decided_by
        if delivered_head and not task.delivered_head:
            task.delivered_head = delivered_head[:64]
        await self._session.flush()

    async def _app_pr_client(self, topic: Topic):  # noqa: ANN202 — GitHubPRClient
        """The App-token client for this project's upstream, or None when the
        project has no GitHub side at all (no installation, or an upstream that
        is not a GitHub https remote)."""
        from app.domain.project.forge import proposal_client

        return await proposal_client(topic.project_id, self._session)

    async def mark_ready(self, room_id: uuid.UUID, task_id: uuid.UUID) -> dict:
        """Mark this task's PR ready; acceptance remains a separate human action."""
        task = await TaskService(self._session).require_in_room(room_id, task_id)
        if task.status != TaskStatus.open or not task.branch_name:
            raise ValidationError("这条任务已结束或没有工作分支")
        topic = await self._topic_or_404(room_id)
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能提交验收")
        if task.pr_number is None:
            return {"ready": False, "reason": "任务还没有 PR，请先提交并同步代码"}
        client = await self._app_pr_client(topic)
        if client is None:
            return {"ready": False, "reason": "这个项目没有绑定 GitHub"}
        from app.domain.review.github_pr import GitHubPRRateLimited

        try:
            view = await client.pr_view(task.pr_number)
        except GitHubPRRateLimited:
            return {
                "ready": False,
                "reason": (
                    "GitHub API 请求额度暂时用尽，PR 尚未标记为可评审；"
                    "额度恢复后请重试。"
                ),
            }
        if view.get("draft"):
            node_id = str(view.get("node_id") or "")
            if not node_id:
                raise ValidationError("GitHub 未返回 PR 的 node_id")
            await client.mark_ready_for_review(node_id)
        return {
            "ready": True,
            "already": not bool(view.get("draft")),
            "pr_number": task.pr_number,
            "pr_url": task.pr_url,
        }

    async def redescribe(
        self,
        place_id: uuid.UUID,
        *,
        actor: str,
        change_subject: str | None = None,
        change_body: str | None = None,
    ) -> AcceptCard:
        """更正这张卡的描述 —— and rewrite the PR from it in the same breath.

        **Why this may be corrected while `Cheese-Task:` may not.** A delivery
        claim is an ASSERTION OF FACT about who wrote the code; letting it be
        edited after filing is letting somebody put another agent's name on a
        change, and the wrong name in permanent history reads exactly like the
        right one. A description is an EXPLANATION of the change, and having a
        reviewer say "that reasoning is wrong" is what review IS. Refusing to
        correct it does not protect history — it guarantees the correction
        happens on the PR page only, and main receives the sentence everyone
        already agreed was false. PR #735 是活例子：评审把 PR 正文改对了，
        `1c298199a` 里留下的仍是递卡那一刻的快照。

        The PR is rewritten from the card, never read back into it. The card is
        the single source of both texts, so they cannot disagree — and the
        trailers (`Cheese-Task`, `Requested-by`, `Cheese-Agent`) stay something
        the platform asserts rather than something anybody can retype in a
        GitHub textarea.

        Only while the card is `pending`. Once it is accepted the commit is
        already in main and there is nothing left to correct here; that case
        belongs in a correction the room records, not in a row nobody reads
        again.
        """
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending,)
        )
        if not cards:
            raise ValidationError("这个话题手上没有待处理的验收卡，没有描述可以改")
        card = cards[0]
        topic = await self._topic_or_404(card.topic_id)
        before_subject, before_body = card.change_subject, card.change_body or ""

        subject = (change_subject or "").strip()
        if subject:
            try:
                card.change_subject = commit_message.check_subject(subject)
            except commit_message.InvalidSubject as exc:
                raise ValidationError(str(exc)) from exc
        if change_body is not None:
            card.change_body = change_body.strip() or None
        if (card.change_subject, card.change_body or "") == (
            before_subject,
            before_body,
        ):
            return card
        await self._session.flush()

        if card.pr_number is not None:
            client = await self._app_pr_client(topic)
            if client is not None:
                who = await identity.attribution(self._session, topic, card=card)
                view = await client.pr_view(card.pr_number)
                await pr_publish.sync_pr_text(
                    client,
                    view,
                    title=pr_text.change_subject(card, topic),
                    body=pr_text.pr_body(topic, "", card, who),
                )
        # 留痕：谁在什么时候把描述从什么改成了什么。这条入口的存在本身需要可追溯，
        # 否则它就是一条能悄悄改「这次改动会在历史里说什么」的路。
        self._notify_merge_result(
            topic,
            say("cardRedescribed", actor=actor),
            meta=notice(
                EVENT_CARD_REDESCRIBED,
                severity=SEVERITY_INFO,
                who=WHO_CHEESE,
                detail=say(
                    "cardRedescribedDetail",
                    beforeSubject=before_subject or say("emptyValue"),
                    afterSubject=card.change_subject or say("emptyValue"),
                    beforeBody=before_body or say("emptyValue"),
                    afterBody=card.change_body or say("emptyValue"),
                ),
                detail_label=say("labelWhatChanged"),
            ),
        )
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def push_fix(
        self, place_id: uuid.UUID, *, drop_dependency: bool = False
    ) -> dict:
        """Observe the branch the CLI already pushed directly to the forge."""
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending, AcceptStatus.conflict)
        )
        cards = [c for c in cards if c.pr_number is not None]
        if drop_dependency:
            from app.domain.project.forge import default_branch
            from app.domain.review import github_pr

            task = await TaskService(self._session).get(place_id)
            if task is None or task.pr_number is None:
                raise ValidationError("任务尚无 PR，请先同步提交后再移除依赖")
            topic = await self._topic_or_404(task.room_id)
            for card in cards:
                await self._pr_repo_of(card, topic)
            publisher = await self._app_pr_client(topic)
            if publisher is None:
                raise ValidationError("项目的代码仓库暂时不可用，无法移除依赖")
            base = await default_branch(task.project_id, self._session)
            try:
                await publisher.update_pr(task.pr_number, base=base)
                status = await publisher.pr_status(task.pr_number)
            except (github_pr.GitHubPrError, github_pr.GitHubPRError) as exc:
                raise ValidationError(f"暂时无法更新 PR 的目标分支：{exc}") from exc
            if status.base_ref != base:
                raise ValidationError("仓库尚未确认新的目标分支，请稍后重试")
            pushed = await self._sync_task_dependency_target(
                task, status, drop_dependency=True
            )
            for card in cards:
                if pushed or card.pr_head_sha != status.head_sha:
                    pushed = True
                    await self._dismiss_stale_accept(card=card, topic=topic)
                    card.pr_head_sha = status.head_sha
                    card.merge_state = None
                    card.rebase_count = 0
                    notes.clear(card)
            await self._session.flush()
            return {
                "pushed": pushed,
                "pr_number": task.pr_number,
                "pr_url": task.pr_url,
                "reason": "" if pushed else "任务已无依赖，PR 没有新提交",
            }
        if not cards:
            return {"pushed": False, "reason": "这个话题手上没有骑着 PR 的验收卡"}
        card = cards[0]
        assert card.pr_number is not None  # filtered above; for the type checker
        topic = await self._topic_or_404(card.topic_id)
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except ValidationError as exc:
            return {"pushed": False, "reason": str(exc)}

        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            return {"pushed": False, "reason": f"拿不到可用的 GitHub 凭据：{reason}"}

        from app.domain.review import github_pr

        client = await self._status_client(topic.project_id)
        try:
            status = await client.pull_request_status(
                owner=owner, repo=repo, number=card.pr_number, token=creds.read
            )
            retargeted = await self._sync_dependency_target(card, status)
            pushed = retargeted or status.head_sha != card.pr_head_sha
            if pushed:
                await self._dismiss_stale_accept(card=card, topic=topic)
                card.pr_head_sha = status.head_sha
                card.merge_state = None
                card.rebase_count = 0
                notes.clear(card)
        except (github_pr.GitHubPrError, github_pr.GitHubPRError) as exc:
            # Same reasoning as the poll path: say it on the card, because the
            # person waiting is looking at the card and not at a log file.
            self._note_poll_failed(card, exc)
            await self._session.flush()
            return {"pushed": False, "reason": f"GitHub 暂时不通：{exc}"}
        await self._session.flush()
        return {
            "pushed": pushed,
            "pr_number": card.pr_number,
            "pr_url": card.pr_url,
            "reason": "" if pushed else "分支上没有 PR 还不知道的提交",
        }

    async def note_poll_crashed(self, card_id: uuid.UUID, exc: BaseException) -> None:
        """Same explanation as `_note_poll_failed`, for a poll that died on
        something other than a GitHub error (the poller's own catch-all).

        Its caller rolled the failed tick back, so this runs on a fresh session
        and is a no-op for a card that has since settled or lost its PR.
        """
        card = await AcceptCardRepository(self._session).get(card_id)
        if card is None or card.status != AcceptStatus.pending:
            return
        self._note_poll_failed(card, exc)
        await self._session.flush()

    def _note_poll_failed(self, card: AcceptCard, exc: BaseException) -> None:
        return _note_poll_failed(card, exc)

    async def _poll_pr_card(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        chat_service,
        runner,
    ) -> None:
        """The poller's three jobs on one card (#718): mirror the merge state,
        send the events the 「谁的活」 table names, and merge an armed card
        when the rules are satisfied. It merges NOTHING otherwise — accepting
        is the human's click, evaluated at click time."""
        number = card.pr_number
        if number is None:  # already guaranteed by advance_pr_card's guard
            return

        # FIRST: did someone already handle this PR on GitHub? A merged-by-hand
        # PR is invisible to every other signal here, and without this the
        # card polls forever.
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=number, token=creds.read
        )
        if status.merged:
            if card.status == AcceptStatus.rejected:
                # The review remains a return. GitHub establishes a separate
                # fact about the batch, including the head that actually landed.
                card.pr_merged_at = status.merged_at or datetime.now(UTC)
                await self._mark_task_merged(card, delivered_head=status.head_sha)
                await self._session.flush()
                self._notify_merge_result(
                    topic,
                    say("acceptDoneAfterReturn", pr=number),
                    meta=notice(
                        EVENT_ACCEPT_DONE,
                        severity=SEVERITY_INFO,
                        who=WHO_PLATFORM,
                        detail=card.pr_url or "",
                    ),
                )
                return
            await self._settle_external_merge(card=card, topic=topic, status=status)
            return
        if card.status == AcceptStatus.rejected:
            # Observing an external merge must never re-arm, approve, update,
            # or merge a returned delivery, even when its checks are green.
            return
        if status.state == "closed":
            await self._void_closed_pr_card(card=card, topic=topic)
            return

        if card.note_code == notes.NoteCode.waiting_merge_queue:
            if await client.merge_queue_entry(
                owner=owner, repo=repo, number=number, token=creds.read
            ):
                return
            # Removal can mean failed checks or a human cancellation. Never re-enqueue.
            card.auto_merge_armed_by = None
            card.auto_merge_armed_at = None
            notes.record(
                card,
                notes.NoteCode.merge_refused,
                f"PR #{number} 已离开合并队列但尚未确认合并，"
                "请检查 GitHub 后重新采纳。",
            )
            await self._session.flush()
            return

        if await self._sync_dependency_target(card, status):
            return

        live = status.head_sha
        if card.pr_head_sha != live:
            if card.pr_head_sha:
                # 新提交作废已有的采纳 (#718, dismiss_stale): the head the
                # reviewer saw moved. Clear what the old head earned —
                # approvals and the auto-merge arm — and tell the reviewer.
                await self._dismiss_stale_accept(card=card, topic=topic)
                # Whatever the note said, it described the old commit.
                notes.clear(card)
            card.pr_head_sha = live
            await self._session.flush()

        verdict, who, protection, enforces, runs = await self._mirror_pr_verdict(
            card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=live,
        )

        # —— 按表发事件，每件各排一条待发，谁都不许把别人挡掉 (pr_signals) ——
        #
        # 收集途中读 GitHub 失败，不能把已经排好队的其它待发一起丢掉。错误推迟
        # 到发完再抛：卡上照样记下这次轮询出过错（advance_pr_card 的 except），
        # 而已经排好的那条已经送到。
        pending: list[pr_signals.PendingNudge] = []
        deferred: Exception | None = None
        kinds = {r.kind for r in verdict.reasons}
        # 红检查按 runs 本身判，不按 verdict 的 reasons：一个和 main 冲突的 PR
        # 的 verdict 是 dirty（冲突最优先），但它上面的红检查照样是芝士要修的
        # 事 —— 三件事互不蕴含，谁都不许把别人吞掉（pr_signals 的规矩）。
        has_red_check = any(
            r.conclusion in ("failure", "timed_out", "cancelled", "action_required")
            for r in runs
            if r.status == "completed"
        )
        if has_red_check or kinds & {"required_check_failed", "check_failed"}:
            # 检查红了 → 事件到做活的 agent，带哪个检查红、日志怎么取。
            # `check_state` 是取失败详情（job 链接 + 日志片段）的那条路。
            try:
                state_word, tail = await client.check_state(
                    owner=owner, repo=repo, ref=live, token=creds.read
                )
                if state_word == "failure":
                    ci = self._ci_nudge(
                        card=card, tail=tail, stage="CI", owner=owner, repo=repo
                    )
                    if ci is not None:
                        pending.append(ci)
            except Exception as exc:  # noqa: BLE001 — 先发完，再抛
                deferred = exc
        try:
            review = await self._review_nudge(
                card=card,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                status=status,
            )
            if review is not None:
                pending.append(review)
        except Exception as exc:  # noqa: BLE001 — 见上：先发完，再抛
            deferred = exc
        if verdict.state == "dirty":
            conflict = self._conflict_nudge(card=card, status=status)
            if conflict is not None:
                pending.append(conflict)
        await self._dispatch_nudges(
            card=card,
            topic=topic,
            pending=pending,
            chat_service=chat_service,
            runner=runner,
        )
        if deferred is not None:
            await self._session.flush()  # the mirror and dispatched ledger keep
            raise deferred

        if verdict.state == "clean":
            # CLEAN → 通知验收人（按 head 去重）。
            await self._notify_ready(card, topic)
        elif verdict.state == "blocked" and "required_check_missing" in kinds:
            # BLOCKED 必跑检查没报到 → 等 CI，不发；超过宽限期转人 ——
            # workflow 改名、被禁用、Actions 断供都长这样，等下去没有尽头，
            # 而出口是叫人，绝不因为等腻了就自动合并。
            if self._required_absence_overdue(card):
                missing = ", ".join(
                    sorted(
                        {
                            name
                            for r in verdict.reasons
                            if r.kind == "required_check_missing"
                            for name in r.checks
                        }
                    )
                )
                await self._note_needs_human(
                    card=card,
                    topic=topic,
                    reason=say(
                        "mergeWithheldCheckMissing",
                        checks=missing,
                        minutes=_REQUIRED_CHECK_GRACE_MINUTES,
                    ),
                    explain=say("mergeWithheldCheckMissingWhy"),
                )
        elif verdict.state == "behind":
            # BEHIND（strict 才出现）→ 平台自己 update-branch；撞冲突的话
            # 下一拍这个 PR 就是 dirty，冲突事件自然转给 agent。
            await self._update_behind_branch(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
            )
            return

        if card.auto_merge_armed_by and verdict.state in ("clean", "unstable"):
            await self._merge_armed_card(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                protection=protection,
                chat_service=chat_service,
                runner=runner,
            )
            return
        await self._session.flush()

    async def _update_behind_branch(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
    ) -> None:
        """BEHIND is the platform's move: GitHub's Update branch, capped.

        绿必须绿在当前基线上（strict）。各自绿在旧基上的两个 PR 相加可以是红
        的。换基后 head 变化，下一轮从新 CI 重新等起；反复换基追不上 main 就
        叫人（上限 3，芝士推新提交时清零）。"""
        if card.rebase_count >= 3:
            await self._note_needs_human(
                card=card,
                topic=topic,
                reason=say("mergeWithheldBehind"),
                explain=say("mergeWithheldBehindWhy"),
            )
            await self._session.flush()
            return
        # `write`, not `read` — this PUSHES a merge of main onto the PR branch
        # (the read mint has no `contents:write`; PR #575/#582 froze on that).
        updated = await client.update_branch(
            owner=owner, repo=repo, number=card.pr_number or 0, token=creds.write
        )
        outcome = (
            "已自动更新分支，等新一轮 CI。"
            if updated
            else "自动更新分支被拒，下一轮重试。"
        )
        card.rebase_count += 1
        notes.annotate(card, f"基线落后于 main，{outcome}")
        await self._session.flush()

    async def _dismiss_stale_accept(self, *, card: AcceptCard, topic: Topic) -> None:
        """新提交作废已有的采纳 (#718)。

        GitHub 的「Dismiss stale pull request approvals when new commits are
        pushed」，这里默认开着 —— GitHub 默认关，因为它假设推代码的是可信的
        人；这里推代码的是拿着 App 写权限的芝士。清掉旧 head 挣到的一切
        （批准票、auto-merge 布防），且只在真有东西被作废时说话。

        通知投给验收人和**被作废的那几票的主人**：重新投一次只有投票的人能做，
        而这句话已经点了他们的名字。"""
        from app.domain.project.protection import branch_protection_of

        project = await self._projects.get(topic.project_id)
        if not branch_protection_of(project).dismiss_stale:
            return
        approvers = await self._repo.list_approver_handles(card.id)
        armed = card.auto_merge_armed_by
        if not approvers and not armed:
            return
        await self._repo.clear_approvals(card.id)
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        voided = "、".join(sorted({*approvers, *((armed,) if armed else ())}))
        await self._tell_the_reviewer(
            card,
            topic,
            say("acceptDismissed", pr=card.pr_number),
            meta=notice(
                EVENT_ACCEPT_DISMISSED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=say(
                    "acceptDismissedDetail", voided=voided, url=card.pr_url or ""
                ),
                detail_label=say("labelWhyVoided"),
            ),
            also=(*approvers, *((armed,) if armed else ())),
        )

    async def _tell_the_reviewer(
        self,
        card: AcceptCard,
        topic: Topic,
        content: str,
        *,
        meta: dict,
        also: Sequence[str] = (),
    ) -> None:
        return await _tell_the_reviewer(
            self._session, card, topic, content, meta=meta, also=also
        )

    async def _notify_ready(self, card: AcceptCard, topic: Topic) -> None:
        return await _notify_ready(self._session, card, topic)

    def _required_absence_overdue(self, card: AcceptCard) -> bool:
        """这张卡等一个没报到的必跑检查，是不是已经等过头了。

        时钟是镜像里的 `since`——「这个 (state, head) 组合从什么时候开始成立」。
        镜像还没写过（第一拍）永远不算过头。"""
        mirror = card.merge_state if isinstance(card.merge_state, dict) else {}
        raw = mirror.get("since")
        if not isinstance(raw, str):
            return False
        try:
            since = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - since).total_seconds()
        return elapsed >= _REQUIRED_CHECK_GRACE_MINUTES * 60

    async def _merge_armed_card(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        protection: BranchProtection,
        chat_service,
        runner,
    ) -> None:
        """绿了自动合 (#718)：the armed card's rules are satisfied — merge it
        with the armer's name on the decision, still guarded by the head sha
        (a push racing this merge gets a 409 and the next tick's
        dismiss-stale handles it, exactly like the click path)."""
        number = card.pr_number
        assert number is not None  # caller checked; keeps the type checker honest
        armer = card.auto_merge_armed_by or ""
        approvers = await self._repo.list_approver_handles(card.id)
        votes = len(set(approvers) | {armer})
        if votes < protection.approvals_required:
            await self._note_needs_human(
                card=card,
                topic=topic,
                reason=say(
                    "mergeWithheldVotes",
                    votes=votes,
                    required=protection.approvals_required,
                ),
                explain=say("mergeWithheldVotesWhy"),
            )
            await self._session.flush()
            return
        attribution = await identity.attribution(
            self._session, topic, card=card, decided_by=armer
        )
        from app.domain.project.forge import ensure_author_email

        if attribution.author:
            await ensure_author_email(
                topic.project_id, self._session, attribution.author.email
            )
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(
                topic, armer, card, attribution
            ),
            sha=card.pr_head_sha,
        )
        if result.stale_head:
            # head 在这一拍里又动了 —— 下一拍镜像到新 head，作废条款接手。
            await self._session.flush()
            return
        if result.queued:
            await self._record_queue_entry(card, armer)
            return
        if result.sha is None:
            await self._note_merge_blocked(
                card=card,
                topic=topic,
                reason=result.blocked_reason or "",
                chat_service=chat_service,
                runner=runner,
            )
            await self._session.flush()
            return
        card.pr_merged_at = datetime.now(UTC)
        await self._mark_task_merged(card, delivered_head=card.pr_head_sha)
        card.pr_head_sha = result.sha
        await self._repo.add_approval(card.id, armer)
        card.decided_by = armer
        card.decided_at = datetime.now(UTC)
        await self._finish_pr_accept(card=card, topic=topic)

    async def _note_needs_human(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        reason: str,
        explain: str | None = None,
    ) -> None:
        return await _note_needs_human(
            self._session, card=card, topic=topic, reason=reason, explain=explain
        )

    async def _settle_external_merge(
        self, *, card: AcceptCard, topic: Topic, status: PullRequestStatus
    ) -> None:
        """Someone merged the PR on GitHub themselves (人工放行, another bot, the
        merge queue). Book it exactly like our own merge — because it is the same
        fact, and since #206 that fact is the whole of what the platform waits
        for."""
        card.pr_merged_at = status.merged_at or datetime.now(UTC)
        await self._mark_task_merged(card, delivered_head=status.head_sha)
        if status.merge_commit_sha:
            # Nice to have, not required: nothing downstream looks a run up by
            # this sha any more, it is just the truest record of what landed.
            card.pr_head_sha = status.merge_commit_sha
        await self._finish_pr_accept(
            card=card,
            topic=topic,
            merged_externally=card.note_code != notes.NoteCode.waiting_merge_queue,
        )

    async def _void_closed_pr_card(self, *, card: AcceptCard, topic: Topic) -> None:
        """The PR was closed on GitHub WITHOUT merging: the review is over.

        A human closing the PR is them saying "not this". Merging behind their
        back, or falling back to a local merge, would be the opposite of what
        they asked for; so the platform voids the card, the same terminal state
        a manual void writes. Delivering again is an ordinary new submission,
        with a new card.
        """
        headline = (
            f"{VOIDED_PREFIX}：PR #{card.pr_number} 已在 GitHub 关闭且没有合并，"
            "平台自动作废了这次审阅。要继续交付，重新提交审阅。"
        )
        card.status = AcceptStatus.revoked
        notes.record(
            card, notes.NoteCode.voided, archive.prefix_note(card.note, headline)
        )
        card.decided_at = card.decided_at or datetime.now(UTC)
        await self._session.flush()
        await self._tell_the_reviewer(
            card,
            topic,
            say("prClosed", pr=card.pr_number),
            meta=notice(
                EVENT_PR_CLOSED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=say("prClosedDetail", url=card.pr_url or ""),
                detail_label=say("labelNextStep"),
            ),
        )

    async def _record_task_nudge(self, *, topic, task, content, headline, meta):
        return await _record_task_nudge(
            self._session,
            topic=topic,
            task=task,
            content=content,
            headline=headline,
            meta=meta,
        )

    async def _note_merge_blocked(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        reason: str,
        chat_service,
        runner,
    ) -> None:
        """Put GitHub's merge refusal on the card's `note` AND wake 芝士 up.
        Before this existed a refusal left `note` empty, so a permanently-
        unmergeable PR looked exactly like a healthy one still waiting on CI.

        The note alone was still not enough (2026-08-11): a note is something
        you have to be looking at. The most common refusal — merge conflicts —
        is exactly the kind 芝士 can fix in its own workspace, so this summons
        it the same way `_ci_nudge` does for a red check. Without the
        summon nobody is working the card and the topic just sits there
        forever (真实案例: PR #242). Note that the conflict dispatch in
        `routes/accept.py` never covers this — that one only runs for the
        synchronous merge at the moment a human clicks 采纳, not for the poll.

        Three things the 60s poll makes mandatory:

        - **No spam.** The note is rewritten only when the text actually
          changes, so an unchanging reason costs one write, not one per poll.
          (Stricter than the nudge ledger's content signature, which cannot
          notice a 405 turning into a 409 — same reason, same string.)
        - **One summon per reason.** The dispatch hangs off that same "the note
          really changed" test, so a 405 that turns into a 409 gets a fresh
          nudge while an unchanging one stays quiet.
        """
        # GitHub 的原话是外部字符串，而它要被贴进芝士的终端（见
        # `pr_signals.sanitize_external`）。
        reason = pr_signals.sanitize_external(reason)
        note = f"PR #{card.pr_number} GitHub 拒绝合并：{reason}"
        current_note = await self._session.scalar(
            select(AcceptCard.note).where(AcceptCard.id == card.id).with_for_update()
        )
        if current_note == note:
            return
        notes.record(card, notes.NoteCode.merge_refused, note)
        logger.warning("PR merge refused for card %s: %s", card.id, reason)
        task = (
            await TaskService(self._session).get(card.task_id) if card.task_id else None
        )
        actionable = task is not None and task.status == TaskStatus.open
        action = (
            f'先执行 cd "$(cheese worktree {card.task_id})" 进入任务目录。'
            "检查 GitHub 返回的具体原因；若是冲突，确认 PR 当前目标分支后再合入，"
            "解决并验证后提交，用 cheese push-fix 更新原 PR。"
            "采纳由人决定；只有人已启用自动合并且项目条件满足时才会自动合并。\n"
            "如果原因不是冲突（比如仓库禁用了这种合并方式），工作区里改不动，"
            "请在话题里说清楚卡在哪、需要谁做什么。"
            if actionable
            else "原任务已关闭或不存在；如需继续修改，请由新任务承接。"
        )
        await self._record_task_nudge(
            topic=topic,
            task=task,
            content=(
                f"任务 {card.task_id} 的 PR #{card.pr_number}（{card.pr_url}）"
                "被 GitHub 拒绝合并：\n"
                f"```\n{reason[:1500]}\n```\n"
                f"{action}"
            ),
            headline=say("mergeRefused", pr=card.pr_number),
            meta=notice(
                EVENT_MERGE_REFUSED,
                severity=SEVERITY_ERROR,
                who=WHO_CHEESE,
                detail=reason[:1500],
                detail_label=say("labelGithubReason"),
            ),
        )

    def _ci_nudge(
        self,
        *,
        card: AcceptCard,
        tail: str,
        stage: str,
        owner: str,
        repo: str,
    ) -> pr_signals.PendingNudge | None:
        return _ci_nudge(card=card, tail=tail, stage=stage, owner=owner, repo=repo)

    async def _review_nudge(
        self,
        *,
        card: AcceptCard,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status,
    ) -> pr_signals.PendingNudge | None:
        return await _review_nudge(
            card=card,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
        )

    def _conflict_nudge(
        self, *, card: AcceptCard, status
    ) -> pr_signals.PendingNudge | None:
        return _conflict_nudge(card=card, status=status)

    async def _dispatch_nudges(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        pending: list[pr_signals.PendingNudge],
        chat_service,
        runner,
    ) -> None:
        """Lock the card, then record each new event and parent intent.

        Signatures, task events and recipient intent commit together. A failed
        producer transaction leaves none of them; the next poll may recreate
        the event. Only committed intent can reach a native session.
        """
        saved_state = await self._session.scalar(
            select(AcceptCard.nudge_state)
            .where(AcceptCard.id == card.id)
            .with_for_update()
        )
        ledger = pr_signals.NudgeLedger.load(saved_state)
        fresh = [p for p in pending if not ledger.already_sent(p.kind, p.signature)]
        if not fresh:
            return
        task = (
            await TaskService(self._session).get(card.task_id) if card.task_id else None
        )
        actionable = task is not None and task.status == TaskStatus.open
        for nudge in fresh:
            if nudge.capped:
                continue
            await self._record_task_nudge(
                topic=topic,
                task=task,
                content=(
                    f"任务 {card.task_id}："
                    f'先执行 cd "$(cheese worktree {card.task_id})"。\n' + nudge.content
                    if actionable
                    else f"{nudge.event}。原任务已关闭或不存在；"
                    "如需继续修改，请由新任务承接。"
                ),
                headline=nudge.event,
                meta=notice(
                    nudge.event_type,
                    severity=SEVERITY_ERROR,
                    who=WHO_CHEESE,
                    detail=nudge.detail or None,
                    detail_label=nudge.detail_label or None,
                ),
            )
        loudest = max(fresh, key=lambda n: pr_signals.NOTE_PRIORITY[n.kind])
        if loudest.note:
            notes.record(card, loudest.note_code, loudest.note)
        for nudge in fresh:
            ledger.record(nudge.kind, nudge.signature)
        card.nudge_state = ledger.dump()

    async def _finish_pr_accept(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        merged_externally: bool = False,
        headline: str = "",
    ) -> None:
        """The PR is merged, so the work is accepted (#206).

        This used to also require the deploy workflow the merge triggered to
        reach success. That gate is gone: merged is a fact about git that holds
        for every project, while "deployed" is a per-project ops concept the
        platform was in no position to define — and cards waited on deploy runs
        that were sometimes never created at all (three real merges on main,
        2026-08-11, produced zero runs), which is a deadlock, not a safeguard.
        Watching the deploy is real work and it keeps a home: the webhook
        primitive already exists for a pipeline to post its outcome into the
        topic, and #190's ops room is where that judgment belongs.

        `headline` is prefixed onto the card's note when the merge was NOT the
        ordinary all-green one — today that means 人工放行 (`FORCE_MERGED_
        PREFIX`), whose whole point is that the card afterwards says who
        decided to merge red and why. It must survive this method, which
        otherwise rewrites `note` wholesale.
        """
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        by = card.decided_by
        how = say("prMergedExternally") if merged_externally else say("prMerged")
        settled = f"PR #{card.pr_number} {how}：{card.pr_url}"
        notes.record(card, None, f"{headline}；{settled}" if headline else settled)
        # 交付完成 ≠ 话题结束 (#442 decision 1)：话题保持 active，归档由人来做。
        await self._stamp_delivery(card, topic, by=by, at=now)
        await self._session.flush()
        await self._session.refresh(card)
        accepted_line = (
            say("acceptDone", actor=by, pr=card.pr_number, how=how)
            if by
            else say("acceptDoneNoActor", pr=card.pr_number, how=how)
        )
        self._notify_merge_result(
            topic,
            accepted_line,
            meta=notice(
                EVENT_ACCEPT_DONE,
                severity=SEVERITY_INFO,
                who=WHO_PLATFORM,
                detail=say("acceptDoneDetail", url=card.pr_url),
                detail_label=say("labelDeliveryNote"),
            ),
        )

    async def _resolve_forge(
        self, project_id: uuid.UUID, *, card: AcceptCard | None = None
    ) -> forge_mod.Forge:
        """Which forge this project's accept goes through — the one place the
        lane is decided (see app.domain.review.forge)."""
        return await forge_mod.resolve(
            project_id=project_id,
            session=self._session,
            proposal_url=card.pr_url if card is not None else None,
        )

    async def _stop_accept_pr_unavailable(
        self, card: AcceptCard, topic: Topic, reason: str
    ) -> NoReturn:
        """绑定 GitHub 的项目采纳永不落 local merge (#363): when the card's PR
        cannot be merged right now (GitHub unreachable, PR closed unmerged, …)
        the accept STOPS — visibly and retryably — instead of bypassing the PR
        and its CI with a direct push. The note is persisted outside this
        transaction because the ValidationError below rolls it back.

        **Roll back BEFORE writing that note.** `_note_outside_accept_txn` uses
        its own connection, and by the time we get here this request's
        transaction may already hold a row lock on the very card it wants to
        write (`_publish_pr_for_accept` opens the PR and flushes `pr_number`
        onto the card, and SQLAlchemy's autoflush can push that UPDATE out even
        without an explicit flush). Two connections, one row, and the one
        holding the lock is the one waiting for the other — the request hangs
        until something times it out, and "采纳按钮点下去没反应" is the worst
        possible presentation of a path whose entire job is to fail visibly.
        The rollback loses nothing: this method always raises, so the accept
        transaction was never going to commit, and the PR itself was already
        recorded durably by `pr_publish.record_pr` on its own connection.

        The room notification is built and dispatched first, while `topic` is
        still live — after a rollback its attributes are expired and reading
        them would go back to the database for no reason."""
        why = reason or say("acceptStoppedPrStalled", pr=card.pr_number)
        card_id = card.id
        note = (
            f"{_ACCEPT_PR_STALLED_PREFIX}（{why}）。绑定 GitHub 的项目采纳只通过"
            "合并 PR 完成，平台不会绕过 PR 直推上游；处理后重试采纳。"
        )
        self._notify_merge_result(
            topic,
            say("acceptStoppedPrUnavailable"),
            meta=notice(
                EVENT_ACCEPT_STOPPED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=say("acceptStoppedPrUnavailableDetail", why=why),
                detail_label=say("labelReason"),
            ),
        )
        await self._session.rollback()
        await self._note_outside_accept_txn(
            card_id, notes.NoteCode.accept_pr_stalled, note
        )
        raise ValidationError(say("acceptPrNotMerged", why=why))

    async def _stop_accept_no_branch(self, card: AcceptCard, topic: Topic) -> NoReturn:
        """带交付主张的卡开不出 PR，因为这棵树的分支上没有任何提交（2026-09-07
        卡 40be3e1a：改动被推到了别的分支）。旧路径把 `open_pr_for_card` 的 None
        当「纯讨论话题」落进本地合并 no-op——卡标成 accepted，人以为交付完成，而
        改动没有合进任何地方。主张交付却无从交付：停下，把该推哪条分支写在卡上，
        推上后重试采纳。

        Same rollback-first dance as `_stop_accept_pr_unavailable`, same reason:
        the note is written on its own connection and must never queue behind a
        row lock this doomed transaction still holds. Everything the note and
        notification need is read while the instances are live, before the
        rollback expires them."""
        card_id = card.id
        subject = (card.change_subject or "").strip()
        if card.task_id is None:
            raise ValidationError(say("legacyCardNoTask"))
        task_id = card.task_id
        work = await TaskService(self._session).require_in_room(topic.id, task_id)
        branch = work.branch_name
        note = (
            f"{_ACCEPT_NO_BRANCH_PREFIX}（{branch}），无法为「{subject}」开 PR。"
            f"改动可能提交到了其他分支，推送到 {branch} 后重新采纳。"
        )
        self._notify_merge_result(
            topic,
            say("acceptStoppedNoCommits"),
            meta=notice(
                EVENT_ACCEPT_STOPPED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=say(
                    "acceptStoppedNoCommitsDetail", subject=subject, branch=branch
                ),
                detail_label=say("labelReason"),
            ),
        )
        await self._session.rollback()
        await self._note_outside_accept_txn(
            card_id, notes.NoteCode.accept_no_branch, note
        )
        raise ValidationError(say("acceptBranchNoCommits", branch=branch))

    async def _publish_pr_for_accept(self, card: AcceptCard, topic: Topic) -> None:
        """无 PR 卡在采纳现场补开 App PR（#296 stage 1 的生产回归修复）.

        A fire-and-forget publish can fail or still be in flight when the
        human clicks — dropping such a card into the local-merge branch
        direct-pushed merge commits to main with no PR at all (dev: c33cfabf,
        8f9b9d94). The repair is to open the App PR HERE and let the click
        merge exactly that PR.

        Synchronous by design: the accept's outcome must depend on the publish
        result, and the click already runs the merge API call inside the
        accept request — one more push plus one create-PR call is the same
        latency class. Racing a still-in-flight fire-and-forget publish is
        benign: the push is force-with-lease of the same branch, `open_pr`
        adopts an already-open PR for the head instead of failing, and
        `record_pr` writes the same numbers this method records.

        On success the PR is recorded on the card DURABLY, outside the accept
        transaction (`pr_publish.record_pr`): if the accept goes on to fail —
        a refusal from the merge call raises ValidationError and rolls this
        request back — the card must keep the PR it now rides, or the next
        attempt would look PR-less again. `open_pr_for_card` can still return
        None (its own not-applicable checks); with the caller pre-checking
        `hosts_proposals`, in practice that means a topic with no tree branch.
        The card is left untouched, and the CALLER decides what a branchless
        topic means: a legacy card with no delivery claim proceeds into the
        no-op local merge, while a card claiming a change stops the accept
        (`_stop_accept_no_branch` — 2026-09-07 卡 40be3e1a).
        When opening the PR FAILS, the accept STOPS: the reason is persisted
        on the card outside this transaction, the room is told, and
        ValidationError surfaces to the caller. Silently direct-pushing main
        without a PR is never a fallback on a bound project (#363, all
        commits go through PR)."""
        try:
            pr = await pr_publish.open_pr_for_card(
                self._session,
                card_id=card.id,
                topic_id=topic.id,
                project_id=topic.project_id,
            )
        except Exception as exc:  # noqa: BLE001 — surface on the card; never direct-push
            # Read every attribute we still need BEFORE the rollback below:
            # rollback expires the instance, and an expired attribute reloads
            # itself with synchronous IO that an AsyncSession cannot perform
            # (MissingGreenlet) — which would replace this readable failure
            # with an unreadable one.
            card_id = card.id
            logger.exception("accept-time PR publication failed for card %s", card_id)
            reason = f"{exc}"[:300]
            note = (
                f"{_ACCEPT_PR_OPEN_FAILED_PREFIX}（{reason}）。"
                "平台不会在没有 PR 的情况下把改动直推上游；修复后重试采纳。"
            )
            self._notify_merge_result(
                topic,
                say("acceptStoppedPrOpenFailed"),
                meta=notice(
                    EVENT_ACCEPT_STOPPED,
                    severity=SEVERITY_ERROR,
                    who=WHO_HUMAN,
                    detail=say("acceptStoppedPrOpenFailedDetail", reason=reason),
                    detail_label=say("labelReason"),
                ),
            )
            # Roll back first, for the same reason as `_stop_accept_pr_
            # unavailable`: the out-of-transaction note writes the card row on
            # its own connection, and it must never be able to queue behind a
            # lock this doomed transaction is still holding.
            await self._session.rollback()
            await self._note_outside_accept_txn(
                card_id, notes.NoteCode.accept_pr_open_failed, note
            )
            raise ValidationError(say("acceptPrOpenFailed")) from exc
        if pr is None:
            return  # PR 路对这个项目/话题不适用 — 本地合并就是它唯一的采纳方式
        # Durable first (survives a later rollback of this request), then the
        # in-memory mirror so the rest of THIS accept sees the PR. Same
        # bind-not-global-factory reasoning as _note_outside_accept_txn.
        factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
        await pr_publish.record_pr(factory, card_id=card.id, pr=pr)
        card.pr_number = int(pr["number"])
        card.pr_url = str(pr.get("html_url") or "")[:255] or None
        if card.note_code is notes.NoteCode.pr_open_failed:
            notes.clear(card)  # mirror record_pr's stale-failure-note clearing
        await self._session.flush()

    async def _note_outside_accept_txn(
        self, card_id: uuid.UUID, code: notes.NoteCode, note: str
    ) -> None:
        return await _note_outside_accept_txn(self._session, card_id, code, note)

    async def merge_queued_pr(self, task_id: uuid.UUID) -> int | None:
        """The task's PR number while its card waits in the merge queue."""
        cards = await self._repo.list_live_for_places(
            [task_id], statuses=(AcceptStatus.pending,)
        )
        return next(
            (
                card.pr_number
                for card in cards
                if card.task_id == task_id
                and card.note_code == notes.NoteCode.waiting_merge_queue
            ),
            None,
        )

    async def _cancel_queued_accept(self, card: AcceptCard) -> None:
        if card.note_code != notes.NoteCode.waiting_merge_queue:
            return
        assert card.pr_number is not None
        topic = await self._topic_or_404(card.topic_id)
        creds, why = await self._pr_poll_credentials(card, topic)
        if creds is None:
            raise ValidationError(say("mergeQueueLeaveFailed", why=why))
        owner, repo = await self._pr_repo_of(card, topic)
        client = await self._status_client(topic.project_id)
        dequeue = getattr(client, "dequeue_pull_request", None)
        if dequeue is None:
            raise ValidationError(say("mergeQueueLeaveUnsupported"))
        await dequeue(owner=owner, repo=repo, number=card.pr_number, token=creds.write)
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=card.pr_number, token=creds.read
        )
        if status.merged:
            raise ValidationError(say("prMergedCantWithdraw"))

    async def reject(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError(say("reviewEndedNoReturn"))
        if decided_by != card.reviewer_handle:
            raise ForbiddenError(say("returnReviewerOnly"))

        await self._cancel_queued_accept(card)
        card.status = AcceptStatus.rejected
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        notes.record(card, None, note)

        # Topic stays active on rejection.
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def revoke(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # Accept is revocable (spec §6.3): only an accepted card can be revoked.
        if card.status != AcceptStatus.accepted:
            raise ValidationError(say("revokeAcceptedOnly"))

        # Only the person who accepted it, or someone who manages the project,
        # may revoke — not any arbitrary handle.
        topic = await self._topic_or_404(card.topic_id)
        if decided_by != card.decided_by and not await MemberService(
            self._session
        ).manages(topic.project_id, decided_by):
            raise ValidationError(say("revokeAcceptForbidden"))

        card.status = AcceptStatus.revoked
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)

        # 撤销的是这次**验收记录**，不是这次合并 —— PR 已经在 main 上了，git 层面
        # revoke 什么都没撤。只清采纳标记；任务仍然关闭，后续修改用新任务交付。
        #
        # 归档状态一律不动，这是 2026-08-17 的对称面：`TopicService.unarchive` 的
        # docstring 说「取消归档不改写采纳记录，那要用撤回采纳」；反过来同理——
        # 撤回采纳不改写归档状态，那是人的决定（取消归档）。以前这里要把话题拉回
        # active，是因为采纳会顺手归档；采纳不再归档之后，一张卡的撤销没有理由
        # 覆盖某个人「把这个话题收起来」的动作。
        await self._stamp_delivery(card, topic, by=None, at=None)

        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def merge_despite_checks(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        reason: str = "",
        head_sha: str | None = None,
    ) -> AcceptCard:
        """人明知规则没满足，仍然决定合并——**署名的**显式出口（人工放行）。

        为什么必须有：红着合有时候是对的。CI 基础设施抽风、与本次改动无关的既有
        失败、赶时间的热修——真正不能接受的不是「红着合」，而是**没有人做过这个
        决定**。平台自己的默认因此是拒绝（`_merge_pr_for_accept`：合并态不是
        clean/unstable 就不合），而这条出口是另一半：**显式放行，且放行必须
        签字**。

        平台不重算「这段代码好不好」：它只把 forge 的结论如实呈上，然后让一个
        **具名的人**在上面按手印。它记什么：谁、什么时候、**当时的检查到底是
        什么状态**（现读一次，读不到就如实写读不到——但绝不因此拒绝放行，凭据坏
        了不该把人锁在门外）、以及人自己写的理由。

        谁能点 (#718)：项目分支保护的人工放行名单（`override_handles`，没配置
        = 项目所有者 + 团队的所有者和管理员）。芝士被 `_forbid_ai` 挡在外面
        （跟 accept/approve/void 同一条线），路由也**故意不进** `app/main.py`
        的 `_CHEESE_WRITE_PATHS`——
        照 `void` 的先例：不进白名单本身拦不住任何东西（没列进去的写路由压根不
        过那个中间件），真正拦住芝士的是这里的 `_forbid_ai` 加路由上的登录校验。

        放行**放的是规则，不是眼睛**：它跟采纳一样要声明「我看的是哪一版」
        （`_seen_head_or_refresh`）。签字的人要为一段具体的代码背书，屏幕上那版
        已经不在了、或者卡面压根没显示过任何版本的时候，这个签名就落到了别的东西
        上。
        """
        from app.domain.project.protection import branch_protection_of

        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending or card.pr_merged_at is not None:
            raise ValidationError(say("overridePendingOnly"))
        if card.pr_number is None:
            raise ValidationError(say("noMergeablePr"))

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "人工放行")

        protection = branch_protection_of(project)
        if protection.override_handles is not None:
            allowed = set(protection.override_handles)
        else:
            allowed = (
                {decided_by}
                if await MemberService(self._session).manages(
                    topic.project_id, decided_by
                )
                else set()
            )
        if decided_by not in allowed:
            raise ForbiddenError(say("overrideListOnly"))
        # 骑着 PR 的卡在这里必然带着一个被展示过的 sha：卡面从没显示过 head 的
        # （刚递、轮询器还没镜像）会被刷新并要求重看，而不是拿现读的 head 去合。
        seen_head = await self._seen_head_or_refresh(
            card, topic, head_sha, say("verbMergeAnyway")
        )
        assert seen_head is not None  # PR lane; the guard above rules None out

        forge = await self._resolve_forge(topic.project_id, card=card)
        return await forge.merge_despite_checks(
            self, card, topic, decided_by, seen_head=seen_head, reason=reason
        )

    async def _override_github_checks(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str,
        reason: str,
    ) -> AcceptCard:
        assert card.pr_number is not None  # The shared override entry requires a PR.
        creds, why = await self._pr_poll_credentials(card, topic)
        if creds is None:
            raise ValidationError(say("overrideNoGithubCredentials", why=why))

        owner, repo = await self._pr_repo_of(card, topic)
        client = await self._status_client(topic.project_id)
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=card.pr_number, token=creds.read
        )
        if not status.merged:
            dependency = await self._dependency_block_reason(card, status)
            if dependency:
                raise ValidationError(dependency)
        # 留痕用，不是门禁：读一次「此刻检查是什么状态」，读不到也照样放行。
        state: str | None = None
        try:
            state, tail = await client.check_state(
                owner=owner, repo=repo, ref=seen_head, token=creds.read
            )
            checks_at_merge = say(
                "forceMergedChecks",
                state=state,
                line=tail.splitlines()[0] if tail else "",
            )
        except Exception as exc:  # noqa: BLE001 — a broken read must not lock a human out
            logger.warning(
                "force-merge check read failed for card %s: %s", card.id, exc
            )
            state = None
            checks_at_merge = say("forceMergedChecksUnread")
        # 读到的状态决定这句话怎么写：全绿时说「明知未全绿」是往历史里写一条从没
        # 发生过的决定（PR #520 真的这么记了一条）。
        verdict = _force_merge_verdict(state)

        number = card.pr_number
        who = await identity.attribution(
            self._session, topic, card=card, decided_by=decided_by
        )
        from app.domain.project.forge import ensure_author_email

        if who.author:
            await ensure_author_email(topic.project_id, self._session, who.author.email)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(topic, decided_by, card, who),
            # 放行合的也是人看到的那个 commit：head 变了 GitHub 409，卡刷新。
            sha=seen_head,
        )
        if result.stale_head:
            await self._refresh_stale_card(
                card, topic, live_head="", action=say("verbMergeAnyway")
            )
            raise ValidationError(say("prNewCommitsDuringOverride", number=number))
        if result.queued:
            await self._record_queue_entry(card, decided_by)
            return card
        if result.sha is None:
            raise ValidationError(
                say(
                    "githubMergeRefused",
                    pr=number,
                    reason=result.blocked_reason or "未说明原因",
                )
            )

        now = datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%d %H:%M UTC")
        tail_reason = (
            say("forceMergedReason", reason=reason.strip()) if reason.strip() else ""
        )
        headline = (
            f"{FORCE_MERGED_PREFIX}：<@{decided_by}> 于 {stamp} 人工放行合并"
            f"（{verdict}；合并时检查状态：{checks_at_merge}）{tail_reason}"
        )
        card.pr_merged_at = now
        await self._mark_task_merged(card, delivered_head=seen_head)
        card.pr_head_sha = result.sha
        await self._repo.add_approval(card.id, decided_by)
        card.decided_by = decided_by
        card.decided_at = now
        await self._finish_pr_accept(card=card, topic=topic, headline=headline)
        logger.warning(
            "card %s: PR #%s force-merged by %s (checks: %s)",
            card.id,
            number,
            decided_by,
            checks_at_merge,
        )
        self._notify_merge_result(
            topic,
            say("forceMerged", actor=f"<@{decided_by}>", pr=number),
            meta=notice(
                EVENT_FORCE_MERGED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=say(
                    "forceMergedDetail",
                    verdict=verdict,
                    checks=checks_at_merge,
                    reason=tail_reason,
                    url=card.pr_url or "",
                ),
                detail_label=say("labelForceMergeRecord"),
            ),
        )
        return card

    async def void(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        """人工作废一张未决的验收卡 (pending_gate 孤儿卡出口, 2026-08-11).

        这是**唯一**能把非终态卡强制收尾的人工动作。它存在的理由是 `create_card`
        的互斥：一张卡卡在 `pending_gate` / `conflict` 上，整个话题就再也递不出
        第二张卡，而 accept/reject/revoke/reassign 四条路由对这些状态全部是拒绝
        的——出口是零。

        ⚠️ **它把卡置为终态，不是"放行到 pending"**。放行等于让卡面的绿勾替一段
        从没被检查过的代码背书；作废 + 重递效果一样而且安全，这条区别是本功能的
        设计前提，不要"优化"掉。

        授权：卡上的验收人、项目所有者、团队的所有者和管理员。它是授权类动作，所以芝士在
        collaborative 模式下被 `_forbid_ai` 挡住（跟 accept/approve 同一条线）
        —— 路由也**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`。
        """
        card = await self._card_or_404(card_id)
        if card.status not in archive.OPEN_CARD_STATUSES:
            raise ValidationError(say("reviewEndedNoVoid"))

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "作废")

        if decided_by != card.reviewer_handle and not await MemberService(
            self._session
        ).manages(topic.project_id, decided_by):
            raise ForbiddenError(say("voidForbidden"))

        await self._cancel_queued_accept(card)
        was = card.status
        reason = say("cardVoidedReason", note=note.strip()) if note.strip() else ""
        headline = (
            f"{VOIDED_PREFIX}：<@{decided_by}> 作废于状态「{was}」。"
            f"话题可以重新提交审阅。{reason}"
        )
        if archive.pr_left_open(card):
            # 跟归档收敛同一条产品判断 (review/archive.py 的模块 docstring)：平台
            # 不拿别人的 token 去关别人名下的 PR。停止跟进 + 留痕。
            headline = (
                f"{VOIDED_PREFIX}：<@{decided_by}> 作废了这次审阅，平台已停止跟进 "
                f"PR #{card.pr_number}。PR 未合并，仍在 GitHub 上打开，需要人决定"
                f"合并还是关闭：{card.pr_url or '（无链接）'}{reason}"
            )
        card.status = AcceptStatus.revoked
        notes.record(
            card, notes.NoteCode.voided, archive.prefix_note(card.note, headline)
        )
        # 只在空的时候补：作废人始终写在 note 里，已有的决议痕迹不覆盖。
        if card.decided_by is None:
            card.decided_by = decided_by
        if card.decided_at is None:
            card.decided_at = datetime.now(UTC)

        await self._session.flush()
        await self._session.refresh(card)

        # 作废的是这张卡，落点就是这张卡（结论 14）：房间主线那一档只留给为房间
        # 本身递的卡（`task_id` 空）。
        landed = landing(
            EventAbout.task if card.task_id is not None else EventAbout.room,
            project_id=topic.project_id,
            room_id=topic.id,
            task_id=card.task_id,
        )
        await BlockRepository(self._session).add(
            project_id=landed.project_id,
            topic_id=landed.topic_id,
            task_id=landed.task_id,
            author="cheese",
            author_type=AuthorType.platform,
            content=say("cardVoided", actor=f"<@{decided_by}>"),
            kind=BlockKind.event,
            meta={
                "platform": True,
                **notice(
                    EVENT_CARD_VOIDED,
                    severity=SEVERITY_INFO,
                    who=WHO_CHEESE,
                    detail=say("cardVoidedDetail", state=f"{was}", reason=reason),
                    detail_label=say("labelVoidNote"),
                ),
            },
        )
        return card
