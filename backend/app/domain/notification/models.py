from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Sequence, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

notification_seq = Sequence("notification_seq")


class NotificationType(str, Enum):
    MENTION = "MENTION"
    REPLY = "REPLY"
    REACTION = "REACTION"
    PROJECT_INVITE = "PROJECT_INVITE"
    DEADLINE_REMIND = "DEADLINE_REMIND"

    TEAM_JOIN_REQUEST = "TEAM_JOIN_REQUEST"
    TEAM_INVITATION = "TEAM_INVITATION"
    TEAM_REQUEST_APPROVED = "TEAM_REQUEST_APPROVED"
    TEAM_REQUEST_REJECTED = "TEAM_REQUEST_REJECTED"
    TEAM_INVITATION_ACCEPTED = "TEAM_INVITATION_ACCEPTED"
    TEAM_INVITATION_DECLINED = "TEAM_INVITATION_DECLINED"
    TEAM_INVITATION_CANCELED = "TEAM_INVITATION_CANCELED"
    TEAM_REQUEST_CANCELED = "TEAM_REQUEST_CANCELED"

    #: 平台在房间里说的、要人动手的那一句（`app.domain.agent.announce`）。所有
    #: 平台提示共用这一个码：要显示的文字是后端给的 `payload.content`，前端不按
    #: 类别拼模板，具体是哪件事看 `payload.eventType`。
    ROOM_NOTICE = "ROOM_NOTICE"

    #: 芝士提出待确认问题，本轮停止等待回答（`announce.notify_question`）。
    #: 与 `ROOM_NOTICE` 分开是因为它不是平台说的：文字是芝士自己的话，不受那一行
    #: 40 字的约束，前端也要按「一个问题」渲染，而不是按一条平台提示。
    CHEESE_QUESTION = "CHEESE_QUESTION"


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index(
            "idx_notification_receiver_read_created",
            "receiver_id",
            "read",
            "created_at",
            postgresql_using="btree",
        ),
        Index(
            "idx_notification_aggregation",
            "receiver_id",
            "aggregation_key",
            "aggregate_until",
            postgresql_using="btree",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, notification_seq, primary_key=True)
    receiver_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    type: Mapped[NotificationType] = mapped_column(
        "type", String(length=255), nullable=False
    )

    metadata_payload: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    content: Mapped[dict | None] = mapped_column("content", JSONB, nullable=True)

    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    is_aggregatable: Mapped[bool] = mapped_column(
        "is_aggregatable", Boolean, nullable=False, default=False
    )
    aggregation_key: Mapped[str | None] = mapped_column(
        "aggregation_key", String(length=255), nullable=True
    )
    aggregate_until: Mapped[datetime | None] = mapped_column(
        "aggregate_until", DateTime(timezone=True), nullable=True
    )
    finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: 这一行是哪一笔投递送来的（`delivery/ledger.py` 的去重键，全表唯一）。
    #:
    #: 「恰好一次」靠的就是它：一次发送在回写 `sent_at` 之前崩掉，补发会把同一笔再
    #: 发一遍，插入撞上这个唯一约束，收件人手里仍然只有一条。新写进来的每一行都带
    #: 着键 —— 发通知只有账本这一处（I11）；NULL 只存在于账本之前写下的旧行，而
    #: Postgres 的唯一索引不认为两个 NULL 相等，所以它们之间互不排斥。
    delivery_key: Mapped[str | None] = mapped_column(
        String(length=160), nullable=True, unique=True
    )

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
