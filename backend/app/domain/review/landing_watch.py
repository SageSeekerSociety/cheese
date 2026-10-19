"""Watching a task's merge on the default branch after it lands.

A PR merged by a task is watched for `task_landing.WATCH_FOR`. Two things are
looked for, both of which nobody has to set up:

- **The checks the repository runs on the merge commit.** One that fails there
  and passed on the commit before it is this merge's: the task's AI teammate is
  told to fix it, the task reopens if it had closed, and its owner hears. A
  check that was failing before the merge too is not this task's, and nothing
  is said. A flaky one is the AI teammate's to tell apart, with the log in front
  of it; the platform does not rerun anything.

  A repository may gate main in batches instead (#4249): one run covers every
  commit since the last green one, so the commits inside a batch have no run of
  their own — their push was superseded, and their check runs are cancelled.
  There the failing files in the red job's log are matched against the files
  each commit of the batch changed: when they point at one commit, only that
  task hears; when they point at none or at several, every task in the batch
  hears and is asked to rule itself out. A later green batch that covers those
  commits takes the blame back (`_withdraw_passed`).

  A batch long enough for it is searched instead of guessed at:
  `culprit_search` reruns the failed tests on commits in the middle of the
  batch, one probe per sweep, and names the first commit they fail on. Only
  that commit's task hears. A search that cannot name one says so with its
  reason, and the batch is told to rule itself out as before — a commit is
  never picked to have an answer.
- **A deployment that includes the merge commit**, where the forge records
  deployments (a GitHub Actions job with `environment:` does). The task's
  conversation says where it was deployed, or that the deployment failed; a
  failure is told to the owner, but reopens nothing: a deployment carries other
  people's commits too, and nothing says which one broke it.

Nothing is said while either is still running, or when the watch runs out.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.core.errors import UpstreamUnavailableError
from app.core.sentences import listing, say
from app.domain.addressing import Event
from app.domain.identity.handles import agent_instance_handle
from app.domain.notice_meta import (
    EVENT_DEPLOY_DONE,
    EVENT_DEPLOY_FAILED,
    EVENT_MAIN_CHECKS_FAILED,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
from app.domain.project.forge import background_quota, quota_serves_background
from app.domain.review import culprit_bisect, culprit_search, landing_forge
from app.domain.review.landing_forge import (
    BatchCommits,
    Check,
    ForgeUnavailable,
    LandingReads,
    gate_run,
)
from app.domain.review.landing_models import ChecksOutcome, DeployOutcome, TaskLanding
from app.domain.review.ports import (
    Announce,
    Chat,
    ProbeRunner,
    RecordInstruction,
    SweepCheckouts,
    TaskTeammate,
)
from app.domain.review.room_notices import tell_room_undelivered
from app.domain.review.task_landing import go_on_while_working, handed_in, tell_agent
from app.domain.room_task.closing import close_overdue
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import TaskService

logger = logging.getLogger("cheesex.review.landing_watch")

#: A completed check that went red. `cancelled` is not one: a newer push to
#: the branch cancels the run it superseded.
_FAILED = frozenset(
    {"failure", "timed_out", "startup_failure", "action_required", "stale"}
)

#: A completed check that passed, as `merge_state` reads it.
_PASS = frozenset({"success", "neutral", "skipped"})

#: How many merges are looked at at once. What a look mostly waits on is the
#: forge, asked with no session open (`_look`) — but it does hold a connection
#: while it reads and writes, so this is a bound on pooled connections as well
#: as on forge calls. Eight of them still leave a worker's 20 + 15 connections
#: (`core.config`, at one worker) to every other request.
AT_ONCE = 8

#: How many commits of one batch are read for attribution. Past that the batch
#: is attributed to nobody in particular rather than to whoever got looked at:
#: a forge call per commit, and a wrong blame costs more than a wide one.
BATCH_COMMITS = 20

#: How long a landing without a merge commit holds back the verdicts of its
#: project's red batches.
UNREAD_FOR = timedelta(minutes=10)

#: What a prompt lists before it says how much it left out.
PROMPT_COMMITS = 10
PROMPT_PATHS = 10


@dataclass(frozen=True, slots=True)
class BatchCommit:
    sha: str
    subject: str


@dataclass(frozen=True, slots=True)
class Batch:
    """The run that covered a merge commit, and who in it may have broken it."""

    #: The commit the batch's gate reported on.
    head: str
    #: How many commits the batch covers, when the forge could say.
    total: int
    #: False when some of them were not read: `total` is then a lower bound,
    #: and no failure may be pinned on one commit.
    complete: bool
    commits: tuple[BatchCommit, ...]
    #: The files the red jobs' test failures or static diagnostics named.
    failed_paths: tuple[str, ...]
    #: The one commit those files point at; None when they point at none, at
    #: several, or at a commit the forge would not let us read whole.
    blamed: str | None
    #: The commit the batch starts after — the last green batch's end — when
    #: the forge could say where the batch starts at all.
    base: str | None = None
    #: ``blamed`` was named by rerunning the failed tests on commits of the
    #: batch (`culprit_search`), not by the files those tests name.
    by_bisect: bool = False
    #: Why a culprit search ran and named nobody; "" when none ran, or one did
    #: and named a commit.
    bisect: str = ""


@dataclass(frozen=True, slots=True)
class Judgement:
    outcome: ChecksOutcome
    failed: tuple[Check, ...]
    batch: Batch | None = None


async def watch_landings(
    chat: Chat,
    announce: Announce,
    *,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    machines: SweepCheckouts | None = None,
    probes: ProbeRunner | None = None,
) -> dict:
    """One look at every merge still being watched, the tasks being written up
    whose AI teammate has gone on working, and the overdue closings.

    ``machines`` is used only by the overdue closings: a task closed here has
    its checkouts removed on the machines holding it (`ports.SweepCheckouts`).
    """

    sessions = chat.session_factory
    try:
        # A search is read by every merge of its batch while that watch lasts;
        # past that only the table holds it, and nothing ever reads it again.
        await culprit_search.purge(sessions)
    except Exception:  # a cleanup that fails must not stop the watch
        logger.warning("purging culprit searches failed", exc_info=True)
    withdrawn = await _withdraw_passed(sessions, record_instruction=record_instruction)
    gone_on = await go_on_while_working(
        sessions,
        record_instruction=record_instruction,
        task_teammate=task_teammate,
    )
    async with sessions() as session:
        closed = await close_overdue(session, chat, machines=machines)
        for overdue in closed:
            await tell_room_undelivered(session, overdue, announce=announce)
        await session.commit()
        watching = list(
            await session.execute(
                select(TaskLanding.id, TaskLanding.task_id)
                .where(
                    or_(
                        TaskLanding.checks == ChecksOutcome.watching,
                        TaskLanding.deploy == DeployOutcome.watching,
                    )
                )
                .order_by(TaskLanding.landed_at)
            )
        )
    # One chain of looks per task, the chains gathered: a task that delivers in
    # steps has a landing per merge, and two looks at the same task must not
    # overlap — the second would read the task as closed before the first
    # reopened it, and reopen it a second time. Different tasks have nothing to
    # say to each other, and their looks go at once.
    chains: dict[uuid.UUID, list[uuid.UUID]] = {}
    for landing_id, task_id in watching:
        chains.setdefault(task_id, []).append(landing_id)
    slots = asyncio.Semaphore(AT_ONCE)
    # One forge read per sweep, however many looks need it: the commits of a
    # batch, their files and the failed tests in its logs are the same answer
    # for every landing it covers.
    memo: dict = {"probes": probes}
    looked_at = {
        landing_id: seen
        for chain in await asyncio.gather(
            *(
                _look_at_one_task(
                    chat,
                    landing_ids,
                    announce=announce,
                    record_instruction=record_instruction,
                    task_teammate=task_teammate,
                    slots=slots,
                    memo=memo,
                )
                for landing_ids in chains.values()
            )
        )
        for landing_id, seen in chain.items()
    }
    return {
        "gone_on": len(gone_on),
        "closed": len(closed),
        "withdrawn": withdrawn,
        # Reported in `landed_at` order, the order the looks went in before they
        # were gathered.
        "landings_looked_at": sum(
            looked_at[landing_id][0] for landing_id, _ in watching
        ),
        "errors": [
            looked_at[landing_id][1]
            for landing_id, _ in watching
            if looked_at[landing_id][1] is not None
        ],
    }


async def _look_at_one_task(
    chat: Chat,
    landing_ids: list[uuid.UUID],
    *,
    announce: Announce,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    slots: asyncio.Semaphore,
    memo: dict,
) -> dict[uuid.UUID, tuple[int, str | None]]:
    """One task's merges, looked at one after the other, each by `_look_at`:
    what its second look reads is what the first wrote, which is what it read
    when the looks went one merge at a time."""

    looked_at = {}
    for landing_id in landing_ids:
        looked_at[landing_id] = await _look_at(
            chat,
            landing_id,
            announce=announce,
            record_instruction=record_instruction,
            task_teammate=task_teammate,
            slots=slots,
            memo=memo,
        )
    return looked_at


async def _look_at(
    chat: Chat,
    landing_id: uuid.UUID,
    *,
    announce: Announce,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    slots: asyncio.Semaphore,
    memo: dict,
) -> tuple[int, str | None]:
    """One merge's look, its failure kept to itself: what this returns is what
    the sweep reports in `landing_id` order, whether the look raised or not."""
    async with slots:
        try:
            looked = await _look(
                chat,
                landing_id,
                announce=announce,
                record_instruction=record_instruction,
                task_teammate=task_teammate,
                memo=memo,
            )
        except Exception as exc:  # one merge must not stop the rest
            logger.warning("landing watch failed for %s", landing_id, exc_info=True)
            return 0, f"{landing_id}: {exc}"
        return looked, None


async def _look(
    chat,
    landing_id: uuid.UUID,
    *,
    announce: Announce,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
    memo: dict,
) -> int:

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
        project_id = task.project_id
        quota = await background_quota(project_id, session)
        pr_number, sha = landing.pr_number, landing.merge_sha
        watching_checks = landing.checks == ChecksOutcome.watching
        watching_deploy = landing.deploy == DeployOutcome.watching
        since = landing.landed_at
    # Asked with no session open, so no connection waits on the forge.
    if not await quota_serves_background(quota):
        return 0

    candidate = reads

    async def policy():
        await candidate.configure()
        return candidate

    reads = await _once(memo, (project_id, "forge-policy"), policy)
    sha = sha or await reads.merge_sha(pr_number)
    if not sha:
        return 0
    checks = (
        await _judge_checks(reads, sha, project_id=project_id, memo=memo)
        if watching_checks
        else None
    )
    if (
        checks is not None
        and checks.outcome == ChecksOutcome.failed
        and checks.batch is not None
    ):
        batch = await _search_for_culprit(
            chat, reads, project_id, checks.batch, memo=memo
        )
        # None while a probe is out: the batch is red, but which task it is
        # belongs to is what this sweep is still finding out.
        checks = None if batch is None else replace(checks, batch=batch)
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
            outcome = checks.outcome
            batch = checks.batch
            if (
                outcome == ChecksOutcome.failed
                and batch is not None
                and batch.blamed not in (None, sha)
            ):
                hears = await _line_will_hear(session, batch.blamed, task.project_id)
                if hears is None:
                    # The blamed line may be one whose first look has not
                    # written its merge commit: decide on the next tick.
                    await session.commit()
                    return 1
                if hears:
                    # The batch is red, but its failed tests point at another
                    # commit in it: that line's own look wakes it, and this
                    # one stays quiet.
                    outcome = ChecksOutcome.preexisting
                else:
                    # Nobody is watching the blamed commit (a push made by
                    # hand, a merge whose watch ran out): staying quiet here
                    # would leave the red batch with no one told. Every line
                    # in the batch hears and rules itself out.
                    batch = replace(batch, blamed=None)
            landing.checks = outcome
            if outcome == ChecksOutcome.failed:
                await _checks_failed(
                    session,
                    task,
                    sha,
                    list(checks.failed),
                    batch=batch,
                    announce=announce,
                    record_instruction=record_instruction,
                    task_teammate=task_teammate,
                )
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
                await _deployment_seen(
                    session, task, landing.deploy, deployed, announce=announce
                )
        await session.commit()
    return 1


async def _line_will_hear(session, blamed: str, project_id) -> bool | None:
    """Whether a landing still waiting on the checks of ``blamed`` exists, so
    that its own look will tell its task. None while a landing has no merge
    commit written yet (its first look writes it): the blamed line may be that
    one, so the answer waits for the next tick — but only for a landing of this
    project that is younger than `UNREAD_FOR`, so one whose merge commit never
    resolves cannot hold every red batch in the project."""
    now = datetime.now(UTC)
    live = TaskLanding.watch_until > now
    if await session.scalar(
        select(TaskLanding.id)
        .where(
            live,
            TaskLanding.merge_sha == blamed,
            TaskLanding.checks.in_((ChecksOutcome.watching, ChecksOutcome.failed)),
        )
        .limit(1)
    ):
        return True
    unread = await session.scalar(
        select(TaskLanding.id)
        .where(
            live,
            TaskLanding.merge_sha.is_(None),
            TaskLanding.checks == ChecksOutcome.watching,
            TaskLanding.landed_at > now - UNREAD_FOR,
            TaskLanding.task_id.in_(
                select(Task.id).where(Task.project_id == project_id)
            ),
        )
        .limit(1)
    )
    return None if unread is not None else False


def _stop_watching(
    landing: TaskLanding, checks: ChecksOutcome, deploy: DeployOutcome
) -> None:
    if landing.checks == ChecksOutcome.watching:
        landing.checks = checks
    if landing.deploy == DeployOutcome.watching:
        landing.deploy = deploy


async def _once(memo: dict, key: tuple, make):
    """One forge answer per sweep: every look that needs the same one waits on
    the same call instead of asking again."""
    task = memo.get(key)
    if task is None:
        task = asyncio.ensure_future(make())
        memo[key] = task
    return await task


def _checks(reads: LandingReads, project_id, sha: str, memo: dict):
    return _once(memo, (project_id, "checks", sha), lambda: reads.checks(sha))


def _green(reads: LandingReads, project_id, memo: dict):
    return _once(memo, (project_id, "green"), reads.main_green)


def _covered(reads: LandingReads, project_id, sha: str, anchor: str, memo: dict):
    """Whether the green batch `main-green` ends covers ``sha``."""
    return _once(
        memo,
        (project_id, "covered", sha, anchor),
        lambda: reads.is_ancestor(sha, anchor),
    )


def _red_checks(runs: list[Check]) -> list[Check]:
    """The checks that are red, one per name.

    A re-run leaves several runs of one name: as `merge_state._outcome` reads
    them, any pass wins, a run still going means nothing is decided, and the
    remaining red is the answer."""
    by_name: dict[str, list[Check]] = {}
    for run in runs:
        by_name.setdefault(run.name, []).append(run)
    red: list[Check] = []
    for group in by_name.values():
        if any(run.status == "completed" and run.conclusion in _PASS for run in group):
            continue
        if any(run.status != "completed" for run in group):
            continue
        for run in group:
            if run.conclusion in _FAILED:
                red.append(run)
                break
    return red


def _reds_elsewhere(runs: list[Check], gate: Check | None) -> list[Check]:
    """Red checks on a commit that the batch gate's own run does not own.

    A completed pass for a name settles it; a completed red blocks even while
    a re-run of it is in flight, because that red has not been settled yet.
    """
    by_name: dict[str, list[Check]] = {}
    for run in runs:
        by_name.setdefault(run.name, []).append(run)
    red: list[Check] = []
    for group in by_name.values():
        others = [
            run
            for run in group
            if run is not gate
            and (gate is None or run.name not in gate.required)
            and (
                gate is None
                or run.suite is None
                or gate.suite is None
                or run.suite != gate.suite
            )
        ]
        if not others:
            continue
        if any(run.status == "completed" and run.conclusion in _PASS for run in others):
            continue
        red.extend(
            run
            for run in others
            if run.status == "completed" and run.conclusion in _FAILED
        )
    return red


async def _judge_checks(
    reads: LandingReads, sha: str, *, project_id, memo: dict
) -> Judgement | None:
    """What the checks for the merge commit ``sha`` came to, or None while they
    are still being decided."""
    own = await _checks(reads, project_id, sha, memo)
    # Queued is as unfinished as in progress: the batch's first look often
    # finds `scope` and `fail-fast` completed while every product job, and the
    # gate itself, still sit in the queue on the shared pool.
    if any(run.status != "completed" for run in own):
        return None
    red = _red_checks(own)
    gate = gate_run(own, reads.required_checks)
    anchor = await _green(reads, project_id, memo)
    if gate is not None and gate.conclusion in _FAILED:
        if anchor and await _covered(reads, project_id, sha, anchor, memo):
            # A later batch went green over this commit: that red is settled.
            return Judgement(ChecksOutcome.passed, ())
        parent = await reads.parent(sha)
        if anchor is None and await _gate_ran(reads, project_id, parent, memo):
            # No batch mark exists and the commit before it was gated on its
            # own: this repository checks each merge as it lands, and the old
            # rule still holds.
            return await _judge_merge(
                reads, sha, red, parent=parent, project_id=project_id, memo=memo
            )
        # The batched gate ran on this very commit and went red.
        return await _judge_batch(
            reads, sha, head=sha, head_checks=own, project_id=project_id, memo=memo
        )
    if gate is not None and gate.conclusion in _PASS:
        # Its own batch passed; a red left here is another workflow's, and the
        # old rule judges it.
        if red:
            return await _judge_merge(
                reads,
                sha,
                red,
                parent=await reads.parent(sha),
                project_id=project_id,
                memo=memo,
            )
        return Judgement(ChecksOutcome.passed, ())
    if gate is None:
        if red:
            return await _judge_merge(
                reads,
                sha,
                red,
                parent=await reads.parent(sha),
                project_id=project_id,
                memo=memo,
            )
        if any(
            run.status == "completed" and run.conclusion != "cancelled" for run in own
        ):
            # A repository with no batch gate runs each merge on its own, and
            # this one finished green.
            return Judgement(ChecksOutcome.passed, ())
    # The gate was cancelled (a newer push superseded this commit's run) or
    # never existed: the commit landed inside a batch, and the gate that covers
    # it is the newest one after it — unless a green batch already moved past.
    if anchor and await _covered(reads, project_id, sha, anchor, memo):
        if red:
            return await _judge_merge(
                reads,
                sha,
                red,
                parent=await reads.parent(sha),
                project_id=project_id,
                memo=memo,
            )
        return Judgement(ChecksOutcome.passed, ())
    covering = await _once(
        memo,
        (project_id, "cover", sha, anchor),
        lambda: reads.covering_run(sha, anchor=anchor),
    )
    if covering is None:
        if red:
            return await _judge_merge(
                reads,
                sha,
                red,
                parent=await reads.parent(sha),
                project_id=project_id,
                memo=memo,
            )
        return None
    head, head_checks = covering
    covering_gate = gate_run(head_checks, reads.required_checks)
    if (
        covering_gate is None
        or covering_gate.conclusion is None
        or covering_gate.conclusion == "cancelled"
    ):
        if red:
            return await _judge_merge(
                reads,
                sha,
                red,
                parent=await reads.parent(sha),
                project_id=project_id,
                memo=memo,
            )
        return None
    if covering_gate.conclusion not in _FAILED:
        if red:
            return await _judge_merge(
                reads,
                sha,
                red,
                parent=await reads.parent(sha),
                project_id=project_id,
                memo=memo,
            )
        return Judgement(ChecksOutcome.passed, ())
    return await _judge_batch(
        reads, sha, head=head, head_checks=head_checks, project_id=project_id, memo=memo
    )


async def _gate_ran(
    reads: LandingReads, project_id, sha: str | None, memo: dict
) -> bool:
    """Whether ``sha`` was checked on its own, rather than in a batch."""
    if not sha:
        return False
    gate = gate_run(await _checks(reads, project_id, sha, memo), reads.required_checks)
    return (
        gate is not None
        and gate.status == "completed"
        and gate.conclusion != "cancelled"
    )


async def _judge_merge(
    reads: LandingReads,
    sha: str,
    red: list[Check],
    *,
    parent: str | None,
    project_id,
    memo: dict,
) -> Judgement | None:
    """The rule for a repository that checks each merge on its own: a check
    that failed on the merge and was failing before it too is not this task's."""
    before = {}
    if parent:
        before = {
            run.name: run for run in await _checks(reads, project_id, parent, memo)
        }
    ours: list[Check] = []
    for run in red:
        earlier = before.get(run.name)
        if earlier is not None and earlier.status != "completed":
            return None
        if earlier is None or earlier.conclusion not in _FAILED:
            ours.append(run)
    if not ours:
        return Judgement(ChecksOutcome.preexisting, ())
    return Judgement(ChecksOutcome.failed, tuple(ours))


