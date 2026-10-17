"""看板上一格的线上形状：哪一列，卡面写什么。

`column` 和 `phrase` 都是**码** —— 列回答「该谁动」，短语回答「在等什么」，画成
哪个词是读者的屏幕按语言决定的事。这一格谁来算、怎么算，在
`room_task/presentation.py`（那条逻辑住在第 4 层，因为它读房间和活的现有事实）。

## 为什么住在第 1 层

它是**两个领域的响应形状**共用的那一段：一条活（`room_task.schemas.TaskOut`）和
一个房间（`topic.schemas.TopicOut`）交出同一个 `presentation` 字段，因为侧栏把两
者画在一起。类曾经住在 `room_task/schemas.py`，于是第 5 层的 `topic.schemas` 为了
一个两字段的线形状向上够了一层 —— C5 分层契约里冻着的就是这条边。

同一件事在别的几处已经做过（`shell_catalog` 的 `ShellOut` 是 `ProjectOut` 的一个
字段、`notice_meta` 的码和 `notice()`、`delivery_events`）：低层要用的那套词汇搬到
低层，读它的人改读向下的边。这里搬的只是**形状**：算它的 `presentation.py` 留下，
它读房间和活的行，仍旧在第 4 层。
"""

from pydantic import BaseModel


class PresentationOut(BaseModel):
    """看板上这一格：哪一列，卡面写什么。

    两个字段一起出，因为它们是一个答案的两半 —— 列说「该谁动」，短语说「在等
    什么」，而每一列只能说属于自己的话（`room_task/presentation.py`）。客户端拿到
    的是结论，不是原料：状态从来不存库，是读的时候从事实算出来的，算的地方只有
    后端这一处，所以网页、CLI 和以后任何一个客户端不可能给出不同答案。
    """

    column: str
    #: 短语的码（`presentation.Phrase`），字由读者的屏幕按语言画。
    phrase: str
