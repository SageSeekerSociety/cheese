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

Two marks are not silence and are let through, each named on the run instead:
a case that only passed on a retry (``--reruns``, recorded by
``scripts.ci_evidence`` in ``reruns.jsonl``), and a case listed in
``tests/quarantine.txt``, which runs as a non-strict xfail.
"""

import argparse
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class SuiteDidNotRun(Exception):
    """The report says the suite did not do what the job asked of it."""


# The reason tests/conftest.py gives a quarantined case's xfail marker.
QUARANTINED = "quarantined:"


def read_reruns(path: Path | None) -> list[dict]:
    """Every retry ``scripts.ci_evidence`` recorded, oldest first."""
    if path is None or not path.exists():
        return []
    reruns = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            rerun = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SuiteDidNotRun(
                f"{path}:{number} is not a rerun record: {exc}"
            ) from exc
        if not isinstance(rerun, dict) or not isinstance(rerun.get("nodeid"), str):
            raise SuiteDidNotRun(f"{path}:{number} has no nodeid")
        reruns.append(rerun)
    return reruns


def _is_crash_placeholder(case: ET.Element) -> bool:
    """The nameless, empty testcase pytest's JUnit writer leaves for the report
    xdist sends when a worker dies mid-case; the case itself is reported again
    by its retry."""
    return not case.get("name") and not case.get("classname") and len(case) == 0


def _is_quarantined(skip: ET.Element) -> bool:
    return skip.get("type") == "pytest.xfail" and (
        skip.get("message") or ""
    ).startswith(QUARANTINED)


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
    junit_xml: Path,
    *,
    at_least: int,
    selection_dir: Path | None = None,
    reruns: Path | None = None,
) -> int:
    """Return how many cases ran, or raise ``SuiteDidNotRun`` saying what is wrong.

    A case counts as having run when it neither was skipped nor errored during
    setup: a case that failed ran and told us something, a case that errored in
    its fixtures did not reach its own body. A quarantined case ran too; its
    result is what the quarantine sets aside.
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
    crashes = sum(1 for rerun in read_reruns(reruns) if rerun.get("crashed"))
    placeholders = 0
    cases = tree.getroot().iter("testcase")
    ran, skipped, errored, executed_nodeids = 0, [], [], []
    for case in cases:
        if _is_crash_placeholder(case):
            placeholders += 1
            continue
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        skip = case.find("skipped")
        if skip is not None and _is_quarantined(skip):
            ran += 1
        elif skip is not None:
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

    if placeholders > crashes:
        raise SuiteDidNotRun(
            f"the JUnit report has {placeholders} nameless testcase(s) but "
            f"{crashes} recorded worker crash(es) that a retry ran again"
        )
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


def not_clean(junit_xml: Path, reruns: Path | None) -> list[str]:
    """One line per case that kept the suite green without passing cleanly:
    retried and passed, or quarantined. Empty when the run was clean."""
    if not junit_xml.exists():
        return []
    try:
        root = ET.parse(junit_xml).getroot()
    except ET.ParseError:
        return []
    final: dict[str, str] = {}
    lines = []
    for case in root.iter("testcase"):
        nodeid = next(
            (
                value
                for prop in case.findall("./properties/property")
                if prop.get("name") == "cheese_nodeid" and (value := prop.get("value"))
            ),
            f"{case.get('classname', '')}::{case.get('name', '')}",
        )
        skip = case.find("skipped")
        if skip is not None and _is_quarantined(skip):
            lines.append(f"{nodeid} is quarantined ({skip.get('message')})")
        failed = case.find("failure") is not None or case.find("error") is not None
        final[nodeid] = "failed" if failed else "passed"
    seen = set()
    for rerun in read_reruns(reruns):
        nodeid = rerun["nodeid"]
        if nodeid in seen or final.get(nodeid) != "passed":
            continue
        seen.add(nodeid)
        how = "its worker died" if rerun.get("crashed") else "it failed"
        lines.append(
            f"{nodeid} passed only on a retry; first {how}: {rerun.get('reason', '')}"
        )
    return lines


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
    parser.add_argument(
        "--reruns",
        type=Path,
        help="reruns.jsonl from scripts.ci_evidence: retries the report hides",
    )
    arguments = parser.parse_args()
    try:
        unclean = not_clean(arguments.junit_xml, arguments.reruns)
    except SuiteDidNotRun as exc:
        print(f"::error::{exc}")
        return 1
    if unclean:
        # A green run that needed a retry is the flake a later run will lose;
        # say so on the run a PR author looks at, as e2e.yml does for its own.
        listing = "\n".join(f"- {line}" for line in unclean)
        print(
            f"::warning title=Backend is not clean::{len(unclean)} case(s): "
            + "; ".join(unclean)
        )
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as output:
                output.write(f"### Backend is not clean\n\n{listing}\n")
    try:
        ran = assert_suite_ran(
            arguments.junit_xml,
            at_least=arguments.at_least,
            selection_dir=arguments.selection_dir,
            reruns=arguments.reruns,
        )
    except SuiteDidNotRun as exc:
        print(f"::error::{exc}")
        return 1
    print(f"{ran} case(s) ran, none skipped (floor {arguments.at_least}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
