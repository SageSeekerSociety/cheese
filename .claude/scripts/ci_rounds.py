#!/usr/bin/env python3
"""Measure distinct PR-head rounds from ci_rounds_collect.py's snapshot.

`distinct_head_rounds` = a distinct PR head with a pull_request Required CI
workflow run. Draft/full promotion, retries and merge-group runs do not add
heads. Existing retrospective metrics call each workflow execution a round;
this report deliberately uses a different, explicit name. Exact `CI required`
judgements are reported separately, never inferred from quick CI. Attribution
labels describe observable evidence, not an inferred intention.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import statistics
from pathlib import Path

from ci_rounds_collect import instant

FULL = "CI required"
DRAFT = "CI required (draft quick)"
RED = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
BASE_SYNC = re.compile(
    r"^Merge (?:(?:remote-tracking )?branch ['\"]?(?:origin/)?main['\"]?|origin/main|main) into "
)


def before(value: str | None, cutoff: str) -> bool:
    return bool(value and instant(value) < instant(cutoff))


def judged(check: dict, cutoff: str) -> bool:
    return before(check.get("completedAt"), cutoff) and check.get("conclusion") in RED | {"SUCCESS"}


def failed_checks(runs: list[dict], cutoff: str) -> list[dict]:
    latest = {}
    for run in runs:
        for check in run["checks"]:
            if judged(check, cutoff):
                name = check["name"]
                item = {"run_id": run["id"], "name": name, "url": check["detailsUrl"],
                        "at": check["completedAt"], "conclusion": check["conclusion"]}
                if name not in latest or instant(item["at"]) > instant(latest[name]["at"]):
                    latest[name] = item
    # Full success supersedes draft failures and older suite failures on this
    # head. Otherwise keep only each check's latest pre-push judgement.
    if latest.get(FULL, {}).get("conclusion") == "SUCCESS":
        return []
    return [item for item in latest.values() if item["conclusion"] in RED]


def head_rows(runs: list[dict]) -> list[dict]:
    groups = {}
    for run in sorted(runs, key=lambda r: (instant(r["created_at"]), r["id"])):
        groups.setdefault(run["head_sha"], []).append(run)
    return [{"sha": sha, "first_at": rows[0]["created_at"], "runs": rows} for sha, rows in groups.items()]


def extra_head(pr: dict, previous: dict, current: dict) -> dict:
    start, end = previous["first_at"], current["first_at"]
    reviews = [
        e for e in pr["timeline"]
        if e.get("event") == "reviewed" and e.get("state", "").upper() == "CHANGES_REQUESTED"
        and e.get("submitted_at") and instant(start) <= instant(e["submitted_at"]) < instant(end)
    ]
    failures = failed_checks(previous["runs"], end)
    message = current["runs"][0]["commit_message"].split("\n")[0]
    evidence = {"previous_sha": previous["sha"], "sha": current["sha"],
                "run_ids": [r["id"] for r in current["runs"]], "failures_before_push": failures,
                "change_requests": [e.get("html_url") or e.get("id") for e in reviews],
                "commit_subject": message}
    # Mutually exclusive labels; raw evidence remains available for overlap.
    if BASE_SYNC.match(message):
        label = "base_sync_subject"
    elif reviews:
        label = "push_after_change_request"
    elif failures:
        label = "push_after_red"
    else:
        label = "unattributed"
    evidence["label"] = label
    return evidence


def same_head(head: dict, timeline: list[dict], cutoff: str) -> dict:
    rows = head["runs"]
    draft = [r for r in rows if any(c["name"] == DRAFT and judged(c, cutoff) for c in r["checks"])]
    full = [r for r in rows if any(c["name"] == FULL and judged(c, cutoff) for c in r["checks"])]
    promotions = [
        e for e in timeline if e.get("event") == "ready_for_review"
        and e.get("created_at") and before(e["created_at"], cutoff)
        and any(instant(d["created_at"]) <= instant(e["created_at"]) <= instant(f["created_at"])
                for d in draft for f in full)
    ]
    red_to_green = any(
        c["name"] == FULL and judged(c, cutoff) and c["conclusion"] in RED
        and any(g["name"] == FULL and judged(g, cutoff) and g["conclusion"] == "SUCCESS"
                and instant(c["completedAt"]) < instant(g["completedAt"])
                for later in rows for g in later["checks"])
        for r in rows for c in r["checks"]
    )
    return {"sha": head["sha"], "runs": len(rows), "additional_run_ids": [r["id"] for r in rows[1:]],
            "draft_and_full_judged": bool(draft and full),
            "promotion_event_evidenced": bool(promotions), "full_red_to_green": red_to_green,
            "rerun_attempts_observed": sum(max(r["run_attempt"] - 1, 0) for r in rows)}


def distribution(values: list[int]) -> dict:
    return {"prs": len(values), "distinct_head_rounds": sum(values),
            "mean": statistics.mean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "histogram": dict(sorted(Counter(values).items())),
            "at_least_two": sum(n >= 2 for n in values), "zero": values.count(0)}


def summarize(data: dict) -> dict:
    if data.get("schema_version") != 1:
        raise ValueError("unsupported snapshot schema")
    since, cutoff = data["since"], data["until"]
    prs = data["prs"]
    if len({p["number"] for p in prs}) != len(prs):
        raise ValueError("duplicate PRs")
    if len({r["id"] for r in data["runs"]}) != len(data["runs"]):
        raise ValueError("duplicate runs")
    rows = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        if not (instant(since) <= instant(pr["created_at"]) < instant(cutoff)):
            raise ValueError("PR outside birth cohort")
        closed_at = pr["merged_at"] or pr["closed_at"]
        run_until = (instant(closed_at) if closed_at and before(closed_at, cutoff)
                     else instant(cutoff))
        runs = [r for r in data["runs"] if pr["number"] in r["cohort_prs"]
                and instant(pr["created_at"]) <= instant(r["created_at"]) < run_until]
        heads = head_rows(runs)
        extras = [extra_head(pr, a, b) for a, b in zip(heads, heads[1:])]
        full_heads = sum(any(c["name"] == FULL and judged(c, cutoff)
                            for r in h["runs"] for c in r["checks"]) for h in heads)
        status = "merged" if before(pr["merged_at"], cutoff) else "closed" if before(pr["closed_at"], cutoff) else "open"
        right_censored = not before(closed_at, cutoff)
        rows.append({"number": pr["number"], "state_at_cutoff": status,
                     "distinct_head_rounds": len(heads),
                     "exposure_hours": (run_until - instant(pr["created_at"])).total_seconds() / 3600,
                     "right_censored": right_censored,
                     "full_judged_heads": full_heads, "extra_heads": extras,
                     "same_head": [same_head(h, pr["timeline"], cutoff) for h in heads],
                     "head_runs": [{"sha": h["sha"], "run_ids": [r["id"] for r in h["runs"]]} for h in heads]})
    extras = [e for p in rows for e in p["extra_heads"]]
    same = [h for p in rows for h in p["same_head"]]
    failures = Counter(c["name"] for e in extras for c in e["failures_before_push"])
    cancellations = [r["id"] for r in data["runs"] if r["conclusion"] == "cancelled" and before(r["updated_at"], cutoff)]
    return {
        "definition": "one distinct PR head with a pull_request Required CI workflow run; quick/full both included",
        "cohort": {k: data[k] for k in ("repo", "since", "until", "observed_at", "timelines_complete")}
                  | {"timeline_scope": data.get("timeline_scope", "all")},
        "timeline_evidence": {
            "complete": bool(data.get("timelines_complete", True)),
            "scope": data.get("timeline_scope", "all"),
            "prs_with_timeline": sum(bool(p["timeline"]) for p in prs),
            "note": ("promotion and review attribution are lower bounds within this scope"
                     if not data.get("timelines_complete", True) else "all matched PR timelines fetched"),
        },
        "all_prs": distribution([p["distinct_head_rounds"] for p in rows]),
        "right_censored_prs": sum(p["right_censored"] for p in rows),
        "interpretation": "open PR counts are lower bounds at cutoff; all-PR mean is not a completed-lifetime mean",
        "by_state_at_cutoff": {s: distribution([p["distinct_head_rounds"] for p in rows if p["state_at_cutoff"] == s])
                               for s in ("merged", "open", "closed")},
        "full_judged_heads": sum(p["full_judged_heads"] for p in rows),
        "extra_head_attribution": dict(Counter(e["label"] for e in extras)),
        "extra_head_total": len(extras), "failed_checks_before_extra_heads": dict(failures.most_common()),
        "same_head_overhead": {"additional_workflow_runs": sum(h["runs"] - 1 for h in same),
                               "draft_and_full_judged_heads": sum(h["draft_and_full_judged"] for h in same),
                               "promotion_event_evidenced_heads": sum(h["promotion_event_evidenced"] for h in same),
                               "full_red_to_green_heads": sum(h["full_red_to_green"] for h in same),
                               "rerun_attempts_observed": sum(h["rerun_attempts_observed"] for h in same)},
        "cancelled_run_ids": cancellations,
        "check_evidence": {
            "scope": "current attempt per workflow run, as observed",
            "note": "historical attempt failures are not collected; red-to-green and failure attribution may be incomplete",
            "reruns": "rerun_attempts_observed counts attempts visible at observation time, not necessarily before cutoff",
            "attribution": "run creation time bounds the evidence interval; labels do not prove why a push happened",
        },
        "flaky_checks": {"verified": None, "reason": "same-head red-to-green is not proof of a flaky test; requires attempt logs"},
        "prs": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(json.loads(args.snapshot.read_text()))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in {"prs", "cancelled_run_ids"}}, indent=2))


if __name__ == "__main__":
    main()
