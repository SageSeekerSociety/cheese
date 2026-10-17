#!/usr/bin/env python3
"""Summarize dependency scans so a scan that never ran cannot read as clean.

The audit lane used to compare each scan's outcome only against `failure` and
default every other value to "no advisory". A scan that was `skipped` or
`cancelled` — including the case where an earlier step failed and the scan was
skipped with it — produced the same summary line as a scan that ran and found
nothing. The lane exists to make a finding visible, so a summary that claims
the opposite of what happened is worse than no summary at all.

A scan has three results, not two:

    success   the scan ran and found nothing at its threshold
    failure   the scan found something, or could not reach the registry/index
    other     the scan did not run; its result is unknown, never clean

Only `success` is written up as clean. This script exits 0 only when every
scan reported `success`; otherwise it lists the scans that were not clean and
exits 1, which fails the job.

    audit_summary.py --scan "title|command|covers|outcome" [--scan ...]

The summary is appended to `$GITHUB_STEP_SUMMARY` (or to `--summary PATH`),
because that is the file GitHub shows on the run page.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from dataclasses import dataclass

CLEAN_OUTCOME = "success"
FINDINGS_OUTCOME = "failure"
FIELD_COUNT = 4


@dataclass(frozen=True)
class Scan:
    """One scan: how to name it, what it ran, what it covers, what happened."""

    title: str
    command: str
    covers: str
    outcome: str

    @property
    def is_clean(self) -> bool:
        return self.outcome == CLEAN_OUTCOME


def parse_scan(raw: str) -> Scan:
    parts = raw.split("|")
    if len(parts) != FIELD_COUNT:
        raise ValueError(
            f"--scan takes {FIELD_COUNT} pipe-separated fields "
            f"(title|command|covers|outcome), got {len(parts)}: {raw!r}"
        )
    title, command, covers, outcome = (part.strip() for part in parts)
    if not title or not command or not covers:
        raise ValueError(f"--scan needs a title, command and covers: {raw!r}")
    # An unset step outcome expands to an empty string in the workflow. It means
    # the same thing as `skipped`: the scan did not run.
    return Scan(
        title=title,
        command=command,
        covers=covers,
        outcome=outcome or "missing",
    )


def result_cell(scan: Scan) -> str:
    if scan.is_clean:
        return "clean — no advisory at the configured threshold"
    if scan.outcome == FINDINGS_OUTCOME:
        return "**findings — or the scan could not reach the registry/index**"
    return f"**scan did not run (`{scan.outcome}`) — this is not a clean result**"


def render(scans: list[Scan]) -> str:
    lines = [
        "## Dependency audit",
        "",
        "This run reports the dependencies as clean only if every scan below "
        "returned `success`.",
        "",
        "| scan | covers | result |",
        "| --- | --- | --- |",
    ]
    for scan in scans:
        lines.append(f"| `{scan.command}` | {scan.covers} | {result_cell(scan)} |")

    unclean = [scan for scan in scans if not scan.is_clean]
    if unclean:
        lines += ["", "### Not clean", ""]
        for scan in unclean:
            if scan.outcome == FINDINGS_OUTCOME:
                reason = (
                    "**findings**, or the scan could not reach the "
                    "registry/index — the step log says which."
                )
            else:
                reason = (
                    f"**scan did not run** (outcome `{scan.outcome}`); "
                    f"nothing checked {scan.covers} in this run."
                )
            lines.append(f"- `{scan.command}` — {reason}")
    else:
        lines += ["", "All scans ran and found nothing at their thresholds."]
    return "\n".join(lines) + "\n"


def annotations(scans: list[Scan]) -> list[str]:
    lines = []
    for scan in scans:
        if scan.is_clean:
            continue
        if scan.outcome == FINDINGS_OUTCOME:
            body = (
                "the scan came back non-zero — a finding, or an unreachable "
                "registry/index; see the step log"
            )
        else:
            body = (
                f"the scan did not run (outcome {scan.outcome}); "
                "this is not a clean result"
            )
        lines.append(f"::warning title={scan.title}::{body}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--scan",
        action="append",
        required=True,
        metavar="TITLE|COMMAND|COVERS|OUTCOME",
        help="one audit scan and the outcome its step reported (repeatable)",
    )
    parser.add_argument(
        "--summary",
        type=pathlib.Path,
        default=None,
        help="append the markdown summary here instead of $GITHUB_STEP_SUMMARY",
    )
    args = parser.parse_args(argv)

    try:
        scans = [parse_scan(raw) for raw in args.scan]
    except ValueError as error:
        parser.error(str(error))

    summary = render(scans)
    target = args.summary or _step_summary_path()
    if target is None:
        print(summary)
    else:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(summary)

    for line in annotations(scans):
        print(line)

    return 0 if all(scan.is_clean for scan in scans) else 1


def _step_summary_path() -> pathlib.Path | None:
    raw = os.environ.get("GITHUB_STEP_SUMMARY")
    return pathlib.Path(raw) if raw else None


if __name__ == "__main__":
    sys.exit(main())
