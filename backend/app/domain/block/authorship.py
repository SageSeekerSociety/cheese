"""一条事件的作者：「这条是参与者写的吗」的两个孪生。

档位本身 `AuthorType` 住在第 1 层的 `app.domain.event_author` —— 判这一列的还有
够不到第 4 层的读者（`topic` 的裸表读法），而读法要求按枚举成员读。留在这里的是
同一句话在 ORM 这一侧的两种写法：读一行用 `is_participant`，写 `WHERE` 用
`participant_blocks`。

「是不是芝士说的」去问署名（`app.domain.identity.handles`）。
"""

from sqlalchemy import ColumnElement

from app.domain.block.models import Block
from app.domain.event_author import AuthorType


def is_participant(author_type: AuthorType) -> bool:
    """这条事件是某个参与者写下的（而不是平台自己写的）。"""
    return author_type is AuthorType.participant


def participant_blocks() -> ColumnElement[bool]:
    """`is_participant` 的 SQL 孪生 —— 同一份判据，不另写一份。"""
    return Block.author_type == AuthorType.participant