async def _judge_batch(
    reads: LandingReads,
    sha: str,
    *,
    head: str,
    head_checks: list[Check],
    project_id,
    memo: dict,
) -> Judgement:
    """The batched gate on ``head`` went red: who in the batch it covers may
    have broken it."""
    red = tuple(_red_checks(head_checks))
    if not red:
        return Judgement(ChecksOutcome.passed, ())
    anchor = await _green(reads, project_id, memo)
    base = anchor
    commits = None
    if anchor:
        commits = await _once(
            memo,
            (project_id, "between", anchor, head),
            lambda: reads.commits_between(anchor, head),
        )
    if commits is None:
        commits, base = await _walk_back(reads, project_id, head, memo)
    paths = tuple(
        await _once(memo, (project_id, "paths", head), lambda: reads.failed_paths(head))
    )
    return Judgement(
        ChecksOutcome.failed,
        red,
        Batch(
            head=head,
            total=commits.total,
            complete=commits.complete,
            commits=tuple(
                BatchCommit(change.sha, change.subject) for change in commits.changes
            ),
            failed_paths=paths,
            blamed=_blame(paths, commits),
            base=base,
        ),
    )


def _searchable(batch: Batch) -> bool:
    """Whether bisecting this batch can say anything the files did not.

    Only a batch read whole gives the search an ordered list to narrow.
    The caller then checks that its paths come from actual test failures;
    static diagnostics give a test probe nothing to judge.
    """
    return (
        batch.complete
        and len(batch.commits) >= culprit_bisect.MIN_COMMITS
        and bool(batch.failed_paths)
    )


