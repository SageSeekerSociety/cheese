"""Comments on a task's changes: written while it awaits review, sent with a 退回.

Who may write: the task's owner, its contributors and whoever reviews it, and
only while a card waits for review. Before that 芝士 is still changing the lines
and a comment on them goes stale in minutes; what to say then is said in the
conversation. A draft is its author's alone, so it is edited and deleted only by
them; a sent comment is part of the record and changes only by what 芝士 reports
about it.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.repository.forge_files import ProjectFiles
from app.domain.review import comment_anchor
from app.domain.review.comment_models import (
    ReviewComment,
    ReviewCommentOutcome,
    ReviewCommentState,
)
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.room_task.models import Task

#: Longest comment body or suggestion; a comment is a remark, not a file.
MAX_TEXT = 20_000


def writers(task: Task, card: AcceptCard | None) -> set[str]:
    """Who may comment on this task's changes."""
    people = {task.owner_handle, task.reviewer_handle, *task.contributor_handles}
    if card is not None:
        people.add(card.reviewer_handle)
    return {handle for handle in people if handle}


class ReviewCommentService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _task(self, task_id: uuid.UUID) -> Task:
        task = await self._session.get(Task, task_id)
        if task is None:
            raise NotFoundError(say("taskNotFound"))
        return task

    async def _pending_card(self, task_id: uuid.UUID) -> AcceptCard | None:
        return await self._session.scalar(
            select(AcceptCard)
            .where(
                AcceptCard.task_id == task_id,
                AcceptCard.status == AcceptStatus.pending,
            )
            .order_by(AcceptCard.created_at.desc())
            .limit(1)
        )

    async def task_of(self, comment_id: uuid.UUID) -> uuid.UUID | None:
        return await self._session.scalar(
            select(ReviewComment.task_id).where(ReviewComment.id == comment_id)
        )

    async def _own_draft(self, comment_id: uuid.UUID, author: str) -> ReviewComment:
        comment = await self._session.get(ReviewComment, comment_id)
        if comment is None or comment.author_handle != author:
            raise NotFoundError(say("reviewCommentNotFound"))
        if comment.state != ReviewCommentState.draft:
            raise UnprocessableEntityError(say("reviewCommentAlreadySent"))
        return comment

    async def _may_write(self, task: Task, author: str) -> None:
        card = await self._pending_card(task.id)
        if card is None:
            raise UnprocessableEntityError(say("reviewCommentNotAwaitingReview"))
        if author not in writers(task, card):
            raise ForbiddenError(say("reviewCommentNotAllowed"))

    async def comments(self, task_id: uuid.UUID, viewer: str) -> list[ReviewComment]:
        """Every sent comment on the task, and the viewer's own drafts, oldest first."""
        rows = await self._session.scalars(
            select(ReviewComment)
            .where(
                ReviewComment.task_id == task_id,
                or_(
                    ReviewComment.state == ReviewCommentState.sent,
                    ReviewComment.author_handle == viewer,
                ),
            )
            .order_by(ReviewComment.created_at)
        )
        return list(rows)

    async def anchors(
        self, task: Task, comments: list[ReviewComment]
    ) -> dict[uuid.UUID, int | None]:
        """Where each sent comment's lines are in the version now under review.

        Only comments sent before that version was handed over have moved; a
        file that cannot be read (removed, binary, too large) leaves its
        comments with no line, which reads as their place being gone.
        """
        moved = [
            c
            for c in comments
            if c.state == ReviewCommentState.sent and c.parent_id is None
        ]
        if not moved:
            return {}
        files = ProjectFiles(self._session, task.project_id, task.id)
        contents: dict[str, str | None] = {}
        found: dict[uuid.UUID, int | None] = {}
        for comment in moved:
            if comment.path not in contents:
                try:
                    read = await files.text(comment.path, "committed")
                    contents[comment.path] = read.get("content")
                except Exception:  # noqa: BLE001 — a file gone from the version
                    contents[comment.path] = None
            content = contents[comment.path]
            found[comment.id] = (
                comment_anchor.find(content, comment.line_text, comment.line_start)
                if content is not None
                else None
            )
        return found

    async def write(
        self,
        task_id: uuid.UUID,
        author: str,
        *,
        path: str,
        line_start: int,
        line_end: int,
        line_text: str,
        commit_sha: str | None,
        body: str,
        suggestion: str | None,
        parent_id: uuid.UUID | None,
    ) -> ReviewComment:
        task = await self._task(task_id)
        await self._may_write(task, author)
        body = body.strip()
        if suggestion is not None and len(suggestion) > MAX_TEXT:
            raise UnprocessableEntityError(say("reviewCommentTooLong"))
        if not body and suggestion is None:
            raise UnprocessableEntityError(say("reviewCommentEmpty"))
        if len(body) > MAX_TEXT:
            raise UnprocessableEntityError(say("reviewCommentTooLong"))
        if parent_id is not None:
            parent = await self._session.get(ReviewComment, parent_id)
            if (
                parent is None
                or parent.task_id != task.id
                or parent.state != ReviewCommentState.sent
                or parent.parent_id is not None
            ):
                raise NotFoundError(say("reviewCommentNotFound"))
            # A reply sits under its comment, so it takes the comment's place.
            path, line_start, line_end = parent.path, parent.line_start, parent.line_end
            line_text, commit_sha, suggestion = (
                parent.line_text,
                parent.commit_sha,
                None,
            )
        if line_start < 1 or line_end < line_start:
            raise UnprocessableEntityError(say("reviewCommentBadLines"))
        comment = ReviewComment(
            task_id=task.id,
            author_handle=author,
            path=path,
            line_start=line_start,
            line_end=line_end,
            line_text=line_text,
            commit_sha=commit_sha,
            body=body,
            suggestion=suggestion,
            parent_id=parent_id,
        )
        self._session.add(comment)
        await self._session.flush()
        return comment

    async def edit(
        self,
        comment_id: uuid.UUID,
        author: str,
        *,
        body: str,
        suggestion: str | None,
    ) -> ReviewComment:
        comment = await self._own_draft(comment_id, author)
        body = body.strip()
        if comment.parent_id is not None:
            suggestion = None
        if not body and suggestion is None:
            raise UnprocessableEntityError(say("reviewCommentEmpty"))
        if len(body) > MAX_TEXT or (
            suggestion is not None and len(suggestion) > MAX_TEXT
        ):
            raise UnprocessableEntityError(say("reviewCommentTooLong"))
        comment.body = body
        comment.suggestion = suggestion
        await self._session.flush()
        return comment

    async def delete(self, comment_id: uuid.UUID, author: str) -> None:
        comment = await self._own_draft(comment_id, author)
        await self._session.delete(comment)
        await self._session.flush()

    async def send(
        self, card: AcceptCard, author: str, comment_ids: list[uuid.UUID]
    ) -> list[ReviewComment]:
        """The author's drafts named here go back to 芝士 with this card.

        A draft not named stays a draft, for the next 退回; naming someone
        else's draft or a sent comment is refused, so nothing is sent on a
        person's behalf.
        """
        if not comment_ids:
            return []
        rows = list(
            await self._session.scalars(
                select(ReviewComment)
                .where(ReviewComment.id.in_(comment_ids))
                .order_by(ReviewComment.path, ReviewComment.line_start)
            )
        )
        if len(rows) != len(set(comment_ids)) or any(
            row.task_id != card.task_id
            or row.author_handle != author
            or row.state != ReviewCommentState.draft
            for row in rows
        ):
            raise UnprocessableEntityError(say("reviewCommentNotSendable"))
        now = datetime.now(UTC)
        for row in rows:
            row.state = ReviewCommentState.sent
            row.card_id = card.id
            row.sent_at = now
        await self._session.flush()
        return rows

    async def last_round(self, task_id: uuid.UUID) -> list[ReviewComment]:
        """The comments the latest 退回 sent: 芝士 answers them when it hands over."""
        latest = await self._session.scalar(
            select(ReviewComment.card_id)
            .where(
                ReviewComment.task_id == task_id,
                ReviewComment.state == ReviewCommentState.sent,
            )
            .order_by(ReviewComment.sent_at.desc())
            .limit(1)
        )
        if latest is None:
            return []
        return list(
            await self._session.scalars(
                select(ReviewComment)
                .where(ReviewComment.card_id == latest)
                .order_by(ReviewComment.path, ReviewComment.line_start)
            )
        )

    async def record_outcomes(self, task_id: uuid.UUID, outcomes: list[dict]) -> None:
        """What 芝士 says it did about the last round's comments.

        Each entry names a comment of that round; one it does not name keeps no
        outcome, which the 改动 tab shows as unanswered.
        """
        if not outcomes:
            return
        round_ = {c.id: c for c in await self.last_round(task_id)}
        for entry in outcomes:
            comment = round_.get(entry["id"])
            if comment is None:
                raise UnprocessableEntityError(
                    say("reviewCommentOutcomeUnknown", id=str(entry["id"]))
                )
            comment.outcome = (
                ReviewCommentOutcome.handled
                if entry["handled"]
                else ReviewCommentOutcome.not_handled
            )
            comment.outcome_note = (entry.get("note") or "").strip()[:2000] or None
        await self._session.flush()


