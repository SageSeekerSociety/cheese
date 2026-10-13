"""Watching a task's merge on the default branch after it lands.

A PR merged by a task is watched for `task_landing.WATCH_FOR`. Two things are
looked for, both of which nobody has to set up:

- **The checks the repository runs on the merge commit.** One that fails there
  and passed on the commit before it is this merge's: the task's AI teammate is
  told to fix it, the task reopens if it had closed, and its owner hears. A
  check that was failing before the merge too is not this task's, and nothing
  is said. A flaky one is the AI teammate's to tell apart, with the log in front
  of it; the platform does not rerun anything.
- **A deployment that includes the merge commit**, where the forge records
  deployments (a GitHub Actions job with `environment:` does). The task's
  conversation says where it was deployed, or that the deployment failed; a
  failure is told to the owner, but reopens nothing: a deployment carries other
  people's commits too, and nothing says which one broke it.

Nothing is said while either is still running, or when the watch runs out.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.core.errors import UpstreamUnavailableError
from app.core.sentences import listing, say
from app.domain.agent.announce import announce
from app.domain.agent.harness.prompt import main_checks_failed_prompt
from app.domain.agent.platform_notices import (
    EVENT_DEPLOY_DONE,
    EVENT_DEPLOY_FAILED,
    EVENT_MAIN_CHECKS_FAILED,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.delivery.addressing import Event
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.forge import background_quota, quota_serves_background
from app.domain.project.models import Project
from app.domain.review import landing_forge
from app.domain.review.landing_forge import Check, ForgeUnavailable, LandingReads
from app.domain.review.landing_models import ChecksOutcome, DeployOutcome, TaskLanding
from app.domain.review.task_landing import tell_agent
from app.domain.room_task.closing import close_overdue
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic

logger = logging.getLogger("cheesex.review.landing_watch")

#: A completed check that went red. `cancelled` is not one: a newer push to
#: the branch cancels the run it superseded.
_FAILED = frozenset({"failure", "timed_out", "startup_failure"})


async def watch_landings(chat) -> dict:
    """One look at every merge still being watched, and the overdue closings."""

    sessions = chat.session_factory
    async with sessions() as session:
        closed = await close_overdue(session, chat)
        await session.commit()
        ids = list(
            await session.scalars(
                select(TaskLanding.id)
                .where(
                    or_(
                        TaskLanding.checks == ChecksOutcome.watching,
                        TaskLanding.deploy == DeployOutcome.watching,
                    )
                )
                .order_by(TaskLanding.landed_at)
            )
        )
    looked = 0
    errors: list[str] = []
    for landing_id in ids:
        try:
            looked += await _look(chat, landing_id)
        except Exception as exc:  # noqa: BLE001 — one merge must not stop the rest
            errors.append(f"{landing_id}: {exc}")
            logger.warning("landing watch failed for %s", landing_id, exc_info=True)
    return {"closed": len(closed), "landings_looked_at": looked, "errors": errors}


async def _look(chat, landing_id: uuid.UUID) -> int:

    sessions = chat.session_factory
    async with sessions() as session:
        landing = await session.get(TaskLanding, landing_id, with_for_update=True)
        if landing is None:
            return 0
        if datetime.now(UTC) >= landing.watch_until:
            _stop_watching(landing, ChecksOutcome.timeout, DeployOutcome.timeout)
            await session.commit()
            return 0
        task = await session.get(Task, landing.task_id)
        if task is None:
            return 0
        try:
            reads = await landing_forge.reads_for(task.project_id, session)
        except UpstreamUnavailableError:
            # Its forge credentials are gone; they do not come back mid-watch.
            reads = None
        if reads is None:
            _stop_watching(
                landing, ChecksOutcome.unavailable, DeployOutcome.unavailable
            )
            await session.commit()
            return 0
        quota = await background_quota(task.project_id, session)
        pr_number, sha = landing.pr_number, landing.merge_sha
        watching_checks = landing.checks == ChecksOutcome.watching
        watching_deploy = landing.deploy == DeployOutcome.watching
        since = landing.landed_at
    # Asked with no session open, so no connection waits on the forge.
    if not await quota_serves_background(quota):
        return 0
    sha = sha or await reads.merge_sha(pr_number)
    if not sha:
        return 0
    checks = await _judge_checks(reads, sha) if watching_checks else None
    deployed = None
    if watching_deploy:
        try:
            deployed = await reads.deployment(sha, since)
        except ForgeUnavailable:
            deployed = DeployOutcome.unavailable

    async with sessions() as session:
        landing = await session.get(TaskLanding, landing_id, with_for_update=True)
        task = await session.get(Task, landing.task_id) if landing else None
        if landing is None or task is None:
            return 0
        landing.merge_sha = sha
        if checks is not None and landing.checks == ChecksOutcome.watching:
            outcome, failed = checks
            landing.checks = outcome
            if outcome == ChecksOutcome.failed:
                await _checks_failed(session, task, sha, failed)
        if deployed is not None and landing.deploy == DeployOutcome.watching:
            if deployed == DeployOutcome.unavailable:
                landing.deploy = DeployOutcome.unavailable
            else:
                landing.environment = deployed.environment[:255]
                landing.deploy = (
                    DeployOutcome.deployed
                    if deployed.state == "success"
                    else DeployOutcome.failed
                )
                await _deployment_seen(session, task, landing.deploy, deployed)
        await session.commit()
    return 1


def _stop_watching(
    landing: TaskLanding, checks: ChecksOutcome, deploy: DeployOutcome
) -> None:
    if landing.checks == ChecksOutcome.watching:
        landing.checks = checks
    if landing.deploy == DeployOutcome.watching:
        landing.deploy = deploy


async def _judge_checks(
    reads: LandingReads, sha: str
) -> tuple[ChecksOutcome, list[Check]] | None:
    """What the checks on ``sha`` came to, or None while they are still running."""
    runs = await reads.checks(sha)
    if not runs or any(run.status != "completed" for run in runs):
        return None
    red = [run for run in runs if run.conclusion in _FAILED]
    if not red:
        return ChecksOutcome.passed, []
    parent = await reads.parent(sha)
    before = {run.name: run for run in await reads.checks(parent)} if parent else {}
    ours: list[Check] = []
    for run in red:
        earlier = before.get(run.name)
        if earlier is not None and earlier.status != "completed":
            return None
        if earlier is None or earlier.conclusion not in _FAILED:
            ours.append(run)
    if not ours:
        return ChecksOutcome.preexisting, []
    return ChecksOutcome.failed, ours


async def _checks_failed(session, task, sha: str, failed: list[Check]) -> None:
    """The merge broke a check on the default branch: the task's AI teammate
    fixes it, in the task, reopened if it had closed."""

    tasks = TaskService(session)
    reopened = True
    if task.status == TaskStatus.closed:
        await tasks.reopen(task)
    elif task.closing_since is not None:
        # Still being written up: it does not close after all.
        task.closing_since = None
        await tasks.next_step(task)
    else:
        reopened = False
    await tell_agent(
        session,
        task,
        main_checks_failed_prompt(
            title=task.title,
            task_id=task.id,
            sha=sha,
            failed=[(run.name, run.url) for run in failed],
            reopened=reopened,
        ),
    )
    agent = f"<@{await _seat(session, task)}>"
    names = listing([run.name for run in failed])
    await announce(
        session,
        place_id=task.room_id,
        task_id=task.id,
        content=say("mainChecksFailedReopened", checks=names, agent=agent)
        if reopened
        else say("mainChecksFailed", checks=names, agent=agent),
        meta=notice(
            EVENT_MAIN_CHECKS_FAILED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail="\n".join(f"{run.name} {run.url}".rstrip() for run in failed),
        ),
        points_at=Event(owner=task.owner_handle),
    )


async def _deployment_seen(session, task, outcome: DeployOutcome, deployed) -> None:

    environment = deployed.environment
    if outcome == DeployOutcome.deployed:
        await announce(
            session,
            place_id=task.room_id,
            task_id=task.id,
            content=say("deployedTo", environment=environment),
            meta=notice(
                EVENT_DEPLOY_DONE,
                severity=SEVERITY_INFO,
                who=WHO_PLATFORM,
                detail=deployed.url or None,
            ),
        )
        return
    await announce(
        session,
        place_id=task.room_id,
        task_id=task.id,
        content=say("deployFailedTo", environment=environment),
        meta=notice(
            EVENT_DEPLOY_FAILED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail=deployed.url or None,
        ),
        points_at=Event(owner=task.owner_handle),
    )


async def _seat(session, task) -> str:

    project = await session.get(Project, task.project_id)
    room = await session.get(Topic, task.room_id)
    agent = await AgentInstanceService(session).for_task(
        project, room, task.agent_handle
    )
    return agent_instance_handle(agent.instance_id)
