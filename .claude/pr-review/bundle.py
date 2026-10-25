#!/usr/bin/env python3
"""bundle.py — the delta-round inheritance rule, as its own command.

pr-review.sh prepare runs it:

  inherit   A delta round (`--since`) whose increment §4 does not re-review — only a merge
            of the base branch, or only tests, docs or copy — inherits the last round's
            conclusion. It writes result.json straight away, the way a tier G change does,
            and no subagent runs. Allowed only when the last round's REVIEW-RESULT.md is on
            this machine, names `--since`, and has no blocker/major: an exempt increment
            cannot fix a blocker, and a conclusion nobody can read is not one to inherit.
            Exit 0 inherited, 1 not (the reason is printed).

Until the review's reading moved to open-code-review, this file also built BUNDLE.md:
call slices and single-file pyright for the subagents to read instead of opening the
files themselves. OCR reads the files on its own and no part of the current method
consumes that section, so it is gone — it still ran pyright over the whole tree on
every prepare, and nothing on the card could ever show for it.

    python3 .claude/pr-review/bundle.py inherit --repo R --base main --since S --head H \\
        --previous DIR/REVIEW-RESULT.md --out DIR/result.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ready  # noqa: E402  — §4's increment rule lives there; inheriting must not grow a second one
import render  # noqa: E402


# ------------------------------------------------------------------ inherit
def kept_serious(kept):
    """Blockers and majors in a result.json already in the round for this same head — one a
    re-prepare carried over. Inheriting would overwrite it with an empty one."""
    try:
        findings = json.loads(Path(kept).read_text()).get("findings") or []
    except (OSError, ValueError, AttributeError):
        return 0
    return sum(1 for f in findings if isinstance(f, dict) and f.get("severity") in ("blocker", "major"))


def inherit(repo, base, since, head, previous, kept=""):
    """(result dict | None, reason). None means: review the increment as usual."""
    if kept and Path(kept).is_file() and (n := kept_serious(kept)):
        return None, f"这一版已经有一份审查结果，里面有 {n} 条 blocker/major，不拿继承盖掉它"
    prev = Path(previous) if previous else None
    if not prev or not prev.is_file():
        return None, "这台机器上没有上一轮的 REVIEW-RESULT.md，读不到它的结论，照常审增量"
    m = render.CARD_LINE.search(prev.read_text())
    if not m:
        return None, f"{prev} 里没有结论行，照常审增量"
    if not (since.startswith(m.group("head")) or m.group("head").startswith(since)):
        return None, f"上一轮审的是 {m.group('head')[:8]}，不是 {since[:8]}，照常审增量"
    if m.group("serious"):
        return None, f"上一轮有 {m.group('serious')} 条 blocker/major，增量要核它们修了没有"
    inc = ready.increment(repo, base, since, head)
    if inc["needs"]:
        return None, f"增量{inc['detail']}，§4 要审"
    detail = inc["detail"]
    return {
        "reviewed_sha": head,
        "summary": f"增量自 {since[:8]}：{detail}。按 §4 继承上一轮 {since[:8]} 的结论（无 blocker/major），"
                   f"没有起分身；这行说的是增量免审，不是有人读过增量。",
        "candidates_found": 0,
        "candidates_refuted": 0,
        "findings": [],
        "not_covered": [f"{since[:12]}..{head[:12]} 的增量（{detail}）按 §4 免审，继承上一轮结论，没有人读过。"],
    }, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # `inherit` is the only subcommand left, and the required subparser means argparse
    # itself exits 2 when it is missing or misspelled — so there is no `cmd` to branch on.
    a = ap.add_subparsers(dest="cmd", required=True).add_parser("inherit")
    a.add_argument("--repo", required=True)
    a.add_argument("--base", default="", help="the PR's base branch name (main)")
    a.add_argument("--since", required=True)
    a.add_argument("--head", required=True)
    a.add_argument("--previous", default="")
    a.add_argument("--kept", default="", help="result.json already in this round, if any")
    a.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    result, why = inherit(args.repo, args.base, args.since, args.head, args.previous, args.kept)
    if result is None:
        print(f"bundle: 不继承：{why}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"bundle: 增量自 {args.since[:8]} {why}，继承上一轮结论，不起分身", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
