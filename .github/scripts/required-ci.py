"""Select merge-diff suites and fail the required check unless all pass.

Three tiers, one judge (:func:`tier_for`):

- ``draft``: a draft pull request runs no suite. It aggregates as ``CI
  required (draft quick)``, a name the merge gate does not read; the platform
  marks a draft ready when it hands the work over, and that starts the ``pr``
  run.
- ``pr``: a ready pull request runs every suite its diff selects except
  :data:`POST_MERGE_SUITES`, and the backend's integration layer only for the
  test files that executed what the diff changed, by the latest coverage map
  (``coverage_map.select``), or whole when there is no map. It aggregates as
  ``CI required`` and checks the commit title.
- ``full``: a push to main runs every selected suite, all of each. Only this
  post-submit result can authorize deployment.

A main push is the post-submit gate: its base is the commit `main-green` names
(:func:`main_green_anchor`), so one run covers every commit that landed since
the gate last passed. The base is never ``github.event.before`` — that is only
the previous tip, and the commits of a run that was superseded while pending
would then fall outside every later diff.

Fail closed: an event this module cannot read as a valid pull_request is the
FULL tier.
"""

import fnmatch
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend_imports
import coverage_map

PATHS = json.loads(Path(__file__).with_name("required-ci-paths.json").read_text())

TIERS = ("full", "pr", "draft")

#: Suites that run only on a push to main: browser flows, the remote executor,
#: the released MCP contract and the Claude Code equivalence runs. They take
#: dedicated runners or several times a pull request's other suites, and broke
#: main far less often than the suites a pull request runs (2026-10-18..20).
POST_MERGE_SUITES = ("e2e", "remote", "mcp", "equivalence")

#: Integration test files per shard: the layer's 718 files over its five
#: shards. A narrowed selection gets as many shards as it needs at that rate.
INTEGRATION_FILES_PER_SHARD = 144
INTEGRATION_SHARDS = 5
#: The floor a whole integration layer is held to (test.yml's default). A
#: narrowed selection has none: its files may hold no integration case at all,
#: and the plan the shards are checked against is what says what had to run.
INTEGRATION_FLOOR = 2400

#: Paths that decide the integration selection itself. A change to one is
#: exactly the change that must not be judged by it.
SELECTION_INPUTS = (
    ".github/scripts/backend_imports.py",
    ".github/scripts/coverage_map.py",
    ".github/scripts/required-ci.py",
    "backend/scripts/ci_shard.py",
)

#: The ref a main push run takes its base from: the commit the last successful
#: gate result on main was about. The ``mark-green`` job in required-ci.yml
#: moves it after a green run; a red one leaves it, so the next run's diff
#: covers both.
MAIN_GREEN_REF = "refs/heads/main-green"
MAIN_GREEN_TRACKING = "refs/remotes/origin/main-green"


def tier_for(event_name, event):
    """Which tier this run must be — ``"draft"``, ``"pr"`` or ``"full"``.

    A pull request whose payload says ``draft: true`` is ``"draft"``, ``false``
    is ``"pr"``. A push to main and every other or unreadable event is
    ``"full"`` (fail closed).
    """
    if event_name == "pull_request" and isinstance(event, dict):
        pull = event.get("pull_request")
        if isinstance(pull, dict):
            draft = pull.get("draft")
            if draft is True:
                return "draft"
            if draft is False:
                return "pr"
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


def gate_changed(paths):
    return any(
        path == ".github/workflows/required-ci.yml"
        or path.startswith(".github/scripts/required-ci")
        or path == ".github/scripts/test_required_ci.py"
        for path in paths
    )


def select(paths, tier="full"):
    """Which suites run: the ones the path map selects (every suite when the
    gate itself changed), none on a draft, and no post-merge suite on a pull
    request."""
    changed_gate = gate_changed(paths)
    selected = {
        suite: changed_gate
        or suite == "guards"
        or any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)
        for suite, patterns in PATHS.items()
    }
    if tier == "draft":
        return {suite: False for suite in selected}
    if tier == "pr":
        for suite in POST_MERGE_SUITES:
            selected[suite] = False
    return selected


