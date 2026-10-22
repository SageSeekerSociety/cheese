"""反馈的附件行：字节记在哪、挂在哪条反馈上。

字节本身不在这里 —— 它们住在 `app.core.blob_store` 里，「放到哪里、读回来」是
`services/attachments.py` 的事。这一层只记两件事：这一行自己那一份住在哪个存储的
哪个键上（`location` + `blob_key`，和 `library_files` 同一个形状，所以换
`files_store` 不会搬动任何一行已有的字节），以及它属于哪条反馈。

判据一条都没有：谁能上传、谁能挂、谁能读，都在 service 里（本包的规矩）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.domain.feedback.models import FeedbackAttachment

if TYPE_CHECKING:
    from app.domain.feedback import repositories as pkg


async def add(
    repository: pkg.FeedbackRepository,
    *,
    attachment_id: uuid.UUID,
    uploader_handle: str,
    name: str,
    content_type: str,
    size: int,
    location: str,
    blob_key: str,
) -> FeedbackAttachment:
    """记一行。**id 由调用方给**：`blob_key` 是拿它拼出来的，而键要在写字节之前就
    定下来（`library.records._new_row` 同一个理由）。"""
    row = FeedbackAttachment(
        id=attachment_id,
        uploader_handle=uploader_handle,
        name=name,
        content_type=content_type,
        bytes=size,
        location=location,
        blob_key=blob_key,
        created_at=datetime.now(UTC),
    )
    repository._session.add(row)
    # flush, not commit: 字节写在同一笔事务里，写失败了这一行随事务回滚
    # （`library.records` 开头那段说的是同一件事）。
    await repository._session.flush()
    return row


async def get(
    repository: pkg.FeedbackRepository, attachment_id: uuid.UUID
) -> FeedbackAttachment | None:
    return await repository._session.get(FeedbackAttachment, attachment_id)


async def get_many(
    repository: pkg.FeedbackRepository, ids: list[uuid.UUID]
) -> list[FeedbackAttachment]:
    if not ids:
        return []
    stmt = select(FeedbackAttachment).where(FeedbackAttachment.id.in_(ids))
    result = await repository._session.execute(stmt)
    return list(result.scalars().all())


async def list_for_feedback(
    repository: pkg.FeedbackRepository, feedback_id: uuid.UUID
) -> list[FeedbackAttachment]:
    """这条反馈的附件，按**上传顺序**（不是 id 顺序：uuid 排出来是随机的）。"""
    stmt = (
        select(FeedbackAttachment)
        .where(FeedbackAttachment.feedback_id == feedback_id)
        .order_by(FeedbackAttachment.created_at, FeedbackAttachment.id)
    )
    result = await repository._session.execute(stmt)
    return list(result.scalars().all())


async def attach(
    repository: pkg.FeedbackRepository,
    *,
    rows: list[FeedbackAttachment],
    feedback_id: uuid.UUID,
) -> None:
    """把这几份挂到那条反馈上。谁可以挂、挂哪几份，调用方已经判过。"""
    for row in rows:
        row.feedback_id = feedback_id
    await repository._session.flush()
