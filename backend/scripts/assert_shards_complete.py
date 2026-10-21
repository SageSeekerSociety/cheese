"""Check every case a plan names ran on exactly one runner or report.

Two callers. CI passes the artifacts directory its shards uploaded and the name
of the independent collection plan; each partition is judged against the cases
it was assigned. A local run has no artifact tree and no shard assignment: give
it the JUnit reports (files, or directories holding them) and the plan of cases
the run promised, and it judges the reports together.
"""

import argparse
from pathlib import Path

from scripts.assert_suite_ran import (
    SuiteDidNotRun,
    assert_suite_ran,
    executed_nodeids,
    read_plan,
    read_selection,
)


def assert_complete(artifacts: Path, plan: str) -> int:
    # Failed-job reruns retain successful jobs from an earlier attempt. Keep
    # every attempt's evidence, but judge the latest result for each partition.
    latest: dict[str, tuple[int, Path]] = {}
    for directory in artifacts.iterdir():
        if not directory.is_dir():
            continue
        name, _, attempt = directory.name.rpartition("-")
        if not name or not attempt.isdigit() or int(attempt) < 1:
            raise SuiteDidNotRun(f"invalid partition artifact name: {directory.name}")
        previous = latest.get(name)
        if previous is None or int(attempt) > previous[0]:
            latest[name] = (int(attempt), directory)
    if plan not in latest:
        raise SuiteDidNotRun("the independent collection plan artifact is missing")
    _, plan_directory = latest.pop(plan)
    _, expected, planned = read_selection(plan_directory)
    if expected != planned:
        raise SuiteDidNotRun("the independent collection plan must not be sharded")
    seen: set[str] = set()
    # A shard may hold no case (a narrowed integration selection, or more
    # shards than cases); the comparison with the plan below is what refuses a
    # case that ran nowhere.
    for _, directory in latest.values():
        _, _, assigned = read_selection(directory, allow_empty=True)
        assert_suite_ran(
            directory / "results.xml",
            at_least=0,
            selection_dir=directory,
            reruns=directory / "reruns.jsonl",
            allow_empty=True,
        )
        duplicate = seen.intersection(assigned)
        if duplicate:
            raise SuiteDidNotRun(f"cases ran on multiple runners: {sorted(duplicate)}")
        seen.update(assigned)
    missing = set(expected) - seen
    unexpected = seen - set(expected)
    if missing or unexpected:
        raise SuiteDidNotRun(
            f"partition coverage differs from independent collection: "
            f"{len(missing)} missing {sorted(missing)}, "
            f"{len(unexpected)} unexpected {sorted(unexpected)}"
        )
    return len(seen)


def find_reports(results: list[Path]) -> list[Path]:
    """Every JUnit report named, or found directly in a named directory.

    A local sharded run keeps one report in each shard's directory, so the
    directory itself is read; a report named twice is only read once.
    """
    reports: list[Path] = []
    seen: set[Path] = set()
    for result in results:
        if result.is_dir():
            found = sorted(result.glob("*.xml"))
            if not found:
                raise SuiteDidNotRun(f"{result} holds no JUnit report (*.xml)")
        elif result.exists():
            found = [result]
        else:
            raise SuiteDidNotRun(f"{result} does not exist")
        for path in found:
            # Resolved, so one report named twice by two spellings (a directory
            # and a file inside it, a symlink) is read once; reading it twice
            # would look like a case that ran in two reports.
            identity = path.resolve()
            if identity not in seen:
                seen.add(identity)
                reports.append(path)
    return reports


def assert_reports_complete(
    results: list[Path], expected: Path, *, reruns: Path | None = None
) -> int:
    """Every case the plan names ran on exactly one of these reports, and no
    report held a case the plan does not name.

    The local counterpart of ``assert_complete``: no artifact tree, no shard
    assignment, just the reports a run produced and the plan it meant to run.
    ``reruns`` is the record ``scripts.ci_evidence`` writes, so a case the
    local run retried is judged on its final attempt instead of looking like a
    case that ran twice.
    """
    reports = find_reports(results)
    if not reports:
        raise SuiteDidNotRun("no JUnit report to judge")
    planned = read_plan(expected)
    seen: set[str] = set()
    for report in reports:
        # A shard can be assigned no case at all when there are more shards
        # than cases; that report is empty and the coverage check below is
        # what refuses a run that executed nothing.
        for nodeid in executed_nodeids(report, reruns, allow_empty=True):
            if nodeid in seen:
                raise SuiteDidNotRun(
                    f"cases ran in more than one report: {nodeid} "
                    "(a duplicate means the plan was not partitioned)"
                )
            seen.add(nodeid)
    if not seen:
        raise SuiteDidNotRun(
            "no report holds an executed case — the run verified nothing"
        )
    missing = sorted(set(planned) - seen)
    unexpected = sorted(seen - set(planned))
    if missing or unexpected:
        raise SuiteDidNotRun(
            "the reports do not cover the plan: "
            f"{len(missing)} missing {missing}, "
            f"{len(unexpected)} unexpected {unexpected}"
        )
    return len(seen)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "artifacts", type=Path, nargs="?", help="the CI partition artifact directory"
    )
    parser.add_argument(
        "--plan",
        help=(
            "with the CI artifacts directory: the independent collection plan's "
            "artifact name; with --results: the path to the plan to judge against"
        ),
    )
    parser.add_argument(
        "--results",
        type=Path,
        nargs="+",
        help="offline mode: JUnit reports (or directories of them) judged together",
    )
    parser.add_argument(
        "--reruns",
        type=Path,
        help="offline mode: reruns.jsonl from scripts.ci_evidence, retries the "
        "reports hide",
    )
    args = parser.parse_args()
    try:
        if args.results is not None:
            if args.artifacts is not None:
                parser.error("--results replaces the CI artifacts directory")
            if args.plan is None:
                parser.error("--results needs --plan")
            count = assert_reports_complete(
                args.results, Path(args.plan), reruns=args.reruns
            )
        else:
            if args.artifacts is None or args.plan is None:
                parser.error("give the artifacts directory and --plan, or --results")
            if args.reruns is not None:
                parser.error("--reruns belongs to the offline --results mode")
            count = assert_complete(args.artifacts, args.plan)
    except SuiteDidNotRun as exc:
        raise SystemExit(f"::error::{exc}") from exc
    print(f"All {count} planned cases ran on exactly one runner.")


if __name__ == "__main__":
    main()
