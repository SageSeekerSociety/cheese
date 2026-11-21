"""A member hands a task's changes in for review themselves.

A change enters the project only as a version of a delivery. A member who
edited the task's files (`agent.file_edits`) hands them in on the same road the
AI teammate's hand-in takes: the same card, the same checks and reviewer rules,
the same adoption (`review.services`). What is theirs is the commit: what the
task's working copy has not committed is committed there, under their name,
and pushed to the task's branch, before the card is filed.

The working copy lives in the task's sandbox, so the commit is made there,
through its executor (`execution.call`), with the platform's own command and
plain git — a sandbox installed before this change runs it as well. A sandbox
that is gone takes nothing with it that a commit could be made from: what it
had not committed was kept on the forge (`repository.kept_work`) and comes back
in the next sandbox, so that is what the person is told. Commits already on
the branch need no sandbox and are handed in as they are.

`standing` says whether the task holds changes that are not in its latest
handed-in version, for the badge 「有未提交的修改」; `uncommitted` is its half
about files nobody committed, and is also asked before a version is adopted
(`review.ports.Uncommitted`). An awake sandbox is asked; a frozen one is not
woken for it, and what the platform kept of it answers instead.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import release_read_session
from app.core.device_failures import DeviceCallError, DeviceOffline
from app.core.errors import (
    ConflictError,
    GatewayUnavailableError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from app.core.sentences import exception_text, say
from app.domain.agent import execution
from app.domain.agent.announce import announce
from app.domain.environment import services as environment_rows
from app.domain.machine.preview_wake import is_frozen
from app.domain.notice_meta import (
    EVENT_CARD_REDESCRIBED,
    EVENT_TASK_FILED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.project import forge as project_forge
from app.domain.repository import identity
from app.domain.repository.forge_files import ProjectFiles
from app.domain.repository.kept_work import has_kept, kept_work
from app.domain.repository.ports import WakeWorkingCopy as Wake
from app.domain.review import commit_message, task_landing
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.services import AcceptService, ReviewerAdmission
from app.domain.room_task import heard
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.services import said_title
from app.domain.topic.models import Topic

logger = logging.getLogger(__name__)

#: A version still waiting on its reviewer: a new one from the same branch
#: moves it (`AcceptService.push_fix`) rather than filing a second card.
WAITING = (AcceptStatus.pending, AcceptStatus.conflict)

#: How long the commit and its push may take on the sandbox. A push runs the
#: project's own pre-push check (`cheese sync`), as a teammate's does.
HAND_IN_S = 300
#: Asking a sandbox whether it has uncommitted files.
STATUS_S = 8

_COMMITTED = "cheese-hand-in:committed"
_HEAD = "cheese-hand-in:head "


class Executor(Protocol):
    """`execution.call`, with the deadline a long command needs."""

    async def __call__(
        self, target: dict, method: str, params: dict, *, timeout: float = ...
    ) -> dict: ...


def _checkout(task_id: uuid.UUID) -> str:
    """The task's checkout on its sandbox (`sandbox/cheese` `_task_root`)."""
    return f'"$HOME/.cheese/tasks/{uuid.UUID(str(task_id))}"'


async def _sandbox(db: AsyncSession, task: Task) -> tuple[dict, bool] | None:
    """The environment holding the task's working copy, and whether its
    sandbox is gone; None while none has held it."""
    room = await db.get(Topic, task.room_id)
    if room is None:
        return None
    return await environment_rows.working_copy(
        db, task.id, str(room.resource_id or room.id)
    )


async def _on_sandbox(
    executor: Executor, target: dict, command: str, *, seconds: int
) -> str:
    """One command's stdout on the task's sandbox, as the platform's own call
    (no project hooks). A command that exits non-zero, does not finish or does
    not reach the sandbox raises with what it said."""
    receipt = await executor(
        target,
        "invoke",
        {
            "id": f"hand-in-{uuid.uuid4().hex}",
            "tool": "Bash",
            "args": {"command": command, "timeout": seconds * 1000},
            "platform": True,
        },
        timeout=seconds + 30,
    )
    if "error" in receipt:
        raise RuntimeError(str(receipt["error"]))
    value = receipt.get("value") or {}
    stdout = value.get("stdout") or ""
    if value.get("backgroundTaskId"):
        raise TimeoutError  # the command outlived its deadline
    if stdout.startswith("Exit code"):
        raise RuntimeError(stdout.strip()[-600:])
    return stdout


