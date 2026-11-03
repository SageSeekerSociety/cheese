"""一条事件的作者：「这条是参与者写的吗」的两个孪生。

档位本身 `AuthorType` 住在第 1 层的 `app.domain.event_author` —— 判这一列的还有
够不到第 4 层的读者（`topic` 的裸表读法），而读法要求按枚举成员读。留在这里的是
同一句话在 ORM 这一侧的两种写法：读一行用 `is_participant`，写 `WHERE` 用
`participant_blocks`。

「是不是芝士说的」去问署名（`app.domain.identity.handles`）。
"""

from sqlalchemy import ColumnElement

from app.domain.block.models import Block, by_assistant
from app.domain.event_author import AuthorType

#: How a line an agent reads says the author's own 芝士 wrote the message for
#: them (``meta.by_assistant``): what people see beside the name, the agents
#: reading the room are told too (docs/agent-principles.md 十九).
WRITTEN_BY_ASSISTANT = "由本人的个人芝士代发"


def is_participant(author_type: AuthorType) -> bool:
    """这条事件是某个参与者写下的（而不是平台自己写的）。"""
    return author_type is AuthorType.participant


def participant_blocks() -> ColumnElement[bool]:
    """`is_participant` 的 SQL 孪生 —— 同一份判据，不另写一份。"""
    return Block.author_type == AuthorType.participant


def speaker(block: Block, name: str | None = None) -> str:
    """Who said ``block``, as a line an agent reads names them: ``name`` (the
    author's handle unless given), and that their own 芝士 wrote it for them
    when it did."""
    who = name or block.author
    return f"{who}（{WRITTEN_BY_ASSISTANT}）" if by_assistant(block) else who
