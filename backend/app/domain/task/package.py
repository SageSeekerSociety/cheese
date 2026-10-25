"""一道题目交给领取者的那一份：说明、阶段、提交内容、评分要点和材料，以及它的每一版
(#4767 第一节)。

材料是题目资料库（`Shelf.of_task`）里现在的每一份文件，字节走 `app.core.blob_store`，
替换时旧的一份留作历史（`superseded_at`），和项目的资料库是同一套记录。

题目内容每变一次，就记一版（`TaskPackageRevision`），写明改了哪几样：发布时是第 1 版，
之后改说明、阶段、提交内容、评分要点，放进、替换、删掉一份材料，各记下一版。每记一版，题目
资料库里那份题目说明（`BRIEF`）跟着换成这一版的样子，所以它的历史就是每一版的说明。
从这道题派生的项目在资料库的 `题目/` 下读到这些（`app.domain.library.mount`），项目总览
和项目里的 agent 都从这里知道题目到了第几版、这一版改了什么。
"""

import asyncio
import io
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.space_access import may_teach_task
from app.core import blob_store
from app.core.errors import BadRequestError, ForbiddenError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.attachment.services import AttachmentService
from app.domain.library import records as library
from app.domain.library.models import LibraryFileRecord
from app.domain.library.service import clean_upload_name
from app.domain.library.shelf import Shelf
from app.domain.notification.preferences import DEFAULT_TIMEZONE
from app.domain.project import starter as starter_archive
from app.domain.project.models import Project, ProjectForge
from app.domain.task.models import (
    Task,
    TaskPackageRevision,
    TaskRubricItem,
    TaskStage,
    TaskSubmissionSchemaEntry,
)
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.user.models import User

#: 题目资料库里每一版生成的那份题目说明。它不是出题人传的材料：材料清单里不列它，
#: 出题人也不能替换、删掉它。
BRIEF = "题目说明.md"
#: 起始代码（#4767 第二节）：题目资料库里叫这个名字的那一份 .zip。它有自己的一栏，
#: 不在材料清单里；换一份和换材料一样留下旧的一份，领题时取的是那一刻的这一份。
STARTER = "起始代码.zip"
#: 题目资料库里不算材料的那两份：生成的说明，和有自己一栏的起始代码。
_NOT_MATERIALS = (BRIEF, STARTER)

#: `changes` 里的几样，和它们在摘要里叫什么。
DESCRIPTION = "description"
SUBMISSION = "submission"
RUBRIC = "rubric"
STAGES = "stages"
FILES = "files"
STARTER_CHANGE = "starter"
PUBLISHED = "published"

_PART_NAMES = {
    DESCRIPTION: "说明",
    STAGES: "阶段",
    SUBMISSION: "提交内容",
    RUBRIC: "评分要点",
}
_FILE_VERBS = {"added": "新增", "replaced": "替换", "removed": "删除"}
#: 起始代码那一格的值（`changes["starter"]`）就是这三个动词之一。
ADDED, REPLACED, REMOVED = "added", "replaced", "removed"

#: 提交内容一项是什么（`task_submission_schema.type`）。
_ENTRY_KINDS = {0: "文字", 1: "文件"}


def shelf(task_id: int) -> Shelf:
    return Shelf.of_task(task_id)


def files_changed(
    *,
    added: Sequence[str] = (),
    replaced: Sequence[str] = (),
    removed: Sequence[str] = (),
) -> dict:
    """`changes` 里材料那一格；什么都没变是空的。"""
    found = {
        verb: list(names)
        for verb, names in (
            ("added", added),
            ("replaced", replaced),
            ("removed", removed),
        )
        if names
    }
    return {FILES: found} if found else {}


def summary(changes: dict) -> str:
    """一版改了什么，一句话：「改了说明、评分要点；新增文件 数据.csv」。"""
    if changes.get(PUBLISHED):
        return "题目发布"
    parts: list[str] = []
    edited = [name for key, name in _PART_NAMES.items() if changes.get(key)]
    if edited:
        parts.append("改了" + "、".join(edited))
    for verb, word in _FILE_VERBS.items():
        names = (changes.get(FILES) or {}).get(verb) or []
        if names:
            parts.append(f"{word}文件 " + "、".join(names))
    starter = changes.get(STARTER_CHANGE)
    if starter in _FILE_VERBS:
        parts.append(f"{_FILE_VERBS[starter]}起始代码")
    return "；".join(parts)


