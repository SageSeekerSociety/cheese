"""A task's 阶段 (stages): the parts its publisher splits the work into, each
with a name, what it asks for, and when it is due.

A task's submission items and rubric lines each belong to a stage, by the
stage's index; a claim hands work in stage by stage, and each stage is graded
on its own (``submission_state``). A task with no stage rows is one implicit
stage, index 0, due by the claim's own deadline: everything about it behaves as
it did before stages existed.

Stages are replaced as a whole, like the submission form. What a submission
already handed in under a stage must keep pointing at that stage when the
publisher reorders the list, so each stage sent may say where it was
(``previousIndex``); a stage that leaves the list while it holds submissions is
refused, since those submissions would then belong to nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task.models import (
    TaskMembership,
    TaskRubricItem,
    TaskStage,
    TaskSubmission,
    TaskSubmissionSchemaEntry,
)

#: Stages a task may have. A course splits one piece of work into a handful of
#: hand-ins; past this it is several tasks.
MAX_STAGES = 12
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 2000


@dataclass(frozen=True)
class StageLine:
    name: str
    description: str
    due_at: datetime | None
    #: The index this stage had before this write; None for a new stage.
    previous: int | None


def stage_count(stages: Sequence[object]) -> int:
    """How many stages work is handed in under: a task without stages is one."""
    return max(1, len(stages))


def stage_lines(raw: list[dict]) -> list[StageLine]:
    """The stages as sent (``{name, description, dueAt, previousIndex}``),
    checked and trimmed.

    ``dueAt`` is milliseconds or empty. ``previousIndex`` names the stage's
    index before this write; a stage sent without the key is taken to be the
    one at its own position, so a writer that never reorders need not say it,
    and ``null`` is a new stage. A row with neither a name nor a description is
    a stage the publisher added and left empty, so it is dropped.
    """
    lines: list[StageLine] = []
    claimed: set[int] = set()
    for position, entry in enumerate(raw):
        name = str(entry.get("name") or "").strip()
        description = str(entry.get("description") or "").strip()
        if not name and not description:
            continue
        if not name:
            raise BadRequestError(say("stageNeedsName"))
        if len(name) > MAX_NAME_CHARS or len(description) > MAX_DESCRIPTION_CHARS:
            raise BadRequestError(
                say(
                    "stageTooLong",
                    name=MAX_NAME_CHARS,
                    description=MAX_DESCRIPTION_CHARS,
                )
            )
        due_raw = entry.get("dueAt")
        due_at: datetime | None = None
        if due_raw not in (None, ""):
            try:
                due_at = datetime.fromtimestamp(int(due_raw) / 1000.0, tz=UTC)
            except (TypeError, ValueError, OverflowError, OSError) as exc:
                raise BadRequestError(say("taskTimeInvalid", field="dueAt")) from exc
        previous_raw = entry.get("previousIndex", position)
        previous: int | None = None
        if previous_raw is not None:
            try:
                previous = int(previous_raw)
            except (TypeError, ValueError):
                previous = None
        # Two stages cannot both have been the same one; the second is new.
        if previous is not None and (previous < 0 or previous in claimed):
            previous = None
        if previous is not None:
            claimed.add(previous)
        lines.append(
            StageLine(
                name=name, description=description, due_at=due_at, previous=previous
            )
        )
    if len(lines) > MAX_STAGES:
        raise BadRequestError(say("stageTooMany", max=MAX_STAGES))
    return lines


def ms(moment: datetime | None) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None


def stages_to_api(rows: Sequence[TaskStage]) -> list[dict]:
    return [
        {
            "name": r.name,
            "description": r.description,
            "dueAt": ms(r.due_at),
        }
        for r in rows
    ]


async def list_stages(session: AsyncSession, task_id: int) -> list[TaskStage]:
    stmt = (
        select(TaskStage)
        .where(TaskStage.task_id == task_id)
        .order_by(TaskStage.index.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_stages_for_tasks(
    session: AsyncSession, task_ids: Sequence[int]
) -> dict[int, list[TaskStage]]:
    if not task_ids:
        return {}
    stmt = (
        select(TaskStage)
        .where(TaskStage.task_id.in_(task_ids))
        .order_by(TaskStage.task_id.asc(), TaskStage.index.asc())
    )
    grouped: dict[int, list[TaskStage]] = {}
    for row in (await session.execute(stmt)).scalars().all():
        grouped.setdefault(row.task_id, []).append(row)
    return grouped


def _task_submissions(task_id: int):
    return TaskSubmission.membership_id.in_(
        select(TaskMembership.id).where(TaskMembership.task_id == task_id)
    )


async def replace_stages(
    session: AsyncSession, task_id: int, lines: Sequence[StageLine]
) -> None:
    """Write the task's stages as ``lines`` say, carrying what already points
    at a stage — submissions, submission items, rubric lines — to where that
    stage now stands. A stage dropped while it holds a live submission is
    refused, before anything is written."""
    old = await list_stages(session, task_id)
    old_count = stage_count(old)
    # Where each old stage went. Without stages, the one implicit stage is the
    # first stage of whatever replaces it, and the reverse.
    moved: dict[int, int] = {}
    for new_index, line in enumerate(lines):
        if line.previous is not None and line.previous < old_count:
            moved[line.previous] = new_index
    if not lines:
        moved = {0: 0}
    gone = [i for i in range(old_count) if i not in moved]
    if gone:
        held = set(
            (
                await session.execute(
                    select(TaskSubmission.stage)
                    .where(
                        _task_submissions(task_id),
                        TaskSubmission.deleted_at.is_(None),
                        TaskSubmission.stage.in_(gone),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if held:
            first = min(held)
            name = old[first].name if first < len(old) else ""
            raise BadRequestError(say("stageHasSubmissions", name=name))
        await session.execute(
            delete(TaskSubmissionSchemaEntry).where(
                TaskSubmissionSchemaEntry.task_id == task_id,
                TaskSubmissionSchemaEntry.stage.in_(gone),
            )
        )
        await session.execute(
            delete(TaskRubricItem).where(
                TaskRubricItem.task_id == task_id,
                TaskRubricItem.stage.in_(gone),
            )
        )
    shifts = {old_i: new_i for old_i, new_i in moved.items() if old_i != new_i}
    if shifts:
        for model, where in (
            (TaskSubmission, _task_submissions(task_id)),
            (
                TaskSubmissionSchemaEntry,
                TaskSubmissionSchemaEntry.task_id == task_id,
            ),
            (TaskRubricItem, TaskRubricItem.task_id == task_id),
        ):
            await session.execute(
                update(model)
                .where(where, model.stage.in_(list(shifts)))
                .values(
                    stage=case(
                        *((model.stage == o, n) for o, n in shifts.items()),
                        else_=model.stage,
                    )
                )
                .execution_options(synchronize_session=False)
            )
    await session.execute(delete(TaskStage).where(TaskStage.task_id == task_id))
    for index, line in enumerate(lines):
        session.add(
            TaskStage(
                task_id=task_id,
                index=index,
                name=line.name,
                description=line.description,
                due_at=line.due_at,
            )
        )
    await session.flush()


async def check_stage_references(session: AsyncSession, task_id: int) -> None:
    """Every submission item and rubric line belongs to a stage the task has.
    Called once the task's stages, form and rubric are all written."""
    count = stage_count(await list_stages(session, task_id))
    for model in (TaskSubmissionSchemaEntry, TaskRubricItem):
        stray = await session.scalar(
            select(model.stage)
            .where(model.task_id == task_id, model.stage >= count)
            .limit(1)
        )
        if stray is not None:
            raise BadRequestError(say("stageReferenceUnknown"))


async def open_stage(
    session: AsyncSession, task_id: int, stage: int, moment: datetime
) -> None:
    """Refuse a hand-in to a stage the task does not have, or to one already
    past its due date. Like the claim's own deadline, moving the due date is
    how a late hand-in is let through."""
    stages = await list_stages(session, task_id)
    if not 0 <= stage < stage_count(stages):
        raise BadRequestError(say("stageReferenceUnknown"))
    if stages:
        due = stages[stage].due_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        if due is not None and due < moment:
            raise BadRequestError(
                say("submissionStagePastDue", name=stages[stage].name)
            )


def stage_of(entry: dict) -> int:
    """The stage a submission item or rubric line, as sent, names: 0 when it
    names none, as on a task without stages."""
    try:
        return max(0, int(entry.get("stage") or 0))
    except (TypeError, ValueError):
        return 0
