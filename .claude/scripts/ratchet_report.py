#!/usr/bin/env python3
"""The `--json` record the ratchet snapshot collector reads.

    python3 .claude/scripts/check-file-sizes.py --json

prints ONE JSON object on stdout and nothing else. The human report stays off in
that mode on purpose: the collector parses stdout, and a report interleaved with
a record is not a record. Errors keep going to stderr, where they already went.

The exit code stays the authority on the verdict, and --json does not change it:
0 pass, 1 violation, 2 could not judge. A checker that could not run at all says
`cannot_judge` and why, rather than printing an empty record — a collector that
read a crash as "no data" is the one answer this whole arrangement exists to
avoid. In the default mode stdout is byte-for-byte what it was: the flag is a
read, not a change to the gate.

`area` and `rule_fingerprint` are NOT in the record. The collector adds them,
because it is the one place that knows which rule files belong to which check;
seven copies of that list is seven places to forget one.

The record's shape is fixed in docs/topics/棘轮页方案 section 3.1. The frontend
ratchets share the same contract in `frontend/scripts/ratchet-report.mjs`;
`backend/scripts/check_boundaries.py` and `check-repo-rules.sh` write the same
shape through a small local helper each, because they live in other directories
and languages and a cross-directory import would be a worse trade than fifteen
duplicated lines.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, NoReturn

#: Fewer violations is better, for every ratchet that reports through here.
BETTER = "down"

#: The longest a `reason` may be. A checker's failure text can be a page long and
#: the record is read by a person expanding a row, not by a log viewer.
REASON_LIMIT = 2000


def as_json() -> bool:
    """Whether the caller asked for the machine-readable form.

    Read from argv here so that a default run stays byte-for-byte what it was.
    """
    return "--json" in sys.argv


def emit(record: dict[str, Any]) -> None:
    """Write the record, as one line.

    Only ever in --json mode, and raising otherwise keeps it that way: a checker
    whose human output grew a JSON line would be a change to every CI log that
    greps it.
    """
    if not as_json():
        raise RuntimeError("ratchet_report.emit() is only for --json runs")
    print(json.dumps(record, ensure_ascii=False))


def cannot_judge(check_id: str, reason: str, *, better: str = BETTER) -> NoReturn:
    """The record for a run that could not judge the tree, and exit 2.

    Every caller of this is a path that used to `return 2`/`sys.exit(2)` after
    printing to stderr. Nothing changes in the default mode: without --json
    there is no record to write and the exit code carries the whole answer.
    """
    if as_json():
        emit(
            {
                "id": check_id,
                "better": better,
                "status": "cannot_judge",
                "reason": str(reason).strip()[:REASON_LIMIT],
            }
        )
    sys.exit(2)


def verdict(
    *,
    check_id: str,
    ok: bool,
    actual: Any,
    frozen: Any,
    stale: Iterable[Any] = (),
    details: Iterable[Any] = (),
    better: str = BETTER,
) -> dict[str, Any]:
    """The record for a run that judged its tree.

    `actual` is what the checker counted this run and `frozen` is what the
    baseline allows. Either may be None when the check does not produce that
    number at all — never 0, which would read as "nothing wrong here" about a
    number nobody measured.

    `stale` is every frozen allowance the run no longer needs: the entry is
    still on the books and has stopped describing the tree. `details` is the
    per-unit breakdown, capped, because the page expands it and an unbounded
    list would make one committed snapshot the size of the tree.
    """
    return {
        "id": check_id,
        "better": better,
        "status": "pass" if ok else "fail",
        "actual": actual,
        "frozen": frozen,
        "stale": list(stale),
        "details": list(details)[:200],
    }
