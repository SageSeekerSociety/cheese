"""Check every independently collected required case ran on exactly one runner."""

import argparse
from pathlib import Path

from scripts.assert_suite_ran import SuiteDidNotRun, assert_suite_ran, read_selection


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
    for _, directory in latest.values():
        _, _, assigned = read_selection(directory)
        assert_suite_ran(directory / "results.xml", at_least=1, selection_dir=directory)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--plan", required=True)
    args = parser.parse_args()
    try:
        count = assert_complete(args.artifacts, args.plan)
    except SuiteDidNotRun as exc:
        raise SystemExit(f"::error::{exc}") from exc
    print(f"All {count} independently collected cases ran on exactly one runner.")


if __name__ == "__main__":
    main()
