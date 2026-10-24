"""资料库的清单：每一份文件叫什么、字节在哪、是谁在哪次对话里给的 (#2114 第一节)。

这张表就是资料库本身——列出来的是这里的行，不是磁盘上的目录。一行是一份存下来的
字节：「替换为新版本」把旧的那一行标成被取代，再为新字节记一行——所以同一个名字
可以有几行，`superseded_at` 为空的那一行是现在这一份。

资料库属于一个项目、一个团队，或者一道题目（`shelf.Shelf`）：一个人自己的资料库，是他
名下那个只有他一人的团队的；题目的资料库装出题人给的材料，从这道题派生的项目在自己资料库
的 `题目/` 下读到它（`mount`）。一行只属于其中一个。

名字里的 `/` 就是文件夹：没有文件夹表，一个文件夹在它里面还有文件时存在。名字和
字节的位置（`location` + `blob_key`，见 `blobs`）是分开的两件事，所以改名、挪进
文件夹只改 `name`。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk, _now


class LibraryFileRecord(UuidPk, Base):
    __tablename__ = "library_files"
    __table_args__ = (
        # 一个名字同一时刻只有一份是现在这一份。
        Index(
            "uq_library_files_current",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        # 团队的资料库也一样。项目的那一条管不到它：那里的 project_id 是空的，
        # 空值在唯一索引里两两不同。
        Index(
            "uq_library_files_team_current",
            "team_id",
            "name",
            unique=True,
            postgresql_where=text("superseded_at IS NULL AND team_id IS NOT NULL"),
        ),
        # 题目的资料库也一样。
        Index(
            "uq_library_files_task_current",
            "task_id",
            "name",
            unique=True,
            postgresql_where=text("superseded_at IS NULL AND task_id IS NOT NULL"),
        ),
        CheckConstraint(
            "num_nonnulls(project_id, team_id, task_id) = 1",
            name="ck_library_files_one_shelf",
        ),
    )

    #: 属于哪个项目的资料库；团队的资料库是空的。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: 属于哪个团队的资料库；项目的资料库是空的。
    team_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("team.id", ondelete="CASCADE", name="fk_library_files_team_id_team"),
        index=True,
        nullable=True,
    )
    #: 属于哪道题目的资料库；项目、团队的资料库是空的。
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("task.id", ondelete="CASCADE", name="fk_library_files_task_id_task"),
        index=True,
        nullable=True,
    )
    #: 资料库里的名字，也就是它的地址（`library/<name>`）；`/` 分出文件夹。
    name: Mapped[str] = mapped_column(String(512))
    #: 字节存在哪一种存储里（`app.core.blob_store` 的 `LOCAL` / `BUCKET`）。
    location: Mapped[str] = mapped_column(
        String(16), default="local", server_default="local"
    )
    #: 字节在那个存储里的键（`.library-blobs/<项目>/<这一行的 id>`，跟着这一行，不跟名字）。
    blob_key: Mapped[str] = mapped_column(String(1024))
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    #: 谁放进来的（handle）。
    added_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 在哪个房间放进来的；从资料库页直接上传的没有房间。
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    #: 从那个房间文件区里哪一份存过来的；那一份由此知道自己已经在资料库里了。
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
