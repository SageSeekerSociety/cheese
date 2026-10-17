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

The same judgement is available offline, without the CI artifact tree: give it
the plan of cases a local run promised to execute (``--expected``) and it names
the ones the report is missing instead of only counting them. That plan is
whatever records what should have run — the selection manifests
``scripts.ci_shard`` writes, or a plain list of node IDs.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


class SuiteDidNotRun(Exception):
    """The report says the suite did not do what the job asked of it."""


# The reason tests/conftest.py gives a quarantined case's xfail marker.
QUARANTINED = "quarantined:"
_QUARANTINE = Path(__file__).resolve().parents[1] / "tests/quarantine.txt"
_ISSUE_LINK = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+")


def read_quarantine(path: Path = _QUARANTINE) -> dict[str, str]:
    """The auditable list used both at collection and when verifying results."""
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise SuiteDidNotRun(
            f"{path} is not a readable quarantine list: {exc}"
        ) from exc
    entries, unowned = {}, []
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        nodeid, _, note = line.partition("#")
        issue = _ISSUE_LINK.search(note)
        if not nodeid.strip() or issue is None:
            unowned.append(f"  {line}")
            continue
        entries[nodeid.strip()] = issue.group()
    if unowned:
        raise SuiteDidNotRun(
            f"{path} lines need `# <GitHub issue link>`:\n" + "\n".join(unowned)
        )
    return entries


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


def _nodeid_of(case: ET.Element) -> str | None:
    values = [
        prop.get("value")
        for prop in case.findall("./properties/property")
        if prop.get("name") == "cheese_nodeid"
    ]
    return values[0] if len(values) == 1 and values[0] else None


def _final_attempts(root: ET.Element, reruns: list[dict]) -> list[ET.Element]:
    """A teardown retry leaves its earlier passing call in JUnit too. Discard
    only those extra passes justified by recorded teardown retries, never a
    skip, error or failure, an unnamed case, or an unexplained duplicate."""
    remaining = Counter(r["nodeid"] for r in reruns if r.get("when") == "teardown")
    seen: set[str] = set()
    final = []
    for case in reversed(list(root.iter("testcase"))):
        nodeid = _nodeid_of(case)
        passed = all(case.find(tag) is None for tag in ("skipped", "error", "failure"))
        if nodeid and nodeid in seen and remaining[nodeid] > 0 and passed:
            remaining[nodeid] -= 1
            continue
        if nodeid:
            seen.add(nodeid)
        final.append(case)
    return list(reversed(final))


def _is_quarantined(case: ET.Element, quarantine: dict[str, str]) -> bool:
    skip = case.find("skipped")
    issue = quarantine.get(_nodeid_of(case) or "")
    return (
        issue is not None
        and skip is not None
        and skip.get("type") == "pytest.xfail"
        and skip.get("message") == f"{QUARANTINED} {issue}"
    )


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


def read_plan(path: Path) -> list[str]:
    """The node IDs a run promised to execute, from a plan a local run can keep.

    A plan is whatever records what should have run. Three forms are accepted:
    a directory of selection manifests (what ``pytest -p scripts.ci_shard
    --ci-selection-output=<dir>`` writes), one such manifest (``selected``, or
    ``nodeids`` / a bare JSON list), or a text file with one node ID per line
    where blank lines and lines starting with ``#`` are ignored.
    """
    if path.is_dir():
        _, selected, _ = read_selection(path)
        return _as_nodeids(path, selected)
    try:
        text = path.read_text()
    except OSError as exc:
        raise SuiteDidNotRun(f"{path} is not a readable plan: {exc}") from exc
    if path.suffix != ".json":
        lines = [line.strip() for line in text.splitlines()]
        return _as_nodeids(path, [line for line in lines if line and line[0] != "#"])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SuiteDidNotRun(f"{path} is not a readable plan: {exc}") from exc
    if isinstance(data, dict):
        match = next(
            (
                data[field]
                for field in ("nodeids", "selected", "assigned")
                if field in data
            ),
            None,
        )
        if match is None:
            raise SuiteDidNotRun(f"{path} must hold nodeids, selected, or assigned")
        data = match
    return _as_nodeids(path, data)


