#!/usr/bin/env python3
"""Collect the first-push job failures of PRs merged into a base branch.

The unit is one merged PR. Its "first head" is the head SHA of the earliest
`required-ci.yml` `pull_request` run associated with the PR's branch. Every job
of every run on that head is read; each name that concluded `failure` or
`timed_out` is recorded once per PR, except the ones that only restate another
job's verdict: `CI required`, `CI required (draft quick)`, `scope`. `fast` (the
draft tier's inline verdict job) and `Backend coverage complete` (the backend
matrix's aggregator) are kept — they judge something a leaf cannot see — but
they are not leaf jobs either, so a ranking built from this file says which row
is which instead of calling all of them leaves. Whether the PR was ever a gate
red is readable from `gate_conclusions` alone: a gate that concluded `failure`
counts even when another run on the same head was cancelled, because that one
did conclude — `first_head_cancelled` says the head was superseded, not that
nothing was judged. A first head whose runs all ended before `CI required`
concluded is not a gate red.

This is a measurement, not a gate. It reuses `trace_retro._gh` for GitHub
access, and every response — the merged-PR listing included — goes through the
cache, so a re-run over the same window with the same `--cache` is free and
deterministic. The cache is keyed by the request alone, so a different window
wants its own directory: pages fetched for one window answer for any other.
`--csv` writes the per-PR rows, which is what `docs/evidence/` links to.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from urllib.parse import quote, urlencode

import trace_retro

REQUIRED_CI = ".github/workflows/required-ci.yml"

# Jobs that restate another job's verdict rather than judge a change.
AGGREGATORS = frozenset({"CI required", "CI required (draft quick)", "scope"})
# The one aggregator branch protection reads. A PR whose first head ever left
# this red is a gate red, whatever its draft branch did.
GATE_JOB = "CI required"

CONCLUDED_RED = frozenset({"failure", "timed_out", "action_required"})

CSV_COLUMNS = (
    "since", "until", "pr", "branch", "merged_at", "first_head",
    "first_head_cancelled", "gate_conclusions", "red_jobs",
)


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")
    return parsed


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def cache_key(repo: str, kind: str, name: str, page: int) -> str:
    """A file name the request maps to one-to-one.

    Folding `/` to `_` is not injective: branches `a/b` and `a_b` would share a
    file, and so would page 2 of branch `x` and page 1 of branch `x-p2`. Either
    collision hands a PR another branch's runs with nothing to notice, so the
    name is quoted and the page number is in every key — the trailing `-p<digits>`
    is then an anchor that recovers both parts exactly.
    """
    return f"{kind}-{repo.replace('/', '_')}-{quote(name, safe='')}-p{page}"


class Cache:
    """One JSON file per key. A cache is an observation, not a live view."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, fetch):
        path = self.root / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except ValueError:
                # A run killed mid-write leaves half a file. It is an observation
                # that never finished: refetch it instead of taking the sweep down.
                path.unlink()
        value = fetch()
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps(value))
        partial.replace(path)
        return value


def api(endpoint: str) -> object:
    return trace_retro._gh(["api", endpoint])


def merged_prs(repo: str, base: str, since: datetime, until: datetime, cache: Cache) -> list[dict]:
    """PRs whose merged_at lies in [since, until)."""
    rows: dict[int, dict] = {}
    for page in range(1, 2001):
        def fetch(p: int = page) -> object:
            return api(
                f"repos/{repo}/pulls?state=closed&base={base}"
                f"&sort=updated&direction=desc&per_page=100&page={p}"
            )

        data = cache.get(cache_key(repo, "prs", base, page), fetch)
        if not isinstance(data, list):
            raise ValueError("invalid PR page")
        for pull in data:
            merged = pull.get("merged_at")
            if merged and since <= instant(merged) < until:
                rows.setdefault(pull["number"], pull)
        print(f"PR page {page}: merged-cohort={len(rows)}", flush=True)
        if len(data) < 100 or instant(data[-1]["updated_at"]) < since:
            break
    return list(rows.values())


def branch_runs(repo: str, branch: str, cache: Cache) -> list[dict]:
    runs = []
    for page in range(1, 21):
        def fetch(p: int = page) -> object:
            return api(
                f"repos/{repo}/actions/runs?"
                + urlencode({"event": "pull_request", "branch": branch, "per_page": 100, "page": p})
            )

        data = cache.get(cache_key(repo, "branch", branch, page), fetch)
        items = data["workflow_runs"]
        runs.extend(items)
        if len(items) < 100:
            break
    return runs


