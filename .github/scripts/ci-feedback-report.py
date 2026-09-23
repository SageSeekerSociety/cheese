#!/usr/bin/env python3
"""Collect reproducible Required CI feedback-time evidence from GitHub Actions."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

FAILURES = {"failure", "timed_out", "action_required", "startup_failure"}


def evidence_suites(record: dict) -> list[str]:
    if record["workflow"] == "remote-execution.yml":
        return ["remote-acceptance", "private-chat"]
    if (
        record["workflow"] == "required-ci.yml"
        and "e2e / e2e" in record["executed_job_names"]
    ):
        return ["e2e"]
    return []


def validate_evidence(value: dict, record: dict, suite: str) -> str | None:
    expected = {
        "schema_version": 1,
        "run_id": record["run_id"],
        "run_attempt": record["attempt"],
        "head_sha": record["head_sha"],
        "suite": suite,
    }
    if not isinstance(value, dict) or any(
        type(value.get(key)) is not type(item) or value.get(key) != item
        for key, item in expected.items()
    ):
        return "evidence_identity_mismatch"
    if type(value.get("clean")) is not bool or not isinstance(value.get("reason"), str):
        return "invalid_evidence_status"
    if any(
        type(value.get(key)) is not int or value[key] < 0
        for key in ("tests", "retries", "skipped")
    ):
        return "invalid_evidence_counts"
    if value["clean"] and (value["tests"] == 0 or value["retries"] or value["skipped"]):
        return "contradictory_clean_evidence"
    return None


def evidence_json(archive: bytes) -> dict:
    # Read only the small normalized receipt; never extract artifact paths.
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        files = bundle.infolist()
        if (
            len(files) != 1
            or files[0].filename != "evidence.json"
            or files[0].file_size > 1_000_000
        ):
            raise ValueError("expected one bounded evidence.json")
        return json.loads(bundle.read(files[0]))


def collect_evidence(records: list[dict], repo: str, output: Path, stamp: str) -> None:
    inventories: dict[int, list[dict] | None] = {}
    for record in records:
        suites = evidence_suites(record)
        evidence = {"status": "unknown", "suites": {}, "reason": "missing_evidence"}
        record["test_evidence"] = evidence
        if record["outcome"] != "success":
            evidence.update(
                status="unknown" if record["outcome"] == "pending" else "not_clean",
                reason=f"workflow_{record['outcome']}",
            )
            continue
        if not suites:
            evidence.update(status="not_applicable", reason="no_instrumented_suite")
            continue
        if record["attempt"] != 1 or record["latest_attempt"] != 1:
            evidence.update(status="not_clean", reason="workflow_rerun")
            continue
        run_id = record["run_id"]
        if run_id not in inventories:
            try:
                pages = gh_pages(
                    f"repos/{repo}/actions/runs/{run_id}/artifacts", {"per_page": "100"}
                )
                atomic_new(
                    output / "raw" / "artifacts" / f"{stamp}-{run_id}.json", pages
                )
                artifacts = flatten(pages, "artifacts")
                if any(page.get("total_count", 0) > len(artifacts) for page in pages):
                    raise ValueError("incomplete artifact inventory")
                inventories[run_id] = artifacts
            except (subprocess.CalledProcessError, ValueError) as error:
                inventories[run_id] = None
                log_line(
                    output / "progress.log",
                    str(run_id),
                    f"evidence unavailable: {type(error).__name__}",
                )
        for suite in suites:
            name = f"ci-test-evidence-{run_id}-{record['attempt']}-{suite}"
            matches = [
                item for item in inventories[run_id] or [] if item.get("name") == name
            ]
            log_line(output / "progress.log", name, "evidence start")
            item = {"status": "unknown", "reason": "missing_or_ambiguous_artifact"}
            evidence["suites"][suite] = item
            if len(matches) != 1 or matches[0].get("expired"):
                log_line(
                    output / "progress.log",
                    name,
                    "evidence unknown: missing, expired, or ambiguous",
                )
                continue
            artifact_id = matches[0]["id"]
            path = output / "raw" / "evidence" / f"{artifact_id}.json"
            try:
                if path.exists():
                    value = json.loads(path.read_text())
                else:
                    value = evidence_json(
                        subprocess.check_output(
                            [
                                "gh",
                                "api",
                                f"repos/{repo}/actions/artifacts/{artifact_id}/zip",
                            ]
                        )
                    )
                    atomic_new(path, value)
                problem = validate_evidence(value, record, suite)
                item.update(artifact_id=artifact_id, receipt=value)
                item.update(
                    status="unknown"
                    if problem
                    else "clean"
                    if value["clean"]
                    else "not_clean",
                    reason=problem or value["reason"],
                )
            except (
                subprocess.CalledProcessError,
                ValueError,
                zipfile.BadZipFile,
                KeyError,
            ) as error:
                item.update(reason=f"unreadable_artifact:{type(error).__name__}")
            log_line(
                output / "progress.log",
                name,
                f"evidence {item['status']}: {item['reason']}",
            )
        states = [item["status"] for item in evidence["suites"].values()]
        status = (
            "not_clean"
            if "not_clean" in states
            else "unknown"
            if "unknown" in states
            else "clean"
        )
        evidence.update(
            status=status,
            reason="all_suites_clean" if status == "clean" else "see_suite_evidence",
        )


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def seconds(start: object, end: object) -> float | None:
    left, right = parse_time(start), parse_time(end)
    if left is None or right is None or right < left:
        return None
    return (right - left).total_seconds()


def ordered_completion(job: dict) -> dt.datetime | None:
    created = parse_time(job.get("created_at"))
    started = parse_time(job.get("started_at"))
    completed = parse_time(job.get("completed_at"))
    if completed is None:
        return None
    if (started and completed < started) or (created and completed < created):
        return None
    return completed


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def distribution(values: list[float]) -> dict:
    return {
        "count": len(values),
        "median_seconds": statistics.median(values) if values else None,
        "p90_seconds_nearest_rank": nearest_rank(values, 0.9),
    }


def outcome(attempt_run: dict, jobs: list[dict]) -> str:
    if attempt_run.get("status") != "completed":
        return "pending"
    conclusion = attempt_run.get("conclusion")
    if conclusion in FAILURES:
        return "failure"
    if conclusion == "cancelled":
        return "cancelled"
    if (
        conclusion == "success"
        and jobs
        and any(job.get("conclusion") == "success" for job in jobs)
    ):
        return "success"
    return "other"


def analyse_attempt(
    attempt_run: dict, attempt: int, jobs: list[dict], workflow: str, event: str
) -> dict:
    unknown: list[str] = []
    created = attempt_run.get("created_at")
    executed = [job for job in jobs if job.get("conclusion") != "skipped"]
    valid_completed = [value for job in executed if (value := ordered_completion(job))]
    final = max(valid_completed).isoformat() if valid_completed else None
    complete_job_timing = bool(executed) and len(valid_completed) == len(executed)
    elapsed = seconds(created, final) if complete_job_timing else None
    if elapsed is None:
        unknown.append("run_created_to_final_job_completed")

    failed = [job for job in jobs if job.get("conclusion") in FAILURES]
    failed_times = [value for job in failed if (value := ordered_completion(job))]
    first_failed = min(failed_times).isoformat() if failed_times else None
    first_failure = (
        seconds(created, first_failed) if len(failed_times) == len(failed) else None
    )
    if failed and first_failure is None:
        unknown.append("run_created_to_first_failure")

    queues: list[float] = []
    queue_unknown = 0
    for job in executed:
        queued = seconds(job.get("created_at"), job.get("started_at"))
        if queued is None:
            queue_unknown += 1
        else:
            queues.append(queued)

    return {
        "workflow": workflow,
        "event": event,
        "run_id": attempt_run.get("id"),
        "attempt": attempt,
        "latest_attempt": attempt_run.get(
            "latest_attempt", attempt_run.get("run_attempt")
        ),
        "head_sha": attempt_run.get("head_sha"),
        "run_created_at": created,
        "outcome": outcome(attempt_run, jobs),
        "final_job_completed_at": final,
        "run_created_to_final_job_completed_seconds": elapsed,
        "run_created_to_first_failure_seconds": first_failure,
        "job_queue_seconds": queues,
        "job_queue_unknown_count": queue_unknown,
        "job_count": len(jobs),
        "executed_job_names": sorted(str(job.get("name")) for job in executed),
        "unknown_metrics": unknown,
    }


def summarize(records: list[dict]) -> dict:
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record["workflow"], record["event"]), []).append(record)
    result = {}
    for (workflow, event), attempts in sorted(groups.items()):
        latest = [
            item for item in attempts if item["attempt"] == item["latest_attempt"]
        ]
        first_attempts = [item for item in attempts if item["attempt"] == 1]
        evidence_first = [
            item
            for item in first_attempts
            if item.get("test_evidence", {}).get("status") != "not_applicable"
        ]
        clean_streak = 0
        if any(
            parse_time(item.get("run_created_at")) is None for item in evidence_first
        ):
            clean_streak = None
        else:
            for item in sorted(
                evidence_first,
                key=lambda item: (item["run_created_at"], item["run_id"]),
                reverse=True,
            ):
                if item.get("test_evidence", {}).get("status") != "clean":
                    break
                clean_streak += 1
        ordered_first = sorted(
            first_attempts,
            key=lambda item: (item.get("run_created_at") or "", item["run_id"]),
        )
        streak = (
            None
            if any(
                parse_time(item.get("run_created_at")) is None for item in ordered_first
            )
            else 0
        )
        if streak is not None:
            for item in reversed(ordered_first):
                if item["outcome"] != "success":
                    break
                streak += 1
        success_elapsed = [
            item["run_created_to_final_job_completed_seconds"]
            for item in latest
            if item["outcome"] == "success"
            and item["run_created_to_final_job_completed_seconds"] is not None
        ]
        first_failures = [
            item["run_created_to_first_failure_seconds"]
            for item in attempts
            if item["run_created_to_first_failure_seconds"] is not None
        ]
        queues = [value for item in attempts for value in item["job_queue_seconds"]]
        selection_groups: dict[str, list[dict]] = {}
        for item in latest:
            signature = " | ".join(item["executed_job_names"]) or "(no executed jobs)"
            selection_groups.setdefault(signature, []).append(item)
        result[f"{workflow}:{event}"] = {
            "unique_run_count": len(latest),
            "attempt_count": len(attempts),
            "cohort_tail_consecutive_clean_first_attempts": clean_streak,
            "first_attempt_test_evidence": {
                state: sum(
                    item.get("test_evidence", {}).get("status", "unknown") == state
                    for item in first_attempts
                )
                for state in ("clean", "not_clean", "unknown", "not_applicable")
            },
            "cohort_tail_consecutive_first_attempt_successes": streak,
            "first_attempt_outcomes": {
                name: sum(item["outcome"] == name for item in first_attempts)
                for name in ("success", "failure", "cancelled", "pending", "other")
            },
            "attempt_outcomes": {
                name: sum(item["outcome"] == name for item in attempts)
                for name in ("success", "failure", "cancelled", "pending", "other")
            },
            "latest_attempt_outcomes": {
                name: sum(item["outcome"] == name for item in latest)
                for name in ("success", "failure", "cancelled", "pending", "other")
            },
            "successful_latest_run_created_to_all_jobs_complete_api_proxy": (
                distribution(success_elapsed)
            ),
            "attempt_run_created_to_first_failure": distribution(first_failures),
            "job_created_to_started_where_available": distribution(queues),
            "job_queue_unknown_count": sum(
                item["job_queue_unknown_count"] for item in attempts
            ),
            "latest_attempt_selection_cohorts": {
                signature: {
                    "run_count": len(items),
                    "successful_run_created_to_all_jobs_complete_api_proxy": distribution(
                        [
                            item["run_created_to_final_job_completed_seconds"]
                            for item in items
                            if item["outcome"] == "success"
                            and item["run_created_to_final_job_completed_seconds"]
                            is not None
                        ]
                    ),
                    "latest_attempt_outcomes": {
                        name: sum(item["outcome"] == name for item in items)
                        for name in (
                            "success",
                            "failure",
                            "cancelled",
                            "pending",
                            "other",
                        )
                    },
                }
                for signature, items in sorted(selection_groups.items())
            },
            "attempts_with_unknown_metrics": [
                {
                    "run_id": item["run_id"],
                    "attempt": item["attempt"],
                    "metrics": item["unknown_metrics"],
                }
                for item in attempts
                if item["unknown_metrics"]
            ],
        }
    return result


def gh_pages(endpoint: str, fields: dict[str, str] | None = None) -> list[dict]:
    command = ["gh", "api", "--method", "GET", "--paginate", "--slurp", endpoint]
    for key, value in (fields or {}).items():
        command.extend(["-f", f"{key}={value}"])
    return json.loads(subprocess.check_output(command, text=True))


def atomic_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def flatten(pages: list[dict], key: str) -> list[dict]:
    return [item for page in pages for item in page.get(key, [])]


def log_line(path: Path, item: str, state: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()}\t{state}\t{item}\n")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def collect(args: argparse.Namespace) -> dict:
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    inputs = {
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "repo": args.repo,
        "workflows": args.workflow,
        "since": args.since,
        "until": args.until,
        "events": getattr(args, "event", None) or ["pull_request", "merge_group"],
    }
    inputs_path = output / "inputs.json"
    if inputs_path.exists():
        if json.loads(inputs_path.read_text()) != inputs:
            raise SystemExit(f"{output} already belongs to different inputs")
    else:
        atomic_new(inputs_path, inputs)

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    progress = output / "progress.log"
    records: list[dict] = []
    for workflow in args.workflow:
        for event in inputs["events"]:
            label = f"runs {workflow} {event}"
            log_line(progress, label, "start")
            endpoint = (
                f"repos/{args.repo}/actions/workflows/{quote(workflow, safe='')}/runs"
            )
            try:
                pages = gh_pages(
                    endpoint,
                    {
                        "per_page": "100",
                        "event": event,
                        "created": f"{args.since}..{args.until}",
                    },
                )
            except Exception as error:
                log_line(progress, label, f"error {type(error).__name__}: {error}")
                raise
            atomic_new(
                output / "raw" / "runs" / f"{stamp}-{safe_name(workflow)}-{event}.json",
                pages,
            )
            runs = flatten(pages, "workflow_runs")
            if (
                any(page.get("total_count", 0) > len(runs) for page in pages)
                or len(runs) >= 1000
            ):
                log_line(
                    progress, label, f"error incomplete-or-capped count={len(runs)}"
                )
                raise SystemExit(
                    f"{label}: API search cap reached or pagination incomplete "
                    f"({len(runs)} runs)"
                )
            log_line(progress, label, f"end count={len(runs)}")
            for run in runs:
                for attempt in range(1, int(run.get("run_attempt") or 1) + 1):
                    key = f"{run['id']}-attempt-{attempt}"
                    record_path = output / "records" / f"{key}.json"
                    if record_path.exists():
                        record = json.loads(record_path.read_text())
                        # A later collection may reveal a rerun. Keep the immutable
                        # attempt record, but use today's snapshot to classify latest.
                        record["latest_attempt"] = run.get("run_attempt")
                        records.append(record)
                        log_line(progress, key, "cached")
                        continue
                    log_line(progress, key, "start")
                    try:
                        attempt_pages = gh_pages(
                            f"repos/{args.repo}/actions/runs/{run['id']}/attempts/{attempt}"
                        )
                        job_pages = gh_pages(
                            f"repos/{args.repo}/actions/runs/{run['id']}/attempts/{attempt}/jobs",
                            {"per_page": "100"},
                        )
                    except Exception as error:
                        log_line(
                            progress, key, f"error {type(error).__name__}: {error}"
                        )
                        raise
                    if len(attempt_pages) != 1 or not isinstance(
                        attempt_pages[0], dict
                    ):
                        log_line(progress, key, "error invalid attempt response")
                        raise SystemExit(f"{key}: invalid workflow attempt response")
                    atomic_new(
                        output / "raw" / "attempts" / f"{stamp}-{key}.json",
                        attempt_pages[0],
                    )
                    atomic_new(
                        output / "raw" / "jobs" / f"{stamp}-{key}.json", job_pages
                    )
                    jobs = flatten(job_pages, "jobs")
                    if any(
                        page.get("total_count", 0) > len(jobs) for page in job_pages
                    ):
                        log_line(progress, key, f"error incomplete jobs={len(jobs)}")
                        raise SystemExit(f"{key}: job pagination incomplete")
                    attempt_run = attempt_pages[0]
                    attempt_run["latest_attempt"] = run.get("run_attempt")
                    record = analyse_attempt(
                        attempt_run, attempt, jobs, workflow, event
                    )
                    records.append(record)
                    if record["outcome"] != "pending":
                        atomic_new(record_path, record)
                    log_line(
                        progress,
                        key,
                        f"end outcome={record['outcome']} jobs={len(jobs)}",
                    )

    # Evidence availability can change after a completed attempt (upload delay,
    # expiration). Re-observe it without rewriting immutable attempt records.
    collect_evidence(records, args.repo, output, stamp)
    report = {
        "generated_at": utc_now(),
        "inputs": inputs,
        "attempts": records,
        "summary": summarize(records),
    }
    report_path = output / f"report-{stamp}.json"
    atomic_new(report_path, report)
    print(
        json.dumps(
            {"report": str(report_path), "summary": report["summary"]},
            indent=2,
            sort_keys=True,
        )
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--workflow", action="append", required=True)
    parser.add_argument(
        "--event",
        action="append",
        choices=["pull_request", "merge_group", "push", "workflow_dispatch"],
    )
    parser.add_argument(
        "--since", required=True, help="inclusive UTC run-created bound"
    )
    parser.add_argument(
        "--until", required=True, help="inclusive UTC run-created bound"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if len(set(args.workflow)) != len(args.workflow):
        parser.error("--workflow values must be unique")
    if args.event and len(set(args.event)) != len(args.event):
        parser.error("--event values must be unique")
    since, until = parse_time(args.since), parse_time(args.until)
    if (
        since is None
        or until is None
        or since.utcoffset() != dt.timedelta(0)
        or until.utcoffset() != dt.timedelta(0)
    ):
        parser.error("--since and --until must be timezone-aware UTC timestamps")
    if since > until:
        parser.error("--since must not follow --until")
    collect(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