def _as_nodeids(path: Path, values: object) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(nodeid, str) or not nodeid.strip() for nodeid in values)
    ):
        raise SuiteDidNotRun(f"{path} must name at least one nonempty test node ID")
    nodeids = [nodeid.strip() for nodeid in values]
    if len(nodeids) != len(set(nodeids)):
        raise SuiteDidNotRun(f"{path} names the same case more than once")
    return nodeids


@dataclass(frozen=True)
class _Run:
    """What one JUnit report says the suite did, before any judgement."""

    junit_xml: Path
    ran: int
    skipped: list[str]
    errored: list[str]
    nodeids: list[str]
    placeholders: int
    crashes: int


def _inspect(junit_xml: Path, reruns: Path | None, *, require_nodeid: bool) -> _Run:
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

    recorded_reruns = read_reruns(reruns)
    placeholders = 0
    cases = _final_attempts(tree.getroot(), recorded_reruns)
    quarantine = read_quarantine(_QUARANTINE)
    ran, skipped, errored, nodeids = 0, [], [], []
    for case in cases:
        if _is_crash_placeholder(case):
            placeholders += 1
            continue
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        skip = case.find("skipped")
        if skip is not None and _is_quarantined(case, quarantine):
            ran += 1
        elif skip is not None:
            why = skip.get("message") or skip.text or "no reason"
            skipped.append(f"{name} — {why}")
        elif (error := case.find("error")) is not None:
            why = error.get("message") or error.text or "no reason"
            errored.append(f"{name} — {why}")
        else:
            ran += 1
        if require_nodeid:
            properties = [
                prop.get("value")
                for prop in case.findall("./properties/property")
                if prop.get("name") == "cheese_nodeid"
            ]
            if len(properties) != 1 or not properties[0]:
                raise SuiteDidNotRun(
                    f"{name} must report exactly one nonempty cheese_nodeid "
                    "property — run pytest with -p scripts.ci_shard so its JUnit "
                    "report says which case each result belongs to"
                )
            nodeids.append(properties[0])
    crashes = sum(1 for rerun in recorded_reruns if rerun.get("crashed"))
    return _Run(junit_xml, ran, skipped, errored, nodeids, placeholders, crashes)


def _require_no_silence(run: _Run) -> None:
    """Refuse a report that could pass while saying nothing about the cases."""
    if run.placeholders > run.crashes:
        raise SuiteDidNotRun(
            f"the JUnit report has {run.placeholders} nameless testcase(s) but "
            f"{run.crashes} recorded worker crash(es) that a retry ran again"
        )
    if len(run.nodeids) != len(set(run.nodeids)):
        raise SuiteDidNotRun(
            "the JUnit report contains duplicate cheese_nodeid properties"
        )
    if run.skipped:
        listing = "\n".join(f"  {line}" for line in run.skipped)
        if run.ran == 0:
            raise SuiteDidNotRun(
                f"every case in {run.junit_xml} was skipped — the suite ran "
                f"nothing, so nothing was verified:\n{listing}"
            )
        raise SuiteDidNotRun(
            f"{len(run.skipped)} case(s) were skipped. A suite that cannot meet its "
            f"environment fails; install what it needs in the job instead:\n{listing}"
        )
    if run.errored:
        listing = "\n".join(f"  {line}" for line in run.errored)
        raise SuiteDidNotRun(
            f"{len(run.errored)} case(s) errored before their test body ran:\n{listing}"
        )
    if run.ran == 0:
        raise SuiteDidNotRun(
            f"{run.junit_xml} reports no executed case — the suite ran nothing, "
            "so nothing was verified"
        )


