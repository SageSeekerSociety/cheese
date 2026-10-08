"""一张混排列表里一行的位置：一个字符串，按字符串从小到大排就是显示的顺序。

资料库页把两处的东西摆进同一张表：记录表里的文件（和它们算出来的文件夹）、文档表
里的文档。两处各自翻页，前端把两串结果像合并两条有序队列那样并起来——这要求两处
用同一个顺序，而且前端能比较任意两行谁在前。位置就是那个顺序本身：

    <组><时间倒过来的 17 位微秒数><同一时刻里的先后>

组小的在前（文件夹是 0，文件和文档是 1）；同组里新的在前；同一微秒再按最后一段
从小到大。最后一段是行的 id（uuid 的十六进制，字符串顺序和 Postgres 的 uuid 顺序
一致）或者文件夹名（查询里用 `COLLATE "C"`，按字节比）。

它也是翻页的游标：下一页从这一行之后接着取。
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.errors import UnprocessableEntityError

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SPAN = 10**17
_WIDTH = 17


@dataclass(frozen=True)
class Rank:
    group: int
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
        raise UnprocessableEntityError("malformed list cursor") from exc
    at = _EPOCH + timedelta(microseconds=_SPAN - inverse)
    return Rank(group, at, raw[1 + _WIDTH :])
