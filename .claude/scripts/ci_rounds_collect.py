#!/usr/bin/env python3
"""Collect a PR-created cohort and its lifetime Required CI evidence via gh.

Run ci_rounds.py on the snapshot; this collector never changes existing metrics.
A cache is an observation, not a live view. Use a fresh cache for a new observation.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode

import trace_retro


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")
    return parsed


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def api(endpoint: str) -> object:
    return trace_retro._gh(["api", endpoint])


def pages(endpoint: str, key: str | None = None) -> list[dict]:
    rows = []
    for page in range(1, 10001):
        separator = "&" if "?" in endpoint else "?"
        data = api(f"{endpoint}{separator}per_page=100&page={page}")
        items = data[key] if key else data
        if not isinstance(items, list):
            raise ValueError(f"invalid page: {endpoint}")
        rows.extend(items)
        if len(items) < 100:
            if key and data["total_count"] != len(rows):
                raise ValueError(f"incomplete pagination: {endpoint}")
            return rows
    raise ValueError(f"pagination limit reached: {endpoint}")


def prs(repo: str, since: datetime, until: datetime) -> list[dict]:
    rows_by_number = {}
    for page in range(1, 10001):
        data = api(f"repos/{repo}/pulls?state=all&sort=created&direction=desc&per_page=100&page={page}")
        if not isinstance(data, list):
            raise ValueError("invalid PR page")
        for pull in data:
            if since <= instant(pull["created_at"]) < until:
                rows_by_number.setdefault(pull["number"], pull)
        print(f"PR page {page}: cohort={len(rows_by_number)}", flush=True)
        if len(data) < 100 or instant(data[-1]["created_at"]) < since:
            return list(rows_by_number.values())
    raise ValueError("PR pagination limit reached")


def runs(repo: str, since: datetime, until: datetime) -> list[dict]:
    query = urlencode({"event": "pull_request", "created": f"{stamp(since)}..{stamp(until)}"})
    endpoint = f"repos/{repo}/actions/workflows/required-ci.yml/runs?{query}"
    first = api(endpoint + "&per_page=100&page=1")
    if first["total_count"] >= 1000:
        if until - since <= timedelta(seconds=1):
            raise ValueError("run search cap within one second")
        midpoint = since + timedelta(seconds=int((until - since).total_seconds()) // 2)
        by_id = {r["id"]: r for r in runs(repo, since, midpoint) + runs(repo, midpoint, until)}
        return list(by_id.values())
    rows = pages(endpoint, "workflow_runs")
    return [r for r in rows if since <= instant(r["created_at"]) < until]


def associate(run: dict, cohort: list[dict]) -> list[int]:
    """Prefer GitHub's PR association; branch fallback must be unambiguous.

    GitHub empties pull_requests on many merged/deleted branches. A unique
    repository+branch+lifetime match supplies evidence there, never SHA alone.
    """
    linked = {p["number"] for p in run["pull_requests"]}
    if linked:
        return [p["number"] for p in cohort if p["number"] in linked]
    candidates = [
        p["number"] for p in cohort
        if p["head"]["ref"] == run["head_branch"]
        and p["head"].get("repo")
        and p["head"]["repo"]["full_name"] == run["repository"]["full_name"]
        and instant(p["created_at"]) <= instant(run["created_at"])
        and (not p["closed_at"] or instant(run["created_at"]) <= instant(p["closed_at"]))
    ]
    if len(candidates) > 1:
        raise ValueError(f"ambiguous branch association: run {run['id']}")
    return candidates


def suites(batch: list[dict]) -> dict[int, list[dict]]:
    selections = " ".join(
        f'r{i}:node(id:{json.dumps(r["check_suite_node_id"])}) {{ ... on CheckSuite {{ '
        'checkRuns(first:100) { pageInfo { hasNextPage } nodes { name conclusion status '
        'startedAt completedAt detailsUrl } } } }'
        for i, r in enumerate(batch)
    )
    response = trace_retro._gh(["api", "graphql", "-f", f"query=query {{ {selections} }}"])
    if response.get("errors"):
        raise ValueError(f"GraphQL errors: {response['errors']}")
    result = {}
    for i, run in enumerate(batch):
        suite = response["data"][f"r{i}"]
        if suite is None or suite["checkRuns"]["pageInfo"]["hasNextPage"]:
            raise ValueError(f"missing or truncated check suite: {run['id']}")
        result[run["id"]] = suite["checkRuns"]["nodes"]
    return result


def collect(repo: str, since: datetime, until: datetime, output: Path, timelines: bool, only_multi_head: bool = False) -> dict:
    cohort = prs(repo, since, until)
    collected = []
    day = since
    while day < until:
        end = min(day + timedelta(days=1), until)
        collected.extend(runs(repo, day, end))
        print(f"Runs through {stamp(end)}: {len(collected)}", flush=True)
        day = end
    matched = []
    unmatched = []
    for run in collected:
        numbers = associate(run, cohort)
        if numbers:
            run["cohort_prs"] = numbers
            matched.append(run)
        else:
            unmatched.append(run["id"])
    checks = {}
    for offset in range(0, len(matched), 25):
        checks.update(suites(matched[offset:offset + 25]))
        print(f"Check suites: {len(checks)}/{len(matched)}", flush=True)
    data = {
        "schema_version": 1, "repo": repo, "since": stamp(since), "until": stamp(until),
        "observed_at": stamp(datetime.now(UTC)), "timelines_complete": False,
        "outside_cohort_run_ids": unmatched,
        "prs": [
            {"number": p["number"], "title": p["title"], "created_at": p["created_at"],
             "closed_at": p["closed_at"], "merged_at": p["merged_at"], "branch": p["head"]["ref"],
             "timeline": []} for p in cohort
        ],
        "runs": [
            {**{k: r[k] for k in ("id", "head_sha", "head_branch", "created_at", "updated_at",
                                   "conclusion", "run_attempt", "cohort_prs", "html_url")},
             "association": "github" if r["pull_requests"] else "unique_branch_lifetime",
             "commit_message": (r.get("head_commit") or {}).get("message", ""),
             "checks": checks[r["id"]]} for r in matched
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    if timelines:
        enrich_timelines(output, output, repo, only_multi_head=only_multi_head)
        data = json.loads(output.read_text())
    return data


def enrich_timelines(snapshot: Path, output: Path, repo: str, only_multi_head: bool = False) -> None:
    data = json.loads(snapshot.read_text())
    head_counts = {
        number: len({run["head_sha"] for run in data["runs"] if number in run["cohort_prs"]})
        for number in {number for run in data["runs"] for number in run["cohort_prs"]}
    }
    matched = {number for run in data["runs"] for number in run["cohort_prs"]}
    wanted = [pr for pr in data["prs"] if pr["number"] in matched
              and (not only_multi_head or head_counts[pr["number"]] >= 2)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = pool.map(lambda pr: pages(f"repos/{repo}/issues/{pr['number']}/timeline"), wanted)
        for index, (pr, events) in enumerate(zip(wanted, fetched)):
            pr["timeline"] = [
                {k: e[k] for k in ("event", "created_at", "submitted_at", "sha", "commit_id",
                                    "state", "id", "html_url") if k in e}
                for e in events
                if e.get("event") in {"ready_for_review", "convert_to_draft", "reviewed",
                                      "committed", "head_ref_force_pushed"}
            ]
            print(f"Timelines: {index + 1}/{len(wanted)} PR #{pr['number']}", flush=True)
    data["timelines_complete"] = not only_multi_head
    data["timeline_scope"] = "multi_head" if only_multi_head else "all"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="SageSeekerSociety/cheese")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, help="enrich an existing run snapshot only")
    parser.add_argument("--only-multi-head", action="store_true", help="fetch timelines only for PRs with at least two observed heads")
    parser.add_argument("--skip-timelines", action="store_true", help="exploratory snapshot only")
    args = parser.parse_args()
    trace_retro.CACHE_DIR = str(args.cache)
    if args.snapshot:
        enrich_timelines(args.snapshot, args.output, args.repo, args.only_multi_head)
        return
    if not args.since or not args.until:
        parser.error("--since and --until are required without --snapshot")
    since, until = instant(args.since), instant(args.until)
    if since >= until or until > datetime.now(UTC):
        parser.error("require since < until <= now")
    collect(args.repo, since, until, args.output, not args.skip_timelines, args.only_multi_head)


if __name__ == "__main__":
    main()
