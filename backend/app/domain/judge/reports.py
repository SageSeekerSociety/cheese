"""读测试脚本的 JUnit XML 报告、读判分命令给的分（方案 §测试脚本、§命令判分）。

和 ``compare``、``container``、``runner`` 一起被原样发到评测主机上（拼接次序见
``script``），所以只用标准库、不写 ``from __future__``。这里只管「读」：报告和分数都是
容器里那个程序写出来的，学生的代码和它跑在同一个容器里，什么都可能写得出来 —— 所以每一
步都按「这是一份不可信的输入」来读：

* 报告先看大小（``runner`` 读之前就按 :data:`REPORT_MAX_BYTES` 截住），带 ``<!DOCTYPE``
  /``<!ENTITY`` 的一律当坏报告 —— 不给实体展开（「十亿笑」）留任何口子；
* 读不出来就抛 :class:`ReportError`，带一个短代码，后端据此标「判分失败」并说明是哪一种
  （``judge.scoring``），不当 0 分；
* 分数是不是落在 0 到满分之间由后端判（``judge.scoring``）：这里只把数读出来。
"""

import json
import re
import xml.etree.ElementTree as ElementTree

#: A report larger than this is not read: a real one for a few hundred tests
#: is tens of KB.
REPORT_MAX_BYTES = 4 * 1024 * 1024
#: score.json larger than this is not read.
SCORE_JSON_MAX_BYTES = 256 * 1024
#: At most this many tests are kept from a report; past it the rest are dropped
#: (and said so), so the reply still fits the connector's 1 MiB.
MAX_TESTS = 1000
#: At most this many items are kept from score.json.
MAX_ITEMS = 200
MAX_ITEM_NAME = 128

#: What one test did, as the result rows store it. A skipped test did not
#: pass, so it scores as ``wrong`` — but keeps its own name for the reader.
TEST_OK = "ok"
TEST_WRONG = "wrong"
TEST_ERROR = "error"
TEST_SKIPPED = "skipped"

_SCORE_LINE = re.compile(
    r"^SCORE:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*([0-9]+(?:\.[0-9]+)?)\s*$"
)


class ReportError(Exception):
    """The report or the score could not be read; ``code`` says how."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _seconds_to_ms(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0 or value > 1e7:  # NaN, negative, absurd
        return None
    return int(value * 1000)


def _status_of(case):
    """A ``<testcase>``'s outcome and what its failure said."""
    for tag, status in (
        ("failure", TEST_WRONG),
        ("error", TEST_ERROR),
        ("skipped", TEST_SKIPPED),
    ):
        found = case.find(tag)
        if found is not None:
            said = (found.get("message") or "").strip()
            body = (found.text or "").strip()
            if body and body != said:
                said = (said + "\n" + body).strip()
            return status, said
    return TEST_OK, ""


def parse_junit(data, message_bytes=1024):
    """The tests of a JUnit XML report, in the order it lists them.

    Each test is ``{id, name, classname, status, ms, message}``; ``id`` is
    ``classname::name`` (just ``name`` when there is no class). Both roots
    work: ``<testsuites>`` (pytest, JUnit 5's console, gotestsum) and a lone
    ``<testsuite>`` (``node --test``). Returns ``(tests, truncated)``.
    Raises :class:`ReportError` when the bytes are not a report.
    """
    if len(data) > REPORT_MAX_BYTES:
        raise ReportError("report_too_large")
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise ReportError("report_malformed")
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError):
        raise ReportError("report_malformed") from None
    if root.tag not in ("testsuites", "testsuite"):
        raise ReportError("report_malformed")
    tests = []
    truncated = False
    for case in root.iter("testcase"):
        if len(tests) >= MAX_TESTS:
            truncated = True
            break
        name = (case.get("name") or "").strip()
        if not name:
            continue
        classname = (case.get("classname") or "").strip()
        status, said = _status_of(case)
        tests.append(
            {
                "id": f"{classname}::{name}" if classname else name,
                "name": name,
                "classname": classname,
                "status": status,
                "ms": _seconds_to_ms(case.get("time")),
                "message": said.encode()[:message_bytes].decode("utf-8", "ignore"),
            }
        )
    return tests, truncated


def _number(raw):
    """A score as a number: an int stays an int, anything else a finite float."""
    if isinstance(raw, bool):
        raise ReportError("score_malformed")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw == raw and abs(raw) < 1e12:
        return int(raw) if raw.is_integer() else raw
    raise ReportError("score_malformed")


def _plain(raw):
    """``"7"`` reads 7, ``"7.5"`` reads 7.5."""
    value = float(raw)
    return int(value) if value.is_integer() else value


def parse_score_line(stdout_text):
    """``SCORE: x/y`` on the last non-blank line of what the commands printed.

    Only the last line counts: a grader prints its verdict at the very end,
    and anything the student's code printed earlier cannot stand in for it.
    Returns ``{"score", "max", "items": []}``.
    """
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if not lines:
        raise ReportError("score_missing")
    found = _SCORE_LINE.match(lines[-1])
    if found is None:
        raise ReportError("score_missing")
    return {
        "score": _plain(found.group(1)),
        "max": _plain(found.group(2)),
        "items": [],
    }


def parse_score_json(data):
    """score.json: ``{"score": x, "max": y, "items": [{name, score, max,
    visible}]}``; ``items`` may be left out. Returns the same shape, checked."""
    if len(data) > SCORE_JSON_MAX_BYTES:
        raise ReportError("score_malformed")
    try:
        found = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReportError("score_malformed") from None
    if not isinstance(found, dict):
        raise ReportError("score_malformed")
    items = []
    raw_items = found.get("items") or []
    if not isinstance(raw_items, list):
        raise ReportError("score_malformed")
    for entry in raw_items[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            raise ReportError("score_malformed")
        items.append(
            {
                "name": str(entry.get("name") or "")[:MAX_ITEM_NAME],
                "score": _number(entry.get("score", 0)),
                "max": _number(entry.get("max", 0)),
                "visible": entry.get("visible", True) is not False,
            }
        )
    return {
        "score": _number(found.get("score")),
        "max": _number(found.get("max")),
        "items": items,
    }
