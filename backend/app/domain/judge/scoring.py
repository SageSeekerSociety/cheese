"""评测主机的回复怎么变成结果行和分数（方案 §看结果、§测试脚本、§命令判分）。

三种形态各一条路，出来都是 :class:`Scored`：结果行（``TaskJudgeRun.results``）、通过
的分值和全部分值（``passed_points``/``total_points``，``grading.tests_line_score`` 按它
们折算评分要点里测试那一行），以及「判分失败」时的那句话。

**判分失败不是 0 分。** 测试脚本没写出报告、报告读不了、判分命令没给出分或给的分不在
0 到满分之间时，这一次评测照样记「完成」（机器时间花了，照扣），但 ``error`` 写着是哪
一种失败、两个分数留空：``auto_score`` 把它和还没跑完一样当 ``pending``，老师看着程序
输出手动给分，学生不因为判分程序坏了丢分。

* ``IO``：每个用例一行，过了拿满它的分值。
* ``SCRIPT``：报告里的每个测试一行。出题人没配分值（没有用例行）时每个测试 1 分；配了
  就按测试名（``classname::name``，或只写 ``name`` —— 报告里只有一个叫这个名字的测试
  时）对上，配了没跑到的记 ``missing``、算没过，报告里多出来的 0 分照样列出来给老师看。
  跳过的测试算没过（``wrong``）。失败信息只给老师，样例（``SAMPLE``）那几条学生也看得到
  （``judge_queue.run_to_api``）。
* ``COMMAND``：分数就是判分命令给的 ``x/y``；两个都是整数就原样记，否则都乘 100 取整
  （7.5/10 记成 750/1000）—— 折算出来的比例不变。score.json 带 ``items`` 时每项一行，
  ``visible: false`` 的那几行学生看不到（``private``）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.sentences import NoticeText, say

#: Inputs and expected outputs kept on a result row for the graders' view.
KEPT_TEXT = 4096
#: A missing test: configured, never in the report. Scores as not passed.
MISSING = "missing"
#: What a skipped test is recorded as: it did not pass.
SKIPPED_AS = "wrong"
SAMPLE, HIDDEN = "SAMPLE", "HIDDEN"

#: How a run was cut short → why the score could not be read. Checked before
#: the runner's own reading error: a run killed at its time limit wrote no
#: report *because* it was killed.
_STOPPED_KEYS = {
    "timeout": "judgeScoreTimedOut",
    "memory": "judgeScoreOutOfMemory",
    "output_limit": "judgeScoreOutputLimit",
}
_ERROR_KEYS = {
    "report_missing": "judgeReportMissing",
    "report_malformed": "judgeReportMalformed",
    "report_too_large": "judgeReportTooLarge",
    "report_empty": "judgeReportEmpty",
    "score_missing": "judgeScoreMissing",
    "score_malformed": "judgeScoreMalformed",
    "score_out_of_range": "judgeScoreOutOfRange",
    "code_dir_missing": "judgeCodeDirMissing",
}


@dataclass
class Scored:
    """One judging's result rows and score, or why it could not be scored."""

    rows: list[dict] = field(default_factory=list)
    passed: int | None = None
    total: int | None = None
    #: 判分失败：this sentence, and the two scores stay empty.
    error: NoticeText | None = None
    #: The program's output the teacher reads (first lines); IO: the compiler's.
    log: str | None = None
    #: The head of ``log`` students may read (``studentLogLines``); None = none.
    student_log: str | None = None


def _failed(code: str, stopped: object, settings: dict) -> NoticeText:
    key = _STOPPED_KEYS.get(str(stopped or "")) or _ERROR_KEYS.get(
        code, "judgeScoreMissing"
    )
    if key == "judgeScoreTimedOut":
        return say(key, seconds=int(settings.get("totalTimeLimitS") or 0))
    return say(key)


def _logs(reply: dict, settings: dict) -> tuple[str | None, str | None]:
    log = str(reply.get("log") or "")[: 16 * KEPT_TEXT]
    lines = max(0, int(settings.get("studentLogLines") or 0))
    student = "\n".join(log.splitlines()[:lines]) if lines and log else None
    return (log or None), student


def io(cases: list[dict], reply: dict) -> Scored:
    """Input/output cases: each case as the setter wrote it, with what it did."""
    did = {
        entry.get("index"): entry
        for entry in reply.get("cases") or []
        if isinstance(entry, dict)
    }
    rows: list[dict] = []
    passed = total = 0
    for case in cases:
        ran = did.get(case["index"]) or {}
        status = str(ran.get("status") or "runtime_error")
        total += case["points"]
        passed += case["points"] if status == "ok" else 0
        row = {
            "index": case["index"],
            "caseId": case["id"],
            "name": case["name"],
            "visibility": case["visibility"],
            "points": case["points"],
            "status": status,
            "ms": ran.get("ms"),
            "memoryKb": ran.get("memoryKb"),
            "input": case["input"][:KEPT_TEXT],
            "expected": case["expected"][:KEPT_TEXT],
        }
        if ran.get("actual") is not None:
            row["actual"] = ran["actual"]
        if ran.get("stderr"):
            row["stderr"] = ran["stderr"]
        rows.append(row)
    compiled = reply.get("compile") or {}
    log = (
        str(compiled.get("output") or "")[:KEPT_TEXT]
        if compiled.get("status") not in (None, "ok")
        else None
    )
    return Scored(rows=rows, passed=passed, total=total, log=log)


