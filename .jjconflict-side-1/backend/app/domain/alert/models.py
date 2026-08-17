"""Alert model — spec §8.5, §8.6.

Called `notification` until #370, in a table called `notifications` next to 知是's
`notification`. One `s` apart, and the two are not the same kind of thing: 知是's
is a person telling a person something (a reply, a reaction, an @ — a social
feed), while this one is the platform reporting on itself — a machine went
offline, a turn failed, a topic wants a human to look. This is the one rename in
#370 where the 2.0 name was the wrong one, so 2.0 moved.

Alerts are the real-time surface of "what changed" / "what needs you".
Ground truth stays in docs; an alert points at it. Graded by how much it
interrupts (spec §3, §8.6):
  silent → 默默记下来, 不打扰
  light  → 对话里轻提一句, 带可操作按钮
  strong → 强提醒, @ 具体的人

Two typical kinds (spec §8.5): change_alert (干完活后的变更提醒) and
decision_request (需要人拍板, 带选项).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps, UuidPk


class AlertLevel(enum.StrEnum):
    silent = "silent"
    light = "light"
    strong = "strong"


class AlertKind(enum.StrEnum):
    change_alert = "change_alert"  # 变更提醒
    decision_request = "decision_request"  # 决策请求 (带选项)
    accept_request = "accept_request"  # 验收卡 (点名)
    heartbeat = "heartbeat"  # 巡检催办
    mention = "mention"  # @点名 (强提醒)


class Alert(UuidPk, Timestamps, Base):
    __tablename__ = "alerts"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    level: Mapped[AlertLevel] = mapped_column(
        Enum(AlertLevel, native_enum=False, length=16)
    )
    kind: Mapped[AlertKind] = mapped_column(
        Enum(AlertKind, native_enum=False, length=16)
    )
    # Who it's addressed to (a user handle); NULL = broadcast/board only.
    target_handle: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    # Extra structured payload: decision options, diffstat, accept card id, etc.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A decision request stays in the inbox until it's resolved (拍板), not just
    # read. The chosen option is stored in payload["resolved_choice"].
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 👍/👎 feedback on proactive messages (spec G3): null / "up" / "down".
    feedback: Mapped[str | None] = mapped_column(String(8), nullable=True)