def assert_suite_ran(
    junit_xml: Path,
    *,
    at_least: int | None = None,
    selection_dir: Path | None = None,
    reruns: Path | None = None,
    expected: Path | None = None,
) -> int:
    """Return how many cases ran, or raise ``SuiteDidNotRun`` saying what is wrong.

    ``at_least`` is a floor on what the job selected. ``expected`` names the
    cases a local run promised to execute, so a report that is green because it
    ran fewer of them fails and names the missing ones. Give at least one; a
    plan is the stronger statement because it can judge an exact set.

    A case counts as having run when it neither was skipped nor errored during
    setup: a case that failed ran and told us something, a case that errored in
    its fixtures did not reach its own body. A quarantined case ran too; its
    result is what the quarantine sets aside.
    """
    if at_least is None and expected is None:
        raise ValueError("assert_suite_ran needs at_least or expected")
    planned = read_plan(expected) if expected is not None else None
    selection_result = (
        read_selection(selection_dir) if selection_dir is not None else None
    )
    run = _inspect(
        junit_xml,
        reruns,
        require_nodeid=selection_dir is not None or planned is not None,
    )
    _require_no_silence(run)

    selected_count = None
    if selection_result is not None:
        selected_count, _, assigned = selection_result
        if run.ran != len(assigned):
            raise SuiteDidNotRun(
                f"the JUnit report contains {run.ran} executed case(s), but the shard "
                f"manifest assigned {len(assigned)}"
            )
        if set(run.nodeids) != set(assigned):
            missing = sorted(set(assigned) - set(run.nodeids))
            unexpected = sorted(set(run.nodeids) - set(assigned))
            raise SuiteDidNotRun(
                "the JUnit report does not contain the cases assigned by the shard "
                f"manifest; missing={missing}, unexpected={unexpected}"
            )
    if planned is not None:
        missing = sorted(set(planned) - set(run.nodeids))
        unexpected = sorted(set(run.nodeids) - set(planned))
        if missing or unexpected:
            raise SuiteDidNotRun(
                "the JUnit report does not contain the cases the plan expected; "
                f"{len(missing)} missing {missing}, "
                f"{len(unexpected)} unexpected {unexpected}"
            )

    if selected_count is not None:
        floor_count = selected_count
    elif planned is not None:
        floor_count = len(planned)
    else:
        floor_count = run.ran
    if at_least is not None and floor_count < at_least:
        raise SuiteDidNotRun(
            f"only {floor_count} case(s) were selected, and this job expects at "
            f"least {at_least}. "
            "Either the selection collapsed or the floor is out of date."
        )
    return run.ran


def executed_nodeids(junit_xml: Path, reruns: Path | None = None) -> list[str]:
    """The node IDs the report says executed, in report order.

    A caller aggregating several reports needs the identities, not just the
    count, so the report must name every case it holds: run pytest with
    ``-p scripts.ci_shard``.
    """
    run = _inspect(junit_xml, reruns, require_nodeid=True)
    _require_no_silence(run)
    return list(run.nodeids)


def not_clean(junit_xml: Path, reruns: Path | None) -> list[str]:
    """One line per case that kept the suite green without passing cleanly:
    retried and passed, or quarantined. Empty when the run was clean."""
    if not junit_xml.exists():
        return []
    try:
        root = ET.parse(junit_xml).getroot()
    except ET.ParseError:
        return []
    quarantine = read_quarantine(_QUARANTINE)
    final: dict[str, str] = {}
    lines = []
    for case in _final_attempts(root, read_reruns(reruns)):
        nodeid = (
            _nodeid_of(case) or f"{case.get('classname', '')}::{case.get('name', '')}"
        )
        skip = case.find("skipped")
        if skip is not None and _is_quarantined(case, quarantine):
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
    parser.add_argument(
        "--expected",
        type=Path,
        help=(
            "the plan of cases this run promised to execute: a selection-manifest "
            "directory, one selection-*.json, or a file of node IDs; requires the "
            "report to contain exactly those cases"
        ),
    )
    arguments = parser.parse_args()
    if arguments.at_least is None and arguments.expected is None:
        parser.error("give --at-least, --expected, or both")
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
            expected=arguments.expected,
        )
    except SuiteDidNotRun as exc:
        print(f"::error::{exc}")
        return 1
    judged = []
    if arguments.expected is not None:
        judged.append(f"matching the plan {arguments.expected}")
    if arguments.at_least is not None:
        judged.append(f"floor {arguments.at_least}")
    print(f"{ran} case(s) ran, none skipped ({', '.join(judged)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
