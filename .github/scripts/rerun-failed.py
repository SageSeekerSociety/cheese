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
  delays the diagnosis. The failed jobs' logs are read per job id when the run
  is a push, so the workflow passes only the run object; GH_TOKEN is already in
  the step's environment.

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

# A job log line carries gh's "<job>\t<step>\t<timestamp> " prefix when it came
# from `gh run view`; the per-job REST logs are prefixed "job <id>\t\t".
_LOG_PREFIX = re.compile(r"^[^\t\n]*\t[^\t\n]*\t(?:\ufeff)?\S+\s")

# A failure report quotes text the test owns — its source, its fixtures and
# parameters, its expected values, its captured output — at every indentation
# level and with no reliable marker: pytest's `--tb=auto` prints `name = repr`
# lines at column 0, and unittest prints the continuation of a multi-line
# assertion message at column 0. Enumerating those shapes is what kept leaving
# a gap, so the rule is a bound instead of a shape list:
#
#   once a step starts printing a test report, nothing later in that step is
#   flake evidence.
#
# The gh log names the job and step on every line, so "that step" is exact. A
# report starts at a FAILURES/ERRORS/short-summary banner, a `_ test_x _`
# header, a `--- Captured ... ---` section, a unittest `FAIL:`/`ERROR:`
# header, or a `Traceback (most recent call last):` line. Before that point —
# setup, install, checkout, the tool's own output — lines are scanned, and so
# is every other step. Indented lines and column-0 report markers are skipped
# as well, for output a report leaves before its own banner.
#
# The bias is deliberate: an unrecognised line is refused rather than rerun, so
# a missed flake costs one person a look while a wrong rerun costs the failed
# job set again and delays the real diagnosis. The refusal side is not free and
# the reader is told which case it is: a flake printed in a step after that
# step's own report began is refused, and the run it happened on still counts
# against MAX_ATTEMPTS, so the commit has one fewer retry left. The refusal
# says "read it and decide by hand" — it does not claim there was no flake.
_REPORT_START = re.compile(
    r"^(?:=+ (?:FAILURES|ERRORS|short test summary info|warnings summary"
    r"|slowest \d+ durations) =+"
    r"|(?:=+ )?\d+ (?:failed|passed|error|skipped)\b[^\n]* =+"
    r"|\W{3,} (?:Failed Tests|Failed Suites|Unhandled Errors|Unhandled Rejection)"
    r"\b|-{3,} Captured .+ -{3,}"
    r"|_{3,} .+ _{3,}$"
    r"|--- FAIL: "
    r"|panic: "
    # vitest indents its per-test line: ` FAIL  src/x.spec.ts > x > fails`.
    r"| *FAIL {2,}\S.* > "
    r"|FAIL: |ERROR: |Traceback \(most recent call last\):$)"
)
# A test runner announces its session before it prints anything a test owns:
# vitest's ` RUN  v5.0.3`, node's TAP stream, pytest's session banner, go's
# `=== RUN`. ANSI colour codes are stripped first, because CI's vitest and
# `gh run view` both colour their output and every marker above is anchored at
# the start of the line.
#
# unittest is covered by its `FAIL:`/`ERROR:` header in _REPORT_START instead:
# it buffers a test's own output and prints it after the header, so the report
# bound already covers the whole of what it writes.
#
# A step that matches one is a test step: every line in it is test-owned text,
# before and after the failure report alike — console output the runner echoes
# live (`stderr | ...`, node's `# Error: ...`), failed suites, unhandled
# rejections, and the report itself. Nothing in such a step is flake evidence.
# Every other step is scanned normally.
_TEST_SESSION = re.compile(
    r"^(?: *RUN +v\d"
    r"|TAP version \d+"
    r"|=+ test session starts =+"
    r"|=== RUN"
    r"|Running \d+ tests? using \d+ workers?)"
)
# A passing pytest run still prints its `warnings summary` and slowest-durations
# banners, and either can carry a signature in a test id. Those banners must
# start the bound too, so the tests below pin each banner name separately: with
# any one of them dropped, a passing shard's log can match as a flake.
_PYTEST_REPORT = re.compile(
    r"^(?:FAILED |ERROR |>{1,2}\s|E {2,}|assert |AssertionError)"
)
# GitHub's runner wraps each step's output in `##[group]<step>` ... endgroup.
# The per-job REST logs have no job/step prefix of their own, so this is what
# keeps the (job, step) bound at step granularity.
_STEP_START = re.compile(r"^##\[group\](?:Run )?(.*)$")
# Annotations are the job's failure summary, not the failing step's output.
# Most reprint a case or a tool's text and are never evidence:
# `##[error]tests/...::test_x — AssertionError: ...` and
# `::error file=...,line=N::<node id> — <message>` from the repo's own
# report_failed_cases.py, GitHub's `##[error]Process completed with exit
# code N`. The text they reprint can quote any test wording, including a
# flake's, so containment in one of them says nothing.
# The runner's own vanishing message is the exception, and GitHub writes it as
# an annotation whose body is that message:
# `##[error]The runner has received a shutdown signal.`,
# `##[error]The hosted runner lost communication with the server.` It carries
# no `##[group]`, so it inherits the test step's key and would be dropped with
# that step's text; only a body that starts with it is evidence.
_ANNOTATION_PREFIX = re.compile(
    r"^(?:##\[error\]|::error(?: file=[^:]*)?:?:?)\s*"
)
_RUNNER_ANNOTATION = re.compile(
    r"^(?:The\s+)?(?:hosted\s+|self-hosted\s+)?runner\b", re.IGNORECASE
)
# OSC sequences (hyperlinks, window titles) and CSI codes with private or
# intermediate bytes: `\x1b]...BEL` and `\x1b[?...h`.
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_ANSI_CSI = re.compile(r"\x1b\[[0-9;?<=>]*[ -/]*[@-~]")


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
    """One log line without gh's prefixes or any ANSI escape sequence."""
    message = _LOG_PREFIX.sub("", line, count=1)
    message = _ANSI_OSC.sub("", message)
    return _ANSI_CSI.sub("", message)