async def current(session: AsyncSession, task_id: int) -> TaskPackageRevision | None:
    """这道题现在是哪一版；一版都没记过（发布于有版本之前、又没改过）是 None。"""
    return await session.scalar(
        select(TaskPackageRevision)
        .where(TaskPackageRevision.task_id == task_id)
        .order_by(TaskPackageRevision.number.desc())
        .limit(1)
    )


async def current_number(session: AsyncSession, task_id: int) -> int | None:
    return await session.scalar(
        select(func.max(TaskPackageRevision.number)).where(
            TaskPackageRevision.task_id == task_id
        )
    )


async def since(
    session: AsyncSession, task_id: int, after: datetime
) -> list[TaskPackageRevision]:
    """`after` 之后记的每一版，新的在前：一个项目建好之后题目又更新过几次。"""
    return list(
        await session.scalars(
            select(TaskPackageRevision)
            .where(
                TaskPackageRevision.task_id == task_id,
                TaskPackageRevision.created_at > after,
            )
            .order_by(TaskPackageRevision.number.desc())
        )
    )


async def _handle(session: AsyncSession, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return await session.scalar(select(User.username).where(User.id == user_id))


#: 题目说明是全体领取者和他们的芝士读的同一份，写不了各人的钟点：阶段的截止
#: 按北京时间写并写明（没报过时区的人也按它读，`notification.preferences`）。
_BRIEF_ZONE = ZoneInfo(DEFAULT_TIMEZONE)


async def brief(session: AsyncSession, task: Task, number: int) -> str:
    """第 `number` 版的题目说明：简介、说明、阶段、提交内容，和公开的评分要点。
    分了阶段时，提交内容和评分要点各写明属于哪个阶段。"""
    stages = list(
        await session.scalars(
            select(TaskStage)
            .where(TaskStage.task_id == task.id)
            .order_by(TaskStage.index)
        )
    )

    def in_stage(index: int) -> str:
        return f"｜{stages[index].name}" if 0 <= index < len(stages) else ""

    entries = list(
        await session.scalars(
            select(TaskSubmissionSchemaEntry)
            .where(TaskSubmissionSchemaEntry.task_id == task.id)
            .order_by(TaskSubmissionSchemaEntry.index)
        )
    )
    lines = [f"# {task.name}", "", f"第 {number} 版", ""]
    if task.intro:
        lines += ["## 简介", "", task.intro, ""]
    if task.description:
        lines += ["## 说明", "", task.description, ""]
    if stages:
        lines += ["## 阶段", ""]
        for i, stage in enumerate(stages, start=1):
            due = (
                f"截止 {stage.due_at.astimezone(_BRIEF_ZONE):%Y-%m-%d %H:%M}（北京时间）"
                if stage.due_at is not None
                else "不设截止"
            )
            lines.append(
                f"{i}. {stage.name}（{due}）"
                + (f"：{stage.description}" if stage.description else "")
            )
        lines.append("")
    if entries:
        lines += ["## 提交内容", ""]
        lines += [
            f"{i}. {entry.description}"
            f"（{_ENTRY_KINDS.get(entry.type, '文字')}{in_stage(entry.stage)}）"
            for i, entry in enumerate(entries, start=1)
        ]
        lines.append("")
    if task.rubric_public:
        rubric = list(
            await session.scalars(
                select(TaskRubricItem)
                .where(TaskRubricItem.task_id == task.id)
                .order_by(TaskRubricItem.index)
            )
        )
        if rubric:
            lines += ["## 评分要点", ""]
            lines += [
                f"- {row.item}（{row.points} 分{in_stage(row.stage)}）"
                + (f"：{row.criteria}" if row.criteria else "")
                for row in rubric
            ]
            lines.append("")
    return "\n".join(lines)


async def record(
    session: AsyncSession, task: Task, *, by: int | None, changes: dict
) -> TaskPackageRevision | None:
    """记下一版：`changes` 说改了哪几样，什么都没改就不记。题目说明跟着换成这一版。

    版本号是读出来再加一，两个人同时改同一道题时两边会读到同一个数，所以先按题目
    取一把锁，把这道题上的记版排成队。"""
    if not changes:
        return None
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"task-package:{task.id}"},
    )
    number = (await current_number(session, task.id) or 0) + 1
    revision = TaskPackageRevision(
        task_id=task.id,
        number=number,
        author_id=by,
        changes=changes,
        created_at=datetime.now(UTC),
    )
    session.add(revision)
    await session.flush()
    content = (await brief(session, task, number)).encode()
    handle = await _handle(session, by)
    if await library.exists(session, shelf(task.id), BRIEF):
        await library.replace(
            session, shelf=shelf(task.id), name=BRIEF, data=content, by=handle or ""
        )
    else:
        await library.add(session, shelf(task.id), BRIEF, content, handle, None)
    return revision


