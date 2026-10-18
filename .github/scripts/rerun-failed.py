"""Decide whether a Required CI run may have its failed jobs rerun.

A red run used to mean an empty commit and all 25-35 jobs queued again.
rerun-failed.yml reruns only what failed, with its own actions:write, when the
App asks with a repository_dispatch; this is the check it runs first. It goes
through here rather than a direct rerun because these conditions (the attempt
limit in particular) are what keep a real failure from being retried into green.

Two kinds of run may be rerun:

* a pull request run, as before; and
* a push to main, once main's full runs are batched there and there is no pull
  request left to push an empty commit to. A push is rerun only when its failed
  job log carries a signature from flake_log_signatures.py — a connection the
  peer reset, a lost runner and the like. A real failure is left for a person to
  read: a retry that cures nothing costs the failed job set again and only
  delays the diagnosis. The log is read with `gh run view --log-failed` when the
  run is a push, so the workflow passes only the run object; GH_TOKEN is already
  in the step's environment.

The same run is rerun at most twice: MAX_ATTEMPTS counts the first run, and a
case still red on the third attempt is not a flake a retry will cure.

`known-flakes.json` beside this file names the patterns this repository has
already diagnosed — a schedule-only ops check, the merge queue's synthetic run,
a Dependabot updater that could not reach the registry — with the shortest
handling for each. What the run object carries is the workflow path and the
event, which is what a pattern matches on; the failed job names and the log are
not in it. A pattern either explains a refusal (rerun false) or annotates a
rerun (rerun true), and neither widens what the conditions below allow: a rerun
true pattern never turns a refusal into a rerun, and no pattern lifts the
attempt limit. The log signatures are a second, narrower list because they are
matched against the log rather than the run object.

A person or the orchestrator can invoke this directly with a run id:

    python3 .github/scripts/rerun-failed.py --run <run id> <owner/repo>
    python3 .github/scripts/rerun-failed.py --run <run id> <owner/repo> --dry-run

The first form fetches the run, checks it, and reruns its failed jobs when it
may; --dry-run only prints the decision. Without --run the helper reads the
saved run JSON, which is what the workflow calls:

    python3 .github/scripts/rerun-failed.py <run.json> <owner/repo>
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from flake_log_signatures import SIGNATURES as LOG_SIGNATURES
from flake_log_signatures import validate as validate_log_signatures

WORKFLOW = ".github/workflows/required-ci.yml"
# The first run plus two reruns. A case that is still red after that is not a
# flake a retry will cure.
MAX_ATTEMPTS = 3

PATTERNS = Path(__file__).with_name("known-flakes.json")
# The keys a pattern may match on, and the run field each one reads.
MATCHED_FIELDS = {"workflow": "path", "event": "event"}
REQUIRED_KEYS = ("id", "title", "match", "rerun", "action", "symptoms")
# How long to wait for `gh` before treating the run as unreadable.
GH_TIMEOUT = 120

# `gh run view --log-failed` puts "<job>\t<step>\t<timestamp> " before each
# line of the job's own output.
_LOG_PREFIX = re.compile(r"^[^\t\n]*\t[^\t\n]*\t(?:\ufeff)?\S+\s")

# A line pytest wrote about a failing test, rather than a tool reporting its
# own failure: the failing source line (`>`), the assertion detail (`E   `),
# the FAILURES/ERRORS banner, the `_ test_x[id] _` header, the short summary,
# and the introspected assertion. Those lines quote whatever the test put in
# an expected value, including an `ECONNRESET` or a rate-limit message; a
# signature found there would call a real failure a flake.
_PYTEST_REPORT = re.compile(
    r"^(?:FAILED |ERROR |>{1,2}\s|E {2,}|_{3,} .* _{3,}$"
    r"|=+ (?:FAILURES|ERRORS|short test summary info) =+"
    r"|assert )"
)


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
        # A workflow path and an event name never carry whitespace, so a value
        # that is blank or padded with some is a typo: matching is the exact
        # `in` test below, so it would match no run and nothing would say so.
        if not isinstance(wanted, list) or not wanted or any(
            not isinstance(value, str) or not value or value != value.strip()
            for value in wanted
        ):
            raise ValueError(
                f"{path}: pattern {where!r} field {field!r} must be a non-empty"
                " list of strings, none of them blank or padded with whitespace"
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


def _message(line: str) -> str:
    """One log line without the "<job>\\t<step>\\t<timestamp> " gh prefixes."""
    return _LOG_PREFIX.sub("", line, count=1)


def flake_log_signature(
    failed_log: str | None, signatures: tuple[dict, ...] | list[dict] | None = None
) -> dict | None:
    """The first known flake signature a non-report line of ``failed_log`` matches.

    Pytest's own report lines are skipped: they quote the test's expected
    values, so an `ECONNRESET` or a rate-limit message written in a test would
    otherwise make a real failure look like infrastructure flake.
    """
    if not failed_log:
        return None
    for line in failed_log.splitlines():
        message = _message(line)
        if _PYTEST_REPORT.match(message):
            continue
        for signature in LOG_SIGNATURES if signatures is None else signatures:
            if re.search(signature["pattern"], message, re.IGNORECASE):
                return signature
    return None


def log_diagnosis(run: dict, signature: dict) -> str:
    """One line naming the flake signature the failed job log matched."""
    return (
        f"run {run.get('id')} failed on known flake {signature['id']!r}"
        f" ({signature['title']}): {signature['action']}"
    )


def refusal(
    run: dict,
    repository: str,
    patterns: list[dict] | None = None,
    *,
    failed_log: str | None = None,
    signatures: tuple[dict, ...] | list[dict] | None = None,
) -> str | None:
    """Why ``run`` may not be rerun, or None when it may.

    A rerun false pattern replaces the reason with its own diagnosis — it says
    the same thing, with what to do about it. A rerun true pattern never
    overrides a reason, so the patterns can only narrow what is rerunnable.
    """
    if (run.get("repository") or {}).get("full_name") != repository:
        return f"run {run.get('id')} is not in {repository}"
    reason = _reason(run, failed_log, signatures)
    pattern = matching_pattern(run, load_patterns() if patterns is None else patterns)
    if pattern is None or pattern["rerun"]:
        return reason
    return diagnosis(run, pattern)


def _reason(
    run: dict,
    failed_log: str | None = None,
    signatures: tuple[dict, ...] | list[dict] | None = None,
) -> str | None:
    """The conditions on the run itself; None when every one of them holds."""
    reason = _gates(run)
    if reason is not None:
        return reason
    if _is_push_to_main(run) and flake_log_signature(failed_log, signatures) is None:
        if failed_log is None:
            return (
                f"run {run.get('id')} is a push to main whose failed job log"
                " could not be read, so a flake cannot be confirmed"
            )
        return (
            f"run {run.get('id')} is a push to main whose failed job log matches"
            " no known flake signature; read it and decide by hand"
        )
    return None


def _gates(run: dict) -> str | None:
    """Every condition that needs only the run object; None when all hold.

    Split out from ``_reason`` so that a caller may ask whether the failed job
    log is worth fetching at all: a run this already refuses never needs it.
    """
    if run.get("path") != WORKFLOW:
        return f"run {run.get('id')} is {run.get('path')!r}, not {WORKFLOW}"
    # A failed merge_group run has already taken its PR out of the queue; a
    # rerun would turn nothing green. The queue's own retry is --reruns.
    event = run.get("event")
    if not (event == "pull_request" or _is_push_to_main(run)):
        if event == "push":
            return (
                f"run {run.get('id')} was triggered by a push to"
                f" {run.get('head_branch')!r}, not by main"
            )
        return (
            f"run {run.get('id')} was triggered by {event!r}, not a pull request"
            " or a push to main"
        )
    if run.get("status") != "completed" or run.get("conclusion") != "failure":
        return (
            f"run {run.get('id')} is {run.get('status')}/{run.get('conclusion')},"
            " not a completed failure"
        )
    attempt = run.get("run_attempt")
    if not isinstance(attempt, int) or attempt >= MAX_ATTEMPTS:
        return f"run {run.get('id')} is on attempt {attempt}; the limit is {MAX_ATTEMPTS}"
    return None


def _is_push_to_main(run: dict) -> bool:
    """Whether ``run`` is a push to main, the one push route that may be rerun."""
    return run.get("event") == "push" and run.get("head_branch") == "main"


def needs_failed_log(run: dict, repository: str) -> bool:
    """Whether the failed job log is the only thing left to decide ``run``.

    True for a push to main in ``repository`` that passes every run-object
    gate: the log is what says whether its red was a flake. Anything else is
    already decided without reading a log, and reading one costs a `gh` call.
    """
    if (run.get("repository") or {}).get("full_name") != repository:
        return False
    return _is_push_to_main(run) and _gates(run) is None


def _gh(*arguments: str) -> subprocess.CompletedProcess | None:
    """Run ``gh``; None when it could not run at all.

    ``errors="replace"`` because a failed job's log can hold bytes that are not
    UTF-8; strictly decoding one would raise instead of letting the caller
    refuse the run for the reason the log does not say.
    """
    try:
        return subprocess.run(
            ["gh", *arguments],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GH_TIMEOUT,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"::warning::gh {arguments[0]}: {error}", file=sys.stderr)
        return None


def fetch_run(run_id: str, repository: str) -> dict | None:
    """The run object `GET /actions/runs/{id}` returns, or None."""
    result = _gh("api", f"repos/{repository}/actions/runs/{run_id}")
    if result is None or result.returncode != 0:
        detail = result.stderr.strip() if result is not None else "gh could not run"
        print(f"::warning::could not read run {run_id}: {detail}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        print(f"::warning::run {run_id} is not JSON: {error}", file=sys.stderr)
        return None


def fetch_failed_logs(run_id: str, repository: str) -> str | None:
    """The failed jobs' log of ``run_id``, or None when it could not be read."""
    result = _gh("run", "view", str(run_id), "--repo", repository, "--log-failed")
    if result is None or result.returncode != 0:
        detail = result.stderr.strip() if result is not None else "gh could not run"
        print(
            f"::warning::could not read the failed job log of run {run_id}: {detail}",
            file=sys.stderr,
        )
        return None
    return result.stdout