async def _search_for_culprit(
    chat: Chat, reads: LandingReads, project_id, batch: Batch, *, memo: dict
) -> Batch | None:
    """Advance the batch's culprit search one step and take its answer.

    None while a probe is out — the batch is red, and which task it belongs to
    is what the next sweeps are finding out. A search that cannot run at all
    (no project execution resource) leaves the batch exactly as the file
    match left it, so a repository without one is attributed as it always was.
    """
    if memo.get("probes") is None or not _searchable(batch):
        return batch

    # A test rerun cannot judge a static-check failure, even in a test file.
    tests = tuple(
        await _once(
            memo,
            (project_id, "test_paths", batch.head),
            lambda: reads.failed_paths(batch.head, tests_only=True),
        )
    )
    if not tests or tests != batch.failed_paths:
        return batch

    async def step():
        return await culprit_search.advance(
            chat.session_factory,
            memo["probes"],
            project_id=project_id,
            head=batch.head,
            base=batch.base,
            commits=[commit.sha for commit in batch.commits],
            tests=list(tests),
        )

    try:
        # One search per batch per sweep, however many of its merges are looked
        # at: they share the same commits, and each costs a forge call.
        finding = await _once(memo, (project_id, "culprit", batch.head), step)
    except Exception:  # a broken search must not leave the red unreported
        logger.warning("culprit search failed for %s", batch.head, exc_info=True)
        return batch
    if finding.state == "searching":
        return None
    if finding.state == "located":
        return replace(batch, blamed=finding.culprit, by_bisect=True)
    if finding.state == "unlocated":
        return replace(batch, blamed=None, by_bisect=False, bisect=finding.reason)
    return batch


