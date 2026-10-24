"""反馈的写与领取 —— 从 `services.py` 的「写」与「领取」两段拆出来。

状态和 timeline 一起动这一个决定在这里：写 `status` 只有 `_move` 一条路，所以
timeline 不会变成「发生过什么」的部分记录。领取也是写的一种（占住 assignee），
和状态机共用同一批前置判据。

`FeedbackService` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from app.core.sentences import say
from app.domain.feedback import announce, claims
from app.domain.feedback import repositories as repo
from app.domain.feedback.models import Feedback, FeedbackStatus
from app.domain.feedback.proposals import AcceptedProposal
from app.domain.feedback.schemas import FeedbackCreate, FeedbackPatch
from app.domain.feedback.services.vocabulary import STATUS_LADDER
from app.domain.identity.services import IdentityService

if TYPE_CHECKING:
    from app.domain.feedback import services as pkg


async def create(
    svc: pkg.FeedbackService,
    body: FeedbackCreate,
    *,
    actor_handle: str,
    actor_user_id: int | None,
    proposal: AcceptedProposal | None = None,
) -> Feedback:
    """File a report.

    `security` is not a body field: it is set by an admin on triage
    (`PATCH /admin/feedback/{id}`), because the reporter's own `visibility`
    already covers "do not show this to everyone", and a reporter marking
    their own report as a security matter is a claim, not a fact.

    **An agent cannot publish on its own** (§5.2). Without this branch an
    agent that went sideways could write straight into the public list; with
    it, the only way an agent-authored report exists is that a person
    pressed send on the card. The refusal names the tool so the model gets
    a next step rather than a dead end.

    The same rule closes the other door: an agent sending an *existing*
    proposal is still an agent publishing itself, just in two steps. Both
    branches sit here rather than in the routes so that neither door depends
    on a caller remembering to pass the flag honestly — and the answer is
    read here too, from the agent-binding the handle does or does not carry,
    so the door no longer depends on a caller passing a flag at all.

    When `proposal` is set, authorship comes from the proposal (the agent
    that found it) and `submitted_by` from the verified caller (the person
    who sent it) — two fields, not one, so 「芝士提的反馈里有多少真的被人发出
    去了」 stays answerable. The *content* still comes from the request body:
    the drawer lets the sender edit before sending, and the person pressing
    send is accountable for what they send.
    """
    if await IdentityService(svc._session).is_agent(actor_handle):
        raise ForbiddenError(say("feedbackAgentCannotPublish"))
    if proposal is not None:
        return await svc._create_from_proposal(
            body,
            proposal=proposal,
            submitted_by_handle=actor_handle,
            submitted_by_user_id=actor_user_id,
        )
    # The author's daily cap, and it sits HERE rather than above the proposal
    # branch on purpose: that branch is already limited (per topic, per
    # fingerprint, and against what was dismissed), and its author is the
    # agent that found the problem, not the person who sent it. This cap is
    # about a person or a script writing straight into a public list.
    #
    # Lock first, then count: the pair is a read-then-insert, and the whole
    # point is the moment somebody is at the cap. See `lock_author`.
    await svc._repo.lock_author(actor_handle)
    # Rolling 24 hours, not a calendar day — the same choice, for the same
    # reason, as the proposal cap: a calendar day resets at midnight, so two
    # minutes either side of it fit twice the limit.
    recent = await svc._repo.count_author_since(
        actor_handle, datetime.now(UTC) - timedelta(days=1)
    )
    cap = settings.feedback_reports_per_author_per_day
    if recent >= cap:
        raise PreconditionFailedError(say("feedbackDailyLimit", recent=recent, cap=cap))
    visibility = body.visibility
    row = await svc._repo.add(
        title=body.title,
        summary=body.summary or body.title,
        kind=body.kind,
        visibility=visibility,
        problem=body.problem,
        author_handle=actor_handle,
        author_user_id=actor_user_id,
        # False, and not a variable: the branch above already refused every
        # agent, so the only author reaching this row is a person. An
        # agent-authored report exists solely via `_create_from_proposal`.
        author_is_agent=False,
        why=body.why,
        expectation=body.expectation,
        what_happened=body.what_happened,
        repro=body.repro,
        evidence=body.evidence,
        logs=body.logs,
        session_id=body.session_id,
        environment=body.environment,
        submitted_by_handle=None,
        submitted_by_user_id=None,
        # 没有房间来源，而且不是「客户端这次没填」：请求体里根本没有这个字段
        # （`FeedbackCreate`）。人从反馈中心提的一条不是在任何房间里说的话，
        # 所以房间那一档对它关着——`may_see` 的 `row.topic_id is None` 早退。
        topic_id=None,
        project_id=None,
        priority=body.priority,
        tags=body.tags,
    )
    # The first timeline entry, written in the same transaction as the row:
    # a report with no `received` entry would show an empty history for the
    # one event that definitely happened.
    await svc._repo.append_timeline(row.id, FeedbackStatus.received, actor_handle)
    # 附件和这条反馈同一笔事务：报告发出去了、截图没挂上，屏幕上就是一条没有证据的
    # 报告，而人以为自己传了。有一个 id 挂不上，整条提交退回（`attach` 的说明）。
    await svc.attach_attachments(
        feedback_id=row.id,
        attachment_ids=body.attachment_ids,
        uploader_handle=actor_handle,
    )
    announce.after_commit(svc._session, row)
    return row


async def _create_from_proposal(
    svc: pkg.FeedbackService,
    body: FeedbackCreate,
    *,
    proposal: AcceptedProposal,
    submitted_by_handle: str,
    submitted_by_user_id: int | None,
) -> Feedback:
    row = await svc._repo.add(
        title=body.title,
        summary=body.summary or body.title,
        kind=body.kind,
        visibility=body.visibility,
        problem=body.problem,
        author_handle=proposal.author_handle,
        author_user_id=None,
        author_is_agent=True,
        why=body.why,
        expectation=body.expectation,
        what_happened=body.what_happened,
        repro=body.repro,
        evidence=body.evidence,
        logs=body.logs,
        session_id=body.session_id,
        environment=body.environment,
        submitted_by_handle=submitted_by_handle,
        submitted_by_user_id=submitted_by_user_id,
        # 从卡上取，不从请求体取——和作者同一条理由，而房间比作者更要紧：它是
        # 可见性并集里「提出它的那个房间」那一档的授权键（`may_see`）。
        topic_id=proposal.topic_id,
        project_id=proposal.project_id,
        priority=body.priority,
        tags=body.tags,
    )
    # The timeline's first entry names the person, not the agent: the agent
    # did not move this report into `received`, the person did.
    await svc._repo.append_timeline(
        row.id, FeedbackStatus.received, submitted_by_handle
    )
    # 上传者是按下发送的人，不是提案的 agent：文件是他在表单里选（或粘）上去的。
    await svc.attach_attachments(
        feedback_id=row.id,
        attachment_ids=body.attachment_ids,
        uploader_handle=submitted_by_handle,
    )
    announce.after_commit(svc._session, row)
    return row


async def set_status(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    status: FeedbackStatus,
    *,
    by_handle: str | None,
    note: str | None = None,
) -> Feedback:
    """Move a report, writing the history entry with it.

    There is no route that writes `status` without going through here — the
    two writes share the caller's transaction, so the timeline cannot end up
    describing a state the row never had.
    """
    row = await svc._repo.get(feedback_id)
    if row is None:
        raise NotFoundError(say("feedbackNotFound"))
    if row.status == status:
        # Idempotent, but not silent: re-setting the same status is a no-op
        # rather than a second timeline entry, because two identical entries
        # a second apart read as a bug in the history.
        return row
    await svc._move(row, status, by_handle=by_handle, note=note)
    return row


async def advance(
    svc: pkg.FeedbackService,
    row: Feedback,
    status: FeedbackStatus,
    *,
    by_handle: str | None,
    note: str | None = None,
    at: datetime | None = None,
) -> bool:
    """Record that a report reached `status`, if it has not reached it yet.

    The platform's own steps come through here — 处理中 when someone is
    assigned, 已修复 when the fixing PR merges. People rarely press those two
    buttons (a fix can land minutes after the report), and a ladder reading
    已上线 above a 处理中 that says 「未开始」 contradicts itself. Unlike
    `set_status` this only moves forward: a report already at or past
    `status` is left where it is, and so is one off the ladder (`declined`,
    a person's answer), so an automatic step never undoes what a person
    decided.

    `at` is when the event happened, when that is not now — a merge is
    recorded at the PR's merge time, not at the deploy that noticed it.
    """
    if row.status not in STATUS_LADDER:
        return False
    if STATUS_LADDER.index(row.status) >= STATUS_LADDER.index(status):
        return False
    await svc._move(row, status, by_handle=by_handle, note=note, at=at)
    return True


async def _move(
    svc: pkg.FeedbackService,
    row: Feedback,
    status: FeedbackStatus,
    *,
    by_handle: str | None,
    note: str | None,
    at: datetime | None = None,
) -> None:
    await svc._repo.set_status(row, status)
    entry = await svc._repo.append_timeline(row.id, status, by_handle)
    entry.note = note
    if at is not None:
        entry.at = at


async def patch_admin(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    body: FeedbackPatch,
    *,
    by_handle: str,
) -> Feedback:
    row = await svc._repo.get(feedback_id)
    if row is None:
        raise NotFoundError(say("feedbackNotFound"))
    if body.priority is not None:
        await svc._repo.set_priority(row, body.priority)
    if body.assignee_handle is not None:
        # Empty string clears the assignee; a handle assigns. `None` means
        # "not in this request" — see FeedbackPatch.
        await svc._repo.set_assignee(row, body.assignee_handle or None)
        if body.assignee_handle:
            # Someone is on it from this moment: that is 处理中.
            await svc.advance(
                row,
                FeedbackStatus.in_progress,
                by_handle=by_handle,
                note=f"已指派给 {body.assignee_handle}",
            )
    if body.security is not None and body.security != row.security:
        await svc._repo.set_security(row, body.security)
        # Marking something a security matter is a routing decision the
        # reporter should be able to see, and it is also the moment the
        # report stops being visible to colleagues. Neither is obvious from
        # the row alone, so it goes in the history.
        await svc._repo.append_timeline(row.id, row.status, by_handle)
    return row


async def claim(
    svc: pkg.FeedbackService,
    ref: str,
    *,
    handle: str,
    is_admin: bool,
    room_project_id: uuid.UUID | None = None,
) -> Feedback:
    """领取：这条记到 `handle` 名下，并从此刻算「处理中」。

    领取是为了**别让两个人修同一个问题**，所以第二个人的领取必须失败，而且要
    说出是谁在修 —— 他下一步是去找那个人，不是换个说法再领一次。判「有没有人」
    之前先锁住这一行（`claims.lock`）：两个同时到的领取，后到的那个读到的是先到的
    已经提交的持有人。

    自己再领一次是空操作，不是第二条时间线。已经办完的（`CLOSED_STATUSES`：
    已修复、已上线、不修复）不接领取：领取记的是「谁在修」，这些没有可修的了，
    回 409 让 agent 不要动它，而不是在详情上挂一个处理人。
    """
    row = await svc.visible_ref(ref, handle=handle, is_admin=is_admin)
    if not await svc.may_claim(
        handle, is_admin=is_admin, room_project_id=room_project_id
    ):
        raise ForbiddenError(say("feedbackClaimDevelopersOnly"))
    locked = await claims.lock(svc._session, row.id)
    if locked is None:
        raise NotFoundError(say("feedbackNotFound"))
    if locked.status in repo.CLOSED_STATUSES:
        raise ConflictError(say("feedbackClaimClosed"))
    holder = locked.assignee_handle
    if holder == handle:
        return locked
    if holder is not None:
        raise ConflictError(
            say("feedbackClaimedByOther", holder=holder), data={"holder": holder}
        )
    await svc._repo.set_assignee(locked, handle)
    await svc.advance(
        locked,
        FeedbackStatus.in_progress,
        by_handle=handle,
        note=claims.CLAIMED_NOTE,
    )
    return locked


async def release(
    svc: pkg.FeedbackService, ref: str, *, handle: str, is_admin: bool
) -> Feedback:
    """放弃领取：持有人自己，或反馈管理员。没人领着时是空操作。

    状态退回「已收录」：领取把一条反馈推成「处理中」，放弃之后就没有人在修了，
    留着「处理中」会让详情自相矛盾 —— 状态说有人在弄，处理人却是空的。退回和它
    那条时间线一起写（`set_status` 走唯一的 `_move`），所以历史里读得到是放下
    这件事发生在什么时候。本来就不在「处理中」的不动。

    已经办完的（`CLOSED_STATUSES`）不接放弃：领取和放弃是修这件事的两端，办完
    之后这一组动作一起关掉。管理员要清掉挂错的人走 `patch_admin` 的改派，那是
    数据修正，不是「放弃领取」。
    """
    row = await svc.visible_ref(ref, handle=handle, is_admin=is_admin)
    locked = await claims.lock(svc._session, row.id)
    if locked is None:
        raise NotFoundError(say("feedbackNotFound"))
    if locked.status in repo.CLOSED_STATUSES:
        raise ConflictError(say("feedbackReleaseClosed"))
    holder = locked.assignee_handle
    if holder is None:
        return locked
    if holder != handle and not is_admin:
        raise ForbiddenError(say("feedbackReleaseHolderOnly", holder=holder))
    await svc._repo.set_assignee(locked, None)
    if locked.status == FeedbackStatus.in_progress:
        await svc.set_status(
            locked.id,
            FeedbackStatus.received,
            by_handle=handle,
            note=claims.RELEASED_NOTE,
        )
    return locked
