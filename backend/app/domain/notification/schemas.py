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
    NotificationType.MENTION,
)


class NotificationCreate(BaseModel):
    level: NotificationLevel
    kind: NotificationType
    title: str = Field(min_length=1, max_length=300)
    body: str = ""
    target_handle: str | None = Field(default=None, max_length=64)
    #: 这条通知关于哪条对话，不是哪个频道：一个频道自己那条线、它的一条任务、或一
    #: 条支线。字段和 URL 里仍旧叫 topic —— 一个任务和一条支线也走
    #: `/topics/{它的 id}/…`（#2422 起代码里的 topic 名不改），校验完照原样存进
    #: `conversation_id`，不折成它所在的频道。
    topic_id: uuid.UUID | None = None
    payload: dict = Field(default_factory=dict)


class ResolveIn(BaseModel):
    # NB: no ``decided_by`` — the decider is the verified caller (ActorResolver),
    # never a body field. A client still sending it is silently ignored.
    chosen: str


class NotificationOut(BaseModel):
    #: bigint，不再是 uuid：两张表并成一张之后主键跟的是 `notification_seq`。
    id: int
    project_id: uuid.UUID | None
    #: 这条关于的那条对话（`conversation_id`）。历史字段名，见 `NotificationCreate`。
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
    created_at: datetime

    @classmethod
    def from_row(cls, row: Notification) -> "NotificationOut":
        return cls(
            id=row.id,
            project_id=row.project_id,
            topic_id=row.conversation_id,
            level=NotificationLevel(row.level) if row.level else None,
            kind=NotificationType(row.type),
            target_handle=row.recipient_handle,
            title=row.title or "",
            body=row.body or "",
            payload=row.metadata_payload or {},
            read=row.read,
            resolved_at=row.resolved_at,
            created_at=row.created_at,
        )
