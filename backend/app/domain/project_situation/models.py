import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProjectSituation(Base):
    """一个项目的「现状」：上一次写下的那几句，和写的时候项目到了哪一步。

    ``major`` 和 ``minor`` 是写那一刻读到的两档进展记号（见 ``material.Marks``）：
    读的时候拿现在的记号和它们比，就知道上次之后有没有新进展、是哪一档。
    """

    __tablename__ = "project_situations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            ondelete="CASCADE",
            name="fk_project_situations_project_id_projects",
        ),
        primary_key=True,
    )
    #: 写下的 Markdown；还没写成过是空串。
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: 写成的那一刻；还没写成过是 None。
    written_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    major: Mapped[str] = mapped_column(String(200), default="", server_default="")
    minor: Mapped[str] = mapped_column(String(200), default="", server_default="")
    #: 正在重写：哪一刻开始的。一个后端领了就不让别的后端再领；领了又没写完（进
    #: 程没了）的，过了 ``service.CLAIM`` 就当没领。
    writing_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 上一次动笔的那一刻，写成没写成都算：两次之间至少隔这么久（``service``
    #: 里两档各自的间隔），问不出来的项目也不会每打开一次就再问一次。
    tried_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 写的那个模型（项目目录里的 id）。
    model: Mapped[str] = mapped_column(String(128), default="", server_default="")
