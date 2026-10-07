import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError
from app.core.sentences import say
from app.domain.avatars.models import Avatar
from app.domain.team.models import (
    PERSONAL_TEAM_ROW,
    ApplicationStatus,
    ApplicationType,
    Team,
    TeamMemberRole,
    TeamMembershipApplication,
    TeamUserRelation,
    TeamVisibility,
)

_HAS_WORD_CHAR_RE = re.compile(r"[\w]", re.UNICODE)


def refuse_anyone_but_the_owner(team: Team, user_id: int) -> None:
    """A personal team is one person's: nobody but its owner is ever in it.

    Working with someone on a personal project is done by inviting them into
    that project as an external member; a group that shares everything makes a
    shared team.
    """
    if (
        team.personal_owner_user_id is not None
        and team.personal_owner_user_id != user_id
    ):
        raise BadRequestError(
            "A personal team has no members but its owner",
            data={"teamId": team.id},
        )


def _use_fts(token: str) -> bool:
    """Return True when *token* is suitable for PostgreSQL FTS."""
    return len(token) > 2 and _HAS_WORD_CHAR_RE.search(token) is not None


class TeamRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_by_name(self, name: str) -> bool:
        # A personal team is shown by its owner's nickname, never by the name
        # stored on it, so it holds no name another team could want.
        stmt = select(func.count(Team.id)).where(
            Team.name == name,
            Team.deleted_at.is_(None),
            Team.personal_owner_user_id.is_(None),
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
        # Search finds only what anyone may find: public, shared teams. A personal
        # team is one person's, and a stealth team is found through its link.
        stmt: Select[tuple[Team]] = select(Team).where(
            Team.deleted_at.is_(None),
            Team.personal_owner_user_id.is_(None),
            Team.visibility == TeamVisibility.PUBLIC.value,
        )
        if query:
            by_handle = func.lower(Team.handle) == query.strip().lower()
            try:
                query_id = int(query)
                stmt = stmt.where(
                    or_(self._name_search_filter(query), by_handle, Team.id == query_id)
                )
            except ValueError:
                stmt = stmt.where(or_(self._name_search_filter(query), by_handle))
        stmt = stmt.order_by(Team.id.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        *,
        query: str | None,
        owner_ids: Sequence[int],
        limit: int,
        offset: int,
        plan_key: str | None = None,
        personal: bool | None = None,
    ) -> tuple[list[Team], int]:
        """Every live team, personal and stealth ones too, newest first — the
        platform console's list. ``query`` matches a name, a handle or an id;
        ``owner_ids`` adds the personal teams of the people it matched.
        ``plan_key`` keeps the teams on that plan; ``personal`` keeps only
        personal teams (True) or only shared ones (False)."""
        stmt = select(Team).where(Team.deleted_at.is_(None))
        if plan_key is not None:
            stmt = stmt.where(Team.plan_key == plan_key)
        if personal is True:
            stmt = stmt.where(Team.personal_owner_user_id.is_not(None))
        elif personal is False:
            stmt = stmt.where(Team.personal_owner_user_id.is_(None))
        if query and query.strip():
            q = query.strip()
            found = [
                Team.name.ilike(f"%{q}%"),
                func.lower(Team.handle) == q.lower(),
            ]
            if q.isdigit():
                found.append(Team.id == int(q))
            if owner_ids:
                found.append(Team.personal_owner_user_id.in_(list(owner_ids)))
            stmt = stmt.where(or_(*found))
        total = await self._session.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
        rows = await self._session.execute(
            stmt.order_by(Team.id.desc()).limit(limit).offset(offset)
        )
        return list(rows.scalars()), int(total or 0)

    async def count_by_plan(self) -> dict[str, int]:
        """How many live teams are on each plan."""
        rows = await self._session.execute(
            select(Team.plan_key, func.count(Team.id))
            .where(Team.deleted_at.is_(None))
            .group_by(Team.plan_key)
        )
        return {key: int(n) for key, n in rows.all()}

    async def member_counts(self, team_ids: Sequence[int]) -> dict[int, int]:
        """team id -> how many members it has; teams with none are absent."""
        if not team_ids:
            return {}
        rows = await self._session.execute(
            select(TeamUserRelation.team_id, func.count(TeamUserRelation.id))
            .where(
                TeamUserRelation.team_id.in_(list(team_ids)),
                TeamUserRelation.deleted_at.is_(None),
            )
            .group_by(TeamUserRelation.team_id)
        )
        return {team_id: int(n) for team_id, n in rows.all()}

    async def get_by_handle(self, handle: str) -> Team | None:
        stmt: Select[tuple[Team]] = select(Team).where(
            func.lower(Team.handle) == handle.lower(), Team.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_join_token(self, token: str) -> Team | None:
        stmt: Select[tuple[Team]] = select(Team).where(
            and_(Team.join_token == token, Team.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, ids: Sequence[int]) -> dict[int, Team]:
        if not ids:
            return {}
        stmt: Select[tuple[Team]] = select(Team).where(
            and_(Team.id.in_(list(ids)), Team.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        teams = list(result.scalars().all())
        return {t.id: t for t in teams}

    async def chosen_avatar_ids(self, team_ids: Sequence[int]) -> dict[int, int]:
        """team id -> 这个团队**自己挑过**的头像 id，没挑过的不在里面。

        和 ``UserProfileRepository.chosen_avatar_ids`` 同一条判据：判断「这是不是全站
        默认头像」看 ``Avatar.avatar_type``，不看 id 是不是 1 —— 默认头像是哪一行是
        种子数据，每个环境不一样。新建团队默认 ``avatar_id=1``（``api/routes/teams.py``
        的 ``CreateTeamRequest``），所以「有 avatar_id」不等于「挑过」；回原始值会让
        所有没挑过的团队共用同一张脸，而区分团队（和人）正是头像唯一的活。没挑过的
        团队不在映射里，调用方据此不回 URL，让客户端画彩色首字母。
        """
        if not team_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Team.id, Team.avatar_id, Avatar.avatar_type)
                .join(Avatar, Avatar.id == Team.avatar_id, isouter=True)
                .where(and_(Team.id.in_(list(team_ids)), Team.deleted_at.is_(None)))
            )
        ).all()
        return {
            team_id: avatar_id
            for team_id, avatar_id, avatar_type in rows
            if avatar_type not in (None, "default")
        }

    async def application_statuses(self, ids: Sequence[int]) -> dict[int, str]:
        """Where each team invitation or join request stands now, by id."""
        if not ids:
            return {}
        stmt = select(
            TeamMembershipApplication.id, TeamMembershipApplication.status
        ).where(
            TeamMembershipApplication.id.in_(list(ids)),
            TeamMembershipApplication.deleted_at.is_(None),
        )
        return {app_id: status for app_id, status in await self._session.execute(stmt)}

    async def list_teams_of_user(self, user_id: int) -> Sequence[Team]:
        """Return teams joined by the given user using team_user_relation."""
        rel_stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(
                TeamUserRelation.user_id == user_id,
                TeamUserRelation.deleted_at.is_(None),
            )
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

    async def list_teams_user_can_use_to_join_task(
        self, user_id: int
    ) -> Sequence[Team]:
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
        # A team task is claimed by a team; one person on their own takes a
        # task open to individuals, so their personal team is never a candidate.
        team_stmt: Select[tuple[Team]] = select(Team).where(
            and_(
                Team.id.in_(team_ids),
                Team.deleted_at.is_(None),
                Team.personal_owner_user_id.is_(None),
            )
        )
        team_result = await self._session.execute(team_stmt)
        return list(team_result.scalars().all())

    async def list_members_of_team(self, team_id: int) -> Sequence[TeamUserRelation]:
        """Return membership rows for a given team."""
        stmt: Select[tuple[TeamUserRelation]] = select(TeamUserRelation).where(
            and_(
                TeamUserRelation.team_id == team_id,
                TeamUserRelation.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_member_relation(
        self, team_id: int, user_id: int
    ) -> TeamUserRelation | None:
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

    async def get_personal_team(self, user_id: int) -> Team | None:
        """The user's personal single-member team (个人 = 单人真团队, v4), or None."""
        stmt = select(Team).where(
            Team.personal_owner_user_id == user_id, Team.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_personal_team(self, user_id: int, name: str) -> Team | None:
        """Provision a user's personal team directly (bypasses the name-uniqueness
        check of the public create flow — personal teams are internal, one per user).

        None when the user already has one. The unique index on the owner makes a
        concurrent caller's insert wait for this one's transaction and then do
        nothing, so two first requests cannot both create a team.
        """
        now = datetime.now(UTC)
        team_id = await self._session.scalar(
            pg_insert(Team)
            .values(
                name=name,
                intro="",
                description="",
                avatar_id=0,
                personal_owner_user_id=user_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[Team.personal_owner_user_id],
                index_where=PERSONAL_TEAM_ROW,
            )
            .returning(Team.id)
        )
        if team_id is None:
            return None
        return await self._session.get(Team, team_id)

    async def add_member(
        self, team_id: int, user_id: int, role: int
    ) -> TeamUserRelation:
        # Every way into a team ends in this write — a direct add, an accepted
        # invitation, an approved request, a join by link — so the rule is kept
        # here, where no path can go around it.
        team = await self.get_by_id(team_id)
        if team is not None:
            refuse_anyone_but_the_owner(team, user_id)
        existing = await self.get_member_relation(team_id, user_id)
        if existing is not None:
            raise ConflictError(
                say("teamAlreadyMember"),
                data={"teamId": team_id, "userId": user_id},
            )
        now = datetime.now(UTC)
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
        relation.deleted_at = datetime.now(UTC)
        relation.updated_at = relation.deleted_at  # type: ignore[assignment]
        await self._session.flush()

    async def soft_delete_team(self, team: Team) -> None:
        now = datetime.now(UTC)
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication
        ).where(
            TeamMembershipApplication.id == application_id,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_pending_for_user_and_team(
        self, user_id: int, team_id: int
    ) -> bool:
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication.id
        ).where(  # type: ignore[assignment]
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication
        ).where(
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication
        ).where(
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication
        ).where(
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication
        ).where(
            TeamMembershipApplication.user_id == user_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            stmt = stmt.where(TeamMembershipApplication.status == status.value)
        stmt = (
            stmt.order_by(TeamMembershipApplication.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(TeamMembershipApplication.id)).where(
            TeamMembershipApplication.user_id == user_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            count_stmt = count_stmt.where(
                TeamMembershipApplication.status == status.value
            )
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
        stmt: Select[tuple[TeamMembershipApplication]] = select(
            TeamMembershipApplication
        ).where(
            TeamMembershipApplication.team_id == team_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            stmt = stmt.where(TeamMembershipApplication.status == status.value)
        stmt = (
            stmt.order_by(TeamMembershipApplication.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(TeamMembershipApplication.id)).where(
            TeamMembershipApplication.team_id == team_id,
            TeamMembershipApplication.type == type_.value,
            TeamMembershipApplication.deleted_at.is_(None),
        )
        if status is not None:
            count_stmt = count_stmt.where(
                TeamMembershipApplication.status == status.value
            )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total
