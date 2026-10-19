"""Select merge-diff suites and fail the required check unless all pass.

Two tiers, one judge. Every pull request runs only the quick tier — the
bounded evidence `ci-fast.py --tier quick` produces. Full suites run after
landing, on a push to main (or a merge_group entry), not again on a ready PR.

Drafts aggregate as ``CI required (draft quick)``; ready PRs aggregate as
``CI required`` and check the commit title. Both require ``fast`` to succeed
and all full suites to be skipped. Landed-code runs also use ``CI required``,
but require the full tier and every selected suite to succeed. Only that
post-submit result can authorize deployment.

A main push is the post-submit gate: its base is the commit `main-green` names
(:func:`main_green_anchor`), so one run covers every commit that landed since
the gate last passed. The base is never ``github.event.before`` — that is only
the previous tip, and the commits of a run that was superseded while pending
would then fall outside every later diff.

Fail closed: an event this module cannot read as a valid pull_request is the
FULL tier, never the fast one.
"""

import fnmatch
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PATHS = json.loads(Path(__file__).with_name("required-ci-paths.json").read_text())

#: The two tiers a run can be. ``full`` = every selected suite; ``quick`` = only
#: the fast job. A draft's aggregation accepts only ``quick``.
TIERS = ("full", "quick")

#: Suites deferred to a run of landed code: on ``pull_request`` events ``scope``
#: always outputs ``false`` for them and ``check`` expects ``skipped`` (while
#: ``branch_heavy=false`` tells ``test.yml`` to skip backend ``integration``).
QUEUE_ONLY_SUITES = ("e2e", "remote")

#: The ref a main push run takes its base from: the commit the last successful
#: gate result on main was about. The ``mark-green`` job in required-ci.yml
#: moves it after a green run; a red one leaves it, so the next run's diff
#: covers both.
MAIN_GREEN_REF = "refs/heads/main-green"
MAIN_GREEN_TRACKING = "refs/remotes/origin/main-green"


def branch_heavy_for(event_name):
    """Whether heavy suites (backend integration, e2e, remote) run on this event.

    On ``pull_request`` runs only the cheap suites run and heavy suites are
    deferred to a run of landed code (``push`` on main, or a ``merge_group``
    entry). Any other or unreadable event returns ``True`` (fail closed).
    """
    return event_name != "pull_request"


def tier_for(event_name, event):
    """Which tier this run must be — ``"full"`` or ``"quick"``.

    Both draft and ready pull requests are ``"quick"``. A ``push`` to main,
    a ``merge_group`` entry and all other events stay ``"full"``. The draft flag
    only chooses the aggregator's name in the workflow, not its evidence tier.
    Missing or malformed PR payloads still fail closed to the full tier.
    """
    if event_name == "merge_group":
        return "full"
    if event_name == "pull_request" and isinstance(event, dict):
        pull = event.get("pull_request")
        if isinstance(pull, dict):
            draft = pull.get("draft")
            if draft is True or draft is False:
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


def select(paths, event_name=None):
    # Changes to the gate itself select every callable suite (except queue-only
    # suites on pull_request runs, which are deferred to merge_group below).
    gate_changed = any(
        path == ".github/workflows/required-ci.yml"
        or path.startswith(".github/scripts/required-ci")
        or path == ".github/scripts/test_required_ci.py"
        for path in paths
    )
    selected = {
        suite: gate_changed
        or suite == "guards"
        or any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)
        for suite, patterns in PATHS.items()
    }
    if event_name is not None and not branch_heavy_for(event_name):
        for suite in QUEUE_ONLY_SUITES:
            selected[suite] = False
    return selected


