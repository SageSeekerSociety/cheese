"""平台侧的执行记录：发出去过什么，哪些拿到了结果，哪些结果未知（结论 57，6.5）。

一次工具调用今天只有两种落点：返回一个结果，或者抛一个异常。中间那一种没有名字
—— **发出去了，而结果永远不会回来了** —— 于是崩溃恢复之后分不开「确定没做」和
「可能已经做过」，重派就只能是重发。

这条记录只在平台这一侧才有意义。执行器自己也记（``remote_execution/runtime.py``
的 ``invoke``：一行 ``requests``，``result`` 为空就是「已受理、结果未知，不要换个
id 重放」），而它记在那台机器上 —— 机器突然损坏的那一刻，那一行连同它要回答的问题
一起没了。6.5 把这两种情形分开写保证正是为此：正常回收走三张收据，突然损坏那一刻
能兑现的只有平台侧已经有的东西，这份记录是其中一份。

``key`` 就是执行器那一行的 id，同一个名字两边各记一次；两条记录说的是同一件事，只
是其中一条活得比那台机器久。所以只有**带这个 id 的调用**在这里留一行：带 id 是执行
器自己的说法，说这次调用是一个「重放要按 key 判重」的副作用；不带 id 的（``ping``、
``context``、``prepare``）按它自己的协议问两遍和问一遍一样，为它们记一行只会让每一
次超时的探活都变成一件要人确认的事。

三种结果，其中一种写不出来
--------------------------

``done`` / ``failed`` 是发出去的那个进程写回来的。``unknown`` 不是：它要写的时候，
该写它的那个进程已经不在了。所以在库里它是「没有人写回来」—— ``outcome IS NULL``,
``unsettled()`` 返回的就是这些行，读出来是 ``Outcome.unknown``。

``unknown`` 这个值仍然会被写进去，但写它的是**重派路径**，含义不同：平台不再等了，
这个问题已经交给人（结论 57「结果未知的操作交人确认」，5.2「通知他一次」）。写下它
之前和之后，这一行读出来都是 ``unknown``——变的不是这次调用的结果，是平台还问不问。
不写的话，一次未知会让这个房间此后每一次扫底都重新问一遍同一个人同一件事。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


def _now() -> datetime:
    return datetime.now(UTC)


class Outcome(StrEnum):
    """这次调用最后怎么了。"""

    #: 执行器答了 —— 不管答的是结果还是它自己的报错，它收下了这次调用。
    done = "done"
    #: 确定没发出去：链路不在，或者机器自己回话说它没能转交。重派它是安全的。
    failed = "failed"
    #: 发出去了，结果不会回来了。不自动重发，交人确认。
    unknown = "unknown"


class DispatchRow(UuidPk, Base):
    __tablename__ = "dispatches"
    __table_args__ = (
        # 重派路径唯一的问题：这个地点还有没有结果没回来的调用？部分索引，所以它
        # 的大小是「此刻悬着的」，而不是「平台有史以来派出去过的」。
        Index(
            "ix_dispatches_unsettled",
            "place_id",
            postgresql_where=("outcome IS NULL"),
        ),
    )

    #: 执行器那一行的 id（``invoke`` 的 ``params["id"]``）——两边同一个名字。
    key: Mapped[str] = mapped_column(String(128), index=True)
    #: 这条活在哪个房间里。重派是按房间做的，所以记录也按房间问。
    place_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    #: 派出去的是哪一种调用。人要确认「可能已经做过」的是什么，这是他能读到的唯一
    #: 一句；参数不进来，它们是这条活的内容，不是这份记录的。
    method: Mapped[str] = mapped_column(String(32))
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    #: 空 = 没有人写回来 = ``unknown``。见模块开头。
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


@dataclass(frozen=True, slots=True)
class Dispatch:
    """一行记录，读出来的样子。``outcome`` 三态俱全，没有第四种。"""

    id: uuid.UUID
    key: str
    method: str
    dispatched_at: datetime
    outcome: Outcome


def _read(row: DispatchRow) -> Dispatch:
    return Dispatch(
        id=row.id,
        key=row.key,
        method=row.method,
        dispatched_at=row.dispatched_at,
        outcome=Outcome(row.outcome) if row.outcome else Outcome.unknown,
    )


def record(
    session: AsyncSession, *, place_id: uuid.UUID, key: str, method: str
) -> uuid.UUID:
    """在**发出之前**记下这次派发，返回这一行的 id。

    行加在调用方自己的 session 上，由调用方在发出之前提交 —— 这份记录存在的全部理由
    是它要比发出它的那个进程活得久，而一条还没提交的记录和没有记录是同一回事。

    id 在这里就定下来，不等 flush：调用方拿着它去发请求，而这一行要在那之前提交，
    两件事之间没有可以插进一次 flush 的位置。
    """
    row = DispatchRow(id=uuid.uuid4(), place_id=place_id, key=key, method=method)
    session.add(row)
    return row.id


async def settle(
    session: AsyncSession, dispatch_id: uuid.UUID, outcome: Outcome
) -> None:
    """把结果写回这一行。``unknown`` 的含义见模块开头：平台不再问了。"""
    row = await session.get(DispatchRow, dispatch_id)
    if row is None:
        return
    row.outcome = outcome.value
    row.settled_at = _now()


async def unsettled(session: AsyncSession, place_id: uuid.UUID) -> list[Dispatch]:
    """这个地点里结果还没回来的派发，最早的在前 —— 重派路径读的就是它。"""
    rows = (
        (
            await session.execute(
                select(DispatchRow)
                .where(
                    DispatchRow.place_id == place_id,
                    DispatchRow.outcome.is_(None),
                )
                .order_by(DispatchRow.dispatched_at)
            )
        )
        .scalars()
        .all()
    )
    return [_read(row) for row in rows]


__all__ = [
    "Dispatch",
    "DispatchRow",
    "Outcome",
    "record",
    "settle",
    "unsettled",
]
