from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Select, and_, or_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.team.models import (
    Team,
    TeamUserRelation,
    TeamMemberRole,
    TeamMembershipApplication,
    ApplicationStatus,
    ApplicationType,
)


class TeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_by_name(self, name: str) -> bool:
        stmt = select(func.count(Team.id)).where(
            Team.name == name,
            Team.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return bool(result.scalar_one() or 0)

    async def get_by_id(self, team_id: int) -> Team | None:
        stmt: Select[tuple[Team]] = select(Team).where(
            and_(Team.id == team_id, Team.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_teams(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int = 0,
    ) -> Sequence[Team]:
        stmt: Select[tuple[Team]] = select(Team).where(Team.deleted_at.is_(None))
        if query:
            like = f"%{query}%"
            try:
                query_id = int(query)
                stmt = stmt.where(or_(Team.name.ilike(like), Team.id == query_id))
            except ValueError:
                stmt = stmt.where(Team.name.ilike(like))
        stmt = stmt.order_by(Team.id.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_ids(self, ids: Sequence[int]) -> dict[int, Team]:
        if not ids:
            return {}
        stmt: Select[tuple[Team]] = select(Team).where(
            and_(Team.id.in_(list(ids)), Team.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        teams = list(result.scalars().all())
        return {t.id: t for t in teams}

    async def list_teams_of_user(self, user_id: int) -> Sequence[Team]:
        """Return teams joined by the given user using team_user_relation."""
        rel_stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(TeamUserRelation.user_id == user_id, TeamUserRelation.deleted_at.is_(None))
        )
        rel_result = await self._session.execute(rel_stmt)
        relations = list(rel_result.scalars().all())
        if not relations:
            return []
        team_ids = {rel.team_id for rel in relations}
        team_stmt: Select[tuple[Team]] = select(Team).where(
            and_(Team.id.in_(list(team_ids)), Team.deleted_at.is_(None))
        )
        team_result = await self._session.execute(team_stmt)
        return list(team_result.scalars().all())

    async def list_members_of_team(self, team_id: int) -> Sequence[TeamUserRelation]:
        """Return membership rows for a given team."""
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(TeamUserRelation.team_id == team_id, TeamUserRelation.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_member_relation(self, team_id: int, user_id: int) -> TeamUserRelation | None:
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            TeamUserRelation.team_id == team_id,
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_team_member(self, team_id: int, user_id: int) -> bool:
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation.id).where(
            and_(
                TeamUserRelation.team_id == team_id,
                TeamUserRelation.user_id == user_id,
                TeamUserRelation.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_admin_and_owner_ids(self, team_id: int) -> set[int]:
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(
                TeamUserRelation.team_id == team_id,
                TeamUserRelation.deleted_at.is_(None),
                TeamUserRelation.role.in_([TeamMemberRole.ADMIN, TeamMemberRole.OWNER]),
            )
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {r.user_id for r in rows}

    async def is_team_at_least_admin(self, team_id: int, user_id: int) -> bool:
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(
                TeamUserRelation.team_id == team_id,
                TeamUserRelation.user_id == user_id,
                TeamUserRelation.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        rel = result.scalar_one_or_none()
        if rel is None:
            return False
        return rel.role in (TeamMemberRole.OWNER, TeamMemberRole.ADMIN)

    async def add_member(self, team_id: int, user_id: int, role: int) -> TeamUserRelation:
        from datetime import datetime as _dt

        now = _dt.utcnow()
        rel = TeamUserRelation(
            team_id=team_id,
            user_id=user_id,
            role=role,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(rel)
        await self._session.flush()
        return rel

    async def soft_delete_member(self, relation: TeamUserRelation) -> None:
        relation.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        relation.updated_at = relation.deleted_at
        await self._session.flush()

    async def soft_delete_team(self, team: Team) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        team.deleted_at = now
        team.updated_at = now
        await self._session.flush()


class TeamMembershipApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, app: TeamMembershipApplication) -> TeamMembershipApplication:
        self._session.add(app)
        await self._session.flush()
        return app

    async def get_by_id(self, application_id: int) -> TeamMembershipApplication | None:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication).where(
            TeamMembershipApplication.id == application_id,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_pending_for_user_and_team(self, user_id: int, team_id: int) -> bool:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication.id).where(
            TeamMembershipApplication.user_id == user_id,
            TeamMembershipApplication.team_id == team_id,
            TeamMembershipApplication.status == ApplicationStatus.PENDING.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def find_pending_by_id_and_initiator_and_type(
        self,
        *,
        application_id: int,
        initiator_id: int,
        type_: ApplicationType,
    ) -> TeamMembershipApplication | None:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication).where(
            TeamMembershipApplication.id == application_id,
            TeamMembershipApplication.initiator_id == initiator_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.status == ApplicationStatus.PENDING.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_pending_by_id_and_user_and_type(
        self,
        *,
        application_id: int,
        user_id: int,
        type_: ApplicationType,
    ) -> TeamMembershipApplication | None:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication).where(
            TeamMembershipApplication.id == application_id,
            TeamMembershipApplication.user_id == user_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.status == ApplicationStatus.PENDING.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_pending_by_id_and_team_and_type(
        self,
        *,
        application_id: int,
        team_id: int,
        type_: ApplicationType,
    ) -> TeamMembershipApplication | None:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication).where(
            TeamMembershipApplication.id == application_id,
            TeamMembershipApplication.team_id == team_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.status == ApplicationStatus.PENDING.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        *,
        user_id: int,
        type_: ApplicationType,
        status: ApplicationStatus | None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[TeamMembershipApplication], int]:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication).where(
            TeamMembershipApplication.user_id == user_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            stmt = stmt.where(TeamMembershipApplication.status == status.value)
        stmt = stmt.order_by(TeamMembershipApplication.id.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(TeamMembershipApplication.id)).where(
            TeamMembershipApplication.user_id == user_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            count_stmt = count_stmt.where(TeamMembershipApplication.status == status.value)
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total

    async def list_for_team(
        self,
        *,
        team_id: int,
        type_: ApplicationType,
        status: ApplicationStatus | None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[TeamMembershipApplication], int]:
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication).where(
            TeamMembershipApplication.team_id == team_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            stmt = stmt.where(TeamMembershipApplication.status == status.value)
        stmt = stmt.order_by(TeamMembershipApplication.id.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(TeamMembershipApplication.id)).where(
            TeamMembershipApplication.team_id == team_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            count_stmt = count_stmt.where(TeamMembershipApplication.status == status.value)
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total
