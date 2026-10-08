"""这个房间的某个路径，字节在哪一个仓库里 —— API 侧的读编排。

`source_bytes` 不是一条规则，是一条**来源**的接线：一个房间的文件可能躺在三个
地方，房间自己的目录、某条任务分支的工作区、项目的资料库，而 `library/<名字>` 这个
前缀在路径本身里就说了是第三种。读的人只看见「这个房间的文件」，所以查看器把来源
当参数收，而不是各自钉死在一个仓库上 —— 那正是「分支上的文档只有裸二进制 diff 能
看」的来由。

它留在 API 侧而不是某个领域里，因为它认得 `db`、也认得项目/房间/任务的寻址方式：
这是路由层替视图接线的事。**规则**（哪种扩展名对应哪个 mime、路径合不合法）在
`app.domain.project.room_files` 里，两边读的是同一份。

本模块不定义 `APIRouter`：它不挂路由，只被挂路由的模块 import。
"""

import uuid
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.library import records as library_records
from app.domain.library import service as library


async def source_bytes(
    db: AsyncSession,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    task: uuid.UUID | None,
    source: Literal["live", "committed"] = "live",
) -> bytes:
    """One of this room's files, from whichever store holds it.

    A room keeps what it delivered outside git; a card keeps what it is still
    writing, on its own branch; the project's 资料库 keeps what someone gave it,
    and `library/<名字>` says so in the path itself. All three are 「这个房间的
    文件」 to a reader, so the viewers take the source as a parameter instead of
    each being wired to one store — that wiring is why a document on a branch
    had no view but a raw binary diff.
    """
    if task is not None or source == "committed":
        if library.library_name(path) is not None:
            raise ValidationError(say("libraryFileNoTaskBranch"))
        from app.domain.repository.forge_files import ProjectFiles

        data, _ = await ProjectFiles(db, project_id, task).raw(path, source)
        return data
    return await library_records.read_attachment(db, project_id, room_id, path)
