"""看那两拍有没有该发没发，把结果记下来。

影子期（2026-10-13 起第一段）已经跑过：只判、只记，不真起轮，把误报核在真实数据
上。它还在，由配置的 `wake_watch_catchup` 决定要不要打开补跳；关掉就退回只判、只
记，判据一个字不变。

落点是运行记录，不是对话：观察者说的是平台的观察，不该把被观察的座位叫起来，也不
该混进人读的聊天里。一次停摆只记一条 —— 记录 id 由「哪一拍、停在哪一刻」算出来
（`beats.stall_record_id`），所以五分钟一轮的巡检不会把同一次停摆记成十几条。

补跳是另一件事，走的是另一条路（`catchup.summon`，只管点名，不管起轮），由调用方
传进来。判完先点名、再记：这样记录里能带上「这一遍点了谁」。反过来不成立 —— 已经
记过的那一次不再改写记录，所以「第一遍点了谁」不会因为后来补上了而更新，那一次的
补跳本身在那条线上看得到。补跳出错只吞掉补跳（`_call_summon`）：判和记是这一层的，
不该因为别人那一半坏了而整遍没有。

判据只从库里读两样：那一拍最近一次真的来的时刻，和现在。中间不碰「有没有开始跑」
「有没有人在处理」这类别的信号 —— 那些正是断掉的那一环说不出来的东西。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError

from app.core.db import SessionFactory
from app.core.sentences import say
from app.domain.agent.run_records import publish
from app.domain.block.models import Block, BlockKind
from app.domain.notice_meta import (
    EVENT_WAKE_MISSED,
    SEVERITY_WARN,
    WHO_PLATFORM,
    notice,
)
from app.domain.run_record.models import RunRecord
from app.domain.run_record.service import as_payload, record
from app.domain.wake_watch.beats import (
    WATCHED,
    Beat,
    Ticking,
    read_stamp,
    stall,
    stall_record_id,
    stamp_text,
)

logger = logging.getLogger(__name__)

#: 判到停摆之后怎么补跳。返回补给了谁；这一遍不补就返回 `None`。
#:
#: 判和补分成两层，是因为判据要能单独重放（`beats.py` 只依赖标准库就是这个缘故），
#: 而补跳要碰库和收件人。关掉补跳就是传 `None`，判和记一个字都不变。
type Summon = Callable[[Beat, Ticking, timedelta, datetime], Awaitable[str | None]]


async def watch(
    sessions: SessionFactory,
    *,
    now: datetime | None = None,
    watched: Iterable[Beat] = WATCHED,
    summon: Summon | None = None,
) -> dict[str, int]:
    """看一遍这几拍，给新判出来的停摆各记一条。返回这一遍记了几条新的。

    返回映射而不是数字：巡检框架只在里面出现真值时才说一句，五分钟一轮的巡检
    不该每轮都报一次「一切正常」。
    """
    at = now or datetime.now(UTC)
    noted = 0
    for beat in watched:
        ticking = await _last_beat(sessions, beat)
        if ticking is None:
            continue
        since = stall(beat, at, ticking.at)
        if since is None:
            continue
        # 先点名、再记：记录里要带上这一遍点了谁。已经记过的那一次不再改写记录，
        # 所以后面几遍补上了也不回头改它 —— 补跳本身在 #1343 那条线上看得到。
        #
        # 补跳抛错不带上判据：判是这一层的事，记是这一层的事，补是别人的事。补的那
        # 一半出错（库抖了、座位解析不出来），这一拍该记的那一条照样记，后面几拍也
        # 照样判 —— 否则一个补跳的毛病会让整个观察者哑掉，而它哑掉本身没人看得见。
        seat = await _call_summon(summon, beat, ticking, since, at)
        if await _note(sessions, beat, ticking, at, since, seat):
            noted += 1
    return {"stalled": noted} if noted else {}


async def _call_summon(
    summon: Summon | None,
    beat: Beat,
    ticking: Ticking,
    since: timedelta,
    at: datetime,
) -> str | None:
    """点名补跳，失败只记日志、返回「这一遍没点名」。"""
    if summon is None:
        return None
    try:
        return await summon(beat, ticking, since, at)
    except Exception:  # noqa: BLE001 — 补跳的错不该带走这一遍的判据
        logger.exception("wake watch: %s 补跳这一步出错，这一遍只判不补", beat.name)
        return None


async def _last_beat(sessions: SessionFactory, beat: Beat) -> Ticking | None:
    """这一拍最近一次真的来过的那一次。

    按库里存的顺序（`seq`，也就是房间里的顺序）倒着找，不按时间排：心跳那条消息
    的时间冻在它第一次发的那天，按时间排会把它排到几天前，或者干脆排不出最近的
    那一条。
    """
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Block.content, Block.created_at, Block.seq)
                .where(
                    Block.conversation_id == beat.conversation_id,
                    Block.kind == BlockKind.message,
                    _might_be(beat),
                )
                .order_by(Block.seq.desc())
                .limit(1)
            )
        ).all()
    if not rows:
        return None
    content, block_at, seq = rows[0]
    at = read_stamp(beat, content, block_at)
    if at is None:
        # 认出来了这一拍，却读不出它说的时间。判停摆就此失去依据，这一遍跳过它 ——
        # 但要说一声，否则「观察者静悄悄」和「一切正常」看起来一模一样。
        logger.warning(
            "wake watch: %s 的消息 %s 认不出时间戳：%r",
            beat.name,
            seq,
            content[:120],
        )
        return None
    return Ticking(at=at, block_at=block_at)


def _might_be(beat: Beat) -> ColumnElement[bool]:
    """库这一侧先粗筛可能属于这一拍的消息，细的判定在 `beats.read_stamp`。

    和那边认的是同一件事：`marker_first` 的必须开头就是那串字，其余的出现在正文
    里就算。用 `strpos` 而不是 `LIKE '%…%'`，绕开 `%` 和 `_` 在各驱动之间的转义
    分歧。这条读法没有专用索引，靠的是 `ix_blocks_conversation_kind_seq`：一个
    房间的消息只占全部块的一小部分（#1343 实测约 13%）。
    """
    if beat.marker_first:
        return Block.content.like(f"{_escaped(beat.marker)}%", escape="\\")
    return func.strpos(Block.content, beat.marker) > 0


def _escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _note(
    sessions: SessionFactory,
    beat: Beat,
    ticking: Ticking,
    now: datetime,
    since: timedelta,
    seat: str | None = None,
) -> bool:
    """记下这一次停摆，返回是不是新记的一条。

    id 由停摆本身算出来，所以先看它在不在：在就是这一次停摆已经记过。查完再写是
    为了不拿异常当常规路径；真撞上并发写的时候，`IntegrityError` 也按「已经记过」
    处理。
    """
    record_id = stall_record_id(beat, ticking.at)
    minutes = int(since.total_seconds() // 60)
    content = say("wakeWatchStalled", beat=say(beat.label), minutes=minutes)
    meta = notice(
        EVENT_WAKE_MISSED,
        severity=SEVERITY_WARN,
        who=WHO_PLATFORM,
        detail=evidence(beat, ticking, now, minutes),
        detail_label=say("labelWakeWatchDetail"),
    )
    # 记录自己那一行也要跟着读者的语言走。`notice()` 只给 detail 那些字段的键，
    # 正文的键在这里补上。
    meta["i18n"] = {"content": content.descriptor(), **meta.get("i18n", {})}
    meta |= {
        "beat": beat.name,
        "stalled_minutes": minutes,
        "last_beat_at": stamp_text(ticking.at),
    }
    if seat is not None:
        # 这一遍把谁叫起来了。没补跳（关着，或者座位忙着）就不写这个键 —— 缺席本身
        # 不说明是哪一种，看日志。
        meta["catch_up_seat"] = seat
    try:
        async with sessions() as session:
            if await session.get(RunRecord, record_id) is not None:
                return False
            kept = await record(
                session,
                conversation_id=beat.conversation_id,
                content=str(content),
                meta=meta,
                record_id=record_id,
            )
            if kept is None:
                return False
            payload = as_payload(kept)
            await session.commit()
    except IntegrityError:
        return False
    logger.warning(
        "wake watch: %s 静了 %s 分钟%s",
        beat.name,
        minutes,
        f"，已点名 {seat} 补跳" if seat else "，这一遍没有点名",
    )
    await publish(payload)
    return True


def evidence(beat: Beat, ticking: Ticking, now: datetime, minutes: int) -> str:
    """判停摆的依据：去看这条记录的人要能自己把这一次核对一遍。

    记录里那份依据和补跳那一轮带的那份是同一处写出来的 —— 座位收到的时候看到的
    和判的人看到的是同一段话，不是两份各写各的。
    """
    period = int(beat.period.total_seconds() // 60)
    grace = int(beat.grace.total_seconds() // 60)
    return "\n".join(
        [
            f"节拍：{beat.name}（{say(beat.label)}）",
            f"最后一拍说的时间：{stamp_text(ticking.at)}",
            f"它落在 {stamp_text(ticking.block_at)} 的那条消息上",
            f"判据：周期 {period} 分 + 宽限 {grace} 分",
            f"这一遍看的时刻：{stamp_text(now)}",
            f"到这里已经静了 {minutes} 分钟",
        ]
    )