def failures(needs, expected_tier, event_name=""):
    """Every reason the required check must NOT pass, given the job results.

    ``needs`` is the aggregating job's ``toJSON(needs)``; ``expected_tier`` is
    what the event demands (:func:`tier_for`), recomputed here so a scope output
    that disagrees with the event cannot smuggle a loose tier past the gate.

    In the full tier a selected suite must be ``success`` and an unselected one
    ``skipped`` (on ``pull_request``, :data:`QUEUE_ONLY_SUITES` are always
    ``skipped`` and left to ``merge_group``); the fast job must be ``skipped``.
    The frontend suite is also due when the ``api`` job saw the OpenAPI document
    move (:func:`api_failures`).
    In the quick tier every
    suite must be ``skipped`` and the fast job must be ``success`` — a green
    product suite on a draft is a failure, not a convenience, because it would
    mean a full suite ran (and could have been reported) under the draft's name.
    Missing, cancelled, invalid and out-of-band results all fall out of the
    exact-match comparison below.
    """
    if not isinstance(needs, dict):
        return [f"needs is not an object: {needs!r}"]
    scope = needs.get("scope", {})
    if scope.get("result") != "success":
        return ["scope did not succeed"]
    outputs = scope.get("outputs", {})
    tier = outputs.get("tier")
    if tier not in TIERS:
        return [f"scope tier missing or invalid: {tier!r}"]
    if tier != expected_tier:
        return [f"scope tier {tier!r} does not match the event's {expected_tier!r}"]
    branch_heavy = outputs.get("branch_heavy")
    if branch_heavy is not None:
        if branch_heavy not in ("true", "false"):
            return [f"scope branch_heavy invalid: {branch_heavy!r}"]
        if event_name:
            expected_heavy = str(branch_heavy_for(event_name)).lower()
            if branch_heavy != expected_heavy:
                return [
                    f"scope branch_heavy {branch_heavy!r} does not match "
                    f"the event's {expected_heavy!r}"
                ]
    errors, api_moved = api_failures(needs, outputs, tier)
    for suite in PATHS:
        selected = outputs.get(suite)
        if selected not in ("true", "false"):
            errors.append(f"{suite}: missing or invalid scope output")
            continue
        # A draft run must not run any full suite, regardless of what it selects;
        # a pull_request run leaves e2e and remote to merge_group.
        should_run = (
            tier == "full"
            and (selected == "true" or (suite == "frontend" and api_moved))
            and (branch_heavy_for(event_name) or suite not in QUEUE_ONLY_SUITES)
        )
        expected = "success" if should_run else "skipped"
        actual = needs.get(suite, {}).get("result", "missing")
        if actual != expected:
            errors.append(f"{suite}: expected {expected}, got {actual}")
    fast_expected = "success" if tier == "quick" else "skipped"
    fast_actual = needs.get("fast", {}).get("result", "missing")
    if fast_actual != fast_expected:
        errors.append(f"fast: expected {fast_expected}, got {fast_actual}")
    return errors


def api_selected(outputs, tier):
    """Whether the ``api`` job must run: a full run whose diff reached the
    backend, where the paths alone have not already brought the frontend suite.

    The frontend compiles against the backend's OpenAPI document, and the
    document is a function of backend files the path map cannot tell apart from
    any other backend file. So the paths select the frontend suite for frontend
    files, and for a backend change the ``api`` job answers instead: it prints
    the document at the base and at the change and says whether they differ.
    The same condition is the job's ``if:`` in required-ci.yml.
    """
    return (
        tier == "full"
        and outputs.get("backend") == "true"
        and outputs.get("frontend") != "true"
    )


def api_failures(needs, outputs, tier):
    """What is wrong with the ``api`` job's result, and whether it saw a move.

    Returns ``(errors, moved)``. When the job is due it must succeed with a
    ``moved`` of ``"true"`` or ``"false"``: a job that failed, or answered
    something else, has not said the surface stayed put, so the frontend suite
    it would have started is not excused. When it is not due it must be skipped.
    """
    api = needs.get("api", {})
    actual = api.get("result", "missing")
    if not api_selected(outputs, tier):
        if actual != "skipped":
            return [f"api: expected skipped, got {actual}"], False
        return [], False
    if actual != "success":
        return [f"api: expected success, got {actual}"], False
    moved = api.get("outputs", {}).get("moved")
    if moved not in ("true", "false"):
        return [f"api: moved output missing or invalid: {moved!r}"], False
    return [], moved == "true"


