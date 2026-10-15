"""本包为别的包自己声明的口子实现的那些东西。

比本包低的 `project` 够不着第 3 层，所以它在自己的 `ports.py` 里写下**自己要问的
那两件事**，实现留在这里，谁在门口谁递进去 —— 和 `agent.announce`、
`agent_instance.adapters` 里那几个同一条规矩：口子按消费者分开写，实现按提供者放。

这里只做一件事：把 `Ledger` 的答案收成那个口子说好的内置类型。名字跟着它实现的
端口叫，并排放着，好一眼看出本包被谁要走了什么。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage.ledger import Ledger


class ComputeCredits:
    """``app.domain.project.ports.ComputeCredits``：某个项目的那份赛题算力。

    `Ledger.earmarks` 交回的是一串 ``ComputeGrant``，这里把「有没有这道题的」就地
    折成布尔；发的那一次交回新建的额度行，调用方用不上，不往外传。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._ledger = Ledger(session)

    async def has_earmark(
        self, project_id: uuid.UUID, source_task_id: int | None
    ) -> bool:
        grants = await self._ledger.earmarks(project_id)
        return any(grant.source_task_id == source_task_id for grant in grants)

    async def grant_earmark(
        self,
        *,
        project_id: uuid.UUID,
        source_task_id: int | None,
        credits_total: float,
    ) -> None:
        await self._ledger.grant_earmark(
            project_id=project_id,
            source_task_id=source_task_id,
            credits_total=credits_total,
        )