def _test_row(test: dict, **over) -> dict:
    status = str(test.get("status") or "")
    row = {
        "name": str(test.get("name") or ""),
        "testId": str(test.get("id") or ""),
        "status": SKIPPED_AS if status == "skipped" else status,
        "ms": test.get("ms"),
    }
    if status == "skipped":
        row["skipped"] = True
    if test.get("message"):
        row["message"] = str(test["message"])
    row.update(over)
    return row


def script(cases: list[dict], reply: dict, settings: dict) -> Scored:
    """A test script's report, scored as the module docstring says."""
    log, student_log = _logs(reply, settings)
    tests = [t for t in reply.get("tests") or [] if isinstance(t, dict)]
    if reply.get("scoreError") or not tests:
        code = str(reply.get("scoreError") or "report_empty")
        return Scored(
            error=_failed(code, reply.get("stopped"), settings),
            log=log,
            student_log=student_log,
        )
    if not cases:
        rows = [
            _test_row(test, index=i, visibility=HIDDEN, points=1)
            for i, test in enumerate(tests)
        ]
        passed = sum(1 for row in rows if row["status"] == "ok")
        return Scored(
            rows=rows,
            passed=passed,
            total=len(rows),
            log=log,
            student_log=student_log,
        )
    by_id = {str(t.get("id")): t for t in tests}
    names: dict[str, list[dict]] = {}
    for test in tests:
        names.setdefault(str(test.get("name")), []).append(test)
    used: set[int] = set()
    rows: list[dict] = []
    passed = total = 0
    for case in cases:
        wanted = str(case.get("testId") or "")
        test = by_id.get(wanted)
        if test is None and len(names.get(wanted, [])) == 1:
            test = names[wanted][0]
        base = {
            "index": case["index"],
            "caseId": case["id"],
            "visibility": case["visibility"],
            "points": case["points"],
        }
        total += case["points"]
        if test is None:
            rows.append(
                {
                    **base,
                    "name": case["name"],
                    "testId": wanted,
                    "status": MISSING,
                    "ms": None,
                }
            )
            continue
        used.add(id(test))
        row = _test_row(test, **base)
        row["name"] = case["name"]
        passed += case["points"] if row["status"] == "ok" else 0
        rows.append(row)
    extra = [t for t in tests if id(t) not in used]
    for offset, test in enumerate(extra):
        rows.append(
            _test_row(
                test,
                index=len(cases) + offset,
                visibility=HIDDEN,
                points=0,
                unscored=True,
            )
        )
    return Scored(
        rows=rows, passed=passed, total=total, log=log, student_log=student_log
    )


def _scaled(score: float, top: float) -> tuple[int, int]:
    """``score/top`` as two integers with the same ratio: as they are when both
    are whole, else both ×100 and rounded."""
    if float(score).is_integer() and float(top).is_integer():
        return int(score), int(top)
    return round(score * 100), round(top * 100)


def _number(raw: object) -> float | None:
    """评测主机报上来的一个分；不是有限的、合理大小的数就当没有。主机那一侧已经卡过
    （``reports.SCORE_LIMIT``），这里再兜一次：这一步出异常会让整轮派发都停下。"""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) and abs(value) < 1e12 else None


def command(reply: dict, settings: dict) -> Scored:
    """A grading command's score, checked to lie in 0..max."""
    log, student_log = _logs(reply, settings)
    found = reply.get("score")
    if reply.get("scoreError") or not isinstance(found, dict):
        code = str(reply.get("scoreError") or "score_missing")
        return Scored(
            error=_failed(code, reply.get("stopped"), settings),
            log=log,
            student_log=student_log,
        )
    score, top = _number(found.get("score")), _number(found.get("max"))
    if score is None or top is None or top <= 0 or not 0 <= score <= top:
        return Scored(
            error=say("judgeScoreOutOfRange"), log=log, student_log=student_log
        )
    passed, total = _scaled(score, top)
    rows: list[dict] = []
    for index, item in enumerate(found.get("items") or []):
        if not isinstance(item, dict):
            continue
        got, most = _number(item.get("score")) or 0.0, _number(item.get("max")) or 0.0
        rows.append(
            {
                "index": index,
                "name": str(item.get("name") or f"第 {index + 1} 项"),
                "visibility": SAMPLE,
                "points": round(most),
                "score": got,
                "maxScore": most,
                "status": "ok" if got >= most else "wrong",
                "private": item.get("visible") is False,
            }
        )
    if not rows:
        rows.append(
            {
                "index": 0,
                "name": "总分",
                "visibility": SAMPLE,
                "points": round(top),
                "score": score,
                "maxScore": top,
                "status": "ok" if score >= top else "wrong",
            }
        )
    return Scored(
        rows=rows, passed=passed, total=total, log=log, student_log=student_log
    )


def scored(kind: str, cases: list[dict], reply: dict, settings: dict) -> Scored:
    """The reply of a judging of ``kind``, scored."""
    if kind == "SCRIPT":
        return script(cases, reply, settings)
    if kind == "COMMAND":
        return command(reply, settings)
    return io(cases, reply)
