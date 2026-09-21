"""平台管理员那份**表**的读写。根那一份在配置里，不在这张表里。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.admin.models import PlatformAdmin


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def added_admin_handles(self) -> frozenset[str]:
        """页面上加的那些 handle —— 判据里「并集」的那一半。

        只取一列：调用方问的是「在不在里面」，不是这张表的任何别的字段。整张表
        通常只有个位数行，所以这一读永远是几十字节 —— 而它挂在每一次 `is_admin`
        上，所以 `AdminService` 在实例上 memo 一次，**一个请求只查一次**（理由写在
        `AdminService.admin_handles`）。
        """
        stmt = select(PlatformAdmin.handle)
        return frozenset((await self._session.execute(stmt)).scalars().all())

    async def list_admins(self) -> Sequence[PlatformAdmin]:
        """名单，按加进来的先后。页面要画「谁加的、什么时候」，所以给整行。"""
        stmt = select(PlatformAdmin).order_by(
            PlatformAdmin.created_at, PlatformAdmin.handle
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def add_admin(self, handle: str, added_by: str) -> bool:
        """加一行；已经在里面就是 no-op。返回「这次到底写没写进去」。

        `add_support` 同一个形状，理由也一样：`has → add` 那对语句是一个并发竞态
        （两个人同时加同一个人，后者的 flush 把 `IntegrityError` 抛到路由上，成了
        一个 500），一条语句输不掉这个竞态，还少一个来回。`RETURNING id` 让布尔值
        诚实 —— 只有真插进去的那次才会回来。
        """
        stmt = (
            pg_insert(PlatformAdmin)
            .values(handle=handle, added_by_handle=added_by)
            .on_conflict_do_nothing(index_elements=["handle"])
            .returning(PlatformAdmin.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def remove_admin(self, handle: str) -> bool:
        """删一行，同样幂等 —— 删一个本来就不在名单里的人，「不在」就是答案。"""
        stmt = select(PlatformAdmin).where(PlatformAdmin.handle == handle)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
