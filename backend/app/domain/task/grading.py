"""分数怎么合成（方案 §7）。

三条规则，都只跟整数打交道：

1. 评分要点的每一项有一个评分方式（``TaskRubricItem.source``）：``TEACHER`` 老师
   评，``TESTS`` 由那一阶段的评测结果折算。同一个阶段最多一项是 ``TESTS``。
2. ``TESTS`` 那一项的得分 = 通过用例的分值之和 ÷ 全部用例分值之和 × 这一项的分值，
   四舍五入到整数。
3. 总分 = 各项得分之和 ÷ 各项分值之和 × 100，四舍五入，仍是 0–100 的整数，写进现有
   的 ``task_submission_review.score``。评分要点合计正好 100 时，总分就是各项相加。

这个模块是纯函数：读进来的是评分要点、这一版提交的逐项得分、评测结果的分子分母，
没有任何数据库访问，于是那几个算分例子都能逐条试。

``TESTS`` 那一项**不自动判通过**：评测结果只给出它的分，是否通过仍由老师点。评测还
没出结果、或者失败时，这一项空着（``score is None``）；老师可以手动填，填了就要写明
原因（``adjusted`` + ``note``，学生看得到）。空着的一项两边都不计 —— 得分之和与分值
之和里都没有它 —— 所以上面第 3 条仍然是同一句话。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.errors import BadRequestError
from app.core.sentences import say

#: 评分方式：老师评，或者由测试点折算。
TEACHER = "TEACHER"
TESTS = "TESTS"
SOURCES = (TEACHER, TESTS)


def _rounded(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` 四舍五入到整数，0.5 向上。

    ``(2n + d) // (2d)`` 就是 ``floor(n/d + 1/2)``，全程整数运算，不会因为浮点数
    在 .5 上抖到少一分。
    """
    if denominator <= 0:
        return 0
    return (2 * numerator + denominator) // (2 * denominator)


def tests_line_score(
    points: int, passed_points: int | None, total_points: int | None
) -> int | None:
    """测试点那一项自动算出来的分：``通过用例的分值之和 ÷ 全部用例分值之和 × 这一项
    的分值``，四舍五入。

    没有评测结果（``passed_points``/``total_points`` 为空）、或者一个用例都没有分值
    （分母为 0）时给 ``None``：这一项还空着，等结果出来或者老师手填。
    """
    if passed_points is None or total_points is None or total_points <= 0:
        return None
    return min(max(points, 0), _rounded(points * passed_points, total_points))


@dataclass(frozen=True)
class SettledLine:
    """一版提交上评分要点某一项的得分。

    ``score`` 为空表示这一项还没分（测试还没出结果、也还没手填）。``adjusted`` 为真
    表示 ``TESTS`` 那一项是老师手动改的，``note`` 是原因 —— 两个一起才作数，学生看
    得到原因（方案 §老师）。
    """

    index: int
    points: int
    source: str
    score: int | None = None
    adjusted: bool = False
    note: str = ""


def _entry_index(entry: dict) -> int:
    try:
        return int(entry.get("index"))
    except (TypeError, ValueError):
        raise BadRequestError(
            say("lineScoreUnknownLine", index=entry.get("index"))
        ) from None


def _entry_score(entry: dict, points: int) -> int:
    try:
        score = int(entry.get("score"))
    except (TypeError, ValueError):
        raise BadRequestError(say("lineScoreRange", points=points)) from None
    if not 0 <= score <= max(points, 0):
        raise BadRequestError(say("lineScoreRange", points=points))
    return score


