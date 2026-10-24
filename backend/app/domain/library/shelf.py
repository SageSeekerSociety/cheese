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

    @classmethod
    def of_project(cls, project_id: uuid.UUID) -> "Shelf":
        return cls(project_id=project_id)

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