#: The workflow that publishes the coverage map, and its artifact.
COVERAGE_MAP_WORKFLOW = "coverage-map.yml"
COVERAGE_MAP_ARTIFACT = "integration-coverage-map"


class Diff:
    """What the integration selection reads of a diff: the changed lines, and
    a path's text at the base and at the head (None when it is absent)."""

    def __init__(self, base, head="HEAD"):
        self.base, self.head = base, head
        self.lines = coverage_map.changed_lines(subprocess.check_output(
            ["git", "diff", "-U0", "--no-renames", base, head, "--", "backend/"]).decode())

    def _show(self, rev, path):
        result = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
        return result.stdout.decode() if result.returncode == 0 else None

    def read_base(self, path):
        return self._show(self.base, path)

    def read_head(self, path):
        return self._show(self.head, path)


def load_coverage_map():
    """``(map, note)`` for the newest published coverage map, or ``(None,
    reason)`` when none can be read: any failure means the whole layer."""
    import io
    import urllib.request
    import zipfile

    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return None, "no GITHUB_TOKEN to read the coverage map with"
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")

    def get(url):
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    try:
        runs = json.loads(get(f"{api}/repos/{repo}/actions/workflows/{COVERAGE_MAP_WORKFLOW}"
                              "/runs?branch=main&status=success&per_page=1"))["workflow_runs"]
        if not runs:
            return None, "no coverage map has been published"
        artifacts = json.loads(get(f"{api}/repos/{repo}/actions/runs/{runs[0]['id']}"
                                   "/artifacts"))["artifacts"]
        artifact = next((a for a in artifacts if a["name"] == COVERAGE_MAP_ARTIFACT
                         and not a["expired"]), None)
        if artifact is None:
            return None, f"run {runs[0]['id']} has no unexpired {COVERAGE_MAP_ARTIFACT}"
        with zipfile.ZipFile(io.BytesIO(get(artifact["archive_download_url"]))) as archive:
            cmap = json.loads(archive.read("map.json"))
    except Exception as error:  # noqa: BLE001 — every failure here means the whole layer
        return None, f"the coverage map could not be read ({error.__class__.__name__}: {error})"
    return cmap, f"coverage map from {cmap['commit'][:9]} ({runs[0]['created_at']})"


def integration_plan(paths, tier, backend, cmap=None, diff=None, map_note=""):
    """``(files, shards, floor, reasons)`` for the backend integration layer.

    ``files`` is ``None`` for every integration test, else the backend-relative
    paths to run (empty: the layer is not due). ``shards`` lists ``"i/n"``
    partitions; ``floor`` is the case count the layer is held to. Only a ``pr``
    run narrows, only with a coverage map ``cmap`` and the run's ``diff``, and
    only when every path that selected the backend suite is a backend file.
    """
    whole = (None, [f"{i}/{INTEGRATION_SHARDS}" for i in range(INTEGRATION_SHARDS)],
             INTEGRATION_FLOOR)
    if tier != "pr":
        return (*whole, [f"the {tier} tier runs the whole layer"])
    if gate_changed(paths) or any(path in SELECTION_INPUTS for path in paths):
        return (*whole, ["the gate or the selection itself changed"])
    patterns = PATHS["backend"]
    outside = sorted(
        path for path in paths
        if not path.startswith("backend/")
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    )
    if outside:
        return (*whole, [f"{outside[0]} selects the backend suite from outside backend/"])
    if cmap is None or diff is None:
        return (*whole, [map_note or "no coverage map"])
    try:
        files, reasons = coverage_map.select(
            cmap, backend, paths, diff.lines, diff.read_base, diff.read_head)
    except Exception as error:  # noqa: BLE001 — a selection that failed selects everything
        return (*whole, [f"the selection failed ({error.__class__.__name__}: {error})"])
    reasons = [map_note, *reasons] if map_note else reasons
    if files is None:
        return (*whole, reasons)
    count = min(INTEGRATION_SHARDS, math.ceil(len(files) / INTEGRATION_FILES_PER_SHARD))
    return files, [f"{i}/{count}" for i in range(count)], 0, reasons