def describe(comment: ReviewComment, anchor: dict[uuid.UUID, int | None]) -> dict:
    """A comment as the 改动 tab reads it.

    `current_line` is where its lines are in the version now under review: the
    same as `line_start` for a draft, wherever the text moved to for a sent
    comment, and null when those lines are gone.
    """
    if comment.id in anchor:
        current = anchor[comment.id]
    else:
        current = comment.line_start
    return {
        "id": str(comment.id),
        "author": comment.author_handle,
        "path": comment.path,
        "line_start": comment.line_start,
        "line_end": comment.line_end,
        "line_text": comment.line_text,
        "current_line": current,
        "body": comment.body,
        "suggestion": comment.suggestion,
        "parent_id": str(comment.parent_id) if comment.parent_id else None,
        "state": comment.state.value,
        "card_id": str(comment.card_id) if comment.card_id else None,
        "sent_at": comment.sent_at.isoformat() if comment.sent_at else None,
        "outcome": comment.outcome.value if comment.outcome else None,
        "outcome_note": comment.outcome_note,
        "created_at": comment.created_at.isoformat(),
    }


def _where(comment: ReviewComment) -> str:
    lines = (
        f"{comment.line_start}"
        if comment.line_end == comment.line_start
        else f"{comment.line_start}-{comment.line_end}"
    )
    return f"{comment.path}:{lines}"


