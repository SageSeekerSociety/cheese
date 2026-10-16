"""A 题目版's 访问域组: the groups, and the domains inside each one.

Split out of ``app/domain/space/repositories.py`` so that module keeps one
reason to change — the two rows a group is made of are read and written
together and nothing else in the space domain touches them. Both classes are
re-exported from ``repositories``, so
``from app.domain.space.repositories import SpaceDomainGroupRepository``
keeps resolving.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import SpaceDomainGroup, SpaceDomainGroupDomain
from app.domain.task.models import TaskAccessDomain


class SpaceDomainGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_groups(self, space_id: int) -> Sequence[SpaceDomainGroup]:
        stmt: Select[tuple[SpaceDomainGroup]] = (
            select(SpaceDomainGroup)
            .where(
                SpaceDomainGroup.space_id == space_id,
                SpaceDomainGroup.deleted_at.is_(None),
            )
            .order_by(SpaceDomainGroup.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(
        self, *, space_id: int, group_id: int
    ) -> SpaceDomainGroup | None:
        stmt: Select[tuple[SpaceDomainGroup]] = select(SpaceDomainGroup).where(
            SpaceDomainGroup.id == group_id,
            SpaceDomainGroup.space_id == space_id,
            SpaceDomainGroup.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_name(self, *, space_id: int, name: str) -> bool:
        stmt = select(SpaceDomainGroup.id).where(
            SpaceDomainGroup.space_id == space_id,
            SpaceDomainGroup.name == name,
            SpaceDomainGroup.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def create_group(
        self, *, space_id: int, name: str, description: str | None
    ) -> SpaceDomainGroup:
        now = datetime.now(UTC)
        group = SpaceDomainGroup(
            space_id=space_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(group)
        await self._session.flush()
        return group

    async def save(self, group: SpaceDomainGroup) -> SpaceDomainGroup:
        self._session.add(group)
        await self._session.flush()
        return group


class SpaceDomainGroupDomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_domains_for_group(self, group_id: int) -> list[str]:
        stmt = (
            select(SpaceDomainGroupDomain.domain)
            .where(
                SpaceDomainGroupDomain.group_id == group_id,
                SpaceDomainGroupDomain.deleted_at.is_(None),
            )
            .order_by(SpaceDomainGroupDomain.domain.asc())
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def list_domains_for_groups(
        self, group_ids: Sequence[int]
    ) -> dict[int, list[str]]:
        if not group_ids:
            return {}
        stmt = (
            select(SpaceDomainGroupDomain.group_id, SpaceDomainGroupDomain.domain)
            .where(
                SpaceDomainGroupDomain.group_id.in_(list(group_ids)),
                SpaceDomainGroupDomain.deleted_at.is_(None),
            )
            .order_by(SpaceDomainGroupDomain.domain.asc())
        )
        result = await self._session.execute(stmt)
        mapping: dict[int, list[str]] = {}
        for group_id, domain in result.all():
            mapping.setdefault(int(group_id), []).append(domain)
        return mapping

    async def replace_domains(self, *, group_id: int, domains: Sequence[str]) -> None:
        now = datetime.now(UTC)
        stmt: Select[tuple[SpaceDomainGroupDomain]] = select(
            SpaceDomainGroupDomain
        ).where(
            SpaceDomainGroupDomain.group_id == group_id,
            SpaceDomainGroupDomain.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for item in existing:
            item.deleted_at = now
            item.updated_at = now
        for domain in domains:
            self._session.add(
                SpaceDomainGroupDomain(
                    group_id=group_id,
                    domain=domain,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()

    async def soft_delete_by_group(self, *, group_id: int) -> None:
        now = datetime.now(UTC)
        stmt: Select[tuple[SpaceDomainGroupDomain]] = select(
            SpaceDomainGroupDomain
        ).where(
            SpaceDomainGroupDomain.group_id == group_id,
            SpaceDomainGroupDomain.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for item in existing:
            item.deleted_at = now
            item.updated_at = now
        await self._session.flush()

    async def list_group_ids_by_domains(
        self, *, space_id: int, domains: Sequence[str]
    ) -> set[int]:
        """Return domain-group IDs whose stored domains intersect the given set."""
        if not domains:
            return set()
        stmt = (
            select(SpaceDomainGroupDomain.group_id)
            .join(
                SpaceDomainGroup, SpaceDomainGroupDomain.group_id == SpaceDomainGroup.id
            )
            .where(
                SpaceDomainGroup.space_id == space_id,
                SpaceDomainGroupDomain.domain.in_(list(domains)),
                SpaceDomainGroupDomain.deleted_at.is_(None),
                SpaceDomainGroup.deleted_at.is_(None),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return {int(row[0]) for row in result.all()}

    async def list_group_ids_by_task_ids(
        self, *, space_id: int, task_ids: Sequence[int]
    ) -> dict[int, set[int]]:
        """一次取回多道题各自的域组，按题目分组（组内无序，调用方排）。

        列表接口一屏就要给一整页题配访问域组，逐题各发两条就是 40 条查询；这条是
        它的批量版本，逐题的答案与 ``TaskAccessDomainRepository.list_by_task_id``
        接 ``list_group_ids_by_domains`` 逐字一致，但保留 task → group 的归属
        —— 一把梭成一个集合会把 A 题的组安到 B 题上。没有访问域、或在本题所属
        板里没有对应组的题不在结果里。

        放在这里而不是 ``TaskAccessDomainRepository``：那边的 import 会给
        ``app.domain.task.repositories`` 添一条指向 space 的边，``domains-acyclic``
        契约（C3）当场变红。
        """
        if not task_ids:
            return {}
        stmt = (
            select(TaskAccessDomain.task_id, SpaceDomainGroup.id)
            .join(
                SpaceDomainGroupDomain,
                SpaceDomainGroupDomain.domain == TaskAccessDomain.domain,
            )
            .join(
                SpaceDomainGroup,
                SpaceDomainGroup.id == SpaceDomainGroupDomain.group_id,
            )
            .where(
                TaskAccessDomain.task_id.in_(list(task_ids)),
                TaskAccessDomain.deleted_at.is_(None),
                SpaceDomainGroupDomain.deleted_at.is_(None),
                SpaceDomainGroup.space_id == space_id,
                SpaceDomainGroup.deleted_at.is_(None),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, set[int]] = {}
        for task_id, group_id in result.all():
            grouped.setdefault(int(task_id), set()).add(int(group_id))
        return grouped
