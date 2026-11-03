"""报告带的附件：上传、挂到报告上、读回来。

**字节不进公开目录，这是这张表存在的全部理由。** 1.0 的 `attachment` 表把它那一份
放在 `/uploads/…` 下面，任何拿到地址的人都能读（`routes/uploads.py` 开头那段说的
就是这件事）；图片那一档更是对所有登录用户开放。反馈有「私密」这一档，私密报告里
的截图如果落在那种地址上，「私密」就只剩标题和正文 —— 方案稿 §8.7 在附件还没做
的时候就把这条写成了第一条判据。所以字节走 `app.core.blob_store`：本地部署时它在
`workspace_root` 下（没有任何路由把那个目录当静态目录发出去），桶部署时在文件桶里，
读回字节只有一条路 —— 本模块的 `for_read` 判过之后的那一条。

**先传、后挂。** 表单在选文件（或粘贴）那一刻就上传，拿到 id；`POST /feedback` 把
这些 id 一起带上，`attach` 在同一个事务里给它们写上 `feedback_id`。为什么不是
「先建报告、再补传」：那会在两步之间留一道缝 —— 报告已经发出去了、截图没传上去，
别人先看到一条没有证据的报告，作者还得回去补。代价是用户中途放弃会留下一个没人
引用的对象，发题目那条路也是这个代价（`package.add_uploads`）。

**挂的时候要问「这是你传的吗」。** 附件 id 会经过浏览器，而把它挂到自己的报告上
是让别人看见一张别人的截图的便宜办法；`feedback_id is None` 那一档又意味着「还没
有任何一条报告替它做可见性判断」。所以 `attach` 三件事一起判：id 存在、是本人的、
还没挂到别处。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, BinaryIO

from app.core import blob_store, raster_images
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.core.sentences import say, size_text
from app.domain.day_windows import utc_now
from app.domain.feedback.models import FeedbackAttachment
from app.domain.library import service as library

if TYPE_CHECKING:
    from app.domain.feedback import services as pkg


def _blob_key(attachment_id: uuid.UUID) -> str:
    """字节在那棵树里的键。和 `library_files` 一样按行 id 走，不按名字：换名字不该
    搬字节，改名也不该让两份文件撞在一起。"""
    return f".feedback-blobs/{attachment_id}"


async def store(
    svc: pkg.FeedbackService,
    *,
    file: BinaryIO,
    filename: str | None,
    content_type: str | None,
    uploader_handle: str,
) -> FeedbackAttachment:
    """收一份文件，记一行，字节写进文件存储。

    顺序是「先记后写」：写字节失败时这一行随事务回滚，不会留下一行指着一份不存在
    的文件（`library.records` 开头那段同一个理由）。
    """
    size = library.size_of(file)
    if not size:
        raise UnprocessableEntityError(say("emptyFile"))
    # 10MB 这一条和房间文件、资料库是同一条线（`library.MAX_FILE_BYTES`）：一个
    # 人会在这里打开的东西。**不按视频那档放宽**：那条 200MB 的路由在网关上另开了
    # 位置（`frontend/nginx.conf` 的 `/api/topics/…/attachments`），这一条没有，
    # 所以报给用户的上限就是网关真正挡住的那个数。
    limit = library.MAX_FILE_BYTES
    if size > limit:
        raise UnprocessableEntityError(say("fileTooLarge", size=size_text(limit)))

    declared = (content_type or "").split(";")[0].strip().lower()
    media_type = declared or "application/octet-stream"
    if declared.startswith("image/"):
        # 浏览器说它是图片是一句声称，字节说了才算：认不出来的按不透明下载发，
        # 永远不进 `<img>`（`routes/topics_attachments.py` 的同一条规矩）。
        head = file.read(12)
        file.seek(0)
        media_type = raster_images.sniff(head) or "application/octet-stream"

    attachment_id = uuid.uuid4()
    store_ = blob_store.for_new()
    key = _blob_key(attachment_id)
    row = await svc._repo.add_attachment(
        attachment_id=attachment_id,
        uploader_handle=uploader_handle,
        name=library.clean_upload_name(filename)[:255],
        content_type=media_type[:255],
        size=size,
        location=store_.location,
        blob_key=key,
    )
    await store_.put(key, file)
    return row


async def attach(
    svc: pkg.FeedbackService,
    *,
    feedback_id: uuid.UUID,
    attachment_ids: list[uuid.UUID],
    uploader_handle: str,
) -> None:
    """把这几个已上传的附件挂到刚建好的这条反馈上。

    一次性、全有或全无：有一个 id 不存在、不是本人的、或者已经属于另一条反馈，
    整批退回。**不挑能挂的挂上** —— 那会让人以为截图传上去了，而屏幕上少一张。
    """
    ids = list(dict.fromkeys(attachment_ids))
    if not ids:
        return
    rows = {row.id: row for row in await svc._repo.get_attachments(ids)}
    unknown = [str(i) for i in ids if i not in rows]
    if unknown:
        raise NotFoundError(
            say("feedbackAttachmentNotFound"), data={"attachmentIds": unknown}
        )
    if any(rows[i].uploader_handle != uploader_handle for i in ids):
        raise ForbiddenError(say("feedbackAttachmentNotYours"))
    if any(rows[i].feedback_id is not None for i in ids):
        raise UnprocessableEntityError(say("feedbackAttachmentFiled"))
    await svc._repo.attach_attachments(
        rows=[rows[i] for i in ids], feedback_id=feedback_id
    )


async def for_read(
    svc: pkg.FeedbackService,
    attachment_id: uuid.UUID,
    *,
    handle: str | None,
    is_admin: bool,
) -> FeedbackAttachment:
    """能读这一份吗。判据**照抄它所属的那条反馈** —— 看得见反馈就看得见附件，
    没有第二条规则。看不见时是 404 而不是 403，和反馈详情同一个理由（`visible_row`）：
    403 确认了它存在。

    还没挂上任何反馈的那一档（表单刚传完、还没提交）只有上传者本人能读：那是他自己
    刚放上去的东西，而它此刻不属于任何一条报告，没有别的话可问。
    """
    row = await svc._repo.get_attachment(attachment_id)
    if row is None:
        raise NotFoundError(say("feedbackAttachmentNotFound"))
    if row.feedback_id is None:
        if not handle or row.uploader_handle != handle:
            raise NotFoundError(say("feedbackAttachmentNotFound"))
        return row
    await svc.visible_row(row.feedback_id, handle=handle, is_admin=is_admin)
    return row


async def read_bytes(row: FeedbackAttachment) -> bytes:
    """字节。行还在、字节没了（桶被清过、盘搬过）时说「附件不存在」，不是 500：
    对读的人这两件事是同一件 —— 这份东西他打不开了。"""
    try:
        return await blob_store.store(row.location).get(row.blob_key)
    except blob_store.BlobMissingError:
        raise NotFoundError(say("feedbackAttachmentNotFound")) from None


def as_download_name(row: FeedbackAttachment) -> str:
    """下载时给浏览器的文件名。行里存的就是上传时的那个名字（已清洗），这里只补
    一个兜底：名字为空时给个能认出来的。"""
    return row.name or f"attachment-{utc_now().strftime('%Y%m%d')}"
