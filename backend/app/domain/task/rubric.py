"""A task's 评分要点 (rubric): a few lines, each naming what is scored, how many
points it is worth, and what earns them.

The publisher writes it with the task; whoever grades the submissions reads it,
and so do the students unless the task keeps it to the graders
(``Task.rubric_public``). Nothing adds the points up into a required total: a
course may score out of 100, out of 10, or not at all.
"""

from dataclasses import dataclass

from app.core.errors import BadRequestError

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


def rubric_lines(raw: list[dict]) -> list[RubricLine]:
    """The lines as sent (``{item, points, criteria}``), checked and trimmed.

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
            raise BadRequestError("Every rubric line needs a name")
        if len(item) > MAX_ITEM_CHARS or len(criteria) > MAX_CRITERIA_CHARS:
            raise BadRequestError("A rubric line is too long")
        try:
            points = int(entry.get("points") or 0)
        except (TypeError, ValueError):
            raise BadRequestError("Rubric points must be a whole number") from None
        if not 0 <= points <= MAX_POINTS:
            raise BadRequestError(f"Rubric points must be between 0 and {MAX_POINTS}")
        lines.append(RubricLine(item=item, points=points, criteria=criteria))
    if len(lines) > MAX_LINES:
        raise BadRequestError(f"A rubric has at most {MAX_LINES} lines")
    return lines


def rubric_to_api(rows) -> list[dict]:
    return [{"item": r.item, "points": r.points, "criteria": r.criteria} for r in rows]