async def _walk_back(
    reads: LandingReads, project_id, head: str, memo: dict
) -> tuple[BatchCommits, str | None]:
    """The batch's commits when the green mark cannot say where it starts: back
    from ``head`` until a batch gate that finished green, which is the base.

    Oldest first, like `commits_between`: the batch reads the same way whether
    the mark or the walk found its start."""
    changes = []
    current = head
    base: str | None = None
    complete = False
    while current and len(changes) < BATCH_COMMITS:
        change = await _once(
            memo,
            (project_id, "commit", current),
            lambda sha=current: reads.commit_change(sha),
        )
        if change is None:
            break
        changes.append(change)
        parent = await reads.parent(current)
        if not parent:
            complete = True
            break
        gate = gate_run(
            await _checks(reads, project_id, parent, memo), reads.required_checks
        )
        if gate is not None and gate.status == "completed":
            if gate.conclusion in _PASS:
                base = parent
                complete = True
                break
        current = parent
    return (
        BatchCommits(
            total=len(changes), changes=tuple(reversed(changes)), complete=complete
        ),
        base,
    )


def _blame(tests: tuple[str, ...], commits: BatchCommits | None) -> str | None:
    """The one commit the failed tests point at, or None when they point at no
    commit, at more than one, or at files the forge did not list whole."""
    if not tests or commits is None:
        return None
    if not commits.complete or not all(c.complete for c in commits.changes):
        return None
    owners: set[str] | None = None
    for test in tests:
        matched = {c.sha for c in commits.changes if _names(test, c.files)}
        if len(matched) != 1:
            return None
        owners = matched if owners is None else owners & matched
        if not owners:
            return None
    return next(iter(owners)) if owners and len(owners) == 1 else None


