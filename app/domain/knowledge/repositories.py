from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.domain.knowledge.models import Knowledge, KnowledgeLabel, KnowledgeUpvote


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        name: str,
        type_: str,
        content: dict,
        description: str | None,
        team_id: int,
        material_id: int | None,
        project_id: int | None,
        discussion_id: int | None,
        created_by: int,
        labels: list[str] | None,
    ) -> Knowledge:
        now = datetime.utcnow()
        knowledge = Knowledge(
            name=name,
            description=description or "",
            type=type_,
            content=content,
            team_id=team_id,
            material_id=material_id,
            created_by=created_by,
            source_type="MANUAL",
            project_id=project_id,
            discussion_id=discussion_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(knowledge)
        await self._session.flush()

        if labels:
            for label in labels:
                lbl = KnowledgeLabel(
                    knowledge_id=knowledge.id,
                    label=label,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
                self._session.add(lbl)
        await self._session.flush()
        return knowledge

    async def get_by_id(self, knowledge_id: int) -> Knowledge | None:
        stmt: Select[tuple[Knowledge]] = select(Knowledge).where(
            Knowledge.id == knowledge_id,
            Knowledge.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(self, knowledge_id: int) -> bool:
        entity = await self.get_by_id(knowledge_id)
        if entity is None:
            return False
        entity.deleted_at = datetime.utcnow()
        await self._session.flush()
        return True

    async def update_entity(
        self,
        *,
        entity: Knowledge,
        name: str | None = None,
        description: str | None = None,
        content: dict | None = None,
        project_id: int | None = None,
    ) -> Knowledge:
        if name is not None:
            entity.name = name
        if description is not None:
            entity.description = description
        if content is not None:
            entity.content = content
        if project_id is not None:
            entity.project_id = project_id
        entity.updated_at = datetime.utcnow()
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def list(
        self,
        *,
        team_id: int,
        project_id: int | None,
        type_: str | None,
        labels: list[str] | None,
        query: str | None,
        limit: int,
        offset: int = 0,
        sort_by: str = "createdAt",
        sort_order: str = "desc",
    ) -> tuple[list[Knowledge], int]:
        stmt: Select[tuple[Knowledge]] = select(Knowledge).where(
            Knowledge.team_id == team_id,
            Knowledge.deleted_at.is_(None),
        )
        if project_id is not None:
            stmt = stmt.where(Knowledge.project_id == project_id)
        if type_ is not None:
            stmt = stmt.where(Knowledge.type == type_)
        if query:
            pattern = f"%{query.lower()}%"
            stmt = stmt.where(
                func.lower(Knowledge.name).ilike(pattern)
                | func.lower(Knowledge.description).ilike(pattern)
            )

        if labels:
            normalized_labels = sorted({lbl.strip() for lbl in labels if lbl.strip()})
            if normalized_labels:
                subq = (
                    select(KnowledgeLabel.knowledge_id)
                    .where(
                        KnowledgeLabel.label.in_(normalized_labels),
                        KnowledgeLabel.deleted_at.is_(None),
                    )
                    .group_by(KnowledgeLabel.knowledge_id)
                    .having(
                        func.count(func.distinct(KnowledgeLabel.label)) == len(normalized_labels)
                    )
                )
                stmt = stmt.where(Knowledge.id.in_(subq))

        if sort_by == "updatedAt":
            order_col = Knowledge.updated_at
        else:
            order_col = Knowledge.created_at

        if sort_order.lower() == "asc":
            stmt = stmt.order_by(order_col.asc())
        else:
            stmt = stmt.order_by(order_col.desc())

        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(Knowledge.id)).where(
            Knowledge.team_id == team_id,
            Knowledge.deleted_at.is_(None),
        )
        if project_id is not None:
            count_stmt = count_stmt.where(Knowledge.project_id == project_id)
        if type_ is not None:
            count_stmt = count_stmt.where(Knowledge.type == type_)
        if query:
            pattern = f"%{query.lower()}%"
            count_stmt = count_stmt.where(
                func.lower(Knowledge.name).ilike(pattern)
                | func.lower(Knowledge.description).ilike(pattern)
            )
        if labels:
            normalized_labels = sorted({lbl.strip() for lbl in labels if lbl.strip()})
            if normalized_labels:
                subq = (
                    select(KnowledgeLabel.knowledge_id)
                    .where(
                        KnowledgeLabel.label.in_(normalized_labels),
                        KnowledgeLabel.deleted_at.is_(None),
                    )
                    .group_by(KnowledgeLabel.knowledge_id)
                    .having(
                        func.count(func.distinct(KnowledgeLabel.label)) == len(normalized_labels)
                    )
                )
                count_stmt = count_stmt.where(Knowledge.id.in_(subq))

        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def get_labels_map(self, knowledge_ids: Sequence[int]) -> dict[int, list[str]]:
        if not knowledge_ids:
            return {}
        stmt: Select[tuple[KnowledgeLabel]] = (
            select(KnowledgeLabel)
            .where(
                KnowledgeLabel.knowledge_id.in_(list(knowledge_ids)),
                KnowledgeLabel.deleted_at.is_(None),
            )
            .order_by(KnowledgeLabel.knowledge_id.asc(), KnowledgeLabel.label.asc())
        )
        result = await self._session.execute(stmt)
        labels: dict[int, list[str]] = {}
        for row in result.scalars().all():
            labels.setdefault(row.knowledge_id, []).append(row.label)
        return labels

    async def get_upvote_counts(self, knowledge_ids: Sequence[int]) -> dict[int, int]:
        if not knowledge_ids:
            return {}
        stmt = (
            select(KnowledgeUpvote.knowledge_id, func.count(KnowledgeUpvote.id))
            .where(
                KnowledgeUpvote.knowledge_id.in_(list(knowledge_ids)),
            )
            .group_by(KnowledgeUpvote.knowledge_id)
        )
        result = await self._session.execute(stmt)
        return {row[0]: int(row[1]) for row in result.all()}

    async def get_upvote_count(self, knowledge_id: int) -> int:
        stmt = select(func.count(KnowledgeUpvote.id)).where(
            KnowledgeUpvote.knowledge_id == knowledge_id,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_user_upvotes(
        self,
        knowledge_ids: Sequence[int],
        user_id: int,
    ) -> set[int]:
        if not knowledge_ids:
            return set()
        stmt = select(KnowledgeUpvote.knowledge_id).where(
            KnowledgeUpvote.knowledge_id.in_(list(knowledge_ids)),
            KnowledgeUpvote.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return {row[0] for row in result.all()}

    async def has_upvote(self, knowledge_id: int, user_id: int) -> bool:
        stmt = select(KnowledgeUpvote.id).where(
            KnowledgeUpvote.knowledge_id == knowledge_id,
            KnowledgeUpvote.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def add_upvote(self, knowledge_id: int, user_id: int) -> None:
        stmt = select(KnowledgeUpvote.id).where(
            KnowledgeUpvote.knowledge_id == knowledge_id,
            KnowledgeUpvote.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise BadRequestError("Already upvoted")
        now = datetime.utcnow()
        upvote = KnowledgeUpvote(
            knowledge_id=knowledge_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(upvote)
        await self._session.flush()

    async def remove_upvote(self, knowledge_id: int, user_id: int) -> None:
        stmt = select(KnowledgeUpvote).where(
            KnowledgeUpvote.knowledge_id == knowledge_id,
            KnowledgeUpvote.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        upvote = result.scalar_one_or_none()
        if upvote is None:
            return
        await self._session.delete(upvote)
        await self._session.flush()

    async def update_labels(self, knowledge_id: int, labels: list[str]) -> None:
        stmt: Select[tuple[KnowledgeLabel]] = select(KnowledgeLabel).where(
            KnowledgeLabel.knowledge_id == knowledge_id,
            KnowledgeLabel.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        existing = list(result.scalars().all())
        now = datetime.utcnow()

        for lbl_entity in existing:
            lbl_entity.deleted_at = now

        for label in labels:
            lbl = KnowledgeLabel(
                knowledge_id=knowledge_id,
                label=label,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(lbl)
        await self._session.flush()
