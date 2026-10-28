"""本包够不着的那一件写：把「拍板了」这一句落到问这件事的那条对话上。

一条决策请求被拍板之后，这个决定要回落到问出口的那条对话里，芝士下一轮读得到。
房间产出在 `block` 里（第 4 层），本包在第 3 层（`docs/manual/dev/architecture.md` 的
层表），自己写不了。

收的都是本包手上就有的东西：项目 id、会话 id、拍板的人、要写的那句话。交回的是落下的
那一行序列化之后的样子（`block.schemas.BlockOut`），不是 ORM 行 —— 本包不认它的形状，
只把它原样投出去。实现按提供者放在 `app/domain/block/adapters.py`，谁在门口谁递
（`app/api/routes/alerts.py`）：口子按消费者分开写，实现按提供者放。

实现**不 commit**：落下的那一行和这条通知自己的改动在同一个事务里，那一笔由本包收尾
（`ProjectNotificationService.resolve` 先写、再 commit、然后才投）。
"""

import uuid
from typing import Protocol


class DecisionLine(Protocol):
    """把「拍板了」这一句落进那条对话，交回能直接投出去的那一份。

    ``author`` 是拍板的人本人的 handle：这句话署他的名，不署平台的。
    ``by_assistant``：是他自己的芝士替他拍的板，这一句标明是芝士代说的。
    """

    async def __call__(
        self,
        *,
        project_id: uuid.UUID,
        conversation_id: uuid.UUID,
        author: str,
        content: str,
        by_assistant: bool = False,
    ) -> dict: ...
