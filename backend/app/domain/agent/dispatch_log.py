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

树里形状最近的是 ``idempotency/store.py`` 加 ``idempotency/models.py``，不是投递那边
的去重：同样是「副作用之前先在调用方自己的 session 上写一行、按 key 判重、结果回
填」，连 ``result`` 为 NULL 的含义都撞上。这份记录仍然不能落在它上面，理由是
``idempotency_keys`` 的不变式 ——「行在 ⇒ 效果发生过」—— 在远端正好不成立：那张表的
行和它守的副作用同一个事务提交，而这里的一行只说「发出去过」，发出去过和发生过之间
隔着一台可能已经没了的机器，这份记录存在的全部理由就是这句话。两张表合成一张，就是
把那条不变式弱化成「行在 ⇒ 效果大概发生过」，而今天靠它判重的那几处副作用（消息、
拆卡）会跟着一起弱。

三种结果，其中一种写不出来
--------------------------

``done`` / ``failed`` 是发出去的那个进程写回来的。``unknown`` 不是：它要写的时候，
该写它的那个进程已经不在了。所以在库里它是「没有人写回来」—— ``outcome IS NULL``,
``unsettled()`` 返回的就是这些行，读出来是 ``Outcome.unknown``。

``unknown`` 这个值仍然会被写进去，但写它的是**重派路径**，含义不同：平台不再等了，
这个问题已经交给人（结论 57「结果未知的操作交人确认」，5.2「通知他一次」）。写下它
之前和之后，这一行读出来都是 ``unknown``——变的不是这次调用的结果，是平台还问不问。
不写的话，一次未知会让这个房间此后每一次扫底都重新问一遍同一个人同一件事。

「没有人写回来」在什么时候等于「不会有人写回来」
------------------------------------------------

这两件事在库里长得一模一样，而分开它们的**不是时间**。写这一行的是设备连接属主进
程，它按设计跨业务后端的发布活着；一次平常的发布里，属主手上那些还在正常跑着的调用
最后照样会把结果写回来，只是此刻库里它们和死掉的那些一样空。

分开它们的是**谁在读**。这张表只有一个读的地方：孤儿轮次扫底里的重派路径
（``runtime.py`` 的 ``_settle_restart_orphans``），而送到它面前的话题都是屏幕已经没
了、或者那条消息根本没送到屏幕的话题 —— 派出这些调用的那几轮，扫底在读之前就已经关
掉了（``_close_turns``）。在这样的话题里，一次还悬着的调用无论那台机器后来怎么样，
结果都再也到不了 agent 面前：要它的那一轮没了。所以对这个读的人来说，「还没人写回
来」就是「不会有人写回来」，``unsettled()`` 不必、也没法再问第二个问题。

这里曾经拿墙上时钟当那第二个问题 ——「比一次调用能在飞的时间（``call_executor`` 的
时限，660 秒）还老才算数」。它在真实时序下整档落空：崩溃和属主重启通常发生在派发之
后几秒到几分钟内，而启动扫底（``main.py`` 的 ``resume_orphans``）紧跟着就跑，每一行
都还太年轻，于是重派照样原样重发 —— 正是这份记录要拦的那一件事。更糟的是这一轮被那
次扫底关掉之后就不再是孤儿，后面任何一轮的 ``since`` 都晚于这行的 ``dispatched_at``，
这行于是永远空着、永远不通知、永远不结清。

判错的代价现在倒向另一头：属主其实还活着、那次调用最后成功了，人却被问了一句本不必
问的话（那一行也就此结清，迟到的 ``done`` 写不进去，见 ``settle``）。要撞上它，得是
这个房间的屏幕已经没了、而同一个房间里另有一轮正在正常调工具 —— 房间里的轮次是排队
跑的，所以这本就少见；就算撞上，代价是有人被问一句，另一头的代价是一次可能已经落地
的写被再做一遍。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import UuidPk


def _now() -> datetime:
    return datetime.now(UTC)


