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

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.auth.checker import require_auth_user, require_permission
from app.auth.core import Action, AuthUserInfo, Resource
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.db.session import get_db
from app.domain.agent_instance.configuration import model_choices
from app.domain.project.services import ProjectService
from app.domain.task.services import TaskService
from app.domain.team.services import team_service
from app.domain.usage.report import LINES, UsageReport

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
    projects, tasks = ProjectService(db), TaskService.of(db)
    names: dict[str, str | None] = {}

    async def project_name(pid: str | None) -> str | None:
        if pid is None:
            return None
        if pid not in names:
            project = await projects.get(uuid.UUID(pid))
            names[pid] = project.name if project else None
        return names[pid]

    for row in report["projects"]:
        row["name"] = await project_name(row["id"])
    for pack in report["packs"]:
        pack["project_name"] = await project_name(pack["project_id"])
        task = (
            await tasks.get_task(pack["task_id"])
            if pack["task_id"] is not None
            else None
        )
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
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    db: AsyncSession = Depends(get_db),
    auth_user: AuthUserInfo = require_permission(
        Action.READ, Resource.TEAM_MEMBERSHIP, "teamId"
    ),
) -> dict:
    _ = auth_user
    team = await team_service(db).get_team(team_id)
    if team is None:
        raise NotFoundError(say("teamNotFound", team=team_id))
    report = await UsageReport(db).team(team.id, team.plan_key)
    return ok(await _named(db, report))
