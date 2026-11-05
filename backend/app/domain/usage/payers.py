"""Who pays for a call, and what their plan allows.

Credits belong only to teams, never to a person inside one. A person's own
credits are packs on their personal team. Where a call happens decides which
team pays: a team's project, that team; a call outside any project or in the
person's own project, their personal team — including when someone else in that
project's room summons 芝士.

Every team is on a plan (``Plan``), and a plan bills one of two ways.
``Terms`` is that plan read as what a charge may spend:

- A monthly plan issues a pack for the month the first time a call of that month
  is charged; until then the balance counts it as already there, so reading a
  balance never writes (``ledger_queries``).
- A windowed plan issues no pack. It lets a team spend up to a cap inside each
  of its time windows (``windows``).
- An unlimited plan refuses nothing, issues nothing and draws on nothing; its
  usage is still recorded.

A ``Payer`` is one team, its terms, the project the call happened in if any,
and whether that team is somebody's personal one.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.team.services import team_service
from app.domain.usage.models import Plan
from app.domain.usage.windows import Window


@dataclass(frozen=True)
class Terms:
    """What a team's plan says about spending."""

    key: str
    unlimited: bool = False
    credits_per_period: float | None = None
    # A windowed plan's windows; a monthly plan has none.
    windows: tuple[Window, ...] = ()
    # The model tiers the plan allows; None is every tier.
    model_tiers: frozenset[str] | None = None

    @classmethod
    def of(cls, plan: Plan) -> "Terms":
        return cls(
            key=plan.key,
            unlimited=plan.unlimited,
            credits_per_period=plan.credits_per_period,
            windows=tuple(Window.of(w) for w in plan.windows or []),
            model_tiers=(
                None if plan.model_tiers is None else frozenset(plan.model_tiers)
            ),
        )

    @property
    def issues(self) -> float:
        """The pack the plan issues each month; 0 when it issues none."""
        if self.unlimited or self.windowed or not self.credits_per_period:
            return 0.0
        return float(self.credits_per_period)

    @property
    def windowed(self) -> bool:
        return not self.unlimited and bool(self.windows)


async def terms_of(session: AsyncSession, plan_keys: Iterable[str]) -> dict:
    """Plan key → ``Terms``, for every key given."""
    keys = sorted(set(plan_keys))
    if not keys:
        return {}
    if isinstance(session, AsyncSession):
        if session.autoflush:
            await session.flush()
        out: dict[str, Terms] = {}
        missing: list[str] = []
        for k in keys:
            found, cached = recall(session, ("plan_terms", k))
            if found and cached is not None:
                out[k] = cached
            else:
                missing.append(k)
        if not missing:
            return out
        rows = await session.execute(select(Plan).where(Plan.key.in_(missing)))
        fetched = {plan.key: Terms.of(plan) for plan in rows.scalars()}
        for k, t in fetched.items():
            out[k] = t
            remember(
                session,
                ("plan_terms", k),
                t,
                tables=frozenset({"plans"}),
            )
        return out
    rows = await session.execute(select(Plan).where(Plan.key.in_(keys)))
    return {plan.key: Terms.of(plan) for plan in rows.scalars()}


async def team_terms(session: AsyncSession, team_id: int) -> Terms:
    """The terms of the plan a team is on."""

    team = await team_service(session).get_team(team_id)
    key = team.plan_key if team is not None else "free"
    return (await terms_of(session, [key]))[key]


@dataclass(frozen=True)
class Payer:
    """The team that pays for a call, the project it happens in if any, whether
    that team is someone's personal team, and what its plan allows."""

    team_id: int
    terms: Terms
    project_id: uuid.UUID | None = None
    personal: bool = False


async def payer_for_person(session: AsyncSession, user_id: int) -> Payer:
    """A call a person makes outside any project: their personal team pays."""

    team = await team_service(session).ensure_personal_team(user_id)
    terms = (await terms_of(session, [team.plan_key]))[team.plan_key]
    return Payer(team_id=team.id, terms=terms, personal=True)


async def payers_for_projects(
    session: AsyncSession, projects: list
) -> dict[uuid.UUID, Payer]:
    """Each project's payer: its team, whoever in the room made the call."""

    teams = await _payer_teams(session, sorted({p.team_id for p in projects}))
    keys = [plan_key for plan_key, _ in teams.values()]
    if len(teams) < len({p.team_id for p in projects}):
        keys.append("free")
    terms = await terms_of(session, keys)
    out: dict[uuid.UUID, Payer] = {}
    for project in projects:
        team = teams.get(project.team_id)
        out[project.id] = Payer(
            team_id=project.team_id,
            terms=terms[team[0] if team is not None else "free"],
            project_id=project.id,
            personal=team is not None and team[1],
        )
    return out