async def instruction(session: AsyncSession, rows: list[ReviewComment]) -> str:
    """What 芝士 is told about the comments a 退回 sent: each with its id, so it
    can say per comment what it did when it hands the task over again."""
    if not rows:
        return ""
    parents = {
        p.id: p
        for p in await session.scalars(
            select(ReviewComment).where(
                ReviewComment.id.in_([r.parent_id for r in rows if r.parent_id])
            )
        )
    }
    out = [
        f"附带 {len(rows)} 条批注，行号是你交上来的那一版里的。逐条处理；重新递卡时用 "
        "comment_outcomes 逐条说明处理了没有、怎么处理的（没处理的写为什么）："
    ]
    for row in rows:
        parent = parents.get(row.parent_id) if row.parent_id else None
        head = f"- [{row.id}] {_where(row)}"
        if parent is not None:
            said = f"，你上次说：{parent.outcome_note}" if parent.outcome_note else ""
            head += f"（回复上一轮的批注「{parent.body}」{said}）"
        out.append(f"{head}：{row.body}" if row.body else head)
        if row.suggestion is not None:
            out.append(f"  修改建议，把这几行改成：\n```\n{row.suggestion}\n```")
    return "\n".join(out)


def summary(rows: list[ReviewComment]) -> str:
    """The comments as the room's line lists them under the 退回."""
    return "\n".join(
        f"{_where(row)}  {row.body or say('reviewCommentSuggestionOnly')}"
        for row in rows
    )
