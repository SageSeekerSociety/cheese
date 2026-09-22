"""项目收件箱的请求 / 响应形状（Pydantic v2）。

字段名是 HTTP 上的那一套（`kind`、`target_handle`、`payload`），表上的列名是并表
之后的那一套（`type`、`recipient_handle`、`metadata`）。翻译只在这里发生一次，
`from_row` 就是那一处。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.notification.models import (
    Notification,
    NotificationLevel,
    NotificationType,
)

#: 项目收件箱写得进来的那几种。人对人的那些（回帖、入队申请、审批结果）不从这条
#: 路进来 —— 它们由各自的调用点经投递账本写入。
PROJECT_NOTIFICATION_KINDS = (
    NotificationType.CHANGE_ALERT,
    NotificationType.DECISION_REQUEST,
    NotificationType.ACCEPT_REQUEST,
    NotificationType.HEARTBEAT,
    NotificationType.MENTION,
)


class NotificationCreate(BaseModel):
    level: NotificationLevel
    kind: NotificationType
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    target_handle: str | None = Field(default=None, max_length=64)
    topic_id: uuid.UUID | None = None
    payload: dict = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    feedback: str


class ResolveIn(BaseModel):
    # NB: no ``decided_by`` — the decider is the verified caller (ActorResolver),
    # never a body field. A client still sending it is silently ignored.
    chosen: str


class NotificationOut(BaseModel):
    #: bigint，不再是 uuid：两张表并成一张之后主键跟的是 `notification_seq`。
    id: int
    project_id: uuid.UUID | None
    topic_id: uuid.UUID | None
    level: NotificationLevel | None
    kind: NotificationType
    target_handle: str | None
    title: str
    body: str
    payload: dict
    #: 读没读。原来是一个 `read_at` 时间戳，而收件箱只问过「读了没」。
    read: bool
    resolved_at: datetime | None = None
    feedback: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: Notification) -> "NotificationOut":
        return cls(
            id=row.id,
            project_id=row.project_id,
            topic_id=row.topic_id,
            level=NotificationLevel(row.level) if row.level else None,
            kind=NotificationType(row.type),
            target_handle=row.recipient_handle,
            title=row.title or "",
            body=row.body or "",
            payload=row.metadata_payload or {},
            read=row.read,
            resolved_at=row.resolved_at,
            feedback=row.feedback,
            created_at=row.created_at,
        )