# --- 材料 -------------------------------------------------------------------------
#
# 三条判据，各自一句话（和题目附件时一样）：
#
# - 谁能放进、替换、删掉：出题人本人，或这块板的所有者 / 管理员（`may_teach_task`）。
# - 谁能看到清单：看得见这道题的人。看不见材料就无法判断要不要领。
# - 谁能下载：出题人 / 板管理员 / 已经领取的人。领取者拿不到材料就没法做题。


async def refuse_unless_teacher(
    session: AsyncSession, task: Task, user_id: int
) -> None:
    if not await may_teach_task(session, task=task, user_id=user_id):
        raise ForbiddenError(say("draftTaskNotYours"))


async def may_download(session: AsyncSession, task: Task, user_id: int) -> bool:
    if await may_teach_task(session, task=task, user_id=user_id):
        return True
    return await TaskVisibilityService(session=session).is_participant(
        task_id=task.id, user_id=user_id
    )


async def _project_task(session: AsyncSession, project_id) -> Task | None:
    project = await session.get(Project, project_id)
    if project is None or project.external_task_id is None:
        return None
    task = await session.get(Task, project.external_task_id)
    if task is None or task.deleted_at is not None:
        return None
    return task


async def mount_for(session: AsyncSession, project_id, user_id: int | None) -> bool:
    """这个人在这个项目的资料库里看不看得到 `题目/`：按题目自己的下载判据，不按项目
    的成员名单（同一个团队里没领题的人、被出题人撤掉的人，项目都还进得去）。"""
    if user_id is None:
        return False
    task = await _project_task(session, project_id)
    return task is not None and await may_download(session, task, user_id)


async def mount_for_agents(session: AsyncSession, project_id) -> bool:
    """项目里的 agent 看不看得到 `题目/`：它们替这个项目干活，项目是领了这道题的那个人
    开的，所以按项目所有者的判据——他还拿得到材料，项目里的 agent 就读得到。"""
    project = await session.get(Project, project_id)
    if project is None or not project.owner_handle:
        return False
    owner = await session.scalar(
        select(User.id).where(User.username == project.owner_handle)
    )
    return await mount_for(session, project_id, owner)


async def refuse_unless_downloader(
    session: AsyncSession, task: Task, user_id: int
) -> None:
    if not await may_download(session, task, user_id):
        raise ForbiddenError(say("taskFilesForTakers"))


async def files(session: AsyncSession, task_id: int) -> list[LibraryFileRecord]:
    """出题人放进题目资料库、现在的每一份材料（不含题目说明和起始代码），按放进来的
    先后。"""
    return list(
        await session.scalars(
            select(LibraryFileRecord)
            .where(
                shelf(task_id).holds(),
                LibraryFileRecord.superseded_at.is_(None),
                LibraryFileRecord.name.not_in(_NOT_MATERIALS),
            )
            .order_by(LibraryFileRecord.created_at, LibraryFileRecord.id)
        )
    )


async def file_counts(session: AsyncSession, task_ids: list[int]) -> dict[int, int]:
    """每道题现在有几份材料；一份都没有的题不在结果里。"""
    if not task_ids:
        return {}
    rows = await session.execute(
        select(LibraryFileRecord.task_id, func.count())
        .where(
            LibraryFileRecord.task_id.in_(task_ids),
            LibraryFileRecord.superseded_at.is_(None),
            LibraryFileRecord.name.not_in(_NOT_MATERIALS),
        )
        .group_by(LibraryFileRecord.task_id)
    )
    return {int(task_id): int(count) for task_id, count in rows}


async def _own_uploads(
    uploads: AttachmentService, attachment_ids: Sequence[int], by: int
) -> list[tuple[bytes, str]]:
    """这个人自己先传成附件的几份文件：字节和文件名。

    只认他自己传的：附件 id 是可猜的连续整数，不认的话，谁都能把别人交作业附的
    文件拷进自己的题目、借这块板公开出去。"""
    ids = list(dict.fromkeys(attachment_ids))
    found = {a.id: a for a in await uploads.get_many(ids)}
    missing = [i for i in ids if i not in found]
    if missing:
        raise BadRequestError(say("draftFileNotYours"), data={"attachmentIds": missing})
    if any(not uploads.is_uploader(found[i], by) for i in ids):
        raise ForbiddenError(say("draftFileNotYours"))
    out: list[tuple[bytes, str]] = []
    for attachment_id in ids:
        content, filename, _ = await uploads.download(attachment_id)
        out.append((content, filename))
    return out


