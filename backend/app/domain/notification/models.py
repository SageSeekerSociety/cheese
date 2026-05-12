from datetime import datetime
from enum import Enum

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Sequence, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

notification_seq = Sequence("notification_seq")


class NotificationType(str, Enum):
    # Align enum values with DB CHECK constraint and OpenAPI NotificationType
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

    type: Mapped[NotificationType] = mapped_column("type", String(length=255), nullable=False)

    metadata_payload: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
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

    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def is_aggregation_active(self, now: datetime | None = None) -> bool:
        from datetime import datetime as _dt

        current = now or _dt.now(tz=self.aggregate_until.tzinfo if self.aggregate_until else None)
        return (
            self.is_aggregatable
            and not self.finalized
            and self.aggregate_until is not None
            and self.aggregate_until > current
        )
