"""A task's 评分要点 (rubric): a few lines, each naming what is scored, how many
points it is worth, what earns them, and how it is scored.

The publisher writes it with the task; whoever grades the submissions reads it,
and so do the students unless the task keeps it to the graders
(``Task.rubric_public``). Nothing adds the points up into a required total: a
course may score out of 100, out of 10, or not at all.

How a line is scored is ``source``: ``TEACHER`` (a person grades it) or
``TESTS`` (the stage's 测试点 fill it in — ``app.domain.task.grading``). A third
one, token usage, is another task's and is refused while it is not here. At
most one line per stage is ``TESTS``, and a ``TESTS`` line needs that stage to
have 测试点.
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

#: How a line is scored.
TEACHER = "TEACHER"
TESTS = "TESTS"
SOURCES = (TEACHER, TESTS)


@dataclass(frozen=True)
class RubricLine:
    item: str
    points: int
    criteria: str
    #: The stage whose submissions it scores (``app.domain.task.stages``).
    stage: int = 0
    #: How it is scored: ``TEACHER`` or ``TESTS``.
    source: str = TEACHER


def rubric_lines(
    raw: list[dict], *, suite_stages: Sequence[int] | None = None
) -> list[RubricLine]:
    """The lines as sent (``{item, points, criteria, stage, source}``), checked
    and trimmed.

    A line with neither a name nor criteria is a row the publisher added and
    left empty, so it is dropped instead of refused.

    ``suite_stages`` is the stages that carry 测试点 (``test_suite`` names
    them); a ``TESTS`` line on any other stage is refused there. Left out — a
    writer checking lines before it knows the suites — that rule is not applied,
    and the lines are only refused when the suites come with them.
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
        source = str(entry.get("source") or "").strip() or TEACHER
        if source not in SOURCES:
            # ``TOKENS`` lands here too: the catalog already names it, another
            # task brings it, and until then a line scored that way would score
            # nothing.
            raise BadRequestError(say("rubricSourceReserved"))
        lines.append(
            RubricLine(
                item=item,
                points=points,
                criteria=criteria,
                stage=stage_of(entry),
                source=source,
            )
        )
    if len(lines) > MAX_LINES:
        raise BadRequestError(say("rubricTooManyLines", max=MAX_LINES))
    check_tests_lines(lines, suite_stages)
    return lines


def check_tests_lines(
    lines: Sequence[RubricLine], suite_stages: Sequence[int] | None
) -> None:
    """At most one ``TESTS`` line per stage, and only on a stage with 测试点.

    Two lines scored by the same stage's 测试点 would take the same number from
    the same run twice; a line on a stage that has no 测试点 has nothing to fill
    it (方案 §挂了测试点、但没有评分要点设为测试点 — and the form refuses it
    rather than leaving it silently empty)."""
    tested: set[int] = set()
    for line in lines:
        if line.source != TESTS:
            continue
        if line.stage in tested:
            raise BadRequestError(say("rubricOneTestsLine"))
        if suite_stages is not None and line.stage not in suite_stages:
            raise BadRequestError(say("rubricTestsWithoutSuite"))
        tested.add(line.stage)


def _line_of(row) -> RubricLine:
    """A stored row (or a test double) read as a :class:`RubricLine`."""
    return RubricLine(
        item=row.item,
        points=row.points,
        criteria=row.criteria,
        stage=row.stage,
        source=getattr(row, "source", TEACHER) or TEACHER,
    )


def tests_line(rows: Sequence[object], stage: int) -> object | None:
    """The stage's ``TESTS`` line, or None when nothing on it is scored by
    测试点 — the case where a run is still worth writing down for the graders
    and the students, but lands in no score."""
    for row in rows:
        if (getattr(row, "source", TEACHER) or TEACHER) == TESTS and (
            row.stage == stage
        ):
            return row
    return None


def lines_for_stage(rows: Sequence[object], stage: int) -> list[RubricLine]:
    """The stage's lines in order (a submission is graded on its own stage,
    方案 §分阶段的题：每个阶段各自合成)."""
    return [_line_of(row) for row in rows if row.stage == stage]


def rubric_to_api(rows, keys: Sequence[str]) -> list[dict]:
    """The lines as readers get them: each names its stage by the stage's id
    (``keys``, the task's stage ids in order), None on a task without stages."""
    return [
        {
            "item": r.item,
            "points": r.points,
            "criteria": r.criteria,
            "stage": stage_ref(r.stage, keys),
            "source": getattr(r, "source", TEACHER) or TEACHER,
        }
        for r in rows
    ]
