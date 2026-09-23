"""A suite that could not meet its environment must fail, not report green.

A skipped case and a case that was never collected both leave the same mark on a
job: a green tick over a question nobody answered. The nightly canary reported
green for two days while one of its eight tests ran (#1236), and a harness
contract that skipped because its binary was missing used to be indistinguishable
from one that held.

So every job that runs a suite ends by reading the suite's own JUnit report back:
no case may be skipped, and at least as many cases must have run as the job says
it has. `at_least` is a floor, not the exact count — it is there to catch a
collapse in what ran, and it is raised when a job's selection grows enough that
the old floor stops meaning anything.
"""

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class SuiteDidNotRun(Exception):
    """The report says the suite did not do what the job asked of it."""


def read_selection(selection_dir: Path) -> tuple[int, list[str], list[str]]:
    paths = sorted(selection_dir.glob("selection-*.json"))
    if not paths:
        raise SuiteDidNotRun(
            f"{selection_dir} has no selection manifests — nothing records what "
            "this shard was supposed to run"
        )

    manifests = []
    for path in paths:
        try:
            manifest = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SuiteDidNotRun(
                f"{path} is not a readable selection manifest: {exc}"
            ) from exc
        if not isinstance(manifest, dict):
            raise SuiteDidNotRun(f"{path} must contain a JSON object")
        manifests.append((path, manifest))

    first_path, first = manifests[0]
    for path, manifest in manifests[1:]:
        if manifest != first:
            raise SuiteDidNotRun(
                f"{path} disagrees with {first_path} — xdist workers did not "
                "collect the same shard"
            )

    index, count = first.get("shard_index"), first.get("shard_count")
    if type(index) is not int or type(count) is not int or not 0 <= index < count:
        raise SuiteDidNotRun(
            f"{first_path} has an invalid shard_index/shard_count: {index!r}/{count!r}"
        )

    lists: dict[str, list[str]] = {}
    for field in ("selected", "assigned"):
        value = first.get(field)
        if (
            not isinstance(value, list)
            or any(not isinstance(nodeid, str) or not nodeid for nodeid in value)
            or len(value) != len(set(value))
        ):
            raise SuiteDidNotRun(
                f"{first_path} field {field!r} must be a list of unique, "
                "nonempty pytest node IDs"
            )
        lists[field] = value

    selected, assigned = lists["selected"], lists["assigned"]
    expected = [
        nodeid
        for nodeid in selected
        if int.from_bytes(hashlib.sha256(nodeid.encode()).digest()) % count == index
    ]
    if assigned != expected:
        raise SuiteDidNotRun(
            f"{first_path} assigned cases do not match shard {index}/{count}"
        )
    if not assigned:
        raise SuiteDidNotRun(f"{first_path} assigned no cases to shard {index}/{count}")
    return len(selected), selected, assigned


def assert_suite_ran(
    junit_xml: Path, *, at_least: int, selection_dir: Path | None = None
) -> int:
    """Return how many cases ran, or raise ``SuiteDidNotRun`` saying what is wrong.

    A case counts as having run when it neither was skipped nor errored during
    setup: a case that failed ran and told us something, a case that errored in
    its fixtures did not reach its own body.
    """
    if not junit_xml.exists():
        raise SuiteDidNotRun(
            f"{junit_xml} does not exist — the suite never got as far as writing "
            "a report, so nothing here says it ran"
        )
    try:
        tree = ET.parse(junit_xml)
    except ET.ParseError as exc:
        raise SuiteDidNotRun(
            f"{junit_xml} is not readable as JUnit XML: {exc}"
        ) from exc

    selection_result = (
        read_selection(selection_dir) if selection_dir is not None else None
    )
    cases = tree.getroot().iter("testcase")
    ran, skipped, errored, executed_nodeids = 0, [], [], []
    for case in cases:
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        skip = case.find("skipped")
        if skip is not None:
            why = skip.get("message") or skip.text or "no reason"
            skipped.append(f"{name} — {why}")
        elif (error := case.find("error")) is not None:
            why = error.get("message") or error.text or "no reason"
            errored.append(f"{name} — {why}")
        else:
            ran += 1
        properties = [
            prop.get("value")
            for prop in case.findall("./properties/property")
            if prop.get("name") == "cheese_nodeid"
        ]
        if selection_dir is not None:
            if len(properties) != 1 or not properties[0]:
                raise SuiteDidNotRun(
                    f"{name} must report exactly one nonempty cheese_nodeid property"
                )
            executed_nodeids.append(properties[0])

    if skipped:
        listing = "\n".join(f"  {line}" for line in skipped)
        raise SuiteDidNotRun(
            f"{len(skipped)} case(s) were skipped. A suite that cannot meet its "
            f"environment fails; install what it needs in the job instead:\n{listing}"
        )
    if errored:
        listing = "\n".join(f"  {line}" for line in errored)
        raise SuiteDidNotRun(
            f"{len(errored)} case(s) errored before their test body ran:\n{listing}"
        )

    selected_count = None
    assigned: list[str] | None = None
    if selection_result is not None:
        selected_count, _, assigned = selection_result
        if ran != len(assigned):
            raise SuiteDidNotRun(
                f"the JUnit report contains {ran} executed case(s), but the shard "
                f"manifest assigned {len(assigned)}"
            )
        if len(executed_nodeids) != len(set(executed_nodeids)):
            raise SuiteDidNotRun(
                "the JUnit report contains duplicate cheese_nodeid properties"
            )
        if set(executed_nodeids) != set(assigned):
            missing = sorted(set(assigned) - set(executed_nodeids))
            unexpected = sorted(set(executed_nodeids) - set(assigned))
            raise SuiteDidNotRun(
                "the JUnit report does not contain the cases assigned by the shard "
                f"manifest; missing={missing}, unexpected={unexpected}"
            )

    floor_count = selected_count if selected_count is not None else ran
    if floor_count < at_least:
        raise SuiteDidNotRun(
            f"only {floor_count} case(s) were selected, and this job expects at "
            f"least {at_least}. "
            "Either the selection collapsed or the floor is out of date."
        )
    return ran


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit_xml", type=Path, help="the suite's JUnit report")
    parser.add_argument(
        "--at-least",
        type=int,
        required=True,
        help="the fewest cases this job accepts as having run",
    )
    parser.add_argument(
        "--selection-dir",
        type=Path,
        help=(
            "directory containing selection-*.json manifests from scripts.ci_shard; "
            "also require the report to contain every case assigned to this shard"
        ),
    )
    arguments = parser.parse_args()
    try:
        ran = assert_suite_ran(
            arguments.junit_xml,
            at_least=arguments.at_least,
            selection_dir=arguments.selection_dir,
        )
    except SuiteDidNotRun as exc:
        print(f"::error::{exc}")
        return 1
    print(f"{ran} case(s) ran, none skipped (floor {arguments.at_least}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
