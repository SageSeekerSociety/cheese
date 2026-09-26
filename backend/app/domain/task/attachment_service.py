"""题目附件：出题时带上的材料，看得见的人都能看到清单，部分人才能下载。

三条判据，各自一句话：

- **谁能传、谁能删**：出题的人本人，或这块板的所有者 / 管理员（``may_teach_task``，
  与编辑题目、替人报名、看提交名单同一处）。传和删都是写题目的动作。
- **谁能看到清单**：看得见这道题的人（``TaskVisibilityService.can_view_task``）。
  清单不能比题目本身更保密 —— 看不见材料就无法判断要不要领。
- **谁能下载**：出题人 / 板管理员 / **已经领取的人**。领取者拿不到材料就没法做题，
  所以这条比「看得见」窄一格、又比「管得了」宽一格。

文件本身不在这里存：``attachment`` 表与 ``app.core.storage`` 早就有那一层
（``POST /attachments`` 上传，meta 里带 filename / contentType / storageKey / size /
uploaderId），这里只把它挂到题目上，并保管题目这一侧的下载计数与生命期。
"""

from datetime import UTC, datetime
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.space_access import may_teach_task
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.storage import StorageBackend
from app.domain.attachment.models import Attachment
from app.domain.attachment.repositories import AttachmentRepository
from app.domain.attachment.services import AttachmentService
from app.domain.task.models import Task, TaskAttachment
from app.domain.task.visibility_service import TaskVisibilityService


