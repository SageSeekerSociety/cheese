"""投递账本的那一行 —— 一条投递在发出之前先是一行记录（结论 58）。

寻址是纯函数，投递不是：它有状态，而且必须能被从中间打断。这张表存的就是那点状
态 —— 谁该收到、凭哪个去重键、写下来的时刻、发出去的时刻。

**为什么是一张表而不是 Redis 的一个键。** 以前这件事由 `notification/dedup.py` 的
一个带 TTL 的 Redis 键兼着：它认的是内容（`事件类型:收件人集合:payload 的 sha1`），
Redis 一重启就没了，Redis 连不上时直接放行。所以「已经算过要发给他」这件事在进程
重启后无从查起，没送到的投递也无从补发 —— 没有任何一行记着它本该发出去。

**行随事件一起提交。** `record()` 写的是调用方的 session，所以这一行和引发它的那条
事件（房间里那个 block）在同一个事务里落库：事件回滚，投递记录跟着回滚，不会留下
一条指向不存在的事件的通知；事件提交了，这一行就在，哪怕这个进程下一秒就没了。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class Delivery(UuidPk, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        # 补发扫的就是这一条：还没发出去的，按写入顺序。**部分索引**：这张表每发一
        # 条通知就插一行，而其中绝大多数下一刻就 `sent_at` 非空了 —— 把它们也索引
        # 进来，等于把写放大挂在通知的热路径上，换来的是一份没人查的条目。
        Index(
            "idx_deliveries_unsent",
            "recorded_at",
            postgresql_where=text("sent_at IS NULL"),
        ),
    )

    #: 引发这条投递的那条事件。去重键跟着它走，所以同一条事件重算多少遍都指回这
    #: 里。**没有索引**：没有一处按它查，留着这一列是为了事后取证 —— 「这条 block
    #: 当时通知了谁」在生产上是手查，不是热路径。
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    #: 收件人的 handle —— 寻址给出的就是它。
    recipient_handle: Mapped[str] = mapped_column(String(64))
    #: 他的站内信收件箱。**在事件发生的时刻解析好存下来**：补发是在事后发生的，那时
    #: 再按 handle 查一遍，查到的是那时候的名册，而这条事件点的是当时那个人。
    receiver_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    external_channels: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="false"
    )
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # Agent delivery attempts are leased independently of model-work completion.
    state: Mapped[str] = mapped_column(
        String(24), default="pending", server_default="pending"
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 去重键，全表唯一。跟着**事件**走（事件 id + 收件人），不跟着这一次发送尝试
    #: 走 —— 重试、补发、同一条事件被算两遍，算出来都是同一个键。
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: 事件发生的时刻（不是写这一行的时刻，虽然通常只差毫秒）。这是「这一笔本该是
    #: 什么时候的事」，一条没送到的投递事后要查的就是它。收件箱那一行的时间戳不取
    #: 它 —— 那一列是「人什么时候能看见」，见 `notification/handlers.py`。
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: 确认已经送出去的回写。NULL = 还没送到，补发会再来一次。
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 发过几次没成。到 `ledger.MAX_ATTEMPTS` 就不再补发 —— 那一行留在这里是死信。
    #: 没有这个上限，一行始终发不出去的投递就是一台每分钟跑一次的定时机器。
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class TimedDelivery(UuidPk, Base):
    """一个参与者设下的闹钟：到 `due_at` 把 `content` 递给 `recipient_handle`。

    **和账本那张表分开，因为它们记的不是同一件事。** `deliveries` 记的是一条已经发
    生的事件送到没送到；这一行记的是一条**还没有发生**的事件，以及它该在什么时候发
    生。把它塞进账本，等于让补发那条扫描去处理一批「故意还没送」的行 —— 而那条扫描
    的全部含义就是「没送到的就再送一次」。

    递出去之后这一行不删：它是「这个闹钟响过了」的记录，也是重启之后不再响第二遍的
    依据。
    """

    __tablename__ = "timed_deliveries"
    __table_args__ = (
        # 每一拍扫的就是这一条：到点了还没递的。**部分索引**：递过的行只会越积越多，
        # 而没有一处按它们查。
        Index(
            "idx_timed_deliveries_due",
            "due_at",
            postgresql_where=text("delivered_at IS NULL"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    #: 请求它的那个地点，也是到点之后这条投递落回去的地方。
    topic_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    #: 收件人 —— 就是请求者自己（结论 17），所以这条原语没有第二条收件人规则。
    recipient_handle: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, unique=True)
    agent_instance_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    receiver_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: 递出去的时刻。NULL = 还没到点，或者到点了还没递成。
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChannelDelivery(UuidPk, Base):
    """One independently retried email or push, committed with its source event."""

    __tablename__ = "delivery_channels"
    __table_args__ = (
        UniqueConstraint("delivery_key", "channel", name="uq_delivery_channel"),
    )
    delivery_key: Mapped[str] = mapped_column(String(160))
    channel: Mapped[str] = mapped_column(String(16))
    receiver_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