def failures(needs, expected_tier):
    """Every reason the required check must NOT pass, given the job results.

    ``needs`` is the aggregating job's ``toJSON(needs)``; ``expected_tier`` is
    what the event demands (:func:`tier_for`), recomputed here so a scope output
    that disagrees with the event cannot smuggle a looser tier past the gate.

    A suite the tier runs and the diff selects must be ``success``; every other
    suite must be ``skipped``. On a draft that is every suite: a green product
    suite there would mean a suite ran under the draft's name. The frontend
    suite is also due when the ``api`` job saw the OpenAPI document move
    (:func:`api_failures`). Missing, cancelled, invalid and out-of-band results
    all fall out of the exact-match comparison below.
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
    errors, api_moved = api_failures(needs, outputs, tier)
    for suite in PATHS:
        selected = outputs.get(suite)
        if selected not in ("true", "false"):
            errors.append(f"{suite}: missing or invalid scope output")
            continue
        runs_here = tier == "full" or (tier == "pr" and suite not in POST_MERGE_SUITES)
        should_run = runs_here and (selected == "true" or (suite == "frontend" and api_moved))
        expected = "success" if should_run else "skipped"
        actual = needs.get(suite, {}).get("result", "missing")
        if actual != expected:
            errors.append(f"{suite}: expected {expected}, got {actual}")
    return errors


def api_selected(outputs, tier):
    """Whether the ``api`` job must run: a run past the draft tier whose diff
    reached the backend.

    The job answers two questions about the backend's OpenAPI document, which
    is a function of backend files the path map cannot tell apart from any
    other backend file. Did it move? The frontend compiles against it, so a
    move runs the frontend suite even when no frontend path did. Did it move in
    a way that breaks a page built against the base, without the API
    generation rising (api_compat.py)? That one matters most when the same
    change edits the frontend too, so the job runs whatever else the paths
    selected. The same condition is the job's ``if:`` in required-ci.yml.
    """
    # A main push with no base to compare against (no `main-green` anchor,
    # or one that could not be read) has every path in scope and nothing to
    # print a base document from; the full frontend suite runs on the paths.
    return tier != "draft" and outputs.get("backend") == "true" and bool(outputs.get("base"))


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
    # The commit object itself, not `rev-list --parents`: in the depth-1
    # checkout scope runs on, a shallow commit is shown with no parents.
    body = subprocess.check_output(["git", "cat-file", "-p", head], cwd=cwd, text=True)
    parents = [line.split()[1] for line in body.split("\n\n", 1)[0].splitlines()
               if line.startswith("parent ")]
    return parents[0] if len(parents) >= 2 else event_base


def effective_base(event_base, head="HEAD"):
    """Resolve the one base both the receipt and suite diff must use."""
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "push":
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
        selected = select(changed, tier)
        outputs = {suite: str(sel).lower() for suite, sel in selected.items()}
        outputs["tier"] = tier
        # The commit the ``api`` job prints the base document from: the same
        # base this diff was taken against, so the two answer one question.
        outputs["base"] = event_base
        cmap, diff, map_note = None, None, ""
        if tier == "pr" and selected.get("backend"):
            cmap, map_note = load_coverage_map()
            if cmap is not None:
                diff = Diff(event_base)
        files, shards, floor, why = integration_plan(
            changed, tier, Path(__file__).resolve().parents[2] / "backend",
            cmap=cmap, diff=diff, map_note=map_note)
        outputs["integration_files"] = json.dumps(files if files is not None else "all")
        outputs["integration_shards"] = json.dumps(shards)
        outputs["integration_floor"] = str(floor)
        print(f"Event base: {event_base_arg or '(none)'}")
        print(f"Effective base: {event_base or '(none: every tracked path)'}")
        print(f"Base reason: {reason}")
        print(f"Tier: {tier}")
        print(f"Integration: {len(shards)} shard(s), "
              f"{'every' if files is None else len(files)} test files")
        for line in why:
            print(f"  {line}")
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
        errors = failures(json.loads(os.environ["CI_NEEDS"]), expected_tier)
        if errors:
            run_url = actions_run_url()
            raise SystemExit("\n".join(f"{error} (run: {run_url})" for error in errors))
        print(f"Every selected suite passed (tier: {expected_tier}).")
    else:
        raise SystemExit("Usage: required-ci.py scope [BASE_SHA] | check | pr-base [EVENT_BASE]")


if __name__ == "__main__":
    main()