def run_jobs(repo: str, run_id: int, cache: Cache) -> list[dict]:
    def fetch():
        jobs: list[dict] = []
        for page in range(1, 21):
            data = api(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}")
            jobs.extend(data["jobs"])
            if len(data["jobs"]) < 100:
                break
        return jobs

    return cache.get(cache_key(repo, "jobs", str(run_id), 1), fetch)


def first_head_runs(pr: dict, runs: list[dict], until: datetime) -> tuple[str | None, list[dict]]:
    """The earliest required-ci run on the PR's branch within the PR's lifetime."""
    lo = instant(pr["created_at"])
    hi = instant(pr["closed_at"]) if pr.get("closed_at") else until
    hi = min(hi, until)
    usable = [
        r for r in runs
        if r["path"] == REQUIRED_CI
        and r["head_branch"] == pr["head"]["ref"]
        and lo <= instant(r["created_at"]) <= hi
    ]
    if not usable:
        return None, []
    usable.sort(key=lambda r: (r["created_at"], r["id"]))
    head = usable[0]["head_sha"]
    same_head = [r for r in usable if r["head_sha"] == head]
    same_head.sort(key=lambda r: (r["created_at"], r["id"]))
    return head, same_head


def collect(repo: str, base: str, since: datetime, until: datetime, cache: Cache) -> dict:
    prs = merged_prs(repo, base, since, until, cache)
    print(f"merged PRs in window: {len(prs)}", flush=True)

    rows = []

    def one(pr: dict) -> dict:
        runs = branch_runs(repo, pr["head"]["ref"], cache)
        head, head_runs = first_head_runs(pr, runs, until)
        red: dict[str, int] = {}
        cancelled = False
        gate: set[str] = set()
        for run in head_runs:
            if run["conclusion"] == "cancelled":
                cancelled = True
            for job in run_jobs(repo, run["id"], cache):
                if job["name"] == GATE_JOB:
                    gate.add(job.get("conclusion"))
                elif job.get("conclusion") in CONCLUDED_RED and job["name"] not in AGGREGATORS:
                    red[job["name"]] = red.get(job["name"], 0) + 1
        return {
            "number": pr["number"],
            "branch": pr["head"]["ref"],
            "created_at": pr["created_at"],
            "merged_at": pr["merged_at"],
            "first_head": head,
            "first_head_runs": [r["id"] for r in head_runs],
            "first_head_cancelled": cancelled,
            "red_jobs": sorted(red),
            "gate_conclusions": sorted(g for g in gate if g),
            "no_run": head is None,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, row in enumerate(pool.map(one, prs), 1):
            rows.append(row)
            if i % 50 == 0:
                print(f"  scanned {i}/{len(prs)}", flush=True)

    counts: dict[str, int] = {}
    for row in rows:
        for name in row["red_jobs"]:
            counts[name] = counts.get(name, 0) + 1

    return {
        "repo": repo,
        "base": base,
        "since": stamp(since),
        "until": stamp(until),
        "observed_at": stamp(datetime.now(UTC)),
        "pr_count": len(rows),
        "with_first_head": sum(1 for r in rows if r["first_head"]),
        "prs_with_any_red": sum(1 for r in rows if r["red_jobs"]),
        "prs_with_gate_red": sum(1 for r in rows if set(r["gate_conclusions"]) & CONCLUDED_RED),
        "failures_by_job": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "rows": sorted(rows, key=lambda r: r["number"]),
    }


def write_csv(path: Path, snapshot: dict) -> None:
    """The per-PR rows, with the window on every row so two windows can be told
    apart by reading one of them."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in snapshot["rows"]:
            writer.writerow([
                snapshot["since"],
                snapshot["until"],
                row["number"],
                row["branch"],
                row["merged_at"],
                row["first_head"] or "",
                "true" if row["first_head_cancelled"] else "false",
                "|".join(row["gate_conclusions"]),
                "|".join(row["red_jobs"]),
            ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="SageSeekerSociety/cheese")
    parser.add_argument("--base", default="main")
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--csv", help="also write the per-PR rows as CSV")
    args = parser.parse_args()

    snapshot = collect(
        args.repo,
        args.base,
        instant(args.since),
        instant(args.until),
        Cache(Path(args.cache)),
    )
    Path(args.output).write_text(json.dumps(snapshot, indent=2))
    print(f"wrote {args.output}")
    if args.csv:
        write_csv(Path(args.csv), snapshot)
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