async def add_uploads(
    session: AsyncSession,
    task: Task,
    *,
    by: int,
    attachment_ids: Sequence[int],
    uploads: AttachmentService,
) -> list[str]:
    """把这个人先传成附件的几份文件（发题页上选的、交给芝士的）拷进题目资料库，
    不另记一版（调用方把它们算进自己那一版）。返回它们在这里的名字。"""
    return [
        await add_file(
            session, task, by=by, filename=clean_upload_name(filename), data=content
        )
        for content, filename in await _own_uploads(uploads, attachment_ids, by)
    ]


def _material(name: str) -> str:
    """出题人点名的一份材料：题目说明不是材料，不能换、不能删；起始代码走自己那一栏，
    那里会先查它是不是一份能解开的压缩包。"""
    if name == BRIEF:
        raise UnprocessableEntityError(say("taskBriefIsGenerated", name=BRIEF))
    if name == STARTER:
        raise UnprocessableEntityError(say("taskStarterHasItsOwnPlace", name=STARTER))
    return name


async def add_file(
    session: AsyncSession,
    task: Task,
    *,
    by: int,
    filename: str,
    data: library.Incoming,
) -> str:
    """放进一份材料，不另记一版（调用方把它算进自己那一版）。撞名拿下一个 `(n)`，
    返回它的名字。"""
    return await library.add(
        session,
        shelf(task.id),
        _material(filename),
        data,
        await _handle(session, by),
        None,
    )


async def replace_file(
    session: AsyncSession,
    task: Task,
    *,
    by: int,
    name: str,
    data: library.Incoming,
) -> None:
    """把一份材料换成新的字节，旧的留作历史，记一版。"""
    await library.replace(
        session,
        shelf=shelf(task.id),
        name=_material(name),
        data=data,
        by=await _handle(session, by) or "",
    )
    await record(session, task, by=by, changes=files_changed(replaced=[name]))


async def remove_file(session: AsyncSession, task: Task, *, by: int, name: str) -> None:
    """扔掉一份材料（连同它被替换下来的几版），记一版。"""
    await library.remove(session, shelf=shelf(task.id), name=_material(name))
    await record(session, task, by=by, changes=files_changed(removed=[name]))


# --- 起始代码 ---------------------------------------------------------------------


async def starter_file(session: AsyncSession, task_id: int) -> LibraryFileRecord | None:
    """这道题现在的起始代码；没有是 None。"""
    return await session.scalar(
        select(LibraryFileRecord).where(
            shelf(task_id).holds(),
            LibraryFileRecord.superseded_at.is_(None),
            LibraryFileRecord.name == STARTER,
        )
    )


async def _spooled(location: str, key: str, size: int, into: BinaryIO) -> None:
    """存储里 ``(location, key)`` 那一份字节，一段一段写进 ``into``。"""
    if size:
        async for piece in blob_store.store(location).chunks(key, 0, size - 1):
            into.write(piece)
    into.seek(0)


class _Stored(io.RawIOBase):
    """存储里的一份字节，当作一个能 seek 的文件来读，每次只按范围取要的那一段。

    给 `zipfile` 在别的线程里读：它只读末尾的目录，几 GB 的压缩包也不必整份取回、
    不进内存。取字节的协程交回 ``loop`` 上跑。"""

    def __init__(self, row: LibraryFileRecord, loop: asyncio.AbstractEventLoop):
        self._store = blob_store.store(row.location)
        self._key = row.blob_key
        self._size = row.bytes
        self._loop = loop
        self._at = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._at

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._at, io.SEEK_END: self._size}[whence]
        self._at = max(0, base + offset)
        return self._at

    async def _range(self, start: int, end: int) -> bytes:
        return b"".join(
            [piece async for piece in self._store.chunks(self._key, start, end)]
        )

    def readinto(self, buffer) -> int:
        count = min(len(buffer), self._size - self._at)
        if count <= 0:
            return 0
        data = asyncio.run_coroutine_threadsafe(
            self._range(self._at, self._at + count - 1), self._loop
        ).result()
        buffer[: len(data)] = data
        self._at += len(data)
        return len(data)


