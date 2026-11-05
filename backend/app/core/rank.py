"""一张混排列表里一行的位置：一个字符串，按字符串从小到大排就是显示的顺序。

资料库页把两处的东西摆进同一张表：记录表里的文件（和它们算出来的文件夹）、文档表
里的文档。两处各自翻页，前端把两串结果像合并两条有序队列那样并起来——这要求两处
用同一个顺序，而且前端能比较任意两行谁在前。位置就是那个顺序本身：

    <组><时间倒过来的 17 位微秒数><同一时刻里的先后>

组小的在前（文件夹是 0，文件和文档是 1）；同组里新的在前；同一微秒再按最后一段
从小到大。最后一段是行的 id（uuid 的十六进制，字符串顺序和 Postgres 的 uuid 顺序
一致）或者文件夹名（查询里用 `COLLATE "C"`，按字节比）。

它也是翻页的游标：下一页从这一行之后接着取。

资料库页选「升 / 降」时不是这个顺序：那时文件夹不再单独一拨，和文件、文档排进同
一条时间线，只比时间，同一时刻才比名字（`timeline`）。两种位置都是可比较的字符串，
但只有同一种能并排用——前端拿到的 `rank` 始终是当前那一种。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.errors import UnprocessableEntityError
from app.core.sentences import say

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SPAN = 10**17
_WIDTH = 17

#: 资料库页那个排序框的三档。`off` 是默认：文件夹一拨、其余一拨，各自新的在前。
SORT_OFF = "off"
SORT_ASC = "asc"
SORT_DESC = "desc"
SORTS = (SORT_OFF, SORT_ASC, SORT_DESC)


@dataclass(frozen=True)
class Rank:
    group: int
    at: datetime
    tie: str


@dataclass(frozen=True)
class Spot:
    """一条时间线上的一处：什么时候，同一时刻再按什么比。"""

    at: datetime
    tie: str


def encode(group: int, at: datetime, tie: str) -> str:
    micros = (at - _EPOCH) // timedelta(microseconds=1)
    return f"{group}{_SPAN - micros:0{_WIDTH}d}{tie}"


def decode(raw: str) -> Rank:
    try:
        group = int(raw[0])
        inverse = int(raw[1 : 1 + _WIDTH])
    except (IndexError, ValueError) as exc:
        raise UnprocessableEntityError(say("listCursorUnreadable")) from exc
    at = _EPOCH + timedelta(microseconds=_SPAN - inverse)
    return Rank(group, at, raw[1 + _WIDTH :])


def title_tie(title: str | None, document_id: uuid.UUID | str) -> str:
    """一份文档在时间线上的名字那一段：标题，然后是一个文件名字里不可能出现的
    分隔符（`chr(1)`），然后是编号。

    标题可以不唯一，同一刻同名的两份文档还得有个先后，不然翻页会漏掉其中一份。资料库
    里的文件用整个路径当 `tie`，那份路径里只有 `/` 这一种结构，碰不到 `chr(1)`。"""
    return f"{title or ''}{chr(1)}{document_id}"


def timeline(at: datetime, tie: str, *, descending: bool) -> str:
    """一条时间线上的位置：`<时间><名字>`，按字符串从小到大就是显示的顺序。

    升序时间从小到大；降序把时间倒过来（`_SPAN - micros`），名字那一段两个方向都是
    从小到大——降序只翻时间，不翻名字。"""
    micros = (at - _EPOCH) // timedelta(microseconds=1)
    if descending:
        return f"{_SPAN - micros:0{_WIDTH}d}{tie}"
    return f"{micros:0{_WIDTH}d}{tie}"


def read_timeline(raw: str, *, descending: bool) -> Spot:
    """`timeline` 写出来的那个游标指的是哪一刻、哪个名字。

    方向得由调用方给：降序存的是倒过来的数，只有知道方向才换得回来。"""
    try:
        first = int(raw[:_WIDTH])
    except (IndexError, ValueError) as exc:
        raise UnprocessableEntityError(say("listCursorUnreadable")) from exc
    micros = _SPAN - first if descending else first
    return Spot(_EPOCH + timedelta(microseconds=micros), raw[_WIDTH:])
