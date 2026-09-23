#!/usr/bin/env python3
"""Normalize CI test reports into one small, fail-closed evidence record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

REMOTE_CASES = {
    "executor",
    "native-terminal-rc",
    "plugin-disabled",
    "plugin-throws",
    "plugin-timeout",
    "executor-disconnected",
    "resident-release",
    "backend-regressions",
}


def _steps(raw: str, required: list[str]) -> tuple[bool, str]:
    try:
        steps = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False, "workflow step outcomes are missing or malformed"
    if not isinstance(steps, dict):
        return False, "workflow step outcomes are missing or malformed"
    bad = [
        name
        for name in required
        if not isinstance(steps.get(name), dict)
        or steps[name].get("outcome") != "success"
    ]
    if bad:
        return False, "required steps did not pass: " + ", ".join(bad)
    return True, ""


def _playwright(path: Path) -> tuple[int, int, int, list[str]]:
    report = json.loads(path.read_text())
    tests = []

    def visit(suite: object) -> None:
        if not isinstance(suite, dict):
            raise TypeError("Playwright suites are malformed")
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                raise TypeError("Playwright specs are malformed")
            entries = spec.get("tests", [])
            if not isinstance(entries, list):
                raise TypeError("Playwright tests are malformed")
            tests.extend(
                (str(spec.get("title", "unnamed test")), test) for test in entries
            )
        for child in suite.get("suites", []):
            visit(child)

    suites = report.get("suites")
    if not isinstance(suites, list):
        raise TypeError("Playwright suites are missing")
    for suite in suites:
        visit(suite)

    retries = 0
    skipped = 0
    problems = []
    for spec_title, test in tests:
        if not isinstance(test, dict) or not isinstance(test.get("results"), list):
            raise TypeError("Playwright test results are malformed")
        results = test["results"]
        retries += sum(
            result.get("retry", 0) > 0 for result in results if isinstance(result, dict)
        )
        if test.get("status") == "skipped" or any(
            isinstance(result, dict) and result.get("status") == "skipped"
            for result in results
        ):
            skipped += 1
        if (
            test.get("status") != "expected"
            or len(results) != 1
            or not isinstance(results[0], dict)
            or results[0].get("retry") != 0
            or results[0].get("status") != "passed"
        ):
            problems.append(str(test.get("title", spec_title)))
    return len(tests), retries, skipped, problems


def _remote(path: Path) -> tuple[int, int, int, list[str]]:
    results = json.loads(path.read_text())
    if not isinstance(results, dict) or set(results) != REMOTE_CASES:
        missing = sorted(
            REMOTE_CASES - set(results) if isinstance(results, dict) else REMOTE_CASES
        )
        extra = sorted(set(results) - REMOTE_CASES) if isinstance(results, dict) else []
        raise ValueError(f"remote cases differ (missing={missing}, extra={extra})")
    malformed = [
        name for name, result in results.items() if not isinstance(result, dict)
    ]
    if malformed:
        raise ValueError("remote results are malformed for: " + ", ".join(malformed))
    problems = [
        name for name, result in results.items() if result.get("passed") is not True
    ]
    invocation = json.loads((path.parent / "invocation.json").read_text())
    if not isinstance(invocation, dict) or not isinstance(invocation.get("count"), int):
        raise TypeError("remote invocation receipt is malformed")
    if invocation["count"] != 1:
        problems.append(f"remote suite ran {invocation['count']} times in one output")
    retries = 0
    for name in REMOTE_CASES:
        attempts = list(path.parent.glob(f"{name}.attempt-*.log"))
        if not attempts:
            raise ValueError(f"remote attempt log is missing for {name}")
        retries += max(0, len(attempts) - 1)
    retries += max(0, invocation["count"] - 1)
    regression_tests, _, skipped, regression_problems = _junit(
        path.parent / "backend-regressions.xml", 1
    )
    problems.extend(regression_problems)
    tests = len(results) - 1 + regression_tests
    return tests, retries, skipped, problems


def _junit(path: Path, at_least: int) -> tuple[int, int, int, list[str]]:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    skipped = sum(case.find("skipped") is not None for case in cases)
    problems = [
        case.get("name", "unnamed test")
        for case in cases
        if case.find("failure") is not None or case.find("error") is not None
    ]
    if len(cases) < at_least:
        problems.append(f"only {len(cases)} tests ran; expected at least {at_least}")
    return len(cases), 0, skipped, problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        required=True,
        choices=("e2e", "remote-acceptance", "private-chat"),
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--tested-sha", required=True)
    parser.add_argument("--steps-json", required=True)
    parser.add_argument("--require-step", action="append", default=[])
    parser.add_argument("--at-least", type=int, default=1)
    args = parser.parse_args()

    clean, reason = _steps(args.steps_json, args.require_step)
    tests = retries = skipped = 0
    problems: list[str] = []
    try:
        if args.suite == "e2e":
            tests, retries, skipped, problems = _playwright(args.source)
        elif args.suite == "remote-acceptance":
            tests, retries, skipped, problems = _remote(args.source)
        else:
            tests, retries, skipped, problems = _junit(args.source, args.at_least)
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ET.ParseError,
    ) as error:
        clean = False
        reason = f"test report is missing or malformed: {error}"

    if tests == 0:
        problems.append("no tests ran")
    if retries:
        problems.append(f"{retries} test retries occurred")
    if skipped:
        problems.append(f"{skipped} tests were skipped")
    if problems:
        clean = False
        reason = reason or "; ".join(problems[:3])
    elif clean:
        reason = "all required tests and steps passed without retries or skips"

    evidence = {
        "schema_version": 1,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "head_sha": args.head_sha,
        "tested_sha": args.tested_sha,
        "suite": args.suite,
        "clean": clean,
        "reason": reason,
        "tests": tests,
        "retries": retries,
        "skipped": skipped,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
