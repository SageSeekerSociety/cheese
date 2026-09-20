"""Feedback data access. No policy in here — visibility lives in `services.py`.

Every method takes and returns rows; "who is allowed to see this" is a service
question, so that a route can never get it right by accident and a test can
exercise the rule without a database.

`HOT_SUPPORTS = 5` is the prototype's own threshold (`stores/feedback.ts`), kept
here rather than in a query string: the `hot` tab and the detail card's 「热门」
badge have to agree, and two copies of a `5` is how they stop agreeing.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackKind,
    FeedbackNote,
    FeedbackPriority,
    FeedbackReadState,
    FeedbackStatus,
    FeedbackSupport,
    FeedbackTimeline,
    FeedbackVisibility,
)

#: 「热门」的阈值 —— 按支持数，不按浏览量。原型给过理由（「浏览是路过，支持是表态」），
#: 这里照抄。`supports >= HOT_SUPPORTS` 且按支持数降序。
HOT_SUPPORTS = 5

#: What "public" means, as one reusable predicate. `security` is in here as well
#: as in `FeedbackService.may_see`, and the duplication is deliberate: the write
#: path sets the flag and the read path honours it, so the two are separate links
#: of one chain, and a list query that only checked `visibility` would publish a
#: security report while the detail endpoint still hid it. The two have to agree,
#: and this constant is what makes them one definition instead of two copies.
#:
#: A tuple, and copied with ``list(...)`` at every use: a shared *mutable* list of
#: WHERE clauses is one ``extend`` away from growing a clause per request, and the
#: symptom (a list that narrows a little more every day) is not one you would
#: trace back here.
PUBLIC_ONLY: tuple[Any, ...] = (
    Feedback.visibility == FeedbackVisibility.public,
    Feedback.security.is_(False),
)


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- 主表 ---------------------------------------------------------------

    async def add(
        self,
        *,
        title: str,
        summary: str,
        kind: FeedbackKind,
        visibility: FeedbackVisibility,
        problem: str,
        author_handle: str,
        author_user_id: int | None,
        author_is_agent: bool,
        why: str | None = None,
        expectation: str | None = None,
        what_happened: str | None = None,
        repro: str | None = None,
        evidence: str | None = None,
        logs: str | None = None,
        session_id: str | None = None,
        environment: str | None = None,
        submitted_by_handle: str | None = None,
        submitted_by_user_id: int | None = None,
        topic_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        priority: FeedbackPriority = FeedbackPriority.normal,
        tags: list[str] | None = None,
    ) -> Feedback:
        row = Feedback(
            title=title,
            summary=summary,
            kind=kind,
            status=FeedbackStatus.received,
            visibility=visibility,
            priority=priority,
            problem=problem,
            why=why,
            expectation=expectation,
            what_happened=what_happened,
            repro=repro,
            evidence=evidence,
            logs=logs,
            session_id=session_id,
            environment=environment,
            author_handle=author_handle,
            author_user_id=author_user_id,
            author_is_agent=author_is_agent,
            submitted_by_handle=submitted_by_handle,
            submitted_by_user_id=submitted_by_user_id,
            topic_id=topic_id,
            project_id=project_id,
            tags=tags or [],
        )
        self._session.add(row)
        # flush, not commit: the caller's transaction spans the timeline append
        # that must land with it (`services.create`).
        await self._session.flush()
        return row

    async def get(self, feedback_id: uuid.UUID) -> Feedback | None:
        stmt: Select[tuple[Feedback]] = select(Feedback).where(
            Feedback.id == feedback_id, Feedback.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    def _list_stmt(
        self,
        *,
        where: Sequence[Any],
        sort: str,
        limit: int,
        offset: int,
    ) -> Select[tuple[Feedback]]:
        stmt = select(Feedback).where(Feedback.deleted_at.is_(None), *where)
        if sort == "supports":
            # `hot` sorts by support count. `GROUP BY feedback.id` rather than a
            # denormalised counter column: MVP lists 20 rows, and this repo has
            # no precedent for a redundant counter (`BlockReaction` has none,
            # `comments.count_votes` is a live aggregate).
            stmt = (
                stmt.outerjoin(
                    FeedbackSupport, FeedbackSupport.feedback_id == Feedback.id
                )
                .group_by(Feedback.id)
                .order_by(
                    func.count(FeedbackSupport.id).desc(), Feedback.created_at.desc()
                )
            )
        else:
            stmt = stmt.order_by(Feedback.created_at.desc(), Feedback.display_no.desc())
        return stmt.limit(limit).offset(offset)

    async def _count(self, where: Sequence[Any]) -> int:
        stmt = select(func.count(Feedback.id)).where(
            Feedback.deleted_at.is_(None), *where
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    def _tab_where(self, tab: str) -> list[Any]:
        """The four public tabs, defined once so list and counts cannot drift.

        A resolved item stops competing for attention, so it sinks out of the
        working tabs — but only a resolved **bug** does. A resolved suggestion
        is a feature the team decided to do and then did; it is still worth
        reading, and hiding it was the wider reading that the product decision
        (§8.23) did not take. The narrow one is also the easier to notice going
        wrong: the wide version quietly removes content nobody is looking for.

        Consequence worth stating: the tabs are filters, not a partition. A
        resolved suggestion is in both `all` and `resolved`, so the tab numbers
        do not sum to a total. That is the decision, not an accounting bug — if
        it ever needs to be a partition, this is the one line to change.
        """
        if tab == "resolved":
            return [Feedback.status == FeedbackStatus.resolved]
        sunk = or_(
            Feedback.status != FeedbackStatus.resolved,
            Feedback.kind != FeedbackKind.bug,
        )
        if tab == "active":
            return [
                sunk,
                Feedback.status.in_(
                    [
                        FeedbackStatus.triaging,
                        FeedbackStatus.planned,
                        FeedbackStatus.in_progress,
                    ]
                ),
            ]
        return [sunk]

    async def list_public(
        self, *, tab: str, q: str | None, sort: str, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        where: list[Any] = list(PUBLIC_ONLY)
        where.extend(self._tab_where(tab))
        if q:
            pattern = f"%{q}%"
            where.append(
                or_(Feedback.title.ilike(pattern), Feedback.summary.ilike(pattern))
            )
        # `hot` means 「支持数 >= 5」 AND sorted by supports — the filter is part
        # of the tab's definition, not just its ordering.
        if tab == "hot":
            hot = (
                select(FeedbackSupport.feedback_id)
                .group_by(FeedbackSupport.feedback_id)
                .having(func.count(FeedbackSupport.id) >= HOT_SUPPORTS)
            )
            where.append(Feedback.id.in_(hot.scalar_subquery()))
            sort = "supports"
        if sort not in ("new", "supports"):
            sort = "new"
        rows = list(
            (
                await self._session.execute(
                    self._list_stmt(where=where, sort=sort, limit=limit, offset=offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, await self._count(where)

    async def public_counts(self) -> dict[str, int]:
        """The four tab numbers, in one round trip each — returned together.

        Four separate requests would render a row of 0s and then jump, and the
        thing that jumps is the first thing on the page.
        """
        where = list(PUBLIC_ONLY)
        all_count = await self._count([*where, *self._tab_where("all")])
        active_count = await self._count([*where, *self._tab_where("active")])
        resolved_count = await self._count([*where, *self._tab_where("resolved")])
        hot = (
            select(FeedbackSupport.feedback_id)
            .group_by(FeedbackSupport.feedback_id)
            .having(func.count(FeedbackSupport.id) >= HOT_SUPPORTS)
        )
        hot_count = await self._count(
            [
                *where,
                Feedback.status != FeedbackStatus.resolved,
                Feedback.id.in_(hot.scalar_subquery()),
            ]
        )
        return {
            "all": all_count,
            "hot": hot_count,
            "active": active_count,
            "resolved": resolved_count,
        }

    async def list_admin(
        self,
        *,
        tab: str,
        assignee: str | None,
        q: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Feedback], int]:
        where: list[Any] = []
        if tab == "private":
            # 私密非安全: the column an admin works through when the reporter
            # asked for it not to be public but it has no security implication.
            where.append(Feedback.visibility == FeedbackVisibility.private)
            where.append(Feedback.security.is_(False))
        elif tab == "agent":
            where.append(Feedback.author_is_agent.is_(True))
        elif tab == "security":
            where.append(Feedback.security.is_(True))
        elif tab == "public":
            # The same predicate `list_public` uses, so the admin's "public" view
            # is what the public actually sees. A looser one here would put rows
            # in front of an admin labelled public that no one else can open.
            where.extend(PUBLIC_ONLY)
        if assignee:
            where.append(Feedback.assignee_handle == assignee)
        if q:
            pattern = f"%{q}%"
            where.append(
                or_(Feedback.title.ilike(pattern), Feedback.summary.ilike(pattern))
            )
        rows = list(
            (
                await self._session.execute(
                    self._list_stmt(where=where, sort="new", limit=limit, offset=offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, await self._count(where)

    async def list_related_to(
        self, handle: str, *, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        """「我的反馈」：我提的 + agent 替我提的 + 指派给我的。

        One query with an OR rather than three: the list is one list, and the
        `author_handle` / `submitted_by_handle` / `assignee_handle` indexes each
        serve one arm of it.
        """
        where = [
            or_(
                Feedback.author_handle == handle,
                Feedback.submitted_by_handle == handle,
                Feedback.assignee_handle == handle,
            )
        ]
        rows = list(
            (
                await self._session.execute(
                    self._list_stmt(where=where, sort="new", limit=limit, offset=offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, await self._count(where)

    async def set_status(self, row: Feedback, status: FeedbackStatus) -> None:
        row.status = status
        await self._session.flush()

    async def set_priority(self, row: Feedback, priority: FeedbackPriority) -> None:
        row.priority = priority
        await self._session.flush()

    async def set_assignee(self, row: Feedback, assignee: str | None) -> None:
        row.assignee_handle = assignee
        await self._session.flush()

    async def set_security(self, row: Feedback, security: bool) -> None:
        row.security = security
        await self._session.flush()

    async def open_count_by(self, handles: Sequence[str]) -> int:
        """Unresolved reports filed by these handles — the agent quota's read."""
        if not handles:
            return 0
        stmt = select(func.count(Feedback.id)).where(
            Feedback.deleted_at.is_(None),
            Feedback.author_handle.in_(list(handles)),
            Feedback.status != FeedbackStatus.resolved,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    # --- 支持 -----------------------------------------------------------------

    async def supports_count(self, feedback_id: uuid.UUID) -> int:
        stmt = select(func.count(FeedbackSupport.id)).where(
            FeedbackSupport.feedback_id == feedback_id
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def supports_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Support counts for a page, in one query.

        The alternative — a count per row — is N+1 on the very list that sorts
        by this number.
        """
        if not ids:
            return {}
        stmt = (
            select(FeedbackSupport.feedback_id, func.count(FeedbackSupport.id))
            .where(FeedbackSupport.feedback_id.in_(list(ids)))
            .group_by(FeedbackSupport.feedback_id)
        )
        return {
            row[0]: int(row[1]) for row in (await self._session.execute(stmt)).all()
        }

    async def supported_by(
        self, ids: Sequence[uuid.UUID], handle: str
    ) -> set[uuid.UUID]:
        """Which of these the viewer already supported — the heart's filled state."""
        if not ids:
            return set()
        stmt = select(FeedbackSupport.feedback_id).where(
            FeedbackSupport.feedback_id.in_(list(ids)),
            FeedbackSupport.author_handle == handle,
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def has_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        stmt = select(FeedbackSupport.id).where(
            FeedbackSupport.feedback_id == feedback_id,
            FeedbackSupport.author_handle == handle,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def add_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        """Idempotent: a repeat POST is a no-op, not a second row.

        Returns whether a row was actually written, so the caller can report a
        count that is the truth rather than a guess.
        """
        if await self.has_support(feedback_id, handle):
            return False
        self._session.add(
            FeedbackSupport(feedback_id=feedback_id, author_handle=handle)
        )
        await self._session.flush()
        return True

    async def remove_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        """Also idempotent — deleting a support that is not there answers 200."""
        stmt = select(FeedbackSupport).where(
            FeedbackSupport.feedback_id == feedback_id,
            FeedbackSupport.author_handle == handle,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    # --- 评论 -----------------------------------------------------------------

    async def list_comments(self, feedback_id: uuid.UUID) -> list[FeedbackComment]:
        stmt = (
            select(FeedbackComment)
            .where(
                FeedbackComment.feedback_id == feedback_id,
                FeedbackComment.deleted_at.is_(None),
            )
            .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_comment(self, comment_id: uuid.UUID) -> FeedbackComment | None:
        stmt = select(FeedbackComment).where(
            FeedbackComment.id == comment_id, FeedbackComment.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_comment(
        self,
        *,
        feedback_id: uuid.UUID,
        author_handle: str,
        author_user_id: int | None,
        author_is_agent: bool,
        body: str,
        parent_id: uuid.UUID | None,
    ) -> FeedbackComment:
        row = FeedbackComment(
            feedback_id=feedback_id,
            parent_id=parent_id,
            author_handle=author_handle,
            author_user_id=author_user_id,
            author_is_agent=author_is_agent,
            body=body,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def soft_delete_comment(self, row: FeedbackComment) -> None:
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()

    # --- 时间线 ---------------------------------------------------------------

    async def list_timeline(self, feedback_id: uuid.UUID) -> list[FeedbackTimeline]:
        stmt = (
            select(FeedbackTimeline)
            .where(FeedbackTimeline.feedback_id == feedback_id)
            .order_by(FeedbackTimeline.at.asc(), FeedbackTimeline.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def append_timeline(
        self, feedback_id: uuid.UUID, status: FeedbackStatus, by_handle: str | None
    ) -> FeedbackTimeline:
        row = FeedbackTimeline(
            feedback_id=feedback_id, status=status, by_handle=by_handle
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # --- 备注 -----------------------------------------------------------------

    async def list_notes(self, feedback_id: uuid.UUID) -> list[FeedbackNote]:
        stmt = (
            select(FeedbackNote)
            .where(FeedbackNote.feedback_id == feedback_id)
            .order_by(FeedbackNote.created_at.asc(), FeedbackNote.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add_note(
        self, feedback_id: uuid.UUID, author_handle: str, body: str
    ) -> FeedbackNote:
        row = FeedbackNote(
            feedback_id=feedback_id, author_handle=author_handle, body=body
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # --- 未读 cursor ----------------------------------------------------------

    async def get_read_state(self, handle: str) -> FeedbackReadState | None:
        stmt = select(FeedbackReadState).where(FeedbackReadState.user_handle == handle)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def bump_read_state(self, handle: str, at: datetime) -> FeedbackReadState:
        row = await self.get_read_state(handle)
        if row is None:
            row = FeedbackReadState(user_handle=handle, last_read_at=at)
            self._session.add(row)
        else:
            row.last_read_at = at
        await self._session.flush()
        return row

    async def count_activity_since(
        self, handles: Sequence[str], since: datetime | None
    ) -> int:
        """New comments + status events on the items these handles are party to.

        Two event tables, counted separately then summed — a UNION would have to
        dedupe rows that carry no shared id, and the sum of two indexed counts is
        cheaper than the sort that costs.
        """
        if not handles:
            return 0
        mine = or_(
            Feedback.author_handle.in_(list(handles)),
            Feedback.submitted_by_handle.in_(list(handles)),
            Feedback.assignee_handle.in_(list(handles)),
        )
        comments_stmt = (
            select(func.count(FeedbackComment.id))
            .join(Feedback, Feedback.id == FeedbackComment.feedback_id)
            .where(
                mine,
                Feedback.deleted_at.is_(None),
                FeedbackComment.deleted_at.is_(None),
                # My own words are not news to me.
                FeedbackComment.author_handle.notin_(list(handles)),
            )
        )
        timeline_stmt = (
            select(func.count(FeedbackTimeline.id))
            .join(Feedback, Feedback.id == FeedbackTimeline.feedback_id)
            .where(
                mine,
                Feedback.deleted_at.is_(None),
                or_(
                    FeedbackTimeline.by_handle.is_(None),
                    FeedbackTimeline.by_handle.notin_(list(handles)),
                ),
            )
        )
        if since is not None:
            comments_stmt = comments_stmt.where(FeedbackComment.created_at > since)
            timeline_stmt = timeline_stmt.where(FeedbackTimeline.at > since)
        comments = int((await self._session.execute(comments_stmt)).scalar_one() or 0)
        events = int((await self._session.execute(timeline_stmt)).scalar_one() or 0)
        return comments + events

    async def latest_activity_of(
        self, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """Newest comment-or-status time per item, for 「最近活动」 sorting/display."""
        if not ids:
            return {}
        out: dict[uuid.UUID, datetime] = {}
        comment_stmt = (
            select(FeedbackComment.feedback_id, func.max(FeedbackComment.created_at))
            .where(
                FeedbackComment.feedback_id.in_(list(ids)),
                FeedbackComment.deleted_at.is_(None),
            )
            .group_by(FeedbackComment.feedback_id)
        )
        for feedback_id, at in (await self._session.execute(comment_stmt)).all():
            if at is not None:
                out[feedback_id] = at
        event_stmt = (
            select(FeedbackTimeline.feedback_id, func.max(FeedbackTimeline.at))
            .where(FeedbackTimeline.feedback_id.in_(list(ids)))
            .group_by(FeedbackTimeline.feedback_id)
        )
        for feedback_id, at in (await self._session.execute(event_stmt)).all():
            if at is not None and (feedback_id not in out or at > out[feedback_id]):
                out[feedback_id] = at
        return out

    async def ids_with_activity_between(
        self, handles: Sequence[str], since: datetime | None
    ) -> set[uuid.UUID]:
        """Which items the unread count is actually about (for the list's dot)."""
        if not handles:
            return set()
        mine = or_(
            Feedback.author_handle.in_(list(handles)),
            Feedback.submitted_by_handle.in_(list(handles)),
            Feedback.assignee_handle.in_(list(handles)),
        )
        comment_stmt = (
            select(FeedbackComment.feedback_id)
            .join(Feedback, Feedback.id == FeedbackComment.feedback_id)
            .where(
                mine,
                FeedbackComment.deleted_at.is_(None),
                FeedbackComment.author_handle.notin_(list(handles)),
            )
        )
        event_stmt = (
            select(FeedbackTimeline.feedback_id)
            .join(Feedback, Feedback.id == FeedbackTimeline.feedback_id)
            .where(
                mine,
                or_(
                    FeedbackTimeline.by_handle.is_(None),
                    FeedbackTimeline.by_handle.notin_(list(handles)),
                ),
            )
        )
        if since is not None:
            comment_stmt = comment_stmt.where(FeedbackComment.created_at > since)
            event_stmt = event_stmt.where(FeedbackTimeline.at > since)
        ids = set((await self._session.execute(comment_stmt)).scalars().all())
        ids |= set((await self._session.execute(event_stmt)).scalars().all())
        return ids

    async def unassigned_count(self) -> int:
        """The admin's 「还没人管」 number, asked once per admin list render."""
        stmt = select(func.count(Feedback.id)).where(
            Feedback.deleted_at.is_(None),
            Feedback.assignee_handle.is_(None),
            Feedback.status != FeedbackStatus.resolved,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def comment_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Live comment counts for a page — one query, same reason as supports."""
        if not ids:
            return {}
        stmt = (
            select(FeedbackComment.feedback_id, func.count(FeedbackComment.id))
            .where(
                FeedbackComment.feedback_id.in_(list(ids)),
                FeedbackComment.deleted_at.is_(None),
            )
            .group_by(FeedbackComment.feedback_id)
        )
        return {
            row[0]: int(row[1]) for row in (await self._session.execute(stmt)).all()
        }
