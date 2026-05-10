import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    Team,
    TeamMemberRole,
    TeamMembershipApplication,
    TeamUserRelation,
)

_HAS_WORD_CHAR_RE = re.compile(r"[\w]", re.UNICODE)


def _use_fts(token: str) -> bool:
    """Return True when *token* is suitable for PostgreSQL FTS."""
    return len(token) > 2 and _HAS_WORD_CHAR_RE.search(token) is not None


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

    @staticmethod
    def _name_search_filter(query: str):
        """FTS filter for team name search.

        Short queries or emoji-only strings fall back to ILIKE; longer
        word-bearing queries use ``to_tsvector / plainto_tsquery``.
        """
        stripped = query.strip()
        if not _use_fts(stripped):
            return Team.name.ilike(f"%{stripped}%")
        tsvector = func.to_tsvector(text("'simple'"), func.coalesce(Team.name, ""))
        tsquery = func.plainto_tsquery(text("'simple'"), stripped)
        return tsvector.op("@@")(tsquery)

    async def list_teams(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int = 0,
    ) -> Sequence[Team]:
        stmt: Select[tuple[Team]] = select(Team).where(Team.deleted_at.is_(None))
        if query:
            try:
                query_id = int(query)
                stmt = stmt.where(or_(self._name_search_filter(query), Team.id == query_id))
            except ValueError:
                stmt = stmt.where(self._name_search_filter(query))
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

    async def list_teams_user_can_use_to_join_task(self, user_id: int) -> Sequence[Team]:
        """Teams the user is OWNER or ADMIN of (eligible to join a TEAM task with).

        Mirrors NT TeamRepository.getTeamsThatUserCanUseToJoinTask. The Python
        eligibility service used to only consider teams that already had a
        TaskMembership row, so a user who hadn't joined yet saw an empty
        `teams` array and the frontend rendered "no eligible teams".
        """
        rel_stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(
                TeamUserRelation.user_id == user_id,
                TeamUserRelation.deleted_at.is_(None),
                TeamUserRelation.role.in_([TeamMemberRole.OWNER, TeamMemberRole.ADMIN]),
            )
        )
        rel_result = await self._session.execute(rel_stmt)
        team_ids = [rel.team_id for rel in rel_result.scalars().all()]
        if not team_ids:
            return []
        team_stmt: Select[tuple[Team]] = select(Team).where(
            and_(Team.id.in_(team_ids), Team.deleted_at.is_(None))
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
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation.id).where(  # type: ignore[assignment]
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
        existing = await self.get_member_relation(team_id, user_id)
        if existing is not None:
            raise ConflictError(
                "User is already a member of this team",
                data={"teamId": team_id, "userId": user_id},
            )
        now = datetime.now(UTC).replace(tzinfo=None)
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
        relation.deleted_at = datetime.now(UTC).replace(tzinfo=None)
        relation.updated_at = relation.deleted_at
        await self._session.flush()

    async def soft_delete_team(self, team: Team) -> None:
        now = datetime.now(UTC).replace(tzinfo=None)
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(TeamMembershipApplication.id).where(  # type: ignore[assignment]
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