class Outcome(StrEnum):
    """这次调用最后怎么了。"""

    #: 执行器把结果送回来了。
    done = "done"
    #: 确定这次调用没有被执行，重派它是安全的。三档，见 ``api/routes/execution.py``
    #: 的分类：链路不在，帧一个字节都没写出去（``DeviceUnreachable``）；链路在而这台
    #: 机器的连接器还在自更新（``DeviceNotReady``，同样挡在发出之前）；帧到了，而执行
    #: 器回话说这个 id 上已经有一次别的输入 —— 那一句挡在 ``invoke`` 碰这次调用之前。
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
    #: 派出去的是哪一个工具（``invoke`` 载荷里的 ``params["tool"]``）。人要确认
    #: 「可能已经做过」的是什么，这是他能读到的唯一一句 —— 记调用方法名没有用，带 id
    #: 的调用只有 ``invoke`` 一种，那一栏于是每次都长成同一个常量。参数不进来：它们
    #: 是这条活的内容，不是这份记录的。长度按 ``mcp__<server>__<tool>`` 给，那种名字
    #: 轻易超过几十个字符。
    tool: Mapped[str] = mapped_column(String(128))
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
    tool: str
    dispatched_at: datetime
    outcome: Outcome


def _read(row: DispatchRow) -> Dispatch:
    return Dispatch(
        id=row.id,
        key=row.key,
        tool=row.tool,
        dispatched_at=row.dispatched_at,
        outcome=Outcome(row.outcome) if row.outcome else Outcome.unknown,
    )


def record(
    session: AsyncSession, *, place_id: uuid.UUID, key: str, tool: str
) -> uuid.UUID:
    """在**发出之前**记下这次派发，返回这一行的 id。

    行加在调用方自己的 session 上，由调用方在发出之前提交 —— 这份记录存在的全部理由
    是它要比发出它的那个进程活得久，而一条还没提交的记录和没有记录是同一回事。

    id 在这里就定下来，不等 flush：调用方拿着它去发请求，而这一行要在那之前提交，
    两件事之间没有可以插进一次 flush 的位置。
    """
    row = DispatchRow(id=uuid.uuid4(), place_id=place_id, key=key, tool=tool)
    session.add(row)
    return row.id


async def settle(
    session: AsyncSession, dispatch_id: uuid.UUID, outcome: Outcome
) -> None:
    """把结果写回这一行，**第一笔算数**。``unknown`` 的含义见模块开头：平台不再问了。

    结清是一次性的：这一行一旦读出来不是「没人写回来」，它就已经参与过一次判断 ——
    最要紧的那种是 ``unknown``，写下它的同时房间里已经有一条通知，有人正照着它去看
    那次改动落地没有。这时候一个迟到的 ``done`` 把它抹平，留下的是一行说「都办妥了」
    的记录和一个仍然被要求去确认的人，而没有任何地方还留着他为什么被叫来。

    所以判重写在 ``WHERE`` 里，一条 UPDATE。「先读一行、看一眼、再写回去」少一道门：
    读和写之间隔着一次往返，而这中间正是扫底判它未知、房间里已经有人照着通知在查的
    那一刻；读到的 ``None`` 于是过期，写下去的 ``done`` 盖掉的是一行已经交到人手上的
    记录。窗口不宽 —— 一次调用 659 秒才回来，这次结清还在连接池上排着队（上面那段注
    释讲的就是这个池子）—— 但它不需要宽，这一行只有一次机会。顺带，每一次工具调用都
    少一次 SELECT。
    """
    await session.execute(
        update(DispatchRow)
        .where(DispatchRow.id == dispatch_id, DispatchRow.outcome.is_(None))
        .values(outcome=outcome.value, settled_at=_now())
    )


async def unsettled(
    session: AsyncSession, place_id: uuid.UUID, *, since: datetime
) -> list[Dispatch]:
    """这个地点里 ``since`` 之后派出去、而没有人写回来的那些，最早的在前。

    在这唯一一个调用者那里，「没有人写回来」就是「不会有人写回来」——为什么，见模块
    开头的第二节。这里不问年龄：问了，真实时序下这个函数整档返回空。

    ``since``：重派路径收拾的是某几轮被打断的对话，而这张表按房间存。一次超时留下的
    空行是会一直空着的 —— 那一轮照常跑完，没有任何路径会再碰它（路由不替它猜，见
    ``api/routes/execution.py`` 的超时那一档）。没有这道门，几天后这个房间的下一次扫
    底照样读得到它：一次本该无条件发生的原样重发被它掐掉，房间里换成一条指着几天前
    那次早就结束的调用的通知 —— 用户这一次的消息真丢了，而提示说的是别的事。所以调
    用方给出这次要收拾的那几轮里最早的开始时刻：这一行是在**发出之前**写的，属于那
    几轮的调用不可能早于它们开始。
    """
    rows = (
        (
            await session.execute(
                select(DispatchRow)
                .where(
                    DispatchRow.place_id == place_id,
                    DispatchRow.outcome.is_(None),
                    DispatchRow.dispatched_at >= since,
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