async def _payer_teams(
    session: AsyncSession, team_ids: list[int]
) -> dict[int, tuple[str, bool]]:
    """Team id → (plan key, whether it is someone's personal team), for the
    teams among ``team_ids`` that exist. Read with the plans those teams are
    on, in one statement: admitting a model call asks this every time, and the
    team and its plan were two round trips. The plans' terms go into the
    request's facts that `terms_of` answers from."""
    if not isinstance(session, AsyncSession):
        teams = await team_service(session).get_teams_by_ids(team_ids)
        return {
            t.id: (t.plan_key, t.personal_owner_user_id is not None)
            for t in teams.values()
        }
    if session.autoflush:
        await session.flush()
    key = _payer_teams_key(team_ids)
    found, cached = recall(session, key)
    if found:
        return cached
    rows = await session.execute(
        select(Team.id, Team.plan_key, Team.personal_owner_user_id, Plan)
        .outerjoin(Plan, Plan.key == Team.plan_key)
        .where(Team.id.in_(team_ids), Team.deleted_at.is_(None))
    )
    teams: dict[int, tuple[str, bool]] = {}
    for team_id, plan_key, owner, plan in rows:
        teams[team_id] = (plan_key, owner is not None)
        if plan is not None:
            remember(
                session,
                ("plan_terms", plan.key),
                Terms.of(plan),
                tables=frozenset({"plans"}),
            )
    remember(session, key, teams, tables=frozenset({"team"}))
    return teams


async def payer_for_project(session: AsyncSession, project_id: uuid.UUID) -> Payer:
    # deferred-import: tests replace this name on app.domain.project.services
    from app.domain.project.services import ProjectService

    project = await ProjectService(session).get_or_404(project_id)
    return (await payers_for_projects(session, [project]))[project.id]


def _payer_teams_key(team_ids: list[int]) -> tuple:
    """The request fact `_payer_teams` answers from, for these team ids in
    any order."""
    return ("payer_teams", tuple(sorted(set(team_ids))))


#: What a caller may have read with the project, in the same statement: given
#: the select, the select with its columns and joins added, and what to do with
#: the extra columns of each row once the project is read (the rows are one
#: per joined row, the project's columns repeated).
#: Project, plan key, personal owner and plan: the columns `project_with_payer`
#: reads itself, ahead of any a ride-along adds.
_PAYER_COLUMNS = 4

RideAlong = Callable[[Select], tuple[Select, Callable[[Project, list[tuple]], None]]]


async def project_with_payer(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    ride_along: RideAlong | None = None,
) -> Project | None:
    """The project row, read with the team that pays for it and that team's
    plan in one statement, for a caller about to ask who pays (model
    admission, on every model call). What it read answers
    `ProjectRepository.get` and `payers_for_projects` for the rest of the
    request; without request facts it is the project alone. ``ride_along``
    adds a caller's own read to the statement (`RideAlong`), and runs only
    when this call reads the project: a project the request has read already
    is answered from the facts and the caller reads its own rows as before."""
    if not isinstance(session, AsyncSession):
        return await session.get(Project, project_id)
    if session.autoflush:
        await session.flush()
    found, cached = recall(session, ("project", project_id))
    if found:
        return cached
    stmt = (
        select(Project, Team.plan_key, Team.personal_owner_user_id, Plan)
        .outerjoin(Team, and_(Team.id == Project.team_id, Team.deleted_at.is_(None)))
        .outerjoin(Plan, Plan.key == Team.plan_key)
        .where(Project.id == project_id)
    )
    record = None
    if ride_along is not None:
        stmt, record = ride_along(stmt)
    rows = (await session.execute(stmt)).all()
    row = rows[0][:_PAYER_COLUMNS] if rows else None
    project = row[0] if row is not None else None
    if record is not None and project is not None:
        record(project, [tuple(r[_PAYER_COLUMNS:]) for r in rows])
    remember(session, ("project", project_id), project, tables=frozenset({"projects"}))
    if row is not None and project is not None and row[1] is not None:
        _, plan_key, owner, plan = row
        remember(
            session,
            _payer_teams_key([project.team_id]),
            {project.team_id: (plan_key, owner is not None)},
            tables=frozenset({"team"}),
        )
        if plan is not None:
            remember(
                session,
                ("plan_terms", plan.key),
                Terms.of(plan),
                tables=frozenset({"plans"}),
            )
    return project
