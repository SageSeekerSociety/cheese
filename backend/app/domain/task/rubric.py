"""A task's 评分要点 (rubric): a few lines, each naming what is scored, how many
points it is worth, and what earns them.

The publisher writes it with the task; whoever grades the submissions reads it,
and so do the students unless the task keeps it to the graders
(``Task.rubric_public``). Nothing adds the points up into a required total: a
course may score out of 100, out of 10, or not at all.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task.stages import stage_of, stage_ref

#: Lines a rubric may have. A rubric is read at a glance; past this it is a
#: grading guide, which belongs in the description or an attachment.
MAX_LINES = 20
MAX_ITEM_CHARS = 64
MAX_CRITERIA_CHARS = 2000
MAX_POINTS = 1000


@dataclass(frozen=True)
class RubricLine:
    item: str
    points: int
    criteria: str
    #: The stage whose submissions it scores (``app.domain.task.stages``).
    stage: int = 0


def rubric_lines(raw: list[dict]) -> list[RubricLine]:
    """The lines as sent (``{item, points, criteria, stage}``), checked and
    trimmed.

    A line with neither a name nor criteria is a row the publisher added and
    left empty, so it is dropped instead of refused.
    """
    lines: list[RubricLine] = []
    for entry in raw:
        item = str(entry.get("item") or "").strip()
        criteria = str(entry.get("criteria") or "").strip()
        if not item and not criteria:
            continue
        if not item:
            raise BadRequestError(say("rubricLineNeedsName"))
        if len(item) > MAX_ITEM_CHARS or len(criteria) > MAX_CRITERIA_CHARS:
            raise BadRequestError(
                say(
                    "rubricLineTooLong",
                    item=MAX_ITEM_CHARS,
                    criteria=MAX_CRITERIA_CHARS,
                )
            )
        try:
            points = int(entry.get("points") or 0)
        except (TypeError, ValueError):
            raise BadRequestError(say("rubricPointsRange", max=MAX_POINTS)) from None
        if not 0 <= points <= MAX_POINTS:
            raise BadRequestError(say("rubricPointsRange", max=MAX_POINTS))
        lines.append(
            RubricLine(
                item=item, points=points, criteria=criteria, stage=stage_of(entry)
            )
        )
    if len(lines) > MAX_LINES:
        raise BadRequestError(say("rubricTooManyLines", max=MAX_LINES))
    return lines


def rubric_to_api(rows, keys: Sequence[str]) -> list[dict]:
    """The lines as readers get them: each names its stage by the stage's id
    (``keys``, the task's stage ids in order), None on a task without stages."""
    return [
        {
            "item": r.item,
            "points": r.points,
            "criteria": r.criteria,
            "stage": stage_ref(r.stage, keys),
        }
        for r in rows
    ]
