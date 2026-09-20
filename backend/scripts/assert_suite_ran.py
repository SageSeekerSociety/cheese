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
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class SuiteDidNotRun(Exception):
    """The report says the suite did not do what the job asked of it."""


def assert_suite_ran(junit_xml: Path, *, at_least: int) -> int:
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

    cases = tree.getroot().iter("testcase")
    ran, skipped = 0, []
    for case in cases:
        skip = case.find("skipped")
        if skip is not None:
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            why = skip.get("message") or skip.text or "no reason"
            skipped.append(f"{name} — {why}")
        elif case.find("error") is None:
            ran += 1

    if skipped:
        listing = "\n".join(f"  {line}" for line in skipped)
        raise SuiteDidNotRun(
            f"{len(skipped)} case(s) were skipped. A suite that cannot meet its "
            f"environment fails; install what it needs in the job instead:\n{listing}"
        )
    if ran < at_least:
        raise SuiteDidNotRun(
            f"only {ran} case(s) ran, and this job expects at least {at_least}. "
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
    arguments = parser.parse_args()
    try:
        ran = assert_suite_ran(arguments.junit_xml, at_least=arguments.at_least)
    except SuiteDidNotRun as exc:
        print(f"::error::{exc}")
        return 1
    print(f"{ran} case(s) ran, none skipped (floor {arguments.at_least}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
