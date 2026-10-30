"""How much of its credits a team has used this month, for its members, and a
person's own (#2397, #2233).

Two reads (``usage.report``), in credits that people see as 点: no tokens, and
nothing split by person inside a team's projects (#394). Each names the models
its plan allows, so a member can see what the plan includes.

- ``GET /users/me/credits/usage``: the caller's personal team, split by product
  line (协作, 问答, 写作, 算力), and the shared teams they are in with what is
  left of each.
- ``GET /teams/{teamId}/credits/usage``: a team's month, split into 协作 and
  算力, for any of its members.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.request_deps import AuthUser
from app.api.response import ok
from app.api.write_access import Can
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.db.session import get_db
from app.domain.agent.model_catalog import model_choices
from app.domain.project.services import ProjectService
from app.domain.task.services import TaskService
from app.domain.team.permissions import Permission as TeamPermission
from app.domain.team.protocol import TeamView
from app.domain.team.services import team_service
from app.domain.usage.report import LINES, TEAM_LINES, UsageReport

router = APIRouter(tags=["credits"])


def _plan_models(plan: dict) -> list[str]:
    """The names of the models the plan's tiers allow."""
    tiers = plan.pop("model_tiers")
    return [
        choice["label"]
        for choice in model_choices(None)
        if tiers is None or choice.get("tier") in tiers
    ]


async def _named(db: AsyncSession, report: dict) -> dict:
    """Name the projects, tasks and models the report refers to by id."""
    report["plan"]["models"] = _plan_models(report["plan"])
    tasks = TaskService.of(db)

    # A project can be a spend row and a pack's target at once: gather both
    # lists' ids first and ask once, rather than one ``get`` per row.
    pids = [row["id"] for row in report["projects"]]
    pids += [pack["project_id"] for pack in report["packs"] if pack["project_id"]]
    by_id = await ProjectService(db).get_projects_by_ids(
        list({uuid.UUID(pid) for pid in pids if pid is not None})
    )

    def project_name(pid: str | None) -> str | None:
        if pid is None:
            return None
        project = by_id.get(uuid.UUID(pid))
        return project.name if project else None

    tasks_by_id = await tasks.get_tasks_by_ids(
        [pack["task_id"] for pack in report["packs"] if pack["task_id"] is not None]
    )

    for row in report["projects"]:
        row["name"] = project_name(row["id"])
    for pack in report["packs"]:
        pack["project_name"] = project_name(pack["project_id"])
        task = tasks_by_id.get(pack["task_id"]) if pack["task_id"] is not None else None
        pack["task_name"] = task.name if task else None
    return report


@router.get("/users/me/credits/usage")
async def my_credit_usage(
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    teams = team_service(db)
    personal = await teams.ensure_personal_team(auth_user.user_id)
    report = UsageReport(db)
    out = await _named(
        db, await report.team(personal.id, personal.plan_key, lines=LINES)
    )
    shared = [
        t
        for t in await teams.get_teams_of_user(auth_user.user_id)
        if t.personal_owner_user_id is None
    ]
    out["teams"] = await report.teams_left(shared)
    await db.commit()
    return ok(out)


@router.get("/teams/{teamId}/credits/usage")
async def team_credit_usage(
    team: Annotated[TeamView, Can(TeamPermission.view_members)],
    me: AuthUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    reports = UsageReport(db)
    personal = team.personal_owner_user_id is not None
    report = await reports.team(
        team.id, team.plan_key, lines=LINES if personal else TEAM_LINES
    )
    if personal:
        shared = [
            t
            for t in await team_service(db).get_teams_of_user(me.user_id)
            if t.personal_owner_user_id is None
        ]
        report["teams"] = await reports.teams_left(shared)
    return ok(await _named(db, report))
