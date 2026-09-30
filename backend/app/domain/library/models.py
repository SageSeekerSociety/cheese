"""资料库里每一份文件是谁、在哪次对话里给的 (#2114 第一节的一部分)。

文件本身还在磁盘上（`service.library_root`），按名字寻址；这张表只记磁盘记不住的
事：谁给的、在哪个房间、什么时候，以及它被替换过几次。一行是一份存下来的字节：
「替换为新版本」把旧的那一行标成被取代、字节挪进历史目录，再为新字节记一行——所以
同一个名字可以有几行，`superseded_at` 为空的那一行是现在这一份。
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
    #: 资料库里的名字，也就是它的地址（`library/<name>`）。
    name: Mapped[str] = mapped_column(String(512))
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
