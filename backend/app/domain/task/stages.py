"""A task's 阶段 (stages): the parts its publisher splits the work into, each
with a name, what it asks for, and when it is due.

A task's submission items and rubric lines each belong to a stage, by the
stage's index; a claim hands work in stage by stage, and each stage is graded
on its own (``submission_state``). A task with no stage rows is one implicit
stage, index 0, due by the claim's own deadline: everything about it behaves as
it did before stages existed.

Stages are replaced as a whole, like the submission form. Each stage has an
id (``TaskStage.key``) that stays with it however the list is reordered, and a
writer sends it back with the stage; what a submission already handed in under
a stage follows the stage by that id. A stage that leaves the list while it
holds submissions is refused, since those submissions would then belong to
nothing.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
    new_stage_key,
)

#: Stages a task may have. A course splits one piece of work into a handful of
#: hand-ins; past this it is several tasks.
MAX_STAGES = 12
MAX_NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 2000
#: What a stage id looks like: short, lowercase, URL-safe.
_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")


@dataclass(frozen=True)
class StageLine:
    name: str
    description: str
    due_at: datetime | None
    #: The stage's id (``TaskStage.key``) when it is one the task already has;
    #: None for a new stage.
    key: str | None


def stage_count(stages: Sequence[object]) -> int:
    """How many stages work is handed in under: a task without stages is one."""
    return max(1, len(stages))


def stage_lines(raw: list[dict]) -> list[StageLine]:
    """The stages as sent (``{id, name, description, dueAt}``), checked and
    trimmed.

    ``id`` is the stage's id: the one the task gave it (``stages_to_api``),
    or, for a new stage, one the writer chose so that the items and rubric
    lines it sends with it can name it. A new stage sent without one gets one,
    and nothing in the same write can name it. ``dueAt`` is milliseconds or empty. A row with neither
    a name nor a description is a stage the publisher added and left empty, so
    it is dropped.
    """
    lines: list[StageLine] = []
    claimed: set[str] = set()
    for entry in raw:
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
        key = str(entry.get("id") or "").strip() or None
        if key is not None and not _KEY.fullmatch(key):
            raise BadRequestError(say("stageIdInvalid", id=key))
        # Two stages cannot both be the same one.
        if key in claimed:
            raise BadRequestError(say("stageIdDuplicate", id=key))
        if key is not None:
            claimed.add(key)
        lines.append(
            StageLine(name=name, description=description, due_at=due_at, key=key)
        )
    if len(lines) > MAX_STAGES:
        raise BadRequestError(say("stageTooMany", max=MAX_STAGES))
    return lines


def ms(moment: datetime | None) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None


def stages_to_api(rows: Sequence[TaskStage]) -> list[dict]:
    return [
        {
            "id": r.key,
            "name": r.name,
            "description": r.description,
            "dueAt": ms(r.due_at),
        }
        for r in rows
    ]


@dataclass(frozen=True)
class StageNews:
    """What a change to a task's stages means to whoever took it: each kept
    stage given a new name (old name, new name), the stages added and removed
    (by name), and each kept stage whose due date moved (name, new due in epoch
    ms or None). A reorder alone changes nothing they would look for and is not
    here."""

    renamed: tuple[tuple[str, str], ...] = ()
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    due_moved: tuple[tuple[str, int | None], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.renamed or self.added or self.removed or self.due_moved)


def stage_news(before: Sequence[dict], after: Sequence[dict]) -> StageNews:
    """Compare two ``stages_to_api`` lists by stage id."""
    old = {s["id"]: s for s in before}
    new = {s["id"]: s for s in after}
    return StageNews(
        renamed=tuple(
            (old[s["id"]]["name"], s["name"])
            for s in after
            if s["id"] in old and old[s["id"]]["name"] != s["name"]
        ),
        added=tuple(s["name"] for s in after if s["id"] not in old),
        removed=tuple(s["name"] for s in before if s["id"] not in new),
        due_moved=tuple(
            (s["name"], s["dueAt"])
            for s in after
            if s["id"] in old and old[s["id"]]["dueAt"] != s["dueAt"]
        ),
    )


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


@dataclass(frozen=True)
class StageChange:
    """A change to a task's stages that may be saved: the stages it has now,
    the new ones (a line without an id matched to a stage by name), and where
    each old stage goes (``stage_moves``)."""

    old: list[TaskStage]
    lines: list[StageLine]
    moved: dict[int, int]


async def planned_stage_change(
    session: AsyncSession, task_id: int, lines: Sequence[StageLine]
) -> StageChange:
    """The change saving ``lines`` would make, checked as the save checks it:
    a stage dropped while it holds a live submission is refused
    (``stageHasSubmissions``). The save (``replace_stages``) and its preview
    (``submission_state.stage_change_effect``) both start here, so the preview
    refuses exactly what the save would."""
    old = await list_stages(session, task_id)
    keyed = keyed_by_name(lines, [(stage.key, stage.name) for stage in old])
    old_ids = [stage.key for stage in old]
    new_ids = [line.key for line in keyed]
    held = set(
        (
            await session.execute(
                select(TaskSubmission.stage)
                .where(
                    _task_submissions(task_id),
                    TaskSubmission.deleted_at.is_(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    refused = refused_stage_change(old_ids, new_ids, held)
    if refused is not None:
        name = old[refused].name if refused < len(old) else ""
        raise BadRequestError(say("stageHasSubmissions", name=name))
    return StageChange(old=old, lines=keyed, moved=stage_moves(old_ids, new_ids))


async def replace_stages(
    session: AsyncSession, task_id: int, lines: Sequence[StageLine]
) -> None:
    """Write the task's stages as ``lines`` say, carrying what already points
    at a stage — submissions, submission items, rubric lines — to where that
    stage now stands. A stage dropped while it holds a live submission is
    refused, before anything is written (``planned_stage_change``)."""
    change = await planned_stage_change(session, task_id, lines)
    old, lines, moved = change.old, change.lines, change.moved
    old_count = stage_count(old)
    rows_moved = row_moves([stage.key for stage in old], [line.key for line in lines])
    gone = [i for i in range(old_count) if i not in rows_moved]
    if gone:
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
    for model, where, moves in (
        (TaskSubmission, _task_submissions(task_id), moved),
        (
            TaskSubmissionSchemaEntry,
            TaskSubmissionSchemaEntry.task_id == task_id,
            rows_moved,
        ),
        (TaskRubricItem, TaskRubricItem.task_id == task_id, rows_moved),
    ):
        shifts = {o: n for o, n in moves.items() if o != n}
        if shifts:
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
                key=line.key or new_stage_key(),
                name=line.name,
                description=line.description,
                due_at=line.due_at,
            )
        )
    await session.flush()


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
    """The stage index of an item or line once its reference was resolved
    (``with_stage_index``)."""
    return int(entry.get("stage") or 0)


def stage_ref(index: int, keys: Sequence[str]) -> str | None:
    """How an item or line names its stage to a reader: the stage's id, or
    None on a task without stages."""
    return keys[index] if 0 <= index < len(keys) else None


def keyed_by_name(
    lines: Sequence[StageLine], known: Sequence[tuple[str, str]]
) -> list[StageLine]:
    """A stage written without an id is the stage the task already has under
    that name, when exactly one has it and no other line claims it; otherwise
    it is new. ``known`` is the existing stages as ``(id, name)``."""
    taken = {line.key for line in lines if line.key}
    out = []
    for line in lines:
        if line.key is None:
            same = [key for key, name in known if name == line.name]
            if len(same) == 1 and same[0] not in taken:
                line = replace(line, key=same[0])
                taken.add(same[0])
        out.append(line)
    return out


def stage_moves(
    old_ids: Sequence[str | None], new_ids: Sequence[str | None]
) -> dict[int, int]:
    """Where each of the old stages went when the stage list changed, by
    position — what the hand-ins follow; a stage missing from the result was
    removed. Hand-ins never move from one stage to another and are never
    merged. The rules (``lib/taskStages.ts`` keeps the same ones for the page):

    - a task that had no stages had one: it is the first of the new stages;
    - when the last stages go, the first old stage becomes the one stage a
      task without stages has, and every other old stage is removed;
    - otherwise a stage is the same stage when its id is.
    """
    if not old_ids or not new_ids:
        return {0: 0}
    return {
        index: list(new_ids).index(key)
        for index, key in enumerate(old_ids)
        if key is not None and key in new_ids
    }


def row_moves(
    old_ids: Sequence[str | None], new_ids: Sequence[str | None]
) -> dict[int, int]:
    """Where submission items and rubric lines go: as ``stage_moves``, except
    that when the last stages go every item and line stays, on the one stage a
    task without stages has — as on the page, nothing to hand in is lost."""
    if old_ids and not new_ids:
        return {index: 0 for index in range(len(old_ids))}
    return stage_moves(old_ids, new_ids)


def refused_stage_change(
    old_ids: Sequence[str | None], new_ids: Sequence[str | None], held: set[int]
) -> int | None:
    """The first old stage (by position) that would be removed while it holds
    a live hand-in — the change is refused — or None when none would."""
    moved = stage_moves(old_ids, new_ids)
    return min((index for index in held if index not in moved), default=None)


@dataclass(frozen=True)
class SettledRows:
    """Items or rubric lines after the stage list changed: what is kept, each
    naming its stage by the new ids, and what was removed with its stage."""

    kept: list[dict]
    removed: list[dict]


def rows_after_stage_change(
    rows: Sequence[object],
    old_ids: Sequence[str],
    new_ids: Sequence[str],
) -> SettledRows:
    """Items or lines (as written, naming their stage by id) once the stages
    went from ``old_ids`` to ``new_ids``: by ``row_moves``, then settled by
    ``settled_stage``. One that names a stage this write added stays with it."""
    moved = row_moves(old_ids, new_ids)
    kept: list[dict] = []
    removed: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ref = row.get("stage")
        if ref in new_ids:
            kept.append({**row, "stage": ref})
            continue
        # The old stage it belonged to; a stale id is the first one's.
        was = settled_stage(ref, old_ids)
        position = list(old_ids).index(was) if was is not None and was in old_ids else 0
        if position not in moved:
            removed.append(row)
            continue
        kept.append({**row, "stage": new_ids[moved[position]] if new_ids else None})
    return SettledRows(kept=kept, removed=removed)


def settled_stage(ref: object, keys: Sequence[str]) -> str | None:
    """The one rule for which stage an item or rubric line belongs to, on every
    path — the form, a draft, 芝士, the API: on a task without stages it has
    none; on a task with stages, one that names none belongs to the first
    stage. An id it names is kept as it is, to be checked against ``keys``."""
    if not keys:
        return None
    if ref in (None, ""):
        return keys[0]
    return str(ref)


def stage_index(ref: object, keys: Sequence[str]) -> int:
    """The position of the stage an item, line or hand-in names by id (after
    ``settled_stage``). Refused when it names a stage the task does not have."""
    settled = settled_stage(ref, keys)
    if settled is None:
        return 0
    if settled in keys:
        return list(keys).index(settled)
    raise BadRequestError(say("stageReferenceUnknown"))


def with_stage_index(entries: Sequence[object], keys: Sequence[str]) -> list[dict]:
    """Items or lines as sent, each with its stage id replaced by the stage's
    position, which is what the rows store."""
    return [
        {**entry, "stage": stage_index(entry.get("stage"), keys)}
        for entry in entries
        if isinstance(entry, dict)
    ]


async def stage_keys(session: AsyncSession, task_id: int) -> list[str]:
    """The task's stage ids, in order; empty for a task without stages."""
    return [stage.key for stage in await list_stages(session, task_id)]