def _log_key(line: str) -> tuple[str, str] | None:
    """The job and step a log line belongs to, if its prefix names them."""
    fields = line.split("\t", 3)
    if len(fields) >= 3:
        return fields[0], fields[1]
    return None


def _signature(signature_id: str) -> dict | None:
    for signature in LOG_SIGNATURES:
        if signature["id"] == signature_id:
            return signature
    return None


def flake_log_signature(
    failed_log: str | None, signatures: tuple[dict, ...] | list[dict] | None = None
) -> dict | None:
    """The first known flake signature a non-report line of ``failed_log`` matches.

    Report lines are skipped (see the patterns above): they quote the test's
    own text, so an `ECONNRESET` or a rate-limit message written in a test
    would otherwise make a real failure look like infrastructure flake. A
    captured-output block and the report markers end no later than the step
    that started them, so a later step's real failure is still read.
    """
    if not failed_log:
        return None
    lines = []
    job = ""
    step = ""
    step_no = 0
    for line in failed_log.splitlines():
        key = _log_key(line)
        message = _message(line)
        if key is not None:
            job, named_step = key
            # An unnamed step field (the per-job REST format) keeps the step
            # from the last `##[group]`; a named one is that step.
            step = named_step or step
        start = _STEP_START.match(message)
        if start is not None:
            step_no += 1
            step = f"{step_no} {start.group(1).strip()}"
        lines.append(((job, step), message))
    test_steps = {key for key, message in lines if _TEST_SESSION.match(message)}
    reported: set[tuple[str, str]] = set()
    runner_lost = _signature("runner-lost")
    for step, message in lines:
        if _ANNOTATION_PREFIX.match(message):
            body = _ANNOTATION_PREFIX.sub("", message)
            if (runner_lost is not None and _RUNNER_ANNOTATION.match(body)
                    and re.search(runner_lost["pattern"], body, re.IGNORECASE)):
                return runner_lost
            continue
        if step in test_steps or step in reported:
            continue
        if _REPORT_START.match(message):
            reported.add(step)
            continue
        if not message.strip() or message[:1].isspace():
            continue
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
            f"run {run.get('id')} is a push to main and no known flake signature"
            " was found in the steps that could hold one; read the failed jobs'"
            " logs and decide by hand"
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


def _failed_job_ids(run_id: str, repository: str) -> list[int] | None:
    """The failed jobs of ``run_id``, or None when the run could not be read.

    Every page: a run with more than a hundred jobs would otherwise hide the
    failed ones past the first page, and their flake lines with them.
    """
    ids: list[int] = []
    page = 1
    while True:
        result = _gh(
            "api",
            f"repos/{repository}/actions/runs/{run_id}/jobs"
            f"?filter=latest&per_page=100&page={page}",
        )
        if result is None or result.returncode != 0:
            detail = result.stderr.strip() if result is not None else "gh could not run"
            print(
                f"::warning::could not read run {run_id}'s jobs: {detail}",
                file=sys.stderr,
            )
            return None
        try:
            jobs = json.loads(result.stdout).get("jobs") or []
        except json.JSONDecodeError as error:
            print(
                f"::warning::run {run_id}'s jobs are not JSON: {error}",
                file=sys.stderr,
            )
            return None
        ids.extend(
            job["id"]
            for job in jobs
            if job.get("conclusion") == "failure" and isinstance(job.get("id"), int)
        )
        if len(jobs) < 100:
            return ids
        page += 1


def fetch_failed_logs(run_id: str, repository: str) -> str | None:
    """The failed jobs' logs of ``run_id``, or None when none could be read.

    One REST call per failed job: `gh run view --log-failed` matches the job
    names it sees in the log archive against the run's job list, and a
    reusable-workflow job is named `backend / contract (0/1)` here but
    `backend  contract (01)` there, so those jobs — every product suite a main
    push runs — never match and their logs are silently missing from its
    output.

    A job whose log cannot be read is named and skipped; the rest still count.
    None, not "", when nothing could be read: the refusal should say so rather
    than claim the logs matched no signature.
    """
    job_ids = _failed_job_ids(run_id, repository)
    if job_ids is None:
        return None
    logs = []
    for job_id in job_ids:
        result = _gh("api", f"repos/{repository}/actions/jobs/{job_id}/logs")
        if result is None or result.returncode != 0 or not result.stdout.strip():
            detail = result.stderr.strip() if result is not None else "gh could not run"
            print(
                f"::warning::could not read the log of job {job_id} in run"
                f" {run_id}: {detail or 'empty output'}",
                file=sys.stderr,
            )
            continue
        # The log text has no job or step of its own, so the job id goes in
        # the job field and the step field is left empty: the `##[group]`
        # markers in the text then name the step, and the (job, step) bound
        # stays at step granularity — a setup failure earlier in a job is not
        # swallowed by a test step later in it.
        logs.append(
            "\n".join(
                f"job {job_id}\t\t{line}"
                for line in result.stdout.splitlines()
            )
        )
    return "\n".join(logs) if logs else None


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
    argv = sys.argv[1:]
    # `--dry-run` is a flag, not an argument: exactly one, anywhere, and every
    # other token is a real argument.
    dry_run = "--dry-run" in argv
    arguments = [argument for argument in argv if argument != "--dry-run"]
    if dry_run and argv.count("--dry-run") != 1:
        return _usage("usage: rerun-failed.py --run <run id> <owner/repo> [--dry-run]")
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