def main_green_anchor(attempts=3):
    """The commit ``main-green`` names, or ``""`` when there is no such ref.

    This is the base of a post-submit run on main. ``ls-remote --exit-code``
    answers "no such ref" (exit 2) separately from a failed read, so a first
    push after the gate moved to main runs with every path in scope instead of
    reddening. A read or fetch that keeps failing is also ``""``, for the same
    reason in the other direction: a wider diff never hides a commit, while a
    red main run would hold the deploy gate over a base this could have widened
    past.
    """
    for attempt in range(1, attempts + 1):
        probe = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", MAIN_GREEN_REF],
            capture_output=True, text=True,
        )
        if probe.returncode == 2:
            return ""
        if probe.returncode == 0 and probe.stdout.strip():
            break
        if attempt == attempts:
            print(f"::warning::reading {MAIN_GREEN_REF} failed "
                  f"({probe.stderr.strip() or probe.returncode}); every path is in scope")
            return ""
        time.sleep(2)
    refspec = f"+{MAIN_GREEN_REF}:{MAIN_GREEN_TRACKING}"
    # Depth one only where the checkout already is: a full clone (a developer
    # running this by hand) keeps its history, which is what lets the ancestry
    # check below answer.
    shallow = subprocess.check_output(
        ["git", "rev-parse", "--is-shallow-repository"]
    ).decode().strip() == "true"
    depth = ["--depth=1"] if shallow else []
    for attempt in range(1, attempts + 1):
        fetched = subprocess.run(
            ["git", "fetch", "--no-tags", *depth, "origin", refspec],
            capture_output=True, text=True,
        )
        if fetched.returncode == 0:
            return subprocess.check_output(
                ["git", "rev-parse", MAIN_GREEN_TRACKING]
            ).decode().strip()
        if attempt == attempts:
            print(f"::warning::fetching {MAIN_GREEN_REF} failed "
                  f"({fetched.stderr.strip() or fetched.returncode}); every path is in scope")
            return ""
        time.sleep(2)
    return ""  # pragma: no cover - the loop either returns or returns above


def _is_ancestor(ancestor, descendant):
    """Whether ``ancestor`` is on ``descendant``'s history.

    A depth-one checkout cannot answer this — git sees each fetched commit as
    its own shallow root and calls two commits on one line unrelated — so a
    shallow repository is answered "yes": the anchor is only ever written from
    a main push run, and refusing every batch over a check the clone cannot
    make would widen the scope of every push to the whole tree.
    """
    shallow = subprocess.check_output(
        ["git", "rev-parse", "--is-shallow-repository"]
    ).decode().strip()
    if shallow == "true":
        print("Shallow checkout: the anchor's ancestry is not checkable here.")
        return True
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
    ).returncode == 0


def merge_ref_base(event_base, head="HEAD", cwd=None):
    """The base a pull_request checkout was actually merged onto.

    The default checkout for a pull_request is GitHub's merge ref: a merge
    commit whose first parent is the base branch tip it was built on. The
    event payload's ``pull_request.base.sha`` is not always that commit: main
    moves between the payload and the merge ref, so a diff against the payload
    counts unrelated main commits as part of the PR. Measured 2026-10-19, a
    backend-only PR selected the frontend suite and spent 333 s running it
    because main's frontend changes sat between the two bases. A commit with
    two parents is the merge; its first parent is the base. Anything else (a
    head checkout, a ref that is not a merge) keeps the event's answer.
    """
    parents = subprocess.check_output(
        ["git", "rev-list", "--parents", "-n", "1", head],
        cwd=cwd, text=True,
    ).split()
    return parents[1] if len(parents) >= 3 else event_base