async def _refuse_unless_starter(row: LibraryFileRecord) -> None:
    """放进来的这一份能不能作为起始代码，当场就说，不等到有人领题。"""
    reader = io.BufferedReader(_Stored(row, asyncio.get_running_loop()), 1 << 20)
    await asyncio.to_thread(starter_archive.check, reader)


async def set_starter(
    session: AsyncSession, task: Task, *, by: int, data: library.Incoming
) -> str:
    """把起始代码设成这一份，不另记一版（调用方把它算进自己那一版）。已经有一份的，
    旧的留作历史。返回 `ADDED` 或 `REPLACED`。

    分段上传的字节要登记时才合成一份，所以先放进来再查。放进来的这一步包在一个保存点里：
    不能用的，这一份的字节当场删掉，退回保存点，旧的那一份照旧是现在这一份。"""
    handle = await _handle(session, by)
    #: (存储, 键)：放进来的那一份字节在哪。退回保存点之后那一行就读不到了，先记下。
    placed: tuple[str, str] | None = None
    try:
        async with session.begin_nested():
            if await library.exists(session, shelf(task.id), STARTER):
                await library.replace(
                    session,
                    shelf=shelf(task.id),
                    name=STARTER,
                    data=data,
                    by=handle or "",
                )
                verb = REPLACED
            else:
                await library.add(session, shelf(task.id), STARTER, data, handle, None)
                verb = ADDED
            row = await starter_file(session, task.id)
            assert row is not None  # placed just above
            placed = (row.location, row.blob_key)
            await _refuse_unless_starter(row)
    except UnprocessableEntityError:
        if placed is not None:
            await blob_store.store(placed[0]).delete(placed[1])
        raise
    return verb


async def set_starter_upload(
    session: AsyncSession,
    task: Task,
    *,
    by: int,
    attachment_id: int,
    uploads: AttachmentService,
) -> str:
    """发题页、芝士写的修改里带的起始代码：这个人自己先传成附件的那一份。"""
    [(content, _)] = await _own_uploads(uploads, [attachment_id], by)
    return await set_starter(session, task, by=by, data=content)


async def remove_starter(session: AsyncSession, task: Task, *, by: int) -> None:
    """不要起始代码了（连同换下来的几版），记一版。之后领题的人拿到的是空仓库。"""
    await library.remove(session, shelf=shelf(task.id), name=STARTER)
    await record(session, task, by=by, changes={STARTER_CHANGE: REMOVED})


async def fetch_starter(session: AsyncSession, task: Task, into: Path) -> int | None:
    """领题这一刻的起始代码写到 ``into``，答题目现在是第几版；没有起始代码是 None。"""
    row = await starter_file(session, task.id)
    if row is None:
        return None
    with into.open("wb") as out:
        await _spooled(row.location, row.blob_key, row.bytes, out)
    return await current_number(session, task.id) or 1


# --- 项目里的 agent ----------------------------------------------------------------


async def section_for_project(
    session: AsyncSession, project: Project | None
) -> str | None:
    """从题目派生的项目里，agent 每一轮读到的「题目」那一段：现在第几版、材料在哪、
    这一版改了什么。不是从题目来的项目，或者那道题已经删了，是 None。"""
    if project is None or project.external_task_id is None:
        return None
    task = await session.get(Task, project.external_task_id)
    if task is None or task.deleted_at is not None:
        return None
    revision = await current(session, task.id)
    if revision is None or not await mount_for_agents(session, project.id):
        return None
    lines = [
        "## 题目",
        f"这个项目来自题目「{task.name}」，题目现在是第 {revision.number} 版。",
        f"题目的材料在项目资料库的 `题目/` 文件夹里，只能读，不能改：列资料库的工具"
        f"看 `题目` 这一层，读附件的工具读 `library/题目/<名字>`，要取到工作机器上"
        f"用 `cheese library get 题目/<名字>`。说明、阶段、提交内容和公开的评分要点在 "
        f"`library/题目/{BRIEF}`。",
    ]
    started = await session.scalar(
        select(ProjectForge.starter_revision).where(
            ProjectForge.project_id == project.id
        )
    )
    if started is not None:
        lines.append(
            f"代码仓库 main 上的第一个提交是题目第 {started} 版的起始代码，"
            f"由平台提交；题目现在的那一份在 `library/题目/{STARTER}`。"
        )
    if revision.number > 1:
        lines.append(
            f"第 {revision.number} 版改了：{summary(revision.changes or {})}。"
            "出题人改过题目，按新的这一版做。"
        )
    return "\n".join(lines)
