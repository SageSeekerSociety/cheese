#!/usr/bin/env python3
"""Check — and repair — the independent review line at the end of an acceptance card body.

Two misplacements are rejected:
  1. The latest independent review conclusion line is not on the last non-empty
     line of the card body (e.g. placed at the top of the body or followed by
     further prose).
  2. The first review conclusion line in the card body (round 1, with no prior
     round above it) carries `（增量自 <sha>）`.
When `--head` is given, a third one is rejected: the line pins a head that is not
the head the card is being delivered for.

The line is easy to get wrong by hand, so the same script can write it:
`--fix` repairs placement and a wrong head from the review's own result, and
`--stamp` is the delivery-time form: it pins the branch head, and when the head
only merged the base branch (or the increment is one §4 does not re-review) it
says `（增量自 <reviewed sha>）`.

The review line grammar is imported from `orchestrator_autoaccept.REVIEW_LINE`
(and, for rendering and the optional tier, from `.claude/pr-review/render.py`)
so the check, the auto-accept reader and `pr-review.sh post` cannot drift.

Usage:
  python3 .claude/scripts/check-review-card-body.py <body-file>
  python3 .claude/scripts/check-review-card-body.py --text <body-file>
  python3 .claude/scripts/check-review-card-body.py --body "<card-body>"
  python3 .claude/scripts/check-review-card-body.py --head <sha> --text <body-file>
  python3 .claude/scripts/check-review-card-body.py --fix --review-result <REVIEW-RESULT.md> \
      --text <body-file> [--out <body-file>]
  python3 .claude/scripts/check-review-card-body.py --stamp --review-result <REVIEW-RESULT.md> \
      --head <sha> [--root <repo>] [--base main] --text <body-file> [--out <body-file>]
  python3 .claude/scripts/check-review-card-body.py --self-test

Exit 0: the card body's review conclusion line placement is valid.
Exit 1: missing review line, review line not on the last line, first round has 增量自,
        the pinned head is not the delivery head, or --stamp would hide an increment
        §4 requires a review for.
Exit 2: usage error or unreadable input file.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def pr_review_dir() -> Path:
    """`.claude/pr-review`, where the module that writes the review line lives.

    Not simply `SCRIPTS_DIR.parent`: a copy of this script (a mutation probe, an
    installed tool) sits somewhere else, and it still has to find the review
    method of the repository it is judging. `PR_REVIEW_DIR` names it outright;
    otherwise look beside this file and up from the working directory."""
    named = os.environ.get("PR_REVIEW_DIR")
    if named:
        return Path(named)
    for base in (SCRIPTS_DIR, Path.cwd()):
        for parent in (base.parent, *base.parents):
            candidate = parent / ".claude" / "pr-review"
            if (candidate / "render.py").exists():
                return candidate
    return SCRIPTS_DIR.parent / "pr-review"


PR_REVIEW_DIR = pr_review_dir()

from orchestrator_autoaccept import REVIEW_LINE  # noqa: E402

#: The text `--fix` removes from a first-round line that wrongly carries it.
SINCE_SUFFIX = re.compile(r"[（(]增量自 [0-9a-f]{7,40}[）)]\s*")


def _review_render():
    """`render` from `.claude/pr-review`, the module that writes the line.

    Imported lazily: a plain check must keep working with nothing but this
    directory on the path."""
    if str(PR_REVIEW_DIR) not in sys.path:
        sys.path.insert(0, str(PR_REVIEW_DIR))
    import render  # noqa: PLC0415 — lazy on purpose, see above

    return render


def card_line(head: str, since: str | None, serious: int, tier: str | None) -> str:
    """The line as `pr-review.sh post` would render it for these facts."""
    render = _review_render()
    findings = [{"severity": "major"} for _ in range(serious)]
    return render.card_line({"findings": findings}, head, since or "", tier or "")


def same_head(pinned: str, head: str) -> bool:
    """`pinned` and `head` name the same commit at the prefixes they were written with."""
    return (
        bool(pinned)
        and bool(head)
        and (head.startswith(pinned) or pinned.startswith(head))
    )


def check_body(text: str, head: str | None = None) -> list[str]:
    """Return a list of problems in `text` (empty when valid).

    `head`, when given, is the commit the card is delivered for: the latest
    review line has to pin that commit (or a prefix of it)."""
    non_empty = [
        (lineno, line.strip())
        for lineno, line in enumerate(text.splitlines(), 1)
        if line.strip()
    ]
    if not non_empty:
        return ["验收卡正文为空，未找到独立 review 结论行"]

    matches: list[tuple[int, re.Match[str]]] = []
    for lineno, line in non_empty:
        for match in REVIEW_LINE.finditer(line):
            matches.append((lineno, match))

    if not matches:
        return ["未找到独立 review 结论行"]

    problems: list[str] = []

    first_lineno, first_match = matches[0]
    if first_match.group(2) is not None:
        problems.append(
            f"第 {first_lineno} 行：首轮独立 review 结论行不应包含「增量自 {first_match.group(2)}」"
        )

    last_lineno, _ = non_empty[-1]
    last_match_lineno, last_match = matches[-1]
    if last_match_lineno != last_lineno:
        problems.append(
            f"第 {last_match_lineno} 行：独立 review 结论行未放在正文最后一行（最后一行在第 {last_lineno} 行）"
        )

    if head is not None:
        pinned = last_match.group(1)
        if not same_head(pinned, head):
            problems.append(
                f"第 {last_match_lineno} 行：独立 review 结论行钉的 head {pinned[:8]} "
                f"不是当前 head {head[:8]}"
            )

    return problems


def review_from(path: Path) -> dict:
    """The first review line of a rendered review (REVIEW-RESULT.md), as facts."""
    m = _review_render().CARD_LINE.search(Path(path).read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{path} 里没有独立 review 结论行")
    return {
        "head": m.group("head"),
        "since": m.group("since"),
        "serious": int(m.group("serious") or 0),
        "tier": m.group("tier") or "",
    }


def git_head(root: str | None) -> str | None:
    """The commit at HEAD of `root` (or of the current directory), or None."""
    try:
        out = subprocess.run(
            ["git", "-C", root or ".", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None


def exempt_increment(root: str, base: str, since: str, head: str) -> tuple[bool, str]:
    """Whether HEAD..since is an increment §4 does not re-review, and why.

    The rule has one owner, `ready.increment` in `.claude/pr-review`; this asks it
    instead of growing a second copy that can drift."""
    if str(PR_REVIEW_DIR) not in sys.path:
        sys.path.insert(0, str(PR_REVIEW_DIR))
    import ready  # noqa: PLC0415 — lazy on purpose

    result = ready.increment(root, base, since, head)
    return not result["needs"], result["detail"]


def _review_lines(text: str) -> list[int]:
    return [i for i, line in enumerate(text.splitlines()) if REVIEW_LINE.search(line)]


def body_append(text: str, line: str) -> str:
    """`text` with `line` as its new last line; earlier rounds are untouched."""
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(line)
    return "\n".join(lines) + "\n"


def body_with_line(text: str, new_line: str) -> str:
    """`text` with its latest review line replaced by `new_line`, on the last line.

    A first round that wrongly carries `（增量自 …）` loses it: the check rejects
    that, and a first round has no earlier round to be an increment of."""
    lines = text.splitlines()
    hits = _review_lines(text)
    if hits:
        hit = hits[-1]
        stripped = lines[hit].strip()
        if _review_render().CARD_LINE.fullmatch(stripped):
            del lines[hit]
        else:
            m = REVIEW_LINE.search(lines[hit])
            assert m is not None
            lines[hit] = lines[hit][: m.start()] + lines[hit][m.end() :]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(new_line)

    first = next(i for i, line in enumerate(lines) if REVIEW_LINE.search(line))
    if REVIEW_LINE.search(lines[first]).group(2) is not None:  # type: ignore[union-attr]
        lines[first] = SINCE_SUFFIX.sub("", lines[first], count=1)
    return "\n".join(lines) + "\n"


def fix_body(
    text: str, source: dict, head: str | None, since: str | None, tier: str | None
) -> str:
    """Rewrite the card body's latest review line from `source`'s facts."""
    target_head = head or source["head"]
    target_since = since if since is not None else source["since"]
    line = card_line(
        target_head, target_since, source["serious"], tier or source["tier"]
    )
    return body_with_line(text, line)


def stamp_body(
    text: str,
    source: dict,
    head: str,
    root: str,
    base: str,
    since: str | None,
    tier: str | None,
) -> str:
    """The delivery-time rewrite: pin `head`, and declare the increment when §4 lets
    the earlier review still cover it."""
    reviewed = source["head"]
    if same_head(head, reviewed):
        increment = None
    elif since is not None:
        increment = since
    else:
        allowed, detail = exempt_increment(root, base, reviewed, head)
        if not allowed:
            raise ValueError(
                f"审查是 {reviewed[:8]} 的，之后又推了提交（{detail}），"
                f"缺一次增量审：prepare <PR> --since {reviewed[:8]}"
            )
        increment = reviewed
    line = card_line(head, increment, source["serious"], tier or source["tier"])

    hits = _review_lines(text)
    if not hits:
        # A card with no review line yet is round 1: the full review goes down
        # first, and a declaration of the exempt increment is a round after it.
        if increment:
            text = body_append(
                text,
                card_line(reviewed, None, source["serious"], tier or source["tier"]),
            )
        return body_append(text, line)

    last = text.splitlines()[hits[-1]]
    m = REVIEW_LINE.search(last)
    if m is not None and same_head(m.group(1), head):
        return body_with_line(text, line)
    return body_append(text, line)


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
    raise ValueError(
        "未提供验收卡正文（请传文件路径、--text、--body 或通过标准输入传入）"
    )


def _default_review_result(args: argparse.Namespace) -> Path | None:
    """The rendered review next to the body, when the caller did not name one."""
    if args.review_result is not None:
        return Path(args.review_result)
    target = args.text if args.text is not None else args.path
    if target is not None and str(target) != "-":
        beside = Path(target).resolve().parent / "REVIEW-RESULT.md"
        if beside.exists():
            return beside
    return None


def self_test() -> int:
    """Offline self-test: the four canonical body shapes, and the head pin."""
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

    # The head pin: the same body, once for the head it names and once for another.
    body = "## 改动原因\n修复问题。\n\n独立 review：head 11111111 结论 无 blocker/major｜档位 B\n"
    for name, head, want_code in (
        ("head 与结论行一致", "11111111", 0),
        ("head 与结论行不一致", "22222222", 1),
    ):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--body", body, "--head", head])
        combined = out.getvalue() + err.getvalue()
        if code != want_code or (want_code == 1 and "不是当前 head" not in combined):
            failures.append(
                f"{name}: got code={code} output={combined!r}, want code={want_code}"
            )

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"PASS: check-review-card-body self-test ({len(cases) + 2} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", type=Path, help="card body markdown file")
    parser.add_argument("--text", type=Path, help="card body markdown file")
    parser.add_argument("--body", help="card body text")
    parser.add_argument("--head", help="the commit the card is delivered for")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite the latest review line from the review",
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="like --fix, but pin the branch head and declare an exempt increment",
    )
    parser.add_argument(
        "--review-result",
        type=Path,
        help="the rendered review whose first line is authoritative",
    )
    parser.add_argument("--root", help="repository whose HEAD is pinned (default: cwd)")
    parser.add_argument(
        "--base", default="main", help="base branch for the increment check"
    )
    parser.add_argument("--since", help="override the increment base sha")
    parser.add_argument("--tier", help="override the review tier A-G")
    parser.add_argument(
        "--out", type=Path, help="write the fixed body here instead of stdout"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run offline self-tests"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.fix and args.stamp:
        print("--fix 与 --stamp 只能选一个", file=sys.stderr)
        return 2

    defined = sum(x is not None for x in (args.path, args.text, args.body))
    if defined > 1:
        print("不能同时指定多个正文输入源", file=sys.stderr)
        return 2

    try:
        body = _read_body(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"无法读取验收卡正文: {exc}", file=sys.stderr)
        return 2

    if not (args.fix or args.stamp):
        problems = check_body(body, args.head)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print("ok: 验收卡独立 review 结论行位置与轮次格式正确")
        return 0

    result_path = _default_review_result(args)
    try:
        source = review_from(result_path) if result_path else None
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"无法读取审查结论: {exc}", file=sys.stderr)
        return 2

    if source is None:
        # Without a rendered review, repair from the line the body already carries.
        m = None
        for m in REVIEW_LINE.finditer(body):
            pass
        if m is None:
            print("没有审查结论可以钉：请传 --review-result", file=sys.stderr)
            return 2
        source = {
            "head": m.group(1),
            "since": m.group(2),
            "serious": int(m.group(4) or 0),
            "tier": "",
        }

    try:
        if args.stamp:
            head = args.head or git_head(args.root)
            if not head:
                print("读不到当前 head：请传 --head", file=sys.stderr)
                return 2
            fixed = stamp_body(
                body, source, head, args.root or ".", args.base, args.since, args.tier
            )
        else:
            head = args.head or git_head(args.root) or source["head"]
            fixed = fix_body(body, source, head, args.since, args.tier)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    problems = check_body(fixed, head)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    if args.out is not None:
        Path(args.out).write_text(fixed, encoding="utf-8")
    else:
        sys.stdout.write(fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