def effective_base(event_base, head="HEAD"):
    """Resolve the one base both the receipt and suite diff must use."""
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "merge_group":
        # The event base may be the preceding queue entry's synthetic tree.
        # Only landed main can exclude a prefix from this run's coverage.
        main_ref = "+refs/heads/main:refs/remotes/origin/main"
        subprocess.check_call(["git", "fetch", "--no-tags", "origin", main_ref])
        shallow = subprocess.check_output(
            ["git", "rev-parse", "--is-shallow-repository"]
        ).decode().strip()
        if shallow == "true":
            # checkout and the workflow's base fetch are both depth one. Fetch
            # the cumulative head too: fetching main alone cannot fill its DAG.
            head_sha = subprocess.check_output(
                ["git", "rev-parse", head]
            ).decode().strip()
            subprocess.check_call(
                ["git", "fetch", "--no-tags", "--unshallow", "origin", main_ref, head_sha]
            )
        base = subprocess.check_output(
            ["git", "merge-base", "refs/remotes/origin/main", head]
        ).decode().strip()
        reason = (
            "merge_group uses the landed origin/main ancestor; the event base may "
            "contain an unlanded queue prefix, whose changes remain in coverage"
        )
    elif event_name == "push":
        base = main_green_anchor()
        if base and not _is_ancestor(base, head):
            # The anchor is only ever written from a main push run, so this
            # should not happen; if it does (a rewritten ref), a diff against it
            # is not a diff of what landed, so widen instead of guessing.
            print(f"::warning::{MAIN_GREEN_REF} ({base}) is not an ancestor of "
                  f"{head}; every tracked path is in scope")
            base = ""
        if base:
            reason = (
                f"push uses the last green gate result ({MAIN_GREEN_REF}), so the "
                "batch covers every commit that landed since; github.event.before "
                "is only the previous tip and would drop a superseded run's commits"
            )
        else:
            reason = (
                f"no usable {MAIN_GREEN_REF} anchor, so every tracked path is in "
                "scope: a commit no run has covered cannot be skipped"
            )
    else:
        base = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{event_base}^{{commit}}"]
        ).decode().strip()
        reason = "uses the event-provided base"
    return base, reason


def changed_files(base, head="HEAD"):
    # No rename detection: moving code must test both the old and new area.
    # Fetch/ancestry failures propagate; never fall back to a narrower diff.
    if not base:
        # No base to compare with (a main push before the first green anchor):
        # every tracked path counts as changed, which is the widest selection
        # this diff can express.
        return subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    return subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head]
    ).decode().split("\0")


def actions_run_url():
    """Return this workflow run's URL, or an explicit marker outside Actions."""
    server = os.environ.get("GITHUB_SERVER_URL", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if not server or not repository or not run_id:
        return "无链接"
    return f"{server.rstrip('/')}/{repository}/actions/runs/{run_id}"


def main():
    if sys.argv[1] == "pr-base":
        event_base = sys.argv[2] if len(sys.argv) > 2 else ""
        print(merge_ref_base(event_base))
    elif sys.argv[1] == "scope":
        event_name, event = _read_event()
        # A main push names no base it can trust, so the argument is empty and
        # effective_base resolves the anchor itself.
        event_base_arg = sys.argv[2] if len(sys.argv) > 2 else ""
        event_base, reason = effective_base(event_base_arg)
        changed = changed_files(event_base)
        tier = tier_for(event_name, event)
        # Non-queue-only select() flags are kept whatever the tier: the aggregation
        # expects every selected FULL suite to be skipped on a draft, while
        # pull_request always leaves e2e and remote false for merge_group.
        selected = select(changed, event_name)
        outputs = {suite: str(sel).lower() for suite, sel in selected.items()}
        outputs["tier"] = tier
        outputs["branch_heavy"] = str(branch_heavy_for(event_name)).lower()
        # The commit the ``api`` job prints the base document from: the same
        # base this diff was taken against, so the two answer one question.
        outputs["base"] = event_base
        print(f"Event base: {event_base_arg or '(none)'}")
        print(f"Effective base: {event_base or '(none: every tracked path)'}")
        print(f"Base reason: {reason}")
        if not branch_heavy_for(event_name):
            print("Queue-only suites: backend integration, e2e/remote 留给落码后的 run")
        suites = ", ".join(suite for suite, is_selected in selected.items() if is_selected)
        print(f"Selected suites: {suites or 'none'}")
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for name, value in outputs.items():
                line = f"{name}={value}"
                print(line)
                print(line, file=output)
    elif sys.argv[1] == "check":
        event_name, event = _read_event()
        expected_tier = tier_for(event_name, event)
        errors = failures(json.loads(os.environ["CI_NEEDS"]), expected_tier, event_name)
        if errors:
            run_url = actions_run_url()
            raise SystemExit("\n".join(f"{error} (run: {run_url})" for error in errors))
        print(f"Every selected suite passed (tier: {expected_tier}).")
    else:
        raise SystemExit("Usage: required-ci.py scope [BASE_SHA] | check | pr-base [EVENT_BASE]")


if __name__ == "__main__":
    main()
