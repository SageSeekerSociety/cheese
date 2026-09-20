"""Feedback rules.

Everything a route must not be trusted to remember lives here:

* **who can see a row** — one predicate, used by every read path, so a new
  endpoint cannot accidentally get it wrong (§4.3 of the design note).
* **status and timeline move together** — one method does both, in the caller's
  transaction. There is no other way to write `status`, which is what keeps the
  timeline from becoming a partial record of what happened.
* **comments are two levels deep** — the fold-up rule, copied from the prototype.

Three product decisions the user had not ruled on are taken here as defaults,
each in one place, each revertible without touching a route:

1. **Who is an admin** — `settings.feedback_admin_handles`, same shape as
   `dogfood_owner_handles`. No new role system: there is no production path that
   assigns `SystemRole.SUPER_ADMIN` today, so a role check would read as
   "nobody" and lock the surface for everyone.
2. **`security` is a subtype of `private`, not a second axis.** A security report
   is invisible to non-admins exactly as a private one is; the flag only routes
   it into the admin's security tab. So `security=True` narrows visibility, and
   the narrowing is applied **at read time** in `may_see` — not by overwriting
   `visibility` when an admin sets the flag. Two reasons it goes that way:
   `visibility` is the reporter's own choice and rewriting it would edit their
   decision behind their back, and deciding who may see a row is a policy
   question, which the repository deliberately does not answer.
3. **Resolved items sink, but only bugs.** See `_visible_tab` in repositories —
   a resolved suggestion stays in the list. Hiding suggestions is the wider
   reading and much harder to notice going wrong; this is the narrow one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.feedback import repositories as repo
from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackStatus,
    FeedbackVisibility,
)
from app.domain.feedback.proposals import AcceptedProposal
from app.domain.feedback.schemas import FeedbackCreate, FeedbackDetail, FeedbackPatch

#: The admin surface's tiers, in the order the admin tab bar draws them.
ADMIN_TABS: tuple[str, ...] = ("public", "private", "agent", "security")

#: The public tabs, in the order the tab bar draws them.
PUBLIC_TABS: tuple[str, ...] = ("all", "hot", "active", "resolved")

#: Movement order shown by the status ladder. `resolved` is reachable from any
#: state, and a reopen (resolved → triaging) is allowed: reports do get
#: re-opened, and a status set that forbids it gets worked around by filing a
#: duplicate instead — which loses the history that makes the report useful.
STATUS_LADDER: tuple[FeedbackStatus, ...] = (
    FeedbackStatus.received,
    FeedbackStatus.triaging,
    FeedbackStatus.planned,
    FeedbackStatus.in_progress,
    FeedbackStatus.resolved,
)


def admin_handles() -> frozenset[str]:
    """The platform-admin set, frozen once per call.

    Frozen rather than a module constant so a settings change takes effect
    without a restart in tests, matching how `profiles.py` consumes
    `dogfood_owner_handles`.
    """
    return frozenset(settings.feedback_admin_handles)


class FeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = repo.FeedbackRepository(session)

    # --- 权限 ---------------------------------------------------------------

    def is_admin(self, handle: str | None) -> bool:
        return bool(handle) and handle in admin_handles()

    def may_see(self, row: Feedback, *, handle: str | None, is_admin: bool) -> bool:
        """The visibility union, in one line — 公开 + 私密 + 是我提的.

        A private report is visible to admins **and to the person who filed it**:
        the reporter must be able to follow their own report, and the agent that
        filed on their behalf counts as them. Everyone else cannot, and gets 404
        rather than 403 — see `visible_row`.

        `security` narrows the public arm, so it is checked in the same breath:
        a row an admin flagged as a security matter is not public even though the
        reporter left `visibility` at its default. That flag is set on triage, by
        someone other than the reporter, and it is the one that must not leak.
        """
        if row.visibility == FeedbackVisibility.public and not row.security:
            return True
        if is_admin:
            return True
        if not handle:
            return False
        return handle in (row.author_handle, row.submitted_by_handle)

    async def visible_row(
        self, feedback_id: uuid.UUID, *, handle: str | None, is_admin: bool
    ) -> Feedback:
        """Load a row or raise 404 — including when the row exists but is hidden.

        Not 403: a 403 on a private report confirms the report exists, which is
        itself the thing the reporter asked to keep quiet. The design note (§4.3)
        makes this the rule for every id-taking feedback endpoint.
        """
        row = await self._repo.get(feedback_id)
        if row is None or not self.may_see(row, handle=handle, is_admin=is_admin):
            raise NotFoundError("反馈不存在")
        return row

    async def require_admin(self, handle: str | None) -> str:
        if not handle:
            raise ForbiddenError("需要登录")
        if not self.is_admin(handle):
            # 403 here and not 404: /admin/feedback is documented as existing, so
            # its existence is not a secret — only its contents are.
            raise ForbiddenError("需要平台管理员")
        return handle

    # --- 读 -----------------------------------------------------------------

    async def list_public(
        self, *, tab: str, q: str | None, sort: str, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        return await self._repo.list_public(
            tab=tab, q=q, sort=sort, limit=limit, offset=offset
        )

    async def list_admin(
        self,
        *,
        tab: str,
        assignee: str | None,
        q: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Feedback], int]:
        if tab not in ADMIN_TABS:
            raise BadRequestError(f"未知的管理视图：{tab}")
        return await self._repo.list_admin(
            tab=tab, assignee=assignee, q=q, limit=limit, offset=offset
        )

    async def list_mine(
        self, *, handle: str, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        return await self._repo.list_related_to(handle, limit=limit, offset=offset)

    async def counts(self, *, handle: str | None) -> dict[str, int]:
        """Tab numbers + the bell's unread count, in one call.

        `unread` is a live count against a cursor, not a stored flag: a stored
        flag is one row per person per item, and the cursor is one row per person
        — see `FeedbackReadState` and the design note §6.2.
        """
        counts = await self._repo.public_counts()
        counts["unassigned"] = await self._repo.unassigned_count()
        if handle:
            state = await self._repo.get_read_state(handle)
            since = state.last_read_at if state else None
            counts["unread"] = await self._repo.count_activity_since([handle], since)
        else:
            counts["unread"] = 0
        return counts

    async def support_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await self._repo.supports_counts(ids)

    async def comment_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await self._repo.comment_counts(ids)

    async def supported_ids(
        self, ids: list[uuid.UUID], handle: str | None
    ) -> set[uuid.UUID]:
        """Which of these the viewer already supported — the heart's filled state."""
        if not handle:
            return set()
        return await self._repo.supported_by(ids, handle)

    async def last_activity(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        """Newest comment-or-status time per row, for the card's 「刚刚」 line."""
        return await self._repo.latest_activity_of(ids)

    async def thread(self, feedback_id: uuid.UUID) -> list[FeedbackComment]:
        return await self._repo.list_comments(feedback_id)

    async def detail(
        self, feedback_id: uuid.UUID, *, handle: str | None, is_admin: bool
    ) -> FeedbackDetail:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        return await self.detail_of(row, handle=handle, is_admin=is_admin)

    async def detail_of(
        self, row: Feedback, *, handle: str | None, is_admin: bool
    ) -> FeedbackDetail:
        """The assembled detail view — schema, not a bag of parts.

        It returns the schema because every caller wants exactly this view and
        four of them built it by hand as ``FeedbackDetail.from_row(row, **parts)``.
        One of those copies shipped a ``"row"`` key inside the parts, which
        collides with the argument of the same name and made every one of them
        raise; a shape that cannot be mis-unpacked is the fix, not four careful
        call sites.

        ``notes`` are admin-only: the field is always present so the frontend has
        one shape, and it is the service that empties it. The empty list is the
        absence, not a redaction the client is trusted to honour.
        """
        thread = await self._repo.list_comments(row.id)
        activity = await self._repo.latest_activity_of([row.id])
        return FeedbackDetail.from_row(
            row,
            supports=await self._repo.supports_count(row.id),
            supported=(
                await self._repo.has_support(row.id, handle) if handle else False
            ),
            comments=len(thread),
            last_activity_at=activity.get(row.id),
            thread=thread,
            timeline=await self._repo.list_timeline(row.id),
            notes=await self._repo.list_notes(row.id) if is_admin else [],
        )

    async def mark_read(self, *, handle: str) -> datetime:
        """Move the cursor to now. Returns the new cursor for the client to echo."""
        now = datetime.now(UTC)
        await self._repo.bump_read_state(handle, now)
        return now

    # --- 写 -----------------------------------------------------------------

    async def create(
        self,
        body: FeedbackCreate,
        *,
        actor_handle: str,
        actor_user_id: int | None,
        actor_is_agent: bool,
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
        on a caller remembering to pass the flag honestly.

        When `proposal` is set, authorship comes from the proposal (the agent
        that found it) and `submitted_by` from the verified caller (the person
        who sent it) — two fields, not one, so 「芝士提的反馈里有多少真的被人发出
        去了」 stays answerable. The *content* still comes from the request body:
        the drawer lets the sender edit before sending, and the person pressing
        send is accountable for what they send.
        """
        if actor_is_agent:
            raise ForbiddenError(
                "agent 不能直接发布反馈：用 `cheese feedback propose` 提案，"
                "由人确认后再发送"
            )
        if proposal is not None:
            return await self._create_from_proposal(
                body,
                proposal=proposal,
                submitted_by_handle=actor_handle,
                submitted_by_user_id=actor_user_id,
            )
        visibility = body.visibility
        row = await self._repo.add(
            title=body.title,
            summary=body.summary or body.title,
            kind=body.kind,
            visibility=visibility,
            problem=body.problem,
            author_handle=actor_handle,
            author_user_id=actor_user_id,
            author_is_agent=actor_is_agent,
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
            topic_id=body.topic_id,
            project_id=body.project_id,
            priority=body.priority,
            tags=body.tags,
        )
        # The first timeline entry, written in the same transaction as the row:
        # a report with no `received` entry would show an empty history for the
        # one event that definitely happened.
        await self._repo.append_timeline(row.id, FeedbackStatus.received, actor_handle)
        return row

    async def _create_from_proposal(
        self,
        body: FeedbackCreate,
        *,
        proposal: AcceptedProposal,
        submitted_by_handle: str,
        submitted_by_user_id: int | None,
    ) -> Feedback:
        row = await self._repo.add(
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
            topic_id=body.topic_id,
            project_id=body.project_id,
            priority=body.priority,
            tags=body.tags,
        )
        # The timeline's first entry names the person, not the agent: the agent
        # did not move this report into `received`, the person did.
        await self._repo.append_timeline(
            row.id, FeedbackStatus.received, submitted_by_handle
        )
        return row

    async def set_status(
        self, feedback_id: uuid.UUID, status: FeedbackStatus, *, by_handle: str
    ) -> Feedback:
        """Move a report, writing the history entry with it.

        There is no route that writes `status` without going through here — the
        two writes share the caller's transaction, so the timeline cannot end up
        describing a state the row never had.
        """
        row = await self._repo.get(feedback_id)
        if row is None:
            raise NotFoundError("反馈不存在")
        if row.status == status:
            # Idempotent, but not silent: re-setting the same status is a no-op
            # rather than a second timeline entry, because two identical entries
            # a second apart read as a bug in the history.
            return row
        await self._repo.set_status(row, status)
        await self._repo.append_timeline(row.id, status, by_handle)
        return row

    async def patch_admin(
        self, feedback_id: uuid.UUID, body: FeedbackPatch, *, by_handle: str
    ) -> Feedback:
        row = await self._repo.get(feedback_id)
        if row is None:
            raise NotFoundError("反馈不存在")
        if body.priority is not None:
            await self._repo.set_priority(row, body.priority)
        if body.assignee_handle is not None:
            # Empty string clears the assignee; a handle assigns. `None` means
            # "not in this request" — see FeedbackPatch.
            await self._repo.set_assignee(row, body.assignee_handle or None)
        if body.security is not None and body.security != row.security:
            await self._repo.set_security(row, body.security)
            # Marking something a security matter is a routing decision the
            # reporter should be able to see, and it is also the moment the
            # report stops being visible to colleagues. Neither is obvious from
            # the row alone, so it goes in the history.
            await self._repo.append_timeline(row.id, row.status, by_handle)
        return row

    # --- 支持 / 评论 ----------------------------------------------------------

    async def support(
        self, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
    ) -> tuple[int, bool]:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        if row.status == FeedbackStatus.resolved:
            # Supporting something already closed changes nothing anyone will
            # look at, and it inflates the `hot` tab with items nobody can act
            # on. 412 rather than 403: the client's fix is to re-read the item,
            # not to stop asking.
            raise PreconditionFailedError("已解决的反馈不再接受支持")
        await self._repo.add_support(row.id, handle)
        return await self._repo.supports_count(row.id), True

    async def unsupport(
        self, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
    ) -> tuple[int, bool]:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        await self._repo.remove_support(row.id, handle)
        return await self._repo.supports_count(row.id), False

    async def comment(
        self,
        feedback_id: uuid.UUID,
        body: str,
        parent_id: uuid.UUID | None,
        *,
        actor_handle: str,
        actor_user_id: int | None,
        actor_is_agent: bool,
        is_admin: bool,
    ) -> tuple[FeedbackComment, Feedback]:
        row = await self.visible_row(
            feedback_id, handle=actor_handle, is_admin=is_admin
        )
        parent = None
        if parent_id is not None:
            parent = await self._repo.get_comment(parent_id)
            if parent is None or parent.feedback_id != row.id:
                # A parent that belongs to another report, or to none: 400, not
                # 404 — the id the client sent is the problem, not a secret.
                raise BadRequestError("回复的评论不属于这条反馈")
            # Two levels only, folded the same way the prototype does it
            # (`stores/feedback.ts::addComment`): a reply to a reply lands under
            # the same top-level comment. Storing depth-3 would mean the client
            # renders a thread it was not designed for, and the fold is cheaper
            # than teaching every reader to recurse.
            parent_id = parent.parent_id or parent.id
        created = await self._repo.add_comment(
            feedback_id=row.id,
            author_handle=actor_handle,
            author_user_id=actor_user_id,
            author_is_agent=actor_is_agent,
            body=body,
            parent_id=parent_id,
        )
        return created, row

    async def delete_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> None:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        comment = await self._repo.get_comment(comment_id)
        if comment is None or comment.feedback_id != row.id:
            raise NotFoundError("评论不存在")
        # Own comment, or admin. Deleting someone else's words is the kind of
        # thing that should be visible as an admin action, so admins can — but
        # the author always can.
        if comment.author_handle != handle and not is_admin:
            raise ForbiddenError("只能删除自己的评论")
        await self._repo.soft_delete_comment(comment)

    async def note(
        self, feedback_id: uuid.UUID, body: str, *, author_handle: str
    ) -> None:
        """Admin-only internal note. Append-only (see `FeedbackNote`)."""
        row = await self._repo.get(feedback_id)
        if row is None:
            raise NotFoundError("反馈不存在")
        await self._repo.add_note(row.id, author_handle, body)