#: Extensions that name the same kind of code. `test_foo.py` is about
#: `foo.py` in this monorepo; a `.test.ts` next to a `.py` of the same name is
#: a coincidence, and matching it would blame the wrong line.
_FAMILIES = (
    frozenset({"py", "pyi"}),
    frozenset({"ts", "tsx", "js", "jsx", "vue"}),
)


def _names(test_path: str, files: tuple[str, ...]) -> bool:
    """Whether a failing test file is one this commit's changes are about."""
    want = test_path.removeprefix("./")
    for path in files:
        changed = path.removeprefix("./")
        if (
            changed == want
            or changed.endswith("/" + want)
            or want.endswith("/" + changed)
        ):
            return True
    stem = _stem(want)
    if not stem:
        return False
    family = _family(want)
    return any(_family(path) == family and _stem(path) == stem for path in files)


def _family(path: str) -> frozenset[str]:
    extension = path.rsplit(".", 1)[-1].lower()
    for family in _FAMILIES:
        if extension in family:
            return family
    return frozenset({extension})


def _stem(path: str) -> str:
    """`backend/tests/unit/test_landing_watch.py` and
    `backend/app/domain/review/landing_watch.py` share `landing_watch`: a test
    file failing is about the module it tests."""
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = name.removeprefix("test_").removesuffix("_test")
    return name.removesuffix(".test")


