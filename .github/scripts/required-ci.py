"""Select merge-diff suites and fail the required check unless all pass.

Two tiers, one judge. A DRAFT pull request runs only the fast tier — the
bounded local evidence `ci-fast.py --tier quick` produces — because a draft is
work in progress, not a delivery to review. Every other run is the full tier:
each suite the merge diff selects. The tier is decided HERE, from the event, so
the check a draft produces can never be the one branch protection requires:

- a draft pull_request run is named ``CI required (draft quick)`` (the
  aggregating job's ``name`` in required-ci.yml) and only the ``fast`` job may
  succeed — every full suite must be SKIPPED, so a draft's green can never be
  read as the full gate's green;
- a merge_group entry, or any pull_request that is not a draft, is named ``CI
  required`` and requires exactly the selected suites, as before.

Fail closed: an event this module cannot read as a non-draft pull_request or a
merge queue entry is the FULL tier (the real gate), never the fast one.
"""

import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys

PATHS = json.loads(Path(__file__).with_name("required-ci-paths.json").read_text())

#: The two tiers a run can be. ``full`` = every selected suite; ``quick`` = only
#: the fast job. A draft's aggregation accepts only ``quick``.
TIERS = ("full", "quick")


def tier_for(event_name, event):
    """Which tier this run must be — ``"full"`` or ``"quick"``.

    Only a DRAFT pull_request is ``"quick"``. A ``merge_group`` entry and any
    pull_request whose ``draft`` flag is false are ``"full"``; so is anything we
    cannot read — a missing/absent flag, a malformed payload, an unknown event,
    or a non-dict event. An unreadable event is the STRICT gate, never the
    loose one: defaulting to ``"quick"`` on a parse surprise would let a draft's
    fast evidence stand in for the real merge gate.

    The GitHub pull_request payload's ``draft`` flag is authoritative and
    correct on every action: it is true on ``opened``/``converted_to_draft`` and
    false on ``ready_for_review``. The action name is not consulted — the flag
    already carries it, and reading the flag keeps one source of truth.
    """
    if event_name == "merge_group":
        return "full"
    if event_name == "pull_request" and isinstance(event, dict):
        pull = event.get("pull_request")
        if isinstance(pull, dict):
            draft = pull.get("draft")
            if draft is False:
                return "full"
            if draft is True:
                return "quick"
    return "full"


def _read_event():
    """The run's event name and payload, as the runner hands them over.

    ``GITHUB_EVENT_NAME`` + the JSON at ``GITHUB_EVENT_PATH``. A missing or
    unreadable file is an empty dict — :func:`tier_for` then reads it as the
    full tier (fail closed), and :func:`failures` still gets a tier to compare
    against. Never raises: a parse surprise must not crash the gate into a
    non-result.
    """
    name = os.environ.get("GITHUB_EVENT_NAME", "")
    path = os.environ.get("GITHUB_EVENT_PATH")
    event = {}
    if path and Path(path).is_file():
        try:
            event = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            event = {}
    if not isinstance(event, dict):
        event = {}
    return name, event


def select(paths):
    # Changes to the gate itself must exercise every callable suite.
    gate_changed = any(
        path == ".github/workflows/required-ci.yml"
        or path.startswith(".github/scripts/required-ci")
        or path == ".github/scripts/test_required_ci.py"
        for path in paths
    )
    return {
        suite: gate_changed
        or suite == "guards"
        or any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)
        for suite, patterns in PATHS.items()
    }


def failures(needs, expected_tier):
    """Every reason the required check must NOT pass, given the job results.

    ``needs`` is the aggregating job's ``toJSON(needs)``; ``expected_tier`` is
    what the event demands (:func:`tier_for`), recomputed here so a scope output
    that disagrees with the event cannot smuggle a loose tier past the gate.

    In the full tier a selected suite must be ``success`` and an unselected one
    ``skipped``; the fast job must be ``skipped``. In the quick tier every
    suite must be ``skipped`` and the fast job must be ``success`` — a green
    product suite on a draft is a failure, not a convenience, because it would
    mean a full suite ran (and could have been reported) under the draft's name.
    Missing, cancelled, invalid and out-of-band results all fall out of the
    exact-match comparison below.
    """
    scope = needs.get("scope", {})
    if scope.get("result") != "success":
        return ["scope did not succeed"]
    outputs = scope.get("outputs", {})
    tier = outputs.get("tier")
    if tier not in TIERS:
        return [f"scope tier missing or invalid: {tier!r}"]
    if tier != expected_tier:
        return [f"scope tier {tier!r} does not match the event's {expected_tier!r}"]
    errors = []
    for suite in PATHS:
        selected = outputs.get(suite)
        if selected not in ("true", "false"):
            errors.append(f"{suite}: missing or invalid scope output")
            continue
        # A draft run must not run any full suite, regardless of what it selects.
        expected = (
            ("success" if selected == "true" else "skipped")
            if tier == "full"
            else "skipped"
        )
        actual = needs.get(suite, {}).get("result", "missing")
        if actual != expected:
            errors.append(f"{suite}: expected {expected}, got {actual}")
    fast_expected = "success" if tier == "quick" else "skipped"
    fast_actual = needs.get("fast", {}).get("result", "missing")
    if fast_actual != fast_expected:
        errors.append(f"fast: expected {fast_expected}, got {fast_actual}")
    return errors


def main():
    if sys.argv[1] == "scope":
        event_name, event = _read_event()
        # No rename detection: moving code must test both the old and new area.
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", sys.argv[2], "HEAD"]
        ).decode().split("\0")
        tier = tier_for(event_name, event)
        # The raw select() flags are kept whatever the tier: the fast job reads
        # them to decide which dependency sets to install, and the aggregation
        # expects every selected FULL suite to be skipped on a draft.
        outputs = {suite: str(sel).lower() for suite, sel in select(changed).items()}
        outputs["tier"] = tier
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for name, value in outputs.items():
                line = f"{name}={value}"
                print(line)
                print(line, file=output)
    elif sys.argv[1] == "check":
        _, event = _read_event()
        expected_tier = tier_for(os.environ.get("GITHUB_EVENT_NAME", ""), event)
        errors = failures(json.loads(os.environ["CI_NEEDS"]), expected_tier)
        if errors:
            raise SystemExit("\n".join(errors))
        print(f"Every selected suite passed (tier: {expected_tier}).")
    else:
        raise SystemExit("Usage: required-ci.py scope BASE_SHA | check")


if __name__ == "__main__":
    main()