class TaskAttachmentRepository:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, task_id: int, attachment_id: int) -> TaskAttachment:
        now = datetime.now(UTC)
        link = TaskAttachment(
            task_id=task_id,
            attachment_id=attachment_id,
            download_count=0,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def list_live(self, *, task_id: int) -> list[TaskAttachment]:
        stmt = (
            select(TaskAttachment)
            .where(
                TaskAttachment.task_id == task_id,
                TaskAttachment.deleted_at.is_(None),
            )
            .order_by(TaskAttachment.id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_live(
        self, *, task_id: int, attachment_id: int
    ) -> TaskAttachment | None:
        stmt = select(TaskAttachment).where(
            TaskAttachment.task_id == task_id,
            TaskAttachment.attachment_id == attachment_id,
            TaskAttachment.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_live_for_attachments(
        self, *, attachment_ids: list[int]
    ) -> list[TaskAttachment]:
        """这些文件里，哪些已经挂在某道题上了（不带 task 条件，跨题也算）。"""
        if not attachment_ids:
            return []
        stmt = select(TaskAttachment).where(
            TaskAttachment.attachment_id.in_(attachment_ids),
            TaskAttachment.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def save(self, link: TaskAttachment) -> TaskAttachment:
        link.updated_at = datetime.now(UTC)
        await self._session.flush()
        return link

    async def soft_delete(self, link: TaskAttachment) -> None:
        now = datetime.now(UTC)
        link.deleted_at = now
        link.updated_at = now
        await self._session.flush()


class TaskAttachmentService:
    def __init__(self, *, session: AsyncSession, storage: StorageBackend) -> None:
        self._session = session
        self._storage = storage
        self._links = TaskAttachmentRepository(session=session)
        self._attachment_repo = AttachmentRepository(session=session)
        self._files = AttachmentService(repo=self._attachment_repo, storage=storage)
        self._visibility = TaskVisibilityService(session=session)

    # ---- 看 ----

    async def list_for_task(
        self, *, task: Task, user_id: int
    ) -> tuple[list[Attachment], list[TaskAttachment], bool]:
        """看得见这道题就能拿到清单；能不能下载另说，一并算好交给路由。

        返回 ``(文件行, 关联行, 能不能下载)``，顺序一一对应。关联行只用来给
        下载计数，路由不该自己去拼两张表。
        """
        if not await self._visibility.can_view_task(task=task, user_id=user_id):
            raise ForbiddenError("You cannot see this task")

        links = await self._links.list_live(task_id=task.id)
        files = {
            attachment.id: attachment
            for attachment in await self._attachment_repo.get_by_ids(
                [link.attachment_id for link in links]
            )
        }
        # 关联行还在、文件行没了（理论上不该发生）：跳过，而不是把 None 交出去。
        pairs = [
            (files[link.attachment_id], link)
            for link in links
            if link.attachment_id in files
        ]
        can_download = await self._may_download(task=task, user_id=user_id)
        return [f for f, _ in pairs], [link for _, link in pairs], can_download

    async def _may_download(self, *, task: Task, user_id: int) -> bool:
        if await may_teach_task(self._session, task=task, user_id=user_id):
            return True
        return await self._visibility.is_participant(task_id=task.id, user_id=user_id)

    # ---- 写 ----

    async def add(
        self,
        *,
        task: Task,
        user_id: int,
        file: BinaryIO,
        filename: str,
        content_type: str | None,
    ) -> tuple[Attachment, TaskAttachment]:
        await self._ensure_publisher(task=task, user_id=user_id)
        attachment = await self._files.upload(
            file=file,
            filename=filename,
            content_type=content_type,
            uploader_id=user_id,
        )
        link = await self._links.create(task_id=task.id, attachment_id=attachment.id)
        return attachment, link

    async def attach_uploaded(
        self, *, task: Task, user_id: int, attachment_ids: list[int]
    ) -> int:
        """建题时把已经上传好的文件一次挂上（``POST /tasks`` 的 ``attachmentIds``）。

        为什么是「先传、再带着 id 建题」而不是「先建题、再逐个补传」：后者会在两步
        之间留下一道缝 —— 题已经发了、附件没传上去，作者还得回去补，别人则先看到
        一道没有材料的题。前者的代价是用户中途放弃时留下一个没人引用的文件，这与
        素材库、PDF 导入抽图今天的处境一样。

        两道校验收在这里，是因为**附件 id 是可猜的连续整数**：不校验的话，任何
        登录用户都能把别人上传的文件（例如别人交作业时附的材料）挂到自己的题目上，
        借这块板把它公开出去。
        """
        ids = list(dict.fromkeys(attachment_ids))
        if not ids:
            return 0
        await self._ensure_publisher(task=task, user_id=user_id)

        files = {
            attachment.id: attachment
            for attachment in await self._attachment_repo.get_by_ids(ids)
        }
        missing = [i for i in ids if i not in files]
        if missing:
            raise BadRequestError(
                "Unknown attachment id(s)", data={"attachmentIds": missing}
            )

        not_mine = [i for i in ids if files[i].meta.get("uploaderId") != user_id]
        if not_mine:
            raise ForbiddenError(
                "Only files you uploaded yourself can be attached",
                data={"attachmentIds": not_mine},
            )

        taken = await self._links.list_live_for_attachments(attachment_ids=ids)
        if taken:
            raise BadRequestError(
                "Attachment(s) already belong to a task",
                data={"attachmentIds": [link.attachment_id for link in taken]},
            )

        for attachment_id in ids:
            await self._links.create(task_id=task.id, attachment_id=attachment_id)
        return len(ids)

    async def download(
        self, *, task: Task, user_id: int, attachment_id: int
    ) -> tuple[bytes, str, str]:
        if not await self._may_download(task=task, user_id=user_id):
            raise ForbiddenError(
                "Only the publisher, a board manager or a participant "
                "can download this attachment"
            )

        link = await self._links.get_live(task_id=task.id, attachment_id=attachment_id)
        if link is None:
            raise NotFoundError("Attachment not found")

        attachment = await self._attachment_repo.get_by_id(attachment_id)
        if attachment is None:
            raise NotFoundError("Attachment not found")

        storage_key = attachment.meta.get("storageKey")
        if not storage_key:
            raise NotFoundError("Attachment storage key not found")
        content = await self._storage.download(storage_key)
        if content is None:
            raise NotFoundError("Attachment file not found in storage")

        # 计数在取到字节之后才加：文件取不出来时不该记一笔「有人下载过」。
        link.download_count += 1
        await self._links.save(link)

        filename = attachment.meta.get("filename", f"attachment_{attachment_id}")
        content_type = attachment.meta.get("contentType", "application/octet-stream")
        return content, filename, content_type

    async def remove(self, *, task: Task, user_id: int, attachment_id: int) -> None:
        """把附件从这道题上拿下来。

        软删这一行，存储上的对象留着：拿下来是「这道题不再列它」，不是「把文件
        毁掉」—— 同一个文件可能还被别处引用（交作业、素材库都走同一张
        ``attachment`` 表），而对象一旦删掉就没有回头路。没人再引用的对象是平台的
        存储生命期问题，不是这一处的。
        """
        await self._ensure_publisher(task=task, user_id=user_id)
        link = await self._links.get_live(task_id=task.id, attachment_id=attachment_id)
        if link is None:
            raise NotFoundError("Attachment not found")
        await self._links.soft_delete(link)

    async def _ensure_publisher(self, *, task: Task, user_id: int) -> None:
        if not await may_teach_task(self._session, task=task, user_id=user_id):
            raise ForbiddenError(
                "Only the task's publisher or a board manager can do this"
            )