async def _checks_failed(
    session,
    task,
    sha: str,
    failed: list[Check],
    *,
    batch: Batch | None = None,
    announce: Announce,
    record_instruction: RecordInstruction | None,
    task_teammate: TaskTeammate | None,
) -> None:
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
            batch=batch,
        ),
        record_instruction=record_instruction,
    )
    agent = f"<@{await _seat(session, task, task_teammate=task_teammate)}>"
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


async def _deployment_seen(
    session, task, outcome: DeployOutcome, deployed, *, announce: Announce
) -> None:

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


async def _seat(session, task, *, task_teammate: TaskTeammate | None) -> str:
    """The seat the task's own AI teammate sits under — the handle a line about
    that task points at. A task whose project or room is gone has no such seat,
    and a line that names nobody is what there is to say (`""`)."""

    teammate = await handed_in(task_teammate)(
        session, task.project_id, task.room_id, task.agent_handle
    )
    if teammate is None:
        return ""
    instance_id, _handle = teammate
    return agent_instance_handle(instance_id)


# 这一段原先和 `agent` 的其它提示词一起放在 `agent/harness/prompt.py`，但那个包里
# 没有一处用得到它 —— 说它的只有上面的 `_checks_failed`。第 9 步把 review 够不着
# `agent` 的调用收进端口时，纯字符串不走端口，搬回说它的地方。


