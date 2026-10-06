"""运行记录：平台在一段对话运行时做了什么、遇到了什么，留给想查的人看。

排队、环境的准备和休眠、AI 服务重试、记忆改动、报错 —— 这些过去和人说的话写在同一
张 `blocks` 里，于是频道主线上平台的话比人的还多。它们不是对话的一部分：读聊天的人
用不着，想知道「这一轮为什么慢」的人去现场看，查平台问题的人去管理后台看。所以单独
一张表，按对话和轮次挂着，保留 `RETENTION`。

一行的内容和过去那条事件一样：一句话（`content`，平台的中文原句）加一份 `meta`
（`notice()` 拼的那些字段：类别、级别、详情、`i18n`）。`kind` 是类别码，从 `meta`
里提出来成一列，因为后台和读取方按它筛。

有几种记录会随事情推进改写自己（重试到第几次、整理完没有、机器连上没有），所以这
里允许原地改写，不是只增不改的日志。
"""

import uuid
from datetime import timedelta

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk

# The registry `conversation_id` points at: mapped wherever this is, so the
# foreign key resolves in a process that never imports `app.models`.
from app.domain.conversation.models import Conversation  # noqa: F401

#: 一条运行记录留多久。过了就删：要查更早的事，看日志和告警。
RETENTION = timedelta(days=30)


class RunRecord(UuidPk, Timestamps, Base):
    __tablename__ = "run_records"
    __table_args__ = (
        # 现场：一段对话的记录，按时间翻页。
        Index("ix_run_records_conversation_created", "conversation_id", "created_at"),
        # 一轮的记录（谁的轮、它在等什么）。
        Index("ix_run_records_turn", "turn_id"),
        # 管理后台：某一类在某段时间里的记录；也是过期清理走的那条路。
        Index("ix_run_records_kind_created", "kind", "created_at"),
        Index("ix_run_records_created", "created_at"),
    )

    # 报错可能发生在任何项目之外（登录页、管理后台），所以两列都可以空。
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # 这件事是谁那一轮的：AI 队友的 handle。一轮在别的东西落下之前就排队或者
    # 失败时，界面靠它知道是哪位队友。
    seat: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(8), default="info")
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
