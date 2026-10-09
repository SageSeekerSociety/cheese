"""Who may review or decide: the default-reviewer ladder, the
in-the-room check, re-routing, the AI refusal, and the overdue clock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import listing, say
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.project.models import AiMode, Project
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
)
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    pass

from app.domain.project.protection import branch_protection_of
from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    _REQUIRED_CHECK_GRACE_MINUTES,
    ReviewerAdmission,
)


async def _reviewer_or_project_default(
    self: pkg.AcceptService,
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

    explicit = (reviewer_handle or "").strip()
    if explicit:
        return explicit
    handed_to = sorted(
        {t.reviewer_handle for t in (from_work or []) if t.reviewer_handle}
    )
    if len(handed_to) > 1:
        raise ValidationError(say("reviewersDiffer", reviewers=listing(handed_to)))
    if handed_to:
        return handed_to[0]
    default = branch_protection_of(project).default_reviewer
    if default:
        return default
    raise ValidationError(say("reviewerUnnamed"))


async def _require_reviewer_in_room(
    self: pkg.AcceptService, topic: Topic, handle: str, admits: ReviewerAdmission
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


async def reassign(
    self: pkg.AcceptService,
    *,
    card_id: uuid.UUID,
    reviewer_handle: str | None = None,
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
    await self._session.flush()
    await self._session.refresh(card)
    return card


def _forbid_ai(
    self: pkg.AcceptService, project: Project | None, handle: str, action: str
) -> None:
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
        raise ValidationError(say("aiCannotOwnChange", action=action))


def _required_absence_overdue(self: pkg.AcceptService, card: AcceptCard) -> bool:
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