def settle_line_scores(
    lines: Sequence,
    raw: Sequence[dict] | None,
    *,
    accepted: bool,
    passed_points: int | None = None,
    total_points: int | None = None,
) -> list[SettledLine]:
    """把请求里送来的逐项得分和评测结果合成到评分要点上（见模块开头那三条）。

    ``lines`` 是这道题保存着的评分要点（``TaskRubricItem``，按 ``index`` 对位），
    ``raw`` 是请求里的 ``lineScores``：每项 ``{index, score, adjusted, note}``。

    * ``TESTS`` 那一项：没手填就用评测结果算；手填的必须带 ``adjusted`` 和原因 ——
      自动算出来的分一旦被改，学生要能看到为什么。
    * ``TEACHER`` 那一项：老师给多少就是多少；``accepted`` 为真时每一项都要有分
      （「通过时老师项都要给分」）。
    * 给来的分数超出这一项的分值，或者指着一个不存在的项，都拒掉。
    """
    by_index = {_entry_index(entry): entry for entry in (raw or [])}
    known = {line.index for line in lines}
    for index in by_index:
        if index not in known:
            raise BadRequestError(say("lineScoreUnknownLine", index=index))

    settled: list[SettledLine] = []
    for line in lines:
        entry = by_index.get(line.index) or {}
        points = int(line.points)
        if line.source == TESTS:
            manual = entry.get("score") is not None
            if manual:
                if not entry.get("adjusted"):
                    # 不改自动分就不用写这一项：手填意味着「我改过它了」。
                    raise BadRequestError(say("lineScoreTestsManual"))
                note = str(entry.get("note") or "").strip()
                if not note:
                    raise BadRequestError(say("lineScoreNeedsReason"))
                settled.append(
                    SettledLine(
                        index=line.index,
                        points=points,
                        source=TESTS,
                        score=_entry_score(entry, points),
                        adjusted=True,
                        note=note,
                    )
                )
                continue
            settled.append(
                SettledLine(
                    index=line.index,
                    points=points,
                    source=TESTS,
                    score=tests_line_score(points, passed_points, total_points),
                )
            )
            continue
        if entry.get("score") is None:
            if accepted:
                raise BadRequestError(say("lineScoreMissing", item=line.item))
            settled.append(SettledLine(index=line.index, points=points, source=TEACHER))
            continue
        settled.append(
            SettledLine(
                index=line.index,
                points=points,
                source=TEACHER,
                score=_entry_score(entry, points),
            )
        )
    return settled


def compose_total(lines: Sequence[SettledLine]) -> int:
    """总分：各项得分之和 ÷ 各项分值之和 × 100，四舍五入，0–100 的整数。

    还没有分的那一项（测试没出结果、老师也还没手填）得分与分值两边都不计；一项都没
    有分时是 0。
    """
    scored = [line for line in lines if line.score is not None]
    total = sum(line.points for line in scored)
    if not scored or total <= 0:
        return 0
    return min(100, _rounded(100 * sum(line.score for line in scored), total))


def line_scores_to_api(lines: Sequence[SettledLine]) -> list[dict]:
    """逐项得分送到接口上的形状，与 ``rubric_to_api`` 的 ``index`` 对位。"""
    return [
        {
            "index": line.index,
            "points": line.points,
            "source": line.source,
            "score": line.score,
            "adjusted": line.adjusted,
            "note": line.note,
        }
        for line in lines
    ]


def line_scores_from_api(raw) -> list[SettledLine]:
    """读回库里存的 ``line_scores``（``TaskSubmissionReview.line_scores``）。

    存的形状就是 ``line_scores_to_api`` 的形状，读回来时逐项照抄；库里那一列是空的
    （这一版之前判的、或者没逐项打分）时给空表。
    """
    out: list[SettledLine] = []
    for entry in raw or []:
        try:
            index = int(entry.get("index"))
            points = int(entry.get("points"))
        except (TypeError, ValueError, AttributeError):
            continue
        score = entry.get("score")
        out.append(
            SettledLine(
                index=index,
                points=points,
                source=str(entry.get("source") or TEACHER),
                score=None if score is None else int(score),
                adjusted=bool(entry.get("adjusted")),
                note=str(entry.get("note") or ""),
            )
        )
    return out
