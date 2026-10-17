"""反馈的可见性与权限判据 —— 从 `services.py` 的「权限」那一段整段拆出来。

「谁管反馈」是反馈自己的名单（`settings.feedback_triage_handles`），**不是**平台
管理员（`app/domain/admin/`）。私密反馈是提交者选择不给所有人看的东西，而平台
管理员是一群要用管理台做别的事的人 —— 当上平台管理员不等于能读每一条私密反馈。
名单只在部署配置里，产品里没有能往里加人的页面。

这里只有判据：一条可见性并集（`may_see`）、它不是的另两个判据（谁能删一条评论、
谁能删整条）、领取资格，以及「这个房间读得到反馈中心吗」。`FeedbackService` 上的
同名方法是转发。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.auth.project_access import may_read_topic
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.feedback import claims
from app.domain.feedback.models import Feedback, FeedbackComment, FeedbackVisibility

if TYPE_CHECKING:
    from app.domain.feedback import services as pkg


async def is_admin(svc: pkg.FeedbackService, handle: str | None) -> bool:
    return bool(handle) and handle in settings.feedback_triage_handles


async def require_admin(svc: pkg.FeedbackService, handle: str | None) -> str:
    if not handle:
        raise ForbiddenError(say("signInRequired"))
    if not await svc.is_admin(handle):
        # 403, not 404: /admin/feedback is documented as existing.
        raise ForbiddenError(say("feedbackAdminRequired"))
    return handle


async def may_see(
    svc: pkg.FeedbackService, row: Feedback, *, handle: str | None, is_admin: bool
) -> bool:
    """The visibility union — 公开 + 管理员 + 是我提的 + **提它的那个房间**.

    结论 47 的三档：开发者（管理员）看得见，提出它的那个房间的成员看得见，反馈
    中心里其他人看不见。前两档一直在这里；第三档是房间这一档，它补的是同一条结
    论的第二句——agent 提的内容在它的房间里全部留痕，所以对那个房间的人藏起来，
    藏掉的只是追踪它的那条路，藏不掉内容。

    A private report is visible to admins **and to the person who filed it**:
    the reporter must be able to follow their own report, and the agent that
    filed on their behalf counts as them. Everyone else cannot, and gets 404
    rather than 403 — see `visible_row`.

    房间这一档取的是**反馈提出那一刻**的名册（`filed_in_a_room_of`），不是现在
    的：按现在取的话，把谁加进这个房间就等于把这个房间历史上的每一条私密反馈一
    并交给他，而加人的那个人并不知道自己在授权。

    它**还要**今天读得到那个房间（`may_read_topic`），两句都成立才开门。少了后
    一句，这一档就是一扇撤不回的门：退房间是硬删名册行，所以撤得掉；退项目不是
    —— `MemberService.remove` 只删 `ProjectMember` 一行，被移出项目的人从此进
    不了这个项目的任何房间，而他在各房间的名册行原地不动。那些行今天是惰性的
    （房间门读的是项目读权，`may_read_topic`），本档把它们变成门，于是「一次加
    人变成一次授权」在离场那一侧原样长了回来。判据留在这里而不是靠名册干净：
    退项目、退队、不再是出题者是三条路，只有「今天还读得到吗」一句话同时管住。

    问的是**房间自己的项目**（`may_read_topic` 从 topic 解出来），不是
    `row.project_id`：这一档的授权键只有一个，就是 `topic_id`，那么项目也必须从它
    解出来。读第二列等于让两个可以各自漂开的字段回答同一个问题——房间被移去别的项
    目、或者哪天 `project_id` 换个来源，两列就不再说同一件事，而鉴权只能有一个答案。

    `security` narrows the public arm, so it is checked in the same breath:
    a row an admin flagged as a security matter is not public even though the
    reporter left `visibility` at its default. That flag is set on triage, by
    someone other than the reporter, and it is the one that must not leak.
    It does **not** narrow the room arm, for the same reason the author keeps
    access to a row that was flagged: 「不能泄露」说的是泄露给没看过它的人，而
    那个房间的人看过。

    Async because the roster and the project's claims are tables — the three
    arms above are on the row, this one is not. Those queries are reached
    only by a row the three cheap arms already refused, and `topic_id is
    None` (报在沙箱里、或房间已删) turns both of them off before either runs.
    """
    if row.visibility == FeedbackVisibility.public and not row.security:
        return True
    if is_admin:
        return True
    if not handle:
        return False
    if handle in (row.author_handle, row.submitted_by_handle):
        return True
    if row.topic_id is None:
        return False
    if not await svc._repo.filed_in_a_room_of_mine(row.id, handle):
        return False
    return await may_read_topic(svc._session, topic_id=row.topic_id, handle=handle)


async def visible_row(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    *,
    handle: str | None,
    is_admin: bool,
) -> Feedback:
    """Load a row or raise 404 — including when the row exists but is hidden.

    Not 403: a 403 on a private report confirms the report exists, which is
    itself the thing the reporter asked to keep quiet. The design note (§4.3)
    makes this the rule for every id-taking feedback endpoint.
    """
    row = await svc._repo.get(feedback_id)
    if row is None or not await svc.may_see(row, handle=handle, is_admin=is_admin):
        raise NotFoundError(say("feedbackNotFound"))
    return row


async def visible_ref(
    svc: pkg.FeedbackService, ref: str, *, handle: str | None, is_admin: bool
) -> Feedback:
    """`visible_row`, by `FB-12` or uuid. Unknown and invisible are one 404."""
    row = await claims.find(svc._session, ref)
    if row is None or not await svc.may_see(row, handle=handle, is_admin=is_admin):
        raise NotFoundError(say("feedbackNotFound"))
    return row


def may_delete_comment(
    svc: pkg.FeedbackService,
    row: FeedbackComment,
    *,
    handle: str | None,
    is_admin: bool,
) -> bool:
    """Who may delete a comment — the author, or an admin.

    Deleting someone else's words is the kind of thing that should be
    visible as an admin action, so admins can — but the author always can.

    One predicate, asked from **two** places: `delete_comment` before it
    deletes, and `comments_out` to fill `CommentOut.can_delete`. Two
    spellings of it is how 「按钮画得出来、点下去 403」 gets invented, and
    this feature has already paid for that lesson once — the support button
    stayed lit on `deployed` rows because the client re-derived a rule the
    service had (see `CLOSED_STATUSES`). The client never re-derives this
    one: it draws the button when the server says `can_delete`.
    """
    return is_admin or (handle is not None and row.author_handle == handle)


def may_delete_feedback(
    svc: pkg.FeedbackService, row: Feedback, *, handle: str | None, is_admin: bool
) -> bool:
    """谁可以删掉**一整条反馈** —— 提它的人，或者平台管理员。

    和评论那条判据（`may_delete_comment`）同一个形状，但作者那一档多算一个人：
    一条反馈有**两个**都算「我提的」的 handle —— 写它的（agent 提案时是那个 agent）
    和按下发送的（人）。只认前者的话，从提案卡提交的人删不掉自己刚提交的东西；
    只认后者的话，agent 自己经手的那条谁也删不掉（`submitted_by_handle` 在那种
    情况下可能是别人）。

    管理员这一档是需求方要的（「不然我怕有人恶意刷」）：删除是**事后清理**，
    挡不住刷 —— 那要在提交侧限流。这里给的只是「× 掉一条」的能力，而这正是
    管理员今天没有的那一个。

    判据只有这一处：`delete_feedback` 删之前问它，`detail_of` 拿它填
    `can_delete`。两处各写一遍就是「按钮画得出来、点下去 403」的来源，这个功能
    已经为那个形状付过一次学费（见 `may_delete_comment` 的 docstring）。
    """
    if is_admin:
        return True
    if handle is None:
        return False
    return handle in (row.author_handle, row.submitted_by_handle)


async def may_claim(
    svc: pkg.FeedbackService,
    handle: str | None,
    *,
    is_admin: bool,
    room_project_id: uuid.UUID | None = None,
) -> bool:
    """谁能领取一条（看得见它之外）—— 反馈管理员，或者在做知是本身的人。

    和 `may_delete_feedback` 同一个用法：`claim` 领之前问它，`detail_of` 拿它填
    `can_claim`，按钮照服务端的答案画。`room_project_id` 见
    `claims.works_on_platform`。
    """
    if not handle:
        return False
    if is_admin:
        return True
    return await claims.works_on_platform(
        svc._session, handle, room_project_id=room_project_id
    )


async def require_platform_room(
    svc: pkg.FeedbackService, room_project_id: uuid.UUID
) -> None:
    """403 unless a room in this project may read the feedback center.

    An agent reads the center from its room (its credential is bound to one),
    and only from a room of a project working on the platform itself — the
    same projects that may claim (`claims.works_on_platform`). Elsewhere it
    could read the reports but not claim one, so it would only be reading
    work it may not do; it is told why instead.
    """
    if not await claims.is_platform_project(svc._session, room_project_id):
        raise ForbiddenError(say("feedbackReadPlatformRoomsOnly"))
