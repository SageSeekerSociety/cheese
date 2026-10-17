#!/usr/bin/env python3
"""Compare explicit PR/commit summary counts with independently supplied evidence.

Examples (run from the repository):
  python3 .claude/scripts/recompute-pr-claims.py --text body.md --base origin/main
  git diff --numstat -z origin/main...HEAD > /tmp/diff.numstat
  python3 .claude/scripts/recompute-pr-claims.py --text body.md --text commit.txt \
      --numstat /tmp/diff.numstat --junit /tmp/tests.xml

--base compares merge-base(base, head) to head, like a PR; it excludes uncommitted
changes. --numstat accepts git's newline or NUL format, including renames. Binary
files count as files but contribute no text lines, matching git's shortstat.
--junit counts testcase elements, not numbers copied from prose. Generate one
complete report for the run being claimed (e.g. pytest --junitxml=/tmp/tests.xml).

Supported *whole diff/run* claims, in Chinese or English, include:
  改了 3 个文件; 修改 3 个文件; 文件数：3; 3 files changed; files: 3
  新增 12 行; 删除 5 行; 12 insertions(+); 5 deletions(-)
  测试总数：8; tests: 8; Ran 8 tests; 通过 7 个测试; 7 tests passed
  测试通过：7; tests passed: 7; 测试失败：1; tests failed: 1
  测试跳过：1; tests skipped: 1; 测试错误：1; tests errors: 1
Also accepts Markdown emphasis and thousands separators. Claims can occur in
sentences; every recognized occurrence is compared, including contradictory ones.
Subsets, historical comparisons, planned counts, ranges, causal statements and
other phrasing are outside this grammar: supply only the current summary, not
examples or quoted history. A successful run verifies only the printed claims,
not every number in the document. This is an authoring helper, not a prose linter.

Exit 0: all recognized claims agree. Exit 1: at least one mismatch. Exit 2:
invalid/missing evidence or no recognized claims (not a pass). Reports are local
inputs; this tool neither runs tests nor proves when a report was generated.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


# Prevent matching the tail of a decimal, signed number or malformed grouping.
NUMBER = r"(?<![\d.,+\-])(?P<count>(?:\d{1,3}(?:,\d{3})+|\d+))(?!\d|[.,]\d)"
PATTERNS = {
    "files": [
        rf"(?:改了|修改了?|更改了?|变更了?)\s*{NUMBER}\s*个?文件",
        rf"文件数\s*[:：]\s*{NUMBER}",
        rf"{NUMBER}\s+files?\s+changed\b",
        rf"\bfiles\s*:\s*{NUMBER}",
    ],
    "added": [
        rf"(?:新增|增加)\s*{NUMBER}\s*行",
        rf"{NUMBER}\s+insertions?\(\+\)",
    ],
    "deleted": [rf"删除\s*{NUMBER}\s*行", rf"{NUMBER}\s+deletions?\(-\)"],
    "tests": [rf"测试总数\s*[:：]\s*{NUMBER}", rf"\btests\s*:\s*{NUMBER}",
              rf"\bRan\s+{NUMBER}\s+tests?\b"],
}
for _metric, _chinese, _english in (
    ("passed", "通过", "passed"), ("failed", "失败", "failed"),
    ("skipped", "跳过", "skipped"), ("errors", "错误", "errors"),
):
    PATTERNS[_metric] = [
        rf"测试{_chinese}\s*[:：]\s*{NUMBER}",
        rf"{_chinese}\s*{NUMBER}\s*(?:个|项|条)?测试",
        rf"\btests\s+{_english}\s*:\s*{NUMBER}",
        rf"{NUMBER}\s+tests?\s+{_english}\b",
    ]


def extract_claims(text: str) -> list[tuple[int, str, int, str]]:
    claims = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = re.sub(r"[*`_]", "", line)
        for metric, patterns in PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                    count = int(match["count"].replace(",", ""))
                    claims.append((line_number, metric, count, match[0]))
    return sorted(claims, key=lambda claim: claim[0])


def parse_numstat(data: bytes) -> dict[str, int]:
    stats = {"files": 0, "added": 0, "deleted": 0}
    if not data:
        return stats
    nul = b"\0" in data
    separator = b"\0" if nul else b"\n"
    if not data.endswith(separator):
        raise ValueError("numstat is truncated: missing final record separator")
    records = iter(data.split(separator)[:-1])
    seen = set()
    for record in records:
        parts = record.split(b"\t", 2)
        if len(parts) != 3:
            raise ValueError("invalid numstat record: expected added, deleted, path")
        added, deleted, path = parts
        if not path and nul:
            old_path, path = next(records, b""), next(records, b"")
            if not old_path:
                raise ValueError("invalid numstat rename source")
        if not path or path in seen:
            raise ValueError("empty or duplicate numstat path")
        seen.add(path)
        if added == deleted == b"-":
            added_count = deleted_count = 0
        elif re.fullmatch(rb"[0-9]+", added) and re.fullmatch(rb"[0-9]+", deleted):
            added_count, deleted_count = int(added), int(deleted)
        else:
            raise ValueError("invalid numstat line counts")
        stats["files"] += 1
        stats["added"] += added_count
        stats["deleted"] += deleted_count
    return stats


def parse_junit(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("expected a JUnit testsuite or testsuites root")
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for case in root.iter("testcase"):
        outcomes = {child.tag for child in case} & {"failure", "error", "skipped"}
        if len(outcomes) > 1:
            raise ValueError("testcase has contradictory JUnit outcomes")
        outcome = next(iter(outcomes), "passed")
        metric = {"failure": "failed", "error": "errors"}.get(outcome, outcome)
        counts["tests"] += 1
        counts[metric] += 1
    # Aggregate-only or partial reports must not look like a successful empty run.
    for suite in root.iter():
        if suite.tag not in {"testsuite", "testsuites"}:
            continue
        cases = list(suite.iter("testcase"))
        actual = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
        for case in cases:
            for tag, field in (("failure", "failures"), ("error", "errors"),
                               ("skipped", "skipped")):
                actual[field] += case.find(tag) is not None
        for field, value in actual.items():
            declared = suite.get(field)
            if declared is not None and (not re.fullmatch(r"[0-9]+", declared)
                                         or int(declared) != value):
                raise ValueError(f"JUnit {field} attribute disagrees with testcase elements")
    if not counts["tests"]:
        raise ValueError("JUnit report contains no testcase results")
    return counts


def git_stats(repo: Path, base: str, head: str) -> dict[str, int]:
    def git(*args: str) -> bytes:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                check=True)
        return result.stdout.strip() if args[0] != "diff" else result.stdout

    # Resolve refs before passing them to diff: neither revision can inject flags
    # or be interpreted as a pathspec/range supplied by the caller.
    base_sha = git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    head_sha = git("rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    ancestor = git("merge-base", base_sha.decode(), head_sha.decode())
    return parse_numstat(git("diff", "--no-ext-diff", "--no-textconv", "--numstat",
                             "-z", "--find-renames", ancestor.decode(), head_sha.decode(),
                             "--"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", type=Path, action="append", required=True,
                        help="UTF-8 PR body/commit message file; repeat to check both")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--numstat", type=Path, help="file containing git diff --numstat [-z]")
    source.add_argument("--base", help="base ref; compare its merge-base with --head")
    parser.add_argument("--head", default="HEAD", help="committed head (default: HEAD)")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository for --base")
    parser.add_argument("--junit", type=Path, help="complete JUnit XML test run report")
    args = parser.parse_args(argv)
    try:
        claims = [(path, claim) for path in args.text
                  for claim in extract_claims(path.read_text(encoding="utf-8"))]
        if not claims:
            raise ValueError("no supported claims found; nothing verified")
        actual = {}
        if args.numstat is not None:
            actual.update(parse_numstat(args.numstat.read_bytes()))
        elif args.base is not None:
            actual.update(git_stats(args.repo, args.base, args.head))
        if args.junit is not None:
            actual.update(parse_junit(args.junit))
    except (OSError, ValueError, ET.ParseError, subprocess.CalledProcessError) as exc:
        print(f"UNVERIFIED: {exc}", file=sys.stderr)
        return 2

    mismatch = missing = False
    for path, (line, metric, claimed, excerpt) in claims:
        measured = actual.get(metric)
        status = "UNVERIFIED" if measured is None else "OK" if measured == claimed else "MISMATCH"
        missing |= measured is None
        mismatch |= measured is not None and measured != claimed
        value = "missing evidence" if measured is None else str(measured)
        print(f"{status} {path}:{line} {metric}: claimed={claimed} measured={value} ({excerpt})")
    print(f"Checked {len(claims)} recognized claims only; other wording is not verified.")
    return 1 if mismatch else 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