def main_checks_failed_prompt(
    *,
    title: str,
    task_id,
    sha: str,
    failed: list[tuple[str, str]],
    reopened: bool,
    batch: Batch | None = None,
) -> str:
    """What a task's agent is told when a check its merge broke fails on the
    default branch."""
    if batch is not None:
        return _batch_checks_failed_prompt(
            title=title,
            task_id=task_id,
            sha=sha,
            failed=failed,
            reopened=reopened,
            batch=batch,
        )
    checks = "\n".join(f"- {name} {url}".rstrip() for name, url in failed)
    told = (
        f"任务「{title}」合并到默认分支的提交 {sha[:12]} 上，下面的检查未通过，"
        f"而它们在合并前的那个提交上是通过的：\n{checks}\n"
    )
    return told + _how_to_fix(task_id, reopened)


def _batch_checks_failed_prompt(
    *,
    title: str,
    task_id,
    sha: str,
    failed: list[tuple[str, str]],
    reopened: bool,
    batch: Batch,
) -> str:
    """The same, for a repository that gates main in batches: the run is on the
    batch's head, and the log's failing files say whose commit it is."""
    lines = [
        f"任务「{title}」的提交 {sha[:12]} 落在主干这一批里，合并后的全量检查没通过。"
    ]
    if batch.total and batch.complete:
        lines.append(
            f"本批共 {batch.total} 个提交，检查跑在最后那个提交 {batch.head[:12]} 上。"
        )
    elif batch.total:
        lines.append(
            f"这一批至少 {batch.total} 个提交（更早的没读全），"
            f"检查跑在提交 {batch.head[:12]} 上。"
        )
    else:
        lines.append(f"这一批的提交范围读不到，检查跑在提交 {batch.head[:12]} 上。")
    if batch.commits:
        lines.append("本批的提交：")
        shown = batch.commits[:PROMPT_COMMITS]
        lines += [f"- {commit.sha[:8]} {commit.subject[:72]}" for commit in shown]
        if batch.total > len(shown):
            lines.append(f"- ……另有 {batch.total - len(shown)} 个")
    lines.append("没通过的检查：")
    lines.append("\n".join(f"- {name} {url}".rstrip() for name, url in failed))
    if batch.failed_paths:
        lines.append("日志里读到的失败文件：")
        shown = batch.failed_paths[:PROMPT_PATHS]
        lines += [f"- {test}" for test in shown]
        if len(batch.failed_paths) > len(shown):
            lines.append(f"- ……另有 {len(batch.failed_paths) - len(shown)} 个")
    if batch.blamed == sha:
        if batch.by_bisect:
            lines.append(
                "平台在这批里挑提交逐个重跑了失败的用例，第一次失败就出在这条提交上，"
                "先按这个方向修。"
            )
        else:
            lines.append("失败文件只有这条线的提交改过，先按这个方向修。")
    elif batch.bisect:
        lines.append(
            f"平台在这批里挑提交逐个重跑了失败的用例，也没能定位到是哪一条"
            f"（{_bisect_reason(batch.bisect)}），本批每条线都先各自排除："
            "确认不是你的改动导致的，就在任务里说明理由；是你的就修。"
        )
    else:
        lines.append(
            "没有哪条在看的线能单独认领这些失败，本批每条线都先各自排除："
            "确认不是你的改动导致的，就在任务里说明理由；是你的就修。"
        )
    return "\n".join(lines) + "\n" + _how_to_fix(task_id, reopened)


