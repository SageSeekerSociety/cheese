#!/usr/bin/env python3
"""Check acceptance card bodies for misplaced or malformed independent review lines.

Two misplacements are rejected:
  1. The latest independent review conclusion line is not on the last non-empty
     line of the card body (e.g. placed at the top of the body or followed by
     further prose).
  2. The first review conclusion line in the card body (round 1, with no prior
     round above it) carries `（增量自 <sha>）`.

The review line grammar is imported directly from `orchestrator_autoaccept.REVIEW_LINE`
so the check and the auto-accept reader cannot drift.

Usage:
  python3 .claude/scripts/check-review-card-body.py <body-file>
  python3 .claude/scripts/check-review-card-body.py --text <body-file>
  python3 .claude/scripts/check-review-card-body.py --body "<card-body>"
  python3 .claude/scripts/check-review-card-body.py --self-test

Exit 0: the card body's review conclusion line placement is valid.
Exit 1: missing review line, review line not on the last line, or first round has 增量自.
Exit 2: usage error or unreadable input file.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from orchestrator_autoaccept import REVIEW_LINE  # noqa: E402

#: Optional tier suffix appended by `.claude/pr-review/render.py` after `REVIEW_LINE`.
TIER_SUFFIX = re.compile(r"\s*(?:｜\s*档位\s*[A-G])?\s*")


def _is_standalone_review_line(line: str, match: re.Match[str]) -> bool:
    """True when `line` is a standalone review conclusion line (with optional tier suffix)."""
    return match.start() == 0 and TIER_SUFFIX.fullmatch(line[match.end() :]) is not None


def check_body(text: str) -> list[str]:
    """Return a list of placement problems in `text` (empty when valid)."""
    non_empty = [
        (lineno, line.strip())
        for lineno, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]
    if not non_empty:
        return ["验收卡正文为空，未找到独立 review 结论行"]

    matches: list[tuple[int, str, re.Match[str]]] = []
    for lineno, line in non_empty:
        for match in REVIEW_LINE.finditer(line):
            matches.append((lineno, line, match))

    if not matches:
        return ["未找到独立 review 结论行"]

    problems: list[str] = []

    standalone = [
        (lineno, line, match)
        for lineno, line, match in matches
        if _is_standalone_review_line(line, match)
    ]
    for lineno, line, match in matches:
        if not _is_standalone_review_line(line, match):
            problems.append(f"第 {lineno} 行：独立 review 结论行必须独占一行")

    first_candidates = [matches[0]]
    if standalone and standalone[0] != matches[0]:
        first_candidates.append(standalone[0])
    for first_lineno, _, first_match in first_candidates:
        if first_match.group(2) is not None:
            problems.append(
                f"第 {first_lineno} 行：首轮独立 review 结论行不应包含「增量自 {first_match.group(2)}」"
            )
            break

    last_lineno, _ = non_empty[-1]
    last_match_lineno, last_match_line, last_match = matches[-1]
    if last_match_lineno != last_lineno or not _is_standalone_review_line(
        last_match_line, last_match
    ):
        problems.append(
            f"第 {last_match_lineno} 行：独立 review 结论行未放在正文最后一行（最后一行在第 {last_lineno} 行）"
        )

    return problems


def _read_body(args: argparse.Namespace) -> str:
    if args.body is not None:
        return args.body
    target = args.text if args.text is not None else args.path
    if target is not None:
        if str(target) == "-":
            return sys.stdin.read()
        return Path(target).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise ValueError("未提供验收卡正文（请传文件路径、--text、--body 或通过标准输入传入）")


def self_test() -> int:
    """Offline self-test covering the four canonical body shapes."""
    cases = [
        (
            "首轮正确",
            "## 改动原因\n修复问题。\n\n独立 review：head 11111111 结论 无 blocker/major｜档位 B\n",
            0,
            "",
        ),
        (
            "结论行不在末行",
            "独立 review：head 11111111 结论 无 blocker/major｜档位 B\n\n## 改动原因\n修复问题。\n",
            1,
            "未放在正文最后一行",
        ),
        (
            "首轮带增量自",
            "## 改动原因\n修复问题。\n\n独立 review：head 22222222（增量自 11111111）结论 无 blocker/major｜档位 B\n",
            1,
            "首轮独立 review 结论行不应包含「增量自",
        ),
        (
            "多轮正确",
            (
                "## 改动原因\n修复问题。\n\n"
                "独立 review：head 11111111 结论 有 1 条｜档位 B\n"
                "独立 review：head 22222222（增量自 11111111）结论 无 blocker/major｜档位 B\n"
            ),
            0,
            "",
        ),
    ]
    failures: list[str] = []
    for name, body, want_code, want_text in cases:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--body", body])
        combined = out.getvalue() + err.getvalue()
        if code != want_code or (want_text and want_text not in combined):
            failures.append(
                f"{name}: got code={code} output={combined!r}, want code={want_code} containing {want_text!r}"
            )
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"PASS: check-review-card-body self-test ({len(cases)} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", type=Path, help="card body markdown file")
    parser.add_argument("--text", type=Path, help="card body markdown file")
    parser.add_argument("--body", help="card body text")
    parser.add_argument(
        "--self-test", action="store_true", help="run offline self-tests"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    defined = sum(x is not None for x in (args.path, args.text, args.body))
    if defined > 1:
        print("不能同时指定多个正文输入源", file=sys.stderr)
        return 2

    try:
        body = _read_body(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"无法读取验收卡正文: {exc}", file=sys.stderr)
        return 2

    problems = check_body(body)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("ok: 验收卡独立 review 结论行位置与轮次格式正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