def rerun_failed_jobs(run_id: str, repository: str) -> str | None:
    """Ask GitHub to rerun the failed jobs; the failure text, or None on success."""
    result = _gh("run", "rerun", str(run_id), "--failed", "--repo", repository)
    if result is None:
        return "gh could not run"
    if result.returncode != 0:
        return result.stderr.strip() or f"gh exited {result.returncode}"
    return None


def _usage(entry: str) -> int:
    print(f"::error::{entry}")
    return 2


def main() -> int:
    arguments = [argument for argument in sys.argv[1:] if argument != "--dry-run"]
    dry_run = len(arguments) != len(sys.argv) - 1
    try:
        validate_log_signatures()
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    patterns = load_patterns()

    if arguments[:1] == ["--run"]:
        if len(arguments) != 3:
            return _usage(
                "usage: rerun-failed.py --run <run id> <owner/repo> [--dry-run]"
            )
        run_id, repository = arguments[1], arguments[2]
        if not run_id.isdigit():
            return _usage(f"run id {run_id!r} is not a number")
        run = fetch_run(run_id, repository)
        if run is None:
            return 1
        rerun = not dry_run
    else:
        if len(arguments) != 2:
            return _usage("usage: rerun-failed.py <run.json> <owner/repo>")
        with open(arguments[0]) as handle:
            run = json.load(handle)
        repository = arguments[1]
        rerun = False

    failed_log = None
    if needs_failed_log(run, repository):
        failed_log = fetch_failed_logs(run.get("id"), repository)
    reason = refusal(run, repository, patterns, failed_log=failed_log)
    if reason:
        print(f"::error::{reason}")
        return 1
    pattern = matching_pattern(run, patterns)
    if pattern:
        print(f"::notice::{diagnosis(run, pattern)}")
    if failed_log is not None:
        signature = flake_log_signature(failed_log)
        if signature:
            print(f"::notice::{log_diagnosis(run, signature)}")
    # --run --dry-run only decides. The saved-JSON path is not a dry run: the
    # workflow reruns with `gh run rerun` right after this exits 0.
    decision = "would rerun" if dry_run else "rerunning"
    print(f"Run {run['id']} attempt {run['run_attempt']}: {decision} its failed jobs.")
    if rerun:
        failure = rerun_failed_jobs(run["id"], repository)
        if failure is not None:
            print(f"::error::could not rerun run {run['id']}: {failure}")
            return 1
        print(f"Rerun requested for run {run['id']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
