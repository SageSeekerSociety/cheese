#!/usr/bin/env python3
"""Required CI job-duration, timeout-headroom and tree-identity measurement.

Read-only for the repository: it calls `gh api` and `git merge-tree` and changes
nothing in it. (`jobs --cache` does keep one raw payload per run under the cache
directory it is given, so a re-run needs no API call for the runs it has already
seen.) The runs it measures are pinned in a file rather than selected as "the
last N", because a sliding window is a different measurement every time the
merge queue moves — re-pin deliberately when you want a new snapshot.

Two questions, and why each needs the pin to be exact:

  jobs   How long does each job class take, and how much of its
         `timeout-minutes` is left? GitHub reports a skipped reusable-workflow
         caller job — and the aggregating job of the tier that did not run —
         with a span that measures nothing: `started_at == completed_at` (0 s) on
         some runs, `started_at > completed_at` (negative) on others. Both are
         dropped: every job whose conclusion is `skipped`, plus every
         aggregator/caller by name (`SKIP_NAMES`). Anything left that is <= 0 s
         fails the run instead of entering a percentile, because a wrong
         percentile and a right one look the same in the table.

  trees  Does the merge_group run test the same tree as the pull_request run for
         the same head? It is supposed to — the queue exists to test
         `merge(base, head)`. Reconstructing that tree locally with
         `git merge-tree` and comparing it against the tree of the queue's own
         commit answers it from the two objects, without trusting either CI log.
         A pair that does not reconstruct cleanly is reported as excluded rather
         than counted as a different tree, and the two ways that happens are
         reported apart: the pair conflicts when replayed, or this clone cannot
         rebuild it at all (a pinned commit it does not have). The first is a
         property of the pair, the second of the clone.

Usage:
    python3 scripts/ci/ci-duration-report.py jobs \\
        --runs scripts/ci/required-ci-pinned-runs.json [--cache DIR]
    python3 scripts/ci/ci-duration-report.py trees \\
        --trees scripts/ci/required-ci-pinned-trees.json [--repo DIR]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = "SageSeekerSociety/cheese"

# Aggregating jobs and reusable-workflow callers: never a per-suite measurement.
# A tier that did not run leaves its aggregator with a span of nothing, and a
# caller's span is its callee's plus queueing.
SKIP_NAMES = {
    "CI required", "CI required (draft quick)", "scope", "fast",
    "backend", "frontend", "e2e", "cli", "guards", "deploy", "harness",
    "mcp", "equivalence", "remote", "cifast",
}

# job class -> the job's `timeout-minutes` in its workflow. Matched by prefix, so
# a matrix leg ("backend / pure (3)") uses its class's cap. The backend test step
# also has a smaller per-matrix cap (contract 5, pure 10, integration 15 min).
TIMEOUTS = {
    "e2e / e2e": 25, "frontend / check": 15, "frontend / unit": 15,
    "frontend / request-paths": 15, "backend / integration": 20,
    "backend / pure": 20, "backend / contract": 20, "backend / static": 15,
    "backend / Backend coverage complete": 5, "guards / guards": 15,
    "remote / acceptance": 30, "remote / private-chat": 30, "remote / windows": 15,
    "harness / provider-requests": 40, "deploy / shell-tests-app-tier": 20,
    "deploy / shell-tests-ingress": 6, "deploy / shell-tests-deploy-scripts": 8,
    "mcp /": 35, "equivalence /": 35, "cifast / cifast": 10,
    "cli / go": 15, "cli / boot-e2e": 40,
}


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def _gh(path: str) -> dict:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"gh api {path} failed: {result.stderr.strip() or result.stdout[:200]}")
    return json.loads(result.stdout)


def _jobs(run_id: int, cache: str | None) -> dict:
    cached = Path(cache) / f"{run_id}.json" if cache else None
    if cached is not None and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    data = _gh(f"repos/{REPO}/actions/runs/{run_id}/jobs?per_page=100")
    if data.get("total_count", len(data.get("jobs", []))) > len(data.get("jobs", [])):
        sys.exit(
            f"run {run_id} reports {data['total_count']} jobs, more than the one page "
            f"of {len(data.get('jobs', []))} fetched — the missing jobs would quietly "
            "shorten this run's evidence"
        )
    if cached is not None:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
    return data


def pctl(values: list[float], p: float) -> float:
    """Percentile by linear interpolation between the two nearest ranks."""
    values = sorted(values)
    position = (len(values) - 1) * p
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def mode_jobs(args) -> int:
    run_ids = json.loads(Path(args.runs).read_text(encoding="utf-8"))["run_ids"]
    by_job: dict[str, list[float]] = {}
    nonpositive: list[str] = []
    for run_id in run_ids:
        for job in _jobs(run_id, args.cache).get("jobs", []):
            if job.get("conclusion") == "skipped" or job["name"] in SKIP_NAMES:
                continue
            started, completed = job.get("started_at"), job.get("completed_at")
            if not (started and completed):
                continue
            duration = (_t(completed) - _t(started)).total_seconds()
            if duration <= 0:
                nonpositive.append(f"{run_id}:{job['name']}={duration:g}s")
                continue
            by_job.setdefault(job["name"], []).append(duration)

    if nonpositive:
        print("FAIL self-check: included jobs with a non-positive span:", file=sys.stderr)
        for entry in nonpositive:
            print("   ", entry, file=sys.stderr)
        return 1

    print(f"# {len(run_ids)} pinned Required CI runs; {len(by_job)} job classes\n")
    print(f"{'job':42s} {'n':>3s} {'P50':>7s} {'P90':>7s} {'max':>7s}")
    headroom: list[tuple[float, str, float, float]] = []
    for name in sorted(by_job, key=lambda key: -max(by_job[key])):
        values = by_job[name]
        print(f"{name:42s} {len(values):3d} {pctl(values, .5):7.0f} {pctl(values, .9):7.0f} {max(values):7.0f}")
        for job_class, timeout in TIMEOUTS.items():
            if name.startswith(job_class):
                headroom.append((timeout * 60 / max(values), name, max(values), timeout * 60))
    print("\n# timeout headroom (smallest first; <1.5x = look)")
    for ratio, name, observed, cap in sorted(headroom)[:8]:
        print(f"  {ratio:4.2f}x  {name} (max {observed:.0f}s of {cap:.0f}s)")
    print("\nOK: no non-positive job spans in the measured set.")
    return 0


def mode_trees(args) -> int:
    rows = json.loads(Path(args.trees).read_text(encoding="utf-8"))["rows"]
    same = 0
    compared = 0
    conflicted: list[int] = []
    unbuildable: list[int] = []
    for row in rows:
        reconstruction = subprocess.run(
            ["git", "merge-tree", "--write-tree", row["base"], row["head"]],
            capture_output=True, text=True, cwd=args.repo,
        )
        tree = reconstruction.stdout.split("\n", 1)[0].strip()
        if reconstruction.returncode != 0:
            # Neither case can be counted as "different" — that would invent a
            # finding — but which one it is decides whether the reader goes after
            # the pinned pair or after the clone. A tree oid on stdout means git
            # did merge and conflicted; nothing on stdout means it never got that
            # far, which is a commit this clone does not hold.
            if tree:
                conflicted.append(row["pr"])
                print(f"  pr {row['pr']:>5}: the pinned pair does not reconstruct cleanly — excluded")
            else:
                unbuildable.append(row["pr"])
                detail = reconstruction.stderr.strip() or "no output"
                print(f"  pr {row['pr']:>5}: this clone cannot build the pair ({detail}) — excluded")
            continue
        compared += 1
        queue_tree = _gh(f"repos/{REPO}/commits/{row['mg_head']}")["commit"]["tree"]["sha"]
        is_same = tree == queue_tree
        same += is_same
        base_same = row["mg_parent"] == row["base"]
        flag = "" if is_same == base_same else "  <-- INVARIANT BROKEN"
        print(f"  pr {row['pr']:>5}: tree_same={is_same!s:5} base_same={base_same!s:5}{flag}")

    if conflicted:
        print(f"\nExcluded {len(conflicted)} of {len(rows)} row(s) (the pair conflicts when "
              f"replayed): pr {', '.join(str(pr) for pr in conflicted)}.")
    if unbuildable:
        print(f"\nExcluded {len(unbuildable)} of {len(rows)} row(s) (this clone cannot rebuild "
              f"the pair — fetch the pinned commits or use a fuller clone): "
              f"pr {', '.join(str(pr) for pr in unbuildable)}.")
    if compared == 0:
        print("No row could be compared — nothing was measured.")
        return 1
    print(f"\n{same}/{compared} merge_group runs tested the SAME tree as the PR run "
          f"({same / compared * 100:.1f}%).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    jobs = sub.add_parser("jobs", help="how long each job class takes, and its timeout headroom")
    jobs.add_argument("--runs", required=True, help="pinned run ids (JSON)")
    jobs.add_argument("--cache", help="directory to keep one jobs payload per run in")
    jobs.set_defaults(func=mode_jobs)
    trees = sub.add_parser("trees", help="did merge_group test the same tree as pull_request")
    trees.add_argument("--trees", required=True, help="pinned rows (JSON)")
    trees.add_argument("--repo", default=".", help="clone whose objects hold the pinned commits")
    trees.set_defaults(func=mode_trees)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
