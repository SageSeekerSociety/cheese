"""一条事件的作者：参与者，还是平台自己。

`AuthorType` 曾经分三档 —— human / ai / system —— 而前两档是同一件事：有人说了
一句话。人和 agent 是同一种参与者（结论 1），区别只在**是哪一个**参与者，而那是
署名（`Block.author`，一条 handle）的事：一个房间坐得下好几个人和好几个 agent，
一个三档枚举说不出是哪一个，只说得出最粗的那一层，于是每个读它的地方都在用一个
答不了自己问题的字段。

所以这里只答一个问题：**这条事件是参与者写的，还是平台自己写的。**「是不是芝士
说的」去问署名（`app.domain.identity.handles`）。

存量行还带着 P8 之前的两个旧值，两个都是参与者；全仓只有这个模块读得到它们，
P8b 把那些行改写成 `participant` 之后这里跟着删。
"""

from sqlalchemy import ColumnElement

from app.domain.block.models import AuthorType, Block

# P8 之前写下的两个旧值。都是参与者：`human` 是人说的，`ai` 是芝士说的。
_LEGACY_PARTICIPANT = (AuthorType.human, AuthorType.ai)

PARTICIPANT_TYPES = (AuthorType.participant, *_LEGACY_PARTICIPANT)


def is_participant(author_type: AuthorType) -> bool:
    """这条事件是某个参与者写下的（而不是平台自己写的）。"""
    return author_type in PARTICIPANT_TYPES


def participant_blocks() -> ColumnElement[bool]:
    """`is_participant` 的 SQL 孪生 —— 同一份名单，不另写一份。"""
    return Block.author_type.in_(PARTICIPANT_TYPES)
