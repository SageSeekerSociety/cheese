"""从题目派生的项目，资料库里多一个只读的 `题目/` (#4767 第一节)。

`题目/` 底下是那道题目的资料库（`Shelf.of_task`）里现在的每一份：出题人给的材料，和
每一版生成的那份题目说明。不是拷贝：读的是题目那一边的行，出题人换了一份文件，项目里
下一次读到的就是新的。项目自己的资料库里不能往这个前缀下写，所以这里的东西都是出题人
给的，项目里谁都改不了它。

项目来自哪道题目记在 `projects.external_task_id`；题目删掉了，这个文件夹跟着不见。
有没有它，跟着项目来自的那一份申请走（`task.package.task_folder_shown`）：还在、已经
通过，项目里的人和 agent 都看得到；等批、被拒、退出或不再有效，谁都看不到。读的那一侧
判好了，放在 `Shelf.with_task` 上交进来，没说就看不到。

题目那一侧的行在这里只读两列（`id`、`deleted_at`），读的是那张表本身：题目那一包要往
资料库里写它的材料，资料库就不能反过来认得它（`.importlinter` C3）。
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, UnprocessableEntityError
from app.core.request_facts import recall, remember
from app.core.sentences import say
from app.domain.library.models import LibraryFileRecord as Row
from app.domain.library.shelf import Shelf
from app.domain.project.models import Project

#: 项目资料库里题目那个文件夹的名字。
FOLDER = "题目"
PREFIX = f"{FOLDER}/"

_task = sa.table("task", sa.column("id", sa.BigInteger), sa.column("deleted_at"))


def under(name: str) -> bool:
    """这个名字是 `题目` 本身，或者在它底下。"""
    return name == FOLDER or name.startswith(PREFIX)


_TABLES = frozenset({"projects", "task"})


def _key(project_id: uuid.UUID) -> tuple[str, uuid.UUID]:
    return ("library_mounted", project_id)


def known(session: AsyncSession, project_id: uuid.UUID, task_id: int | None) -> None:
    """读的那一侧已经把项目行和它那道题目的行取出来了（`task_folder_shown`）：
    挂着哪道题目照它记下，这一个请求里 `mounted` 不必再问一遍。`task_id` 是还在的
    那道题目，题目删掉了或者项目不来自题目时是 None。"""
    remember(session, _key(project_id), task_id, tables=_TABLES)


async def mounted(session: AsyncSession, shelf: Shelf) -> int | None:
    """这个资料库底下挂着哪道题目的资料库：从一道还在的题目派生的项目才有。不问读的人
    是谁——写的路、项目自己被盖住的那几行都按它算。

    一个请求里读资料库的那几步各问一次，答案同一个，所以只问一次。项目行已经在这个
    会话里、而它不来自题目时，不用问数据库。"""
    project_id = shelf.project_id
    if project_id is None:
        return None
    found, cached = recall(session, _key(project_id))
    if found:
        return cached
    sync = session.sync_session
    loaded = sync.identity_map.get(sync.identity_key(Project, (project_id,)))
    if (
        loaded is not None
        and "external_task_id" not in inspect(loaded).expired_attributes
        and loaded.external_task_id is None
    ):
        known(session, project_id, None)
        return None
    task_id = await session.scalar(
        sa.select(Project.external_task_id)
        .join(_task, _task.c.id == Project.external_task_id)
        .where(Project.id == project_id, _task.c.deleted_at.is_(None))
    )
    known(session, project_id, task_id)
    return task_id


async def task_of(session: AsyncSession, shelf: Shelf) -> int | None:
    """这个资料库里看得到的那道题目的资料库：挂着一个，而且项目来自的那一份申请还在、
    已经通过（`Shelf.with_task`）。"""
    if not shelf.with_task:
        return None
    return await mounted(session, shelf)


async def resolve(session: AsyncSession, shelf: Shelf, name: str) -> tuple[Shelf, str]:
    """`name` 实际在哪个资料库、在那里叫什么：`题目/` 底下的在题目那一边，去掉前缀；
    别的原样。项目的申请没有通过或不再有效时，就是拿不到（403），不落回项目自己
    在这个前缀下被盖住的那几行。"""
    if not name.startswith(PREFIX):
        return shelf, name
    task_id = await mounted(session, shelf)
    if task_id is None:
        return shelf, name
    if not shelf.with_task:
        raise ForbiddenError(say("libraryTaskFolderNeedsApprovedClaim", folder=FOLDER))
    return Shelf.of_task(task_id), name[len(PREFIX) :]


async def refuse_write(session: AsyncSession, shelf: Shelf, *names: str) -> None:
    """往 `题目/` 底下写、从那里挪走或扔掉，一律不行：那是出题人的材料。"""
    if not any(under(name) for name in names):
        return
    if await mounted(session, shelf) is not None:
        raise UnprocessableEntityError(say("libraryTaskFolderReadOnly", folder=FOLDER))


def project_rows(shelf: Shelf, task_id: int | None) -> sa.ColumnElement[bool]:
    """项目自己的那些行里，在这个清单上的：挂着题目时，项目自己在 `题目/` 底下的
    行（挂上之前放进来的）被题目那一份盖住。"""
    if task_id is None or shelf.project_id is None:
        return sa.true()
    return sa.and_(Row.name != FOLDER, Row.name.not_like(f"{PREFIX}%"))


def shown(name: str) -> str:
    """题目那一边的一个名字在项目里的地址。"""
    return f"{PREFIX}{name}"
