#!/usr/bin/env python3
"""Say which CI machines are not there, one line per machine.

The pool's liveness check used to be a job asking for the shared `cheese-ci`
label: if it ran, at least one machine was alive. That is all it could prove,
because every machine answered to the same label — and on 2026-09-15 two of the
three were down for hours while the third carried the repository, and the only
symptom anyone saw was CI being slow.

Asking the API instead of running a job is what makes per-machine coverage
affordable: it needs none of the pool, so it cannot queue behind a merge burst
and report that as death — the failure mode that kept the check to one job in
the first place. A runner that is registered and `offline` is broken by
definition; one that is `busy` is working.

Each machine carries a `cheese-ci-box-<n>` label alongside the shared one, so a
machine whose slots are all gone is visible as an absence rather than inferred
from a count.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

POOL_LABEL = "cheese-ci"
BOX_LABEL_PREFIX = "cheese-ci-box-"


def fetch_runners(repo: str, token: str) -> list[dict]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runners?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("runners", [])


def labels_of(runner: dict) -> set[str]:
    return {label["name"] for label in runner.get("labels", [])}


def assess(runners: list[dict]) -> tuple[list[str], list[str]]:
    """(healthy lines, problem lines) — grouped by machine, not by slot.

    A machine is a problem when ANY of its slots is offline: half a machine is
    already lost capacity, and it is the state that precedes losing all of it.
    """
    boxes: dict[str, list[dict]] = {}
    for runner in runners:
        labels = labels_of(runner)
        if POOL_LABEL not in labels:
            continue
        box = next(
            (name for name in sorted(labels) if name.startswith(BOX_LABEL_PREFIX)),
            "unlabelled",
        )
        boxes.setdefault(box, []).append(runner)

    healthy: list[str] = []
    problems: list[str] = []
    for box, slots in sorted(boxes.items()):
        offline = [s for s in slots if s.get("status") != "online"]
        summary = ", ".join(
            f"{s['name']}={s.get('status')}"
            + ("(busy)" if s.get("busy") and s.get("status") == "online" else "")
            for s in sorted(slots, key=lambda s: s["name"])
        )
        (problems if offline else healthy).append(f"{box}: {summary}")
    if not boxes:
        problems.append(f"no runner carries the {POOL_LABEL} label at all")
    return healthy, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("REPO", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN", ""))
    args = parser.parse_args(argv)
    if not args.repo or not args.token:
        print("REPO and GH_TOKEN are required", file=sys.stderr)
        return 2

    try:
        runners = fetch_runners(args.repo, args.token)
    except urllib.error.HTTPError as error:
        # Not knowing is not the same as everything being fine, and it is also
        # not a machine being down — say which it is.
        print(f"::warning::could not read the runner list: HTTP {error.code}")
        return 0

    healthy, problems = assess(runners)
    for line in healthy:
        print(f"ok   {line}")
    for line in problems:
        print(f"DOWN {line}")
    if problems:
        joined = "; ".join(problems)
        print(f"::error::CI pool is short a machine — {joined}")
        summary = os.environ.get("GITHUB_OUTPUT")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"problems={joined}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
