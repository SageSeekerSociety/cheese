"""一条事件的作者：参与者，还是平台自己。

`AuthorType` 曾经分三档 —— human / ai / system —— 而前两档是同一件事：有人说了
一句话。人和 agent 是同一种参与者（结论 1），区别只在**是哪一个**参与者，而那是
署名（`Block.author`，一条 handle）的事：一个房间坐得下好几个人和好几个 agent，
一个三档枚举说不出是哪一个，只说得出最粗的那一层，于是每个读它的地方都在用一个
答不了自己问题的字段。

所以这里只答一个问题：**这条事件是参与者写的，还是平台自己写的。**「是不是芝士
说的」去问署名（`app.domain.identity.handles`）。

判据写成「是不是 participant」而不是列举平台那一档的名字：那一档正在改名，今天
写下的是 `system`，两次发布之后是 `platform`（见 `AuthorType.platform`），而两个
名字都不是 participant，于是这里一个字都不用跟着改。
"""

from sqlalchemy import ColumnElement

from app.domain.block.models import AuthorType, Block


def is_participant(author_type: AuthorType) -> bool:
    """这条事件是某个参与者写下的（而不是平台自己写的）。"""
    return author_type is AuthorType.participant


def participant_blocks() -> ColumnElement[bool]:
    """`is_participant` 的 SQL 孪生 —— 同一份判据，不另写一份。"""
    return Block.author_type == AuthorType.participant