#: Why a culprit search named nobody, said to the batch in its own words.
#: `culprit_bisect` states the reason; a reader of the task should not have to
#: know the planner's vocabulary to understand what happened.
_BISECT_REASONS = {
    "a probe could not judge": "重跑的结果判断不了（探测运行失败或被取消）",
    "the probe did not finish": "探测运行没有在期限内结束",
    "the search ran out of time": "搜索没有在期限内完成",
    "a probe could not be started": "平台没能派出探测运行",
    "probe budget spent": "探测次数用完了，还没缩到一条提交",
    "no passing base to check against": "这批之前没有能通过的同一次重跑做对照",
    "the base does not pass": "这批之前的那个提交上这些用例也失败",
    "too few commits to bisect": "提交太少，没有二分",
}


def _bisect_reason(reason: str) -> str:
    return _BISECT_REASONS.get(reason, reason)


def _how_to_fix(task_id, reopened: bool) -> str:
    if not reopened:
        return (
            "看失败日志找到原因，把修复放进这一步一起交付。"
            "确认是偶发失败、和这次改动无关的，在任务里说明理由。"
        )
    return (
        "任务已重新打开。从项目最新的代码修复："
        f'先执行 cd "$(cheese worktree {task_id})"，'
        "看失败日志找到原因再改，修好后用 cheese_accept_request 递交付，"
        "completes_task 填 true。确认是偶发失败、和这次改动无关的，"
        "在任务里说明理由，然后用 cheese_close_task 关闭任务，不提交改动。"
    )


def batch_passed_prompt(*, title: str, sha: str) -> str:
    """What a task hears when a later batch made its blamed commit green."""
    return (
        f"任务「{title}」的提交 {sha[:12]} 之前被主干那一批的全量检查判过红，"
        "后续的一批已经跑绿并覆盖了它，这条催修撤销，不用再为它改。"
        "如果你已经动手修了，照常交付；还没开始的就停在这里。"
    )


async def _withdraw_passed(
    sessions, *, record_instruction: RecordInstruction | None
) -> int:
    """Take back the fix asked for when a later batch went green over the
    commit: `main-green` moving past it is that batch passing."""
    now = datetime.now(UTC)
    async with sessions() as session:
        rows = list(
            await session.execute(
                select(
                    TaskLanding.id,
                    TaskLanding.task_id,
                    TaskLanding.merge_sha,
                    Task.project_id,
                )
                .join(Task, Task.id == TaskLanding.task_id)
                .where(
                    TaskLanding.checks == ChecksOutcome.failed,
                    TaskLanding.merge_sha.is_not(None),
                    TaskLanding.watch_until >= now,
                )
            )
        )
    by_project: dict[uuid.UUID, list[tuple]] = {}
    for landing_id, task_id, merge_sha, project_id in rows:
        by_project.setdefault(project_id, []).append((landing_id, task_id, merge_sha))
    withdrawn = 0
    for project_id, blames in by_project.items():
        async with sessions() as session:
            try:
                reads = await landing_forge.reads_for(project_id, session)
            except UpstreamUnavailableError:
                reads = None
        if reads is None:
            continue
        try:
            await reads.configure()
            anchor = await reads.main_green()
        except Exception:  # a forge read that fails withdraws nothing
            logger.warning(
                "reading main-green failed for %s", project_id, exc_info=True
            )
            continue
        if not anchor:
            continue
        for landing_id, task_id, merge_sha in blames:
            try:
                if not await reads.is_ancestor(merge_sha, anchor):
                    continue
                runs = await reads.checks(merge_sha)
            except Exception:  # a forge read that fails withdraws nothing
                continue
            gate = gate_run(runs, reads.required_checks)
            # Only a red check in the batch's own workflow run is taken back:
            # a workflow that runs beside it (an image build, say) is another
            # suite, and Required CI going green says nothing about it. A
            # completed red counts even while a re-run of it is in flight: the
            # failure has not been settled yet.
            if _reds_elsewhere(runs, gate):
                continue
            async with sessions() as session:
                landing = await session.get(
                    TaskLanding, landing_id, with_for_update=True
                )
                if landing is None or landing.checks != ChecksOutcome.failed:
                    continue
                task = await session.get(Task, task_id)
                landing.checks = ChecksOutcome.preexisting
                if task is not None and record_instruction is not None:
                    await tell_agent(
                        session,
                        task,
                        batch_passed_prompt(title=task.title, sha=merge_sha),
                        record_instruction=record_instruction,
                    )
                await session.commit()
            withdrawn += 1
    return withdrawn
