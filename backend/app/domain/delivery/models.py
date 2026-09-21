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

from sqlalchemy import BigInteger, DateTime, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


class Delivery(UuidPk, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        # 补发扫的就是这一条：还没发出去的，按写入顺序。
        Index("idx_deliveries_unsent", "sent_at", "recorded_at"),
    )

    #: 引发这条投递的那条事件。去重键跟着它走，所以同一条事件重算多少遍都指回这里。
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    #: 收件人的 handle —— 寻址给出的就是它。
    recipient_handle: Mapped[str] = mapped_column(String(64))
    #: 他的站内信收件箱。**在事件发生的时刻解析好存下来**：补发是在事后发生的，那时
    #: 再按 handle 查一遍，查到的是那时候的名册，而这条事件点的是当时那个人。
    receiver_id: Mapped[int] = mapped_column(BigInteger)
    #: 去重键，全表唯一。跟着**事件**走（事件 id + 收件人），不跟着这一次发送尝试
    #: 走 —— 重试、补发、同一条事件被算两遍，算出来都是同一个键。
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    #: 事件发生的时刻（不是写这一行的时刻，虽然通常只差毫秒）。补发出去的那条通知
    #: 按它落时间戳，收件人看到的才是事情发生的时间，而不是我们恢复的时间。
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: 确认已经送出去的回写。NULL = 还没送到，补发会再来一次。
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