async def uncommitted(
    session: AsyncSession, task: Task, *, executor: Executor | None = None
) -> bool:
    """Whether the task's checkout has files it has not committed.

    An awake sandbox is asked about the files it tracks (a person's save
    changes one; files a teammate left untracked are not a reason to stop an
    adoption). One that is frozen is not thawed for this, and one that is
    gone cannot be asked: what the platform kept of it answers (`has_kept`),
    which is only ever on the platform's own forge. Neither answering is no."""
    if not task.branch_name:
        return False
    found = await _sandbox(session, task)
    if found is not None and not found[1] and not await is_frozen(session, task.id):
        try:
            said = await _on_sandbox(
                executor or execution.call,
                found[0],
                f"git --no-optional-locks -C {_checkout(task.id)} "
                "status --porcelain --untracked-files=no",
                seconds=STATUS_S,
            )
            return bool(said.strip())
        except Exception:  # the kept snapshot answers instead
            logger.info("could not ask the sandbox of task %s", task.id, exc_info=True)
    try:
        return await has_kept(task.project_id, session, task.id)
    except Exception:  # an unreadable forge is not a change
        logger.info("could not read kept work of task %s", task.id, exc_info=True)
        return False


@dataclass(frozen=True)
class Standing:
    """Where a task's work stands against what was handed in."""

    #: The checkout had files it had not committed when it was last kept.
    uncommitted: bool
    #: The branch has commits its latest handed-in version does not.
    ahead: bool
    #: A version is waiting on its reviewer: the next one is 「再提交一版」.
    waiting: bool

    @property
    def unhanded(self) -> bool:
        return self.uncommitted or self.ahead


async def standing(db: AsyncSession, task: Task) -> Standing:
    """Whether the task holds changes not in its latest handed-in version.

    Commits are read from the forge: the branch's head against the latest
    version's. Uncommitted files are `uncommitted`'s answer: an awake sandbox
    says; otherwise the kept snapshot, which follows a person's save by about
    ten seconds and is kept only on the platform's own forge. A version whose
    head is not known yet (filed a moment ago) counts as holding the branch."""
    latest = (await AcceptService(db).latest_by_task([task.id])).get(task.id)
    waiting = latest is not None and latest.status in WAITING
    if task.status != TaskStatus.open or not task.branch_name:
        return Standing(uncommitted=False, ahead=False, waiting=waiting)
    head = await project_forge.branch_head(task.project_id, db, task.branch_name)
    ahead = False
    if head is not None:
        if latest is not None and latest.pr_head_sha == head:
            ahead = False
        elif waiting:
            ahead = bool(latest and latest.pr_head_sha)
        else:
            ahead = bool(
                await ProjectFiles(db, task.project_id, task.id).changed_files()
            )
    return Standing(
        uncommitted=await uncommitted(db, task),
        ahead=ahead,
        waiting=waiting,
    )


async def _author(db: AsyncSession, handle: str) -> str:
    """The member as a commit's author: their GitHub identity when connected,
    the platform's address for them otherwise (`identity.as_trailer`)."""
    try:
        resolved = await identity.resolve_for_handle(db, handle)
    except Exception:  # their address, not their link, is what the commit needs
        resolved = None
    author = identity.as_trailer(handle, resolved)
    if not author or "\n" in author or "\r" in author:
        raise ValidationError(say("handInFailed", error=handle))
    return author


def commit_command(task_id: uuid.UUID, *, author: str, message: str) -> str:
    """Commit everything the checkout has not committed as ``author``, push
    the branch, and say the head.

    The commit skips the checkout's hooks: they credit the task's author (the
    AI teammate) and name the member as a co-author of their own commit. The
    committer is the checkout's own (the platform's author for the task). The
    push is `cheese sync`, the one every teammate commit takes, which also
    keeps what is left uncommitted (nothing, once this ran)."""
    where = _checkout(task_id)
    task = shlex.quote(str(uuid.UUID(str(task_id))))
    return (
        f"cd {where} && git add -A && "
        f"if git diff --cached --quiet; then :; else "
        f"printf '%s' {shlex.quote(message)} | git -c core.hooksPath=/dev/null "
        f"commit -q --author={shlex.quote(author)} -F - && echo {_COMMITTED}; fi && "
        f"cheese sync --task {task} >&2 && "
        f'echo "{_HEAD}$(git rev-parse HEAD)"'
    )


@dataclass(frozen=True)
class HandedIn:
    card: AcceptCard
    #: The branch head the version holds.
    head: str | None
    #: A version was waiting and this one moved it.
    again: bool


