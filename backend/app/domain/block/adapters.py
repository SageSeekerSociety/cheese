"""本包为 `notification` 声明的口子实现的那些东西。

`notification` 在第 3 层，够不着第 4 层的 `block`（`docs/manual/dev/architecture.md` 的
层表），所以它在自己的 `ports.py` 里写下**自己要的那一次写**（`DecisionLine`），实现留
在这里，谁在门口谁递进去 —— 和 `task/adapters.py`、`thread/adapters.py`、
`usage/adapters.py` 同一条规矩：口子按消费者分开写，实现按提供者放。

这里只做一件事：把落下的那一行收成可以直接投出去的形状（`BlockOut`）。本模块不 commit
—— 那一笔和调用方自己那几行在同一个事务里，由调用方收尾。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut


class DecisionLine:
    """`app.domain.notification.ports.DecisionLine`：把「拍板了」这一句落进那条对话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __call__(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        author: str,
        content: str,
    ) -> dict:
        block = await BlockRepository(self._session).add(
            project_id=project_id,
            conversation_id=conversation_id,
            author=author,
            author_type=AuthorType.participant,
            content=content,
            kind=BlockKind.message,
        )
        return BlockOut.model_validate(block).model_dump(mode="json")
