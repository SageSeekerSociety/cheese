"""A task's 评分要点 (rubric): a few lines, each naming what is scored, how many
points it is worth, what earns them, and how it is scored.

The publisher writes it with the task; whoever grades the submissions reads it,
and so do the students unless the task keeps it to the graders
(``Task.rubric_public``). Nothing adds the points up into a required total: a
course may score out of 100, out of 10, or not at all.

A line is scored by hand (``manual``) unless its ``kind`` says the platform
scores it. ``usage`` scores the credits a claim spent on the task
(``app.domain.task.usage_score``), either against two thresholds or by rank
among the claims that handed work in; its ``config`` says which. ``tests``
scores the stage's 测试点: the share of the stage's latest judge run's points
that passed (``app.domain.task.grading``). The stage that holds an automatic
line has to total 100 points: the grader then gives the manual lines their
share and the platform adds the rest, so the review still reads 「已通过 N 分」
out of 100. One task has at most one ``usage`` line (a claim spends its credits
once); one stage has at most one ``tests`` line, and it needs that stage to
carry 测试点.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task.stages import stage_of, stage_ref

#: Lines a rubric may have. A rubric is read at a glance; past this it is a
#: grading guide, which belongs in the description or an attachment.
MAX_LINES = 20
MAX_ITEM_CHARS = 64
MAX_CRITERIA_CHARS = 2000
MAX_POINTS = 1000

MANUAL = "manual"
USAGE = "usage"
TESTS = "tests"
#: Who may score a line: the grader, or the platform from the claim's credits
#: or the stage's 测试点.
LINE_KINDS = (MANUAL, USAGE, TESTS)
AUTOMATIC_KINDS = frozenset({USAGE, TESTS})
#: What a stage with an automatic line has to total.
AUTO_TOTAL = 100
#: How a ``usage`` line scores: between two thresholds, or by rank.
USAGE_THRESHOLD = "threshold"
USAGE_RANK = "rank"
USAGE_MODES = (USAGE_THRESHOLD, USAGE_RANK)
#: The name a ``usage`` line gets when the publisher leaves it empty.
USAGE_DEFAULT_NAME = "额度用量"
#: Credits a threshold may be: far past any one claim's spending.
MAX_CREDITS = 1_000_000


@dataclass(frozen=True)
class RubricLine:
    item: str
    points: int
    criteria: str
    #: The stage whose submissions it scores (``app.domain.task.stages``).
    stage: int = 0
    #: Who scores it (``LINE_KINDS``) and, for an automatic line, how.
    kind: str = MANUAL
    config: Mapping = field(default_factory=dict)

    @property
    def automatic(self) -> bool:
        return self.kind in AUTOMATIC_KINDS


def rubric_lines(
    raw: list[dict], *, suite_stages: Sequence[int] | None = None
) -> list[RubricLine]:
    """The lines as sent (``{item, points, criteria, stage, kind, config}``),
    checked and trimmed.

    A line with neither a name nor criteria is a row the publisher added and
    left empty, so it is dropped instead of refused.

    ``suite_stages`` is the stages that carry 测试点 (``test_suite`` names
    them); a ``tests`` line on any other stage is refused there. Left out — a
    writer checking lines before it knows the suites — that rule is not applied,
    and the lines are only refused when the suites come with them.
    """
    lines: list[RubricLine] = []
    for entry in raw:
        kind = str(entry.get("kind") or MANUAL)
        if kind not in LINE_KINDS:
            raise BadRequestError(say("rubricKindUnknown"))
        item = str(entry.get("item") or "").strip()
        criteria = str(entry.get("criteria") or "").strip()
        if kind == USAGE:
            item = item or USAGE_DEFAULT_NAME
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
                item=item,
                points=points,
                criteria=criteria,
                stage=stage_of(entry),
                kind=kind,
                config=_usage_config(entry.get("config")) if kind == USAGE else {},
            )
        )
    if len(lines) > MAX_LINES:
        raise BadRequestError(say("rubricTooManyLines", max=MAX_LINES))
    _check_automatic(lines, suite_stages)
    return lines


def _line_of(row) -> RubricLine:
    """A stored row (or a test double) read as a :class:`RubricLine`."""
    return RubricLine(
        item=row.item,
        points=row.points,
        criteria=row.criteria,
        stage=row.stage,
        kind=getattr(row, "kind", MANUAL) or MANUAL,
        config=dict(getattr(row, "config", None) or {}),
    )


def tests_line(rows: Sequence[object], stage: int) -> object | None:
    """The stage's ``tests`` line, or None when nothing on it is scored by
    测试点 — the case where a run is still worth writing down for the graders
    and the students, but lands in no score."""
    for row in rows:
        if (getattr(row, "kind", MANUAL) or MANUAL) == TESTS and getattr(
            row, "stage", None
        ) == stage:
            return row
    return None


def lines_for_stage(rows: Sequence[object], stage: int) -> list[RubricLine]:
    """The stage's lines in order (a submission is graded on its own stage,
    方案 §分阶段的题：每个阶段各自合成)."""
    return [_line_of(row) for row in rows if getattr(row, "stage", None) == stage]


def _number(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise BadRequestError(say("rubricUsageThresholds")) from None
    if not 0 <= number <= MAX_CREDITS:
        raise BadRequestError(say("rubricUsageThresholds"))
    return number


def _usage_config(raw: object) -> dict:
    """A ``usage`` line's settings as stored: the mode, and on ``threshold``
    the credits at or under which it earns full points (``full``) and at or
    over which it earns none (``zero``)."""
    config = raw if isinstance(raw, Mapping) else {}
    mode = str(config.get("mode") or USAGE_THRESHOLD)
    if mode not in USAGE_MODES:
        raise BadRequestError(say("rubricUsageModeUnknown"))
    if mode == USAGE_RANK:
        return {"mode": mode}
    full = _number(config.get("full"))
    zero = _number(config.get("zero"))
    if zero <= full:
        raise BadRequestError(say("rubricUsageThresholds"))
    return {"mode": mode, "full": full, "zero": zero}


def _check_automatic(
    lines: Sequence[RubricLine], suite_stages: Sequence[int] | None = None
) -> None:
    """The rules an automatic line brings: one ``usage`` line a task, one
    ``tests`` line a stage (the latter only on a stage that carries 测试点),
    and every stage holding an automatic line totals ``AUTO_TOTAL``."""
    if sum(1 for line in lines if line.kind == USAGE) > 1:
        raise BadRequestError(say("rubricUsageOnce"))
    tested: set[int] = set()
    for line in lines:
        if line.kind != TESTS:
            continue
        if line.stage in tested:
            raise BadRequestError(say("rubricOneTestsLine"))
        if suite_stages is not None and line.stage not in suite_stages:
            raise BadRequestError(say("rubricTestsWithoutSuite"))
        tested.add(line.stage)
    for stage in {line.stage for line in lines if line.automatic}:
        total = sum(line.points for line in lines if line.stage == stage)
        if total != AUTO_TOTAL:
            raise BadRequestError(
                say("rubricAutoTotal", total=total, required=AUTO_TOTAL)
            )


def rubric_to_api(rows, keys: Sequence[str]) -> list[dict]:
    """The lines as readers get them: each names its stage by the stage's id
    (``keys``, the task's stage ids in order), None on a task without stages."""
    return [_line_to_api(r, keys) for r in rows]


def _line_to_api(r, keys: Sequence[str]) -> dict:
    line = {
        "item": r.item,
        "points": r.points,
        "criteria": r.criteria,
        "stage": stage_ref(r.stage, keys),
    }
    # A manual line reads as it always did; only an automatic one says who
    # scores it and how.
    if (r.kind or MANUAL) != MANUAL:
        line["kind"] = r.kind
        line["config"] = dict(r.config or {})
    return line


def _credits_text(value: float) -> str:
    return f"{value:g}"


def automatic_rule(kind: str, config: Mapping) -> str:
    """How an automatic line scores, in a sentence for the task's description
    (``app.domain.task.package``) and the drafting assistant."""
    if kind == TESTS:
        return "按测试点自动给分：这一阶段最新一次评测里通过用例的分值占比折算"
    if kind != USAGE:
        return ""
    if config.get("mode") == USAGE_RANK:
        return "按全班排名自动给分：交过作业的人里这道题用掉的额度越少分越高，题目截止时定稿"
    return (
        f"按额度自动给分：不超过 {_credits_text(float(config.get('full') or 0))} 额度得满分，"
        f"达到 {_credits_text(float(config.get('zero') or 0))} 额度得 0 分，中间按比例"
    )
