"""哪一个资料库：一个项目的、一个团队的（一个人自己的资料库，是他名下那个只有他一人的
团队的），或者一道题目的（出题人给的材料）。资料库里的读写都以它为单位。"""

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement

from app.domain.library.models import LibraryFileRecord


@dataclass(frozen=True)
class Shelf:
    """一个资料库。"""

    project_id: uuid.UUID | None = None
    team_id: int | None = None
    task_id: int | None = None
    #: 一个项目的资料库里有没有 `题目/`（`mount`）：那是题目的材料，跟着项目来自的
    #: 那一份申请走，申请还在、已经通过才有。由读的那一侧判好了交进来；不说就是没有。
    #: 写不看它：`题目/` 底下谁都写不进去。
    with_task: bool = False

    @classmethod
    def of_project(cls, project_id: uuid.UUID, *, with_task: bool) -> "Shelf":
        """``with_task`` has no default on purpose: whoever reads a project's
        library decides whether it shows `题目/` (in a request,
        `app.api.project_library.shelf_for`), so a reader that forgot to cannot
        build one."""
        return cls(project_id=project_id, with_task=with_task)

    @classmethod
    def of_team(cls, team_id: int) -> "Shelf":
        return cls(team_id=team_id)

    @classmethod
    def of_task(cls, task_id: int) -> "Shelf":
        return cls(task_id=task_id)

    def holds(self) -> ColumnElement[bool]:
        """一行属于这个资料库。"""
        if self.project_id is not None:
            return LibraryFileRecord.project_id == self.project_id
        if self.task_id is not None:
            return LibraryFileRecord.task_id == self.task_id
        return LibraryFileRecord.team_id == self.team_id

    def owns(self, row: LibraryFileRecord) -> bool:
        """``row`` 是这个资料库的一行。"""
        return (row.project_id, row.team_id, row.task_id) == (
            self.project_id,
            self.team_id,
            self.task_id,
        )

    @property
    def key(self) -> str:
        """存储里的键、加锁时用的名字：项目还是项目编号（已有的字节不挪），
        团队、题目带上前缀。"""
        if self.project_id is not None:
            return str(self.project_id)
        if self.task_id is not None:
            return f"task-{self.task_id}"
        return f"team-{self.team_id}"