async def _commit_on_sandbox(
    db: AsyncSession,
    task: Task,
    *,
    branch: str,
    who: str,
    message: str,
    executor: Executor,
    wake: Wake,
) -> bool:
    """Commit and push what the task's checkout has not committed; whether a
    commit was made. A sandbox that is gone holding uncommitted work refuses
    with what became of it; one gone with nothing kept leaves the branch as
    the only thing to hand in."""
    found = await _sandbox(db, task)
    if found is None or found[1]:
        kept = None
        try:
            kept = await kept_work(
                task.project_id, db, task.id, branch, task.base_branch or ""
            )
        except Exception:  # what is on the branch can still be handed in
            logger.info("could not read kept work of task %s", task.id, exc_info=True)
        if kept is None:
            return False
        raise UpstreamUnavailableError(
            say("handInSandboxReleasedKept")
            if kept.laid_back
            else say("handInSandboxReleasedKeptBranchMoved")
        )
    author = await _author(db, who)
    # Nothing is written yet, and the commit and its push can take minutes:
    # the connection goes back to the pool for them.
    await release_read_session(db)
    await wake(task.id)
    try:
        said = await _on_sandbox(
            executor,
            found[0],
            commit_command(task.id, author=author, message=message),
            seconds=HAND_IN_S,
        )
    except (DeviceCallError, DeviceOffline, TimeoutError) as exc:
        # The machine's own words name its sockets and paths: they go to the
        # log, and the person is told what they can act on.
        logger.warning("hand-in of task %s on its sandbox failed: %s", task.id, exc)
        raise GatewayUnavailableError(say("handInMachineNotResponding")) from exc
    except Exception as exc:
        raise GatewayUnavailableError(
            say("handInFailed", error=exception_text(exc) or type(exc).__name__)
        ) from exc
    head = next(
        (
            line.removeprefix(_HEAD).strip()
            for line in said.splitlines()
            if line.startswith(_HEAD)
        ),
        None,
    )
    if not head or await project_forge.branch_head(task.project_id, db, branch) != head:
        raise GatewayUnavailableError(say("handInPushUnconfirmed"))
    return _COMMITTED in said


async def hand_in(
    db: AsyncSession,
    service: AcceptService,
    *,
    task: Task,
    who: str,
    subject: str,
    body: str | None,
    reviewers: list[str] | None,
    admits_reviewer: ReviewerAdmission,
    working: bool,
    executor: Executor | None = None,
    wake: Wake,
) -> HandedIn:
    """Hand the task's working copy in as a new version, by ``who``.

    ``working``: the task's AI teammate has a turn running in it now; nothing
    is committed under it. A version waiting on its reviewer is moved to the
    new head (the card stays, its approvals go); otherwise a card is filed
    as a teammate's is. Either way the reviewers follow the usual ladder
    unless ``reviewers`` names them."""
    if task.status != TaskStatus.open or task.closing_since or task.deploying_since:
        raise ValidationError(say("taskEndedOrNoBranch"))
    branch = task.branch_name
    if not branch:
        raise NotFoundError(say("taskNoOwnFileVersion"))
    if working:
        raise ConflictError(say("handInWhileWorking"))
    try:
        subject = commit_message.check_subject(subject)
    except commit_message.InvalidSubject as exc:
        raise ValidationError(exception_text(exc)) from exc
    body = (body or "").strip() or None
    message = subject + (f"\n\n{body}" if body else "")
    committed = await _commit_on_sandbox(
        db,
        task,
        branch=branch,
        who=who,
        message=message,
        executor=executor or execution.call,
        wake=wake,
    )
    head = await project_forge.branch_head(task.project_id, db, branch)
    latest = (await service.latest_by_task([task.id])).get(task.id)
    waiting = [latest] if latest is not None and latest.status in WAITING else []
    if waiting:
        held = waiting[0].pr_head_sha
        card = await _again(
            db,
            service,
            task=task,
            card=waiting[0],
            who=who,
            subject=subject,
            body=body,
            reviewers=reviewers,
            admits_reviewer=admits_reviewer,
            moved=committed or (held is not None and head is not None and head != held),
        )
    else:
        comparison = await ProjectFiles(db, task.project_id, task.id).comparison()
        if not (
            comparison
            and comparison.get("total_commits")
            and comparison.get("files") != []
        ):
            raise ValidationError(say("handInNothingNew"))
        card = await service.create_card(
            topic_id=task.room_id,
            task_id=task.id,
            reviewer_handles=reviewers,
            change_subject=subject,
            change_body=body,
            admits_reviewer=admits_reviewer,
            handed_in_by=who,
        )
    await heard.inform(
        db,
        task,
        heard.member_handed_in(
            by=who, subject=subject, head=head, committed=committed, again=bool(waiting)
        ),
    )
    return HandedIn(card=card, head=head, again=bool(waiting))


