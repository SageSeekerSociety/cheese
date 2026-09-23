#!/usr/bin/env python3
"""Choose a fixed UTC cohort window for the recurring CI feedback collector."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


def parse_utc(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise argparse.ArgumentTypeError("timestamps must be timezone-aware UTC")
    return parsed


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def cohort_window(now: dt.datetime, since: str = "", until: str = "") -> tuple[str, str]:
    if bool(since) != bool(until):
        raise ValueError("--since and --until must be supplied together")
    if since:
        start, end = parse_utc(since), parse_utc(until)
        if start > end:
            raise ValueError("--since must not follow --until")
        return format_utc(start), format_utc(end)
    now = now.astimezone(dt.UTC)
    current_block = now.replace(
        hour=(now.hour // 6) * 6, minute=0, second=0, microsecond=0
    )
    end = current_block - dt.timedelta(hours=6)
    return (
        format_utc(end - dt.timedelta(hours=6)),
        format_utc(end - dt.timedelta(seconds=1)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--now", type=parse_utc)
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        since, until = cohort_window(args.now or dt.datetime.now(dt.UTC), args.since, args.until)
    except (argparse.ArgumentTypeError, ValueError) as error:
        parser.error(str(error))
    args.parameters.parent.mkdir(parents=True, exist_ok=True)
    args.parameters.write_text(
        json.dumps({"since": since, "until": until}, indent=2, sort_keys=True) + "\n"
    )
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"since={since}\nuntil={until}\n")
    print(f"Required CI cohort: {since} through {until}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
