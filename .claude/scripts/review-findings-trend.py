#!/usr/bin/env python3
"""What the reviews keep finding, from the results this machine still has.

    python3 .claude/scripts/review-findings-trend.py [--dir DIR]... [--top N] [--json]

Reads every `result.json` under the given prepare directories — by default the
`/tmp/pr-review-*.input` ones `pr-review.sh prepare` leaves behind, which is
what one machine has seen since it last rebooted — and ranks what came back, by
kind and by the area of the tree the finding is in. Offline, read-only.

The point is the top of the list: a defect class that shows up review after
review is one a check should catch before a reviewer does. Whether a class is
worth a check is not this script's call and it does not make one; it counts.

A finding is clustered by two things: its `kind`, and the first two path
components of the file it is in — `frontend/src`, `backend/app`, `.claude/rules`
— rather than the file itself. The file is too fine to repeat: two reviews
rarely name the same one. The kind is what says which check could hold it, so a
cluster is `kind｜area` and the same area under two kinds stays two lines.

Each line counts rounds and distinct PRs, because they mean different things,
and gives the worst severity seen and how many of the findings were unverified
(rung below 4 — those are the ones a single missed confirmation could have
invented). A round is one result.json: a PR reviewed twice contributes to two.
A tier G round has no findings and still counts as a round read, or the share
of clean reviews would be invisible.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

#: What `prepare` leaves in $PR_REVIEW_DIR; the default is the same variable.
DEFAULT_GLOB = "/tmp/pr-review-*.input"
PR_FROM_DIR = re.compile(r"^pr-review-(\d+)-")


def area_of(path):
    """The first two path components of a file, or the path itself when it is shorter.
    `backend/app/domain/x.py` -> `backend/app`; `f.txt` -> `f.txt`; `.claude/rules/a.md`
    -> `.claude/rules`. Wider than a file so repeats accumulate, narrower than a top-level
    directory so `backend/app` and `backend/alembic` do not read as the same class."""
    parts = [p for p in str(path).split("/") if p]
    return "/".join(parts[:2]) if len(parts) > 2 else "/".join(parts) or "(no file)"


def pr_of(dirname):
    hit = PR_FROM_DIR.match(dirname)
    return hit.group(1) if hit else "(unknown)"


def rounds_of(dirs):
    """Every result.json under `dirs`, oldest-looking order irrelevant: (dir, result)."""
    seen = []
    for root in dirs:
        for path in sorted(Path(root).glob("**/result.json")):
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"review-findings-trend: skip {path}: {exc}", file=sys.stderr)
                continue
            seen.append((path.parent, result))
    return seen


def cluster(rounds):
    """({cluster: {...}}, totals) over the rounds. A finding with no file counts as one
    cluster of its own rather than being dropped: a finding nobody can place is itself
    something worth seeing at the top of a list."""
    counts, per_cluster = Counter(), {}
    for directory, result in rounds:
        pr = pr_of(directory.name)
        for finding in result.get("findings") or []:
            key = f"{finding.get('kind') or '(no kind)'}｜{area_of(finding.get('file') or '')}"
            row = per_cluster.setdefault(
                key, {"rounds": set(), "prs": set(), "severities": Counter(),
                      "unverified": 0, "titles": []})
            # The round is the directory, the same one for every finding in it: two
            # findings of one class in a single review are one round, not two.
            row["rounds"].add(str(directory))
            row["prs"].add(pr)
            row["severities"][finding.get("severity") or "(none)"] += 1
            if isinstance(finding.get("rung"), int) and finding["rung"] < 4:
                row["unverified"] += 1
            if finding.get("title") and len(row["titles"]) < 3:
                row["titles"].append(finding["title"])
            counts[key] += 1
    return per_cluster, counts


#: Worst first, for the severity column: what a cluster is worth is its worst case, not
#: its average, since one blocker among twenty minors is what would make it a check.
SEVERITY_ORDER = ("blocker", "major", "minor")


def worst(severities):
    for name in SEVERITY_ORDER:
        if severities.get(name):
            return name
    return next(iter(severities), "")


def report(rounds, top):
    per_cluster, counts = cluster(rounds)
    ranked = sorted(per_cluster.items(), key=lambda kv: (-counts[kv[0]], kv[0]))
    rows = []
    for key, row in ranked[:top]:
        rows.append({
            "cluster": key,
            "findings": counts[key],
            "rounds": len(row["rounds"]),
            "prs": len(row["prs"]),
            "worst": worst(row["severities"]),
            "unverified": row["unverified"],
            "titles": row["titles"],
        })
    findings = sum(counts.values())
    return {
        "rounds_read": len(rounds),
        "rounds_with_nothing": sum(1 for _, r in rounds if not (r.get("findings") or [])),
        "findings": findings,
        "kinds": dict(Counter(f.get("kind") or "(no kind)"
                              for _, r in rounds for f in (r.get("findings") or [])).most_common()),
        "clusters": len(per_cluster),
        "top": rows,
    }


def render(out):
    if not out["rounds_read"]:
        return ("没有任何审查结果可读。`pr-review.sh prepare` 会把结果放在 "
                "$PR_REVIEW_DIR（默认 /tmp），换机器或重启过就没了；用 --dir 指到别处。")
    lines = [f"读了 {out['rounds_read']} 轮审查：{out['rounds_with_nothing']} 轮没有 findings，"
             f"其余共 {out['findings']} 条，归成 {out['clusters']} 类。", ""]
    if out["kinds"]:
        lines.append("按类型：" + "、".join(f"{k} {v}" for k, v in out["kinds"].items()))
        lines.append("")
    if not out["top"]:
        lines.append("没有 findings 可排行。")
        return "\n".join(lines)
    lines.append("| # | 聚类（类型｜区域） | 条数 | 轮数 | PR 数 | 最重 | 未证实 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, row in enumerate(out["top"], 1):
        lines.append(f"| {i} | `{row['cluster']}` | {row['findings']} | {row['rounds']} "
                     f"| {row['prs']} | {row['worst']} | {row['unverified']} |")
    with_titles = [r for r in out["top"] if r["titles"]]
    if with_titles:
        lines.append("")
        lines.append("排头几类的例子：")
        for row in with_titles:
            lines.append(f"- `{row['cluster']}`：" + "；".join(row["titles"]))
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", action="append", default=[],
                        help=f"一个结果目录；可重复。默认 {DEFAULT_GLOB}")
    parser.add_argument("--top", type=int, default=20, help="榜单长度，默认 20")
    parser.add_argument("--json", action="store_true", help="按 JSON 输出，给别的脚本读")
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("--top 至少是 1")

    dirs = args.dir or [str(p) for p in sorted(Path("/").glob(DEFAULT_GLOB.lstrip("/")))]
    if not dirs:
        print(f"review-findings-trend: {DEFAULT_GLOB} 下一个目录都没有", file=sys.stderr)
    out = report(rounds_of(dirs), args.top)
    print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else render(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