async def _again(
    db: AsyncSession,
    service: AcceptService,
    *,
    task: Task,
    card: AcceptCard,
    who: str,
    subject: str,
    body: str | None,
    reviewers: list[str] | None,
    admits_reviewer: ReviewerAdmission,
    moved: bool,
) -> AcceptCard:
    """A new version of the delivery that is waiting: the card follows the
    branch to its new head (the reviewer's old view cannot be adopted any
    more, and approvals go as a teammate's new commit takes them), and takes
    the 说明 and reviewers given."""
    if not moved:
        raise ValidationError(say("handInNothingNew"))
    await service.push_fix(task.id)
    # A card stuck on a merge conflict keeps its text and reviewers: those
    # change only while it waits on its reviewer.
    if reviewers and card.status == AcceptStatus.pending:
        current = (await service.reviewer_handles_for_cards([card.id])).get(card.id, [])
        if set(current) != set(reviewers):
            await service.reassign(
                card_id=card.id,
                reviewer_handles=reviewers,
                admits_reviewer=admits_reviewer,
                by=who,
            )
    if card.status == AcceptStatus.pending:
        # The line below is the record of this version, its 说明 included.
        await service.redescribe(
            task.id,
            actor=who,
            change_subject=subject,
            change_body=body,
            recorded_by_caller=True,
        )
    room = await db.get(Topic, task.room_id)
    if room is not None:
        detail = "\n\n".join(
            part for part in (card.change_subject or "", card.change_body or "") if part
        )
        await service._tell_the_reviewer(
            card,
            room,
            say(
                "cardHandedInAgain",
                actor=f"<@{who}>",
                title=card.change_subject or said_title(task),
            ),
            meta=notice(
                EVENT_CARD_REDESCRIBED,
                severity=SEVERITY_INFO,
                who=WHO_HUMAN,
                detail=detail or None,
                detail_label=say("labelChanges"),
            ),
        )
    card.handed_in_by = who
    await db.flush()
    await db.refresh(card)
    return card


async def record_at_origin(
    db: AsyncSession, service: AcceptService, task: Task, handed: HandedIn
) -> None:
    """Record where the task came from that a member handed a version in for
    review: who, the version's title, and whom it waits on. The AI teammate
    that opened the task from the channel's main line reads it in its next
    input (`task_landing.task_handed_in_opener_note`) and is not woken: the
    member did it where people see it.

    Not for a version they adopt in the same go (「提交并采纳」): that is one
    thing, recorded once, when it lands (`task_landing.delivery_landed`)."""
    card = handed.card
    reviewers = (await service.reviewer_handles_for_cards([card.id])).get(card.id, [])
    refers: dict = {"card_id": str(card.id)}
    if not handed.again:
        refers["version"] = await db.scalar(
            select(func.count())
            .select_from(AcceptCard)
            .where(AcceptCard.task_id == task.id)
        )
    await task_landing.record_at_origin(
        db,
        task,
        EVENT_TASK_FILED,
        say(
            "cardHandedIn",
            who=f"<@{card.handed_in_by}>",
            title=card.change_subject or said_title(task),
            reviewer="、".join(f"<@{h}>" for h in reviewers),
        ),
        announce=announce,
        refers={"delivery": refers},
        informs=(
            await task_landing.task_handed_in_opener_note(
                db, task, card, reviewers=reviewers
            )
            if await task_landing.main_line_opener(db, task) is not None
            else None
        ),
    )


@dataclass(frozen=True)
class FileChange:
    path: str
    additions: int
    deletions: int


def file_changes(diff: str) -> list[FileChange]:
    """Each file a unified diff changes, with its added and removed lines."""
    found: list[FileChange] = []
    path: str | None = None
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if path is not None:
                found.append(FileChange(path, added, removed))
            path = line.rsplit(" b/", 1)[-1] if " b/" in line else line.split()[-1]
            added = removed = 0
        elif line.startswith("+++ b/") and path is not None:
            path = line.removeprefix("+++ b/")
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    if path is not None:
        found.append(FileChange(path, added, removed))
    return found


async def live_diff(
    db: AsyncSession, task: Task, *, executor: Executor, wake: Wake
) -> str:
    """What handing in now would put in the project: the task's working copy
    against its base, uncommitted files included (the committed branch when
    no sandbox holds it)."""
    return await ProjectFiles(
        db,
        task.project_id,
        task.id,
        release_session=True,
        executor=executor,
        wake=wake,
    ).diff("live")


async def recent_subjects(db: AsyncSession, project_id: uuid.UUID) -> list[str]:
    """The default branch's latest commit titles: the convention a hand-in's
    title follows. Empty when the forge cannot say."""
    try:
        commits = await ProjectFiles(db, project_id, None).commits(None, 12)
    except Exception:  # a convention is a hint, not a requirement
        return []
    return [c["subject"] for c in commits if not c["subject"].startswith("Merge ")]
