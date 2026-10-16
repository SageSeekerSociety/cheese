"""Decide whether a Required CI run may have its failed jobs rerun.

The platform's GitHub App cannot rerun a job itself (it has no actions:write),
so a red run used to mean an empty commit and all 25-35 jobs queued again.
rerun-failed.yml holds actions:write instead and reruns only what failed, when
the App asks with a repository_dispatch; this is the check it runs first.

Reads the run as `GET /repos/{owner}/{repo}/actions/runs/{id}` returns it and
exits non-zero, naming the reason, unless every condition holds.

`known-flakes.json` beside this file names the patterns this repository has
already diagnosed — a schedule-only ops check, the merge queue's synthetic run,
a Dependabot updater that could not reach the registry — with the shortest
handling for each. What the run object carries is the workflow path and the
event, which is what a pattern matches on; the failed job names and the log are
not in it. A pattern either explains a refusal (rerun false) or annotates a
rerun (rerun true), and neither widens what the conditions below allow: a rerun
true pattern never turns a refusal into a rerun, and no pattern lifts the
attempt limit.
"""

import json
import sys
from pathlib import Path

WORKFLOW = ".github/workflows/required-ci.yml"
# The first run plus two reruns. A case that is still red after that is not a
# flake a retry will cure.
MAX_ATTEMPTS = 3

PATTERNS = Path(__file__).with_name("known-flakes.json")
# The keys a pattern may match on, and the run field each one reads.
MATCHED_FIELDS = {"workflow": "path", "event": "event"}
REQUIRED_KEYS = ("id", "title", "match", "rerun", "action", "symptoms")


def load_patterns(path: Path = PATTERNS) -> list[dict]:
    """The patterns in ``path``; raises ValueError on one this file cannot use."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    about = document.get("about")
    if not isinstance(about, str) or not about.strip():
        raise ValueError(f"{path}: about must be a non-empty string")
    patterns = document.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(f"{path}: patterns must be a non-empty list")
    seen = set()
    for pattern in patterns:
        _validate(path, pattern)
        if pattern["id"] in seen:
            raise ValueError(f"{path}: two patterns are called {pattern['id']!r}")
        seen.add(pattern["id"])
    return patterns


def _validate(path: Path, pattern: object) -> None:
    if not isinstance(pattern, dict):
        raise ValueError(f"{path}: {pattern!r} is not an object")
    where = pattern.get("id") or repr(pattern)
    for key in REQUIRED_KEYS:
        if key not in pattern:
            raise ValueError(f"{path}: pattern {where!r} has no {key}")
    if not isinstance(pattern["rerun"], bool):
        raise ValueError(f"{path}: pattern {where!r} rerun must be true or false")
    for key in ("id", "title", "action"):
        if not isinstance(pattern[key], str) or not pattern[key].strip():
            raise ValueError(f"{path}: pattern {where!r} needs a {key}")
    match = pattern["match"]
    if not isinstance(match, dict) or not match:
        raise ValueError(f"{path}: pattern {where!r} has an empty match")
    for field, wanted in match.items():
        if field not in MATCHED_FIELDS:
            raise ValueError(
                f"{path}: pattern {where!r} matches {field!r}, and a run only"
                f" carries {sorted(MATCHED_FIELDS)}"
            )
        if not isinstance(wanted, list) or not all(
            isinstance(value, str) and value for value in wanted
        ):
            raise ValueError(
                f"{path}: pattern {where!r} field {field!r} must be a non-empty"
                " list of strings"
            )
    symptoms = pattern["symptoms"]
    if not isinstance(symptoms, list) or not symptoms or not all(
        isinstance(symptom, str) and symptom.strip() for symptom in symptoms
    ):
        raise ValueError(
            f"{path}: pattern {where!r} needs the symptoms it was recognised by"
        )


def matching_pattern(run: dict, patterns: list[dict]) -> dict | None:
    """The first pattern ``run`` matches, or None when it matches none."""
    for pattern in patterns:
        if all(
            run.get(MATCHED_FIELDS[field]) in wanted
            for field, wanted in pattern["match"].items()
        ):
            return pattern
    return None


def diagnosis(run: dict, pattern: dict) -> str:
    """One line naming ``pattern``, what to do, and the symptoms recorded for it."""
    symptoms = "".join(f" | {s}" for s in pattern["symptoms"])
    return (
        f"run {run.get('id')} matches known pattern {pattern['id']!r}"
        f" ({pattern['title']}): {pattern['action']}{symptoms}"
    )


def refusal(run: dict, repository: str, patterns: list[dict] | None = None) -> str | None:
    """Why ``run`` may not be rerun, or None when it may.

    A rerun false pattern replaces the reason with its own diagnosis — it says
    the same thing, with what to do about it. A rerun true pattern never
    overrides a reason, so the patterns can only narrow what is rerunnable.
    """
    if (run.get("repository") or {}).get("full_name") != repository:
        return f"run {run.get('id')} is not in {repository}"
    reason = _reason(run)
    pattern = matching_pattern(run, load_patterns() if patterns is None else patterns)
    if pattern is None or pattern["rerun"]:
        return reason
    return diagnosis(run, pattern)


def _reason(run: dict) -> str | None:
    """The conditions on the run itself; None when every one of them holds."""
    if run.get("path") != WORKFLOW:
        return f"run {run.get('id')} is {run.get('path')!r}, not {WORKFLOW}"
    # A failed merge_group run has already taken its PR out of the queue; a
    # rerun would turn nothing green. The queue's own retry is --reruns.
    if run.get("event") != "pull_request":
        return f"run {run.get('id')} was triggered by {run.get('event')!r}, not a pull request"
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        return (
            f"run {run.get('id')} is {run.get('status')}/{run.get('conclusion')},"
            " not a completed failure"
        )
    attempt = run.get("run_attempt")
    if not isinstance(attempt, int) or attempt >= MAX_ATTEMPTS:
        return f"run {run.get('id')} is on attempt {attempt}; the limit is {MAX_ATTEMPTS}"
    return None


def main() -> int:
    with open(sys.argv[1]) as handle:
        run = json.load(handle)
    patterns = load_patterns()
    reason = refusal(run, sys.argv[2], patterns)
    if reason:
        print(f"::error::{reason}")
        return 1
    pattern = matching_pattern(run, patterns)
    if pattern:
        print(f"::notice::{diagnosis(run, pattern)}")
    print(f"Run {run['id']} attempt {run['run_attempt']}: rerunning its failed jobs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
