"""`FeedbackService` —— 状态在这里，方法全是一句转发。

`_session` 与 `_repo` 由 `__init__` 建立；每个方法的实现都在同级子模块里同名的那
个函数中（`visibility` / `reads` / `writes` / `engagement`），所以行为和 patch 点
和拆文件之前一样。包的说明在 `app/domain/feedback/services/__init__.py`。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import BinaryIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback import repositories as repo
from app.domain.feedback.models import (
    Feedback,
    FeedbackAttachment,
    FeedbackComment,
    FeedbackStatus,
)
from app.domain.feedback.proposals import AcceptedProposal
from app.domain.feedback.schemas import (
    CommentOut,
    FeedbackCreate,
    FeedbackDetail,
    FeedbackPatch,
)

from . import attachments, engagement, reads, visibility, writes


class FeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = repo.FeedbackRepository(session)

    # --- 权限 ------------------------------------------------------------------

    async def is_admin(self, handle: str | None) -> bool:
        return await visibility.is_admin(self, handle=handle)

    async def require_admin(self, handle: str | None) -> str:
        return await visibility.require_admin(self, handle=handle)

    async def may_see(
        self, row: Feedback, *, handle: str | None, is_admin: bool
    ) -> bool:
        return await visibility.may_see(self, row=row, handle=handle, is_admin=is_admin)

    async def visible_row(
        self, feedback_id: uuid.UUID, *, handle: str | None, is_admin: bool
    ) -> Feedback:
        return await visibility.visible_row(
            self, feedback_id=feedback_id, handle=handle, is_admin=is_admin
        )

    async def visible_ref(
        self, ref: str, *, handle: str | None, is_admin: bool
    ) -> Feedback:
        return await visibility.visible_ref(
            self, ref=ref, handle=handle, is_admin=is_admin
        )

    def may_delete_comment(
        self, row: FeedbackComment, *, handle: str | None, is_admin: bool
    ) -> bool:
        return visibility.may_delete_comment(
            self, row=row, handle=handle, is_admin=is_admin
        )

    def may_delete_feedback(
        self, row: Feedback, *, handle: str | None, is_admin: bool
    ) -> bool:
        return visibility.may_delete_feedback(
            self, row=row, handle=handle, is_admin=is_admin
        )

    async def may_claim(
        self,
        handle: str | None,
        *,
        is_admin: bool,
        room_project_id: uuid.UUID | None = None,
    ) -> bool:
        return await visibility.may_claim(
            self, handle=handle, is_admin=is_admin, room_project_id=room_project_id
        )

    async def require_platform_room(self, room_project_id: uuid.UUID) -> None:
        return await visibility.require_platform_room(
            self, room_project_id=room_project_id
        )

    # --- 读（列表 / 统计 / 详情） -----------------------------------------------------

    async def list_public(
        self,
        *,
        tab: str,
        q: str | None,
        sort: str,
        limit: int,
        offset: int,
        author: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
    ) -> tuple[list[Feedback], int]:
        return await reads.list_public(
            self,
            tab=tab,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
            author=author,
            status=status,
            kind=kind,
            since=since,
        )

    async def list_admin(
        self,
        *,
        tab: str,
        assignee: str | None,
        q: str | None,
        sort: str = "new",
        limit: int,
        offset: int,
        since: datetime | None = None,
        resolved_since: datetime | None = None,
        deployed_since: datetime | None = None,
    ) -> tuple[list[Feedback], int]:
        return await reads.list_admin(
            self,
            tab=tab,
            assignee=assignee,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
            since=since,
            resolved_since=resolved_since,
            deployed_since=deployed_since,
        )

    async def list_mine(
        self, *, handle: str, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        return await reads.list_mine(self, handle=handle, limit=limit, offset=offset)

    async def counts(
        self, *, handle: str | None, is_admin: bool = False
    ) -> dict[str, int]:
        return await reads.counts(self, handle=handle, is_admin=is_admin)

    async def admin_board_counts(self) -> dict[str, dict[str, int]]:
        return await reads.admin_board_counts(self)

    async def created_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, int]:
        return await reads.created_series(self, since=since, until=until)

    async def reached_series(
        self, *, status: FeedbackStatus, since: datetime, until: datetime
    ) -> dict[date, int]:
        return await reads.reached_series(self, status=status, since=since, until=until)

    async def count_created_between(self, *, since: datetime, until: datetime) -> int:
        return await reads.count_created_between(self, since=since, until=until)

    async def count_reached_resolved_between(
        self, *, since: datetime, until: datetime
    ) -> int:
        return await reads.count_reached_resolved_between(
            self, since=since, until=until
        )

    async def support_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await reads.support_counts(self, ids=ids)

    async def comment_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await reads.comment_counts(self, ids=ids)

    async def supported_ids(
        self, ids: list[uuid.UUID], handle: str | None
    ) -> set[uuid.UUID]:
        return await reads.supported_ids(self, ids=ids, handle=handle)

    async def last_activity(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        return await reads.last_activity(self, ids=ids)

    async def chosen_avatars(self, handles: Iterable[str]) -> dict[str, int]:
        return await reads.chosen_avatars(self, handles=handles)

    async def thread_page(
        self,
        feedback_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = repo.THREAD_PAGE,
        replies_limit: int = repo.REPLIES_PAGE,
    ) -> repo.CommentPage:
        return await reads.thread_page(
            self,
            feedback_id=feedback_id,
            after=after,
            limit=limit,
            replies_limit=replies_limit,
        )

    async def replies_page(
        self,
        feedback_id: uuid.UUID,
        parent_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = repo.REPLIES_PAGE,
    ) -> tuple[list[FeedbackComment], str | None]:
        return await reads.replies_page(
            self, feedback_id=feedback_id, parent_id=parent_id, after=after, limit=limit
        )

    async def comments_out(
        self,
        rows: Sequence[FeedbackComment],
        *,
        handle: str | None,
        is_admin: bool,
        avatars: Mapping[str, int] | None = None,
        reply_counts: Mapping[uuid.UUID, int] | None = None,
        reply_cursors: Mapping[uuid.UUID, str] | None = None,
    ) -> list[CommentOut]:
        return await reads.comments_out(
            self,
            rows=rows,
            handle=handle,
            is_admin=is_admin,
            avatars=avatars,
            reply_counts=reply_counts,
            reply_cursors=reply_cursors,
        )

    async def detail(
        self, feedback_id: uuid.UUID, *, handle: str | None, is_admin: bool
    ) -> FeedbackDetail:
        return await reads.detail(
            self, feedback_id=feedback_id, handle=handle, is_admin=is_admin
        )

    async def detail_of(
        self,
        row: Feedback,
        *,
        handle: str | None,
        is_admin: bool,
        room_project_id: uuid.UUID | None = None,
    ) -> FeedbackDetail:
        return await reads.detail_of(
            self,
            row=row,
            handle=handle,
            is_admin=is_admin,
            room_project_id=room_project_id,
        )

    async def mark_read(self, *, handle: str) -> datetime:
        return await reads.mark_read(self, handle=handle)

    # --- 写与领取 ----------------------------------------------------------------

    async def create(
        self,
        body: FeedbackCreate,
        *,
        actor_handle: str,
        actor_user_id: int | None,
        proposal: AcceptedProposal | None = None,
    ) -> Feedback:
        return await writes.create(
            self,
            body=body,
            actor_handle=actor_handle,
            actor_user_id=actor_user_id,
            proposal=proposal,
        )

    async def _create_from_proposal(
        self,
        body: FeedbackCreate,
        *,
        proposal: AcceptedProposal,
        submitted_by_handle: str,
        submitted_by_user_id: int | None,
    ) -> Feedback:
        return await writes._create_from_proposal(
            self,
            body=body,
            proposal=proposal,
            submitted_by_handle=submitted_by_handle,
            submitted_by_user_id=submitted_by_user_id,
        )

    async def set_status(
        self,
        feedback_id: uuid.UUID,
        status: FeedbackStatus,
        *,
        by_handle: str | None,
        note: str | None = None,
    ) -> Feedback:
        return await writes.set_status(
            self, feedback_id=feedback_id, status=status, by_handle=by_handle, note=note
        )

    async def advance(
        self,
        row: Feedback,
        status: FeedbackStatus,
        *,
        by_handle: str | None,
        note: str | None = None,
        at: datetime | None = None,
    ) -> bool:
        return await writes.advance(
            self, row=row, status=status, by_handle=by_handle, note=note, at=at
        )

    async def _move(
        self,
        row: Feedback,
        status: FeedbackStatus,
        *,
        by_handle: str | None,
        note: str | None,
        at: datetime | None = None,
    ) -> None:
        return await writes._move(
            self, row=row, status=status, by_handle=by_handle, note=note, at=at
        )

    async def patch_admin(
        self, feedback_id: uuid.UUID, body: FeedbackPatch, *, by_handle: str
    ) -> Feedback:
        return await writes.patch_admin(
            self, feedback_id=feedback_id, body=body, by_handle=by_handle
        )

    async def claim(
        self,
        ref: str,
        *,
        handle: str,
        is_admin: bool,
        room_project_id: uuid.UUID | None = None,
    ) -> Feedback:
        return await writes.claim(
            self,
            ref=ref,
            handle=handle,
            is_admin=is_admin,
            room_project_id=room_project_id,
        )

    async def release(self, ref: str, *, handle: str, is_admin: bool) -> Feedback:
        return await writes.release(self, ref=ref, handle=handle, is_admin=is_admin)

    # --- 支持 / 评论 / 删除 --------------------------------------------------------

    async def support(
        self, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
    ) -> tuple[int, bool]:
        return await engagement.support(
            self, feedback_id=feedback_id, handle=handle, is_admin=is_admin
        )

    async def unsupport(
        self, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
    ) -> tuple[int, bool]:
        return await engagement.unsupport(
            self, feedback_id=feedback_id, handle=handle, is_admin=is_admin
        )

    async def comment(
        self,
        feedback_id: uuid.UUID,
        body: str,
        parent_id: uuid.UUID | None,
        *,
        actor_handle: str,
        actor_user_id: int | None,
        is_admin: bool,
    ) -> tuple[FeedbackComment, Feedback]:
        return await engagement.comment(
            self,
            feedback_id=feedback_id,
            body=body,
            parent_id=parent_id,
            actor_handle=actor_handle,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )

    async def like_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> tuple[int, bool]:
        return await engagement.like_comment(
            self,
            feedback_id=feedback_id,
            comment_id=comment_id,
            handle=handle,
            is_admin=is_admin,
        )

    async def unlike_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> tuple[int, bool]:
        return await engagement.unlike_comment(
            self,
            feedback_id=feedback_id,
            comment_id=comment_id,
            handle=handle,
            is_admin=is_admin,
        )

    async def _visible_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> FeedbackComment:
        return await engagement._visible_comment(
            self,
            feedback_id=feedback_id,
            comment_id=comment_id,
            handle=handle,
            is_admin=is_admin,
        )

    async def delete_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> None:
        return await engagement.delete_comment(
            self,
            feedback_id=feedback_id,
            comment_id=comment_id,
            handle=handle,
            is_admin=is_admin,
        )

    async def delete_feedback(
        self,
        feedback_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> None:
        return await engagement.delete_feedback(
            self, feedback_id=feedback_id, handle=handle, is_admin=is_admin
        )

    async def note(
        self, feedback_id: uuid.UUID, body: str, *, author_handle: str
    ) -> None:
        return await engagement.note(
            self, feedback_id=feedback_id, body=body, author_handle=author_handle
        )

    # --- 附件 -----------------------------------------------------------------

    async def store_attachment(
        self,
        *,
        file: BinaryIO,
        filename: str | None,
        content_type: str | None,
        uploader_handle: str,
    ) -> FeedbackAttachment:
        return await attachments.store(
            self,
            file=file,
            filename=filename,
            content_type=content_type,
            uploader_handle=uploader_handle,
        )

    async def attach_attachments(
        self,
        *,
        feedback_id: uuid.UUID,
        attachment_ids: list[uuid.UUID],
        uploader_handle: str,
    ) -> None:
        return await attachments.attach(
            self,
            feedback_id=feedback_id,
            attachment_ids=attachment_ids,
            uploader_handle=uploader_handle,
        )

    async def attachment_for_read(
        self,
        attachment_id: uuid.UUID,
        *,
        handle: str | None,
        is_admin: bool,
    ) -> FeedbackAttachment:
        return await attachments.for_read(
            self, attachment_id, handle=handle, is_admin=is_admin
        )

    async def attachment_bytes(self, row: FeedbackAttachment) -> bytes:
        return await attachments.read_bytes(row)
