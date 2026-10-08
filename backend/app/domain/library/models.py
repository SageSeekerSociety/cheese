"""资料库的清单：每一份文件叫什么、字节在哪、是谁在哪次对话里给的 (#2114 第一节)。

这张表就是资料库本身——列出来的是这里的行，不是磁盘上的目录。一行是一份存下来的
字节：「替换为新版本」把旧的那一行标成被取代，再为新字节记一行——所以同一个名字
可以有几行，`superseded_at` 为空的那一行是现在这一份。

名字里的 `/` 就是文件夹：没有文件夹表，一个文件夹在它里面还有文件时存在。名字和
字节的位置（`location` + `blob_key`，见 `blobs`）是分开的两件事，所以改名、挪进
文件夹只改 `name`。
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, text
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
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    #: 资料库里的名字，也就是它的地址（`library/<name>`）；`/` 分出文件夹。
    name: Mapped[str] = mapped_column(String(512))
    #: 字节存在哪一种存储里（`blobs.LOCAL`）。
    location: Mapped[str] = mapped_column(
        String(16), default="local", server_default="local"
    )
    #: 字节在那个存储里的键。加这一列之前写下的行是空的，读时按旧的目录推出来
    #: （`records.blob_key`），下一次迁移补齐后设为非空。
    blob_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    #: 谁放进来的（handle）。
    added_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 在哪个房间放进来的；从资料库页直接上传的没有房间。
    room_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
