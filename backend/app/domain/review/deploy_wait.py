"""Tasks waiting for their deploy: released when it is live, marked when main's
CI is red and holds it back.

A task whose last delivery merged in a project that waits for its deploy
(`deploy_scope`) stays open with `Task.deploying_since` set
(`task_landing.delivery_landed`). Two things move it:

* `ship` — the dev deploy, once its release is live, hands over every commit
  between the release it replaced and the one it put live
  (`scripts/ship_tasks.py`, run by `.github/workflows/deploy-dev.yml`, the same
  list `feedback.shipping` reads). A waiting task is live when its PR is one of
  those commits (the squash title ends in `(#N)`), or when it merged before
  the oldest of them — main is linear, so such a merge was already in the
  release this one replaced, and its own report was missed. Each such task is
  written up and closed (`task_landing.deploy_landed`).
* `mark_blocked` — on a clock, from the CI runs the platform already records
  for its own repository (`ci_activity`): when the newest finished Required CI
  run on main since the task merged failed, nothing will deploy until main is
  green again, and the task shows that run (`Task.deploy_blocked_url`). A green
  run clears it. Nothing here gives up on a task: a main that stays red keeps
  it waiting, in plain sight.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.domain.ci_activity.models import CiWorkflowRun
from app.domain.review.deploy_scope import (
    deploy_repositories,
    projects_of,
    repository_of_url,
)
from app.domain.review.models import DeployReport
from app.domain.review.ports import RecordInstruction, TaskTeammate
from app.domain.review.task_landing import deploy_landed
from app.domain.room_task.lock_order import lock_task_for_change
from app.domain.room_task.models import Task

logger = logging.getLogger(__name__)

_PR = re.compile(r"\(#(\d+)\)\s*$")

#: The path of the workflow whose green on main lets the deploy go
#: (`ci_activity.board.REQUIRED_CI_PATH`, read here without the board).
REQUIRED_CI_PATH = ".github/workflows/required-ci.yml"

#: A task's merge is seen by the platform a little after its push to main (a
#: poll, an accept that waited on the forge); main's run for it can be created
#: before `deploying_since`.
_MERGE_SEEN_LATE = timedelta(minutes=10)

_RED = ("failure", "timed_out", "startup_failure")


def pull_request_of(message: str) -> int | None:
    """The PR a squash commit came from, read off its title's `(#N)`."""
    match = _PR.search(message.split("\n", 1)[0])
    return int(match.group(1)) if match else None


def _merged_at(commit: dict) -> datetime | None:
    raw = commit.get("merged_at")
    if not raw:
        return None
    moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def _waiting_ids(
    session: AsyncSession, projects: set[uuid.UUID], commits: list[dict]
) -> list[uuid.UUID]:
    """The tasks of ``projects`` the shipped ``commits`` put live (module
    docstring)."""
    if not projects:
        return []
    prs = {n for c in commits if (n := pull_request_of(c.get("message", "")))}
    times = [t for c in commits if (t := _merged_at(c)) is not None]
    live = [Task.pr_number.in_(prs)] if prs else []
    if times:
        live.append(Task.accepted_at < min(times))
    if not live:
        return []
    return list(
        await session.scalars(
            select(Task.id).where(
                Task.project_id.in_(projects),
                Task.deploying_since.is_not(None),
                or_(*live),
            )
        )
    )


async def ship(
    sessions: SessionFactory,
    commits: list[dict],
    *,
    repository_url: str,
    sha: str,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
) -> list[uuid.UUID]:
    """A deploy of ``repository_url`` put ``sha`` live, shipping ``commits``
    (`[{"sha", "message", "merged_at"}]`, oldest first). Keeps the report and
    releases every task it put live; returns their ids.

    Each task is released in a transaction of its own: one that fails is
    logged and left waiting for the next deploy, and holds up no other."""
    repo = repository_of_url(repository_url)
    async with sessions() as session:
        session.add(
            DeployReport(repo=repo, sha=sha[:64], reported_at=datetime.now(UTC))
        )
        ids = (
            await _waiting_ids(session, await projects_of(session, repo), commits)
            if repo in deploy_repositories()
            else []
        )
        await session.commit()
    released = []
    for task_id in ids:
        try:
            async with sessions() as session:
                task = await lock_task_for_change(session, task_id)
                if task is not None and await deploy_landed(
                    session,
                    task,
                    record_instruction=record_instruction,
                    task_teammate=task_teammate,
                ):
                    released.append(task_id)
                await session.commit()
        except Exception:  # one task must not stop the rest
            logger.warning("could not release task %s", task_id, exc_info=True)
    return released


async def mark_blocked(sessions: SessionFactory) -> int:
    """Show, on every task waiting for its deploy, the red run of main's
    Required CI that holds it back, or clear it once main is green (module
    docstring). Only the platform's own repository's runs are recorded, so
    only tasks there can be marked. Returns how many tasks changed."""
    repo = settings.platform_repository
    async with sessions() as session:
        projects = await projects_of(session, repo)
        if not projects:
            return 0
        waiting = list(
            await session.scalars(
                select(Task).where(
                    Task.project_id.in_(projects), Task.deploying_since.is_not(None)
                )
            )
        )
        if not waiting:
            return 0
        oldest = min(t.deploying_since for t in waiting if t.deploying_since)
        runs = (
            await session.execute(
                select(
                    CiWorkflowRun.created_at,
                    CiWorkflowRun.conclusion,
                    CiWorkflowRun.html_url,
                )
                .where(
                    CiWorkflowRun.repo == repo,
                    CiWorkflowRun.workflow_path == REQUIRED_CI_PATH,
                    CiWorkflowRun.event == "push",
                    CiWorkflowRun.head_branch == "main",
                    CiWorkflowRun.status == "completed",
                    CiWorkflowRun.conclusion.is_not(None),
                    CiWorkflowRun.conclusion != "cancelled",
                    CiWorkflowRun.created_at >= oldest - _MERGE_SEEN_LATE,
                )
                .order_by(CiWorkflowRun.created_at.desc())
            )
        ).all()
        changed = 0
        for task in waiting:
            blocked = _blocked_by(task, runs)
            if blocked != task.deploy_blocked_url:
                task.deploy_blocked_url = blocked
                changed += 1
        await session.commit()
    return changed


def _blocked_by(task: Task, runs) -> str | None:
    """The red run holding ``task`` back: the newest finished run on main since
    it merged, when that one failed. ``runs`` is newest first."""
    since = (task.deploying_since or datetime.now(UTC)) - _MERGE_SEEN_LATE
    for created_at, conclusion, html_url in runs:
        if created_at < since:
            return None
        if conclusion in _RED:
            return (html_url or "")[:500] or None
        return None
    return None
