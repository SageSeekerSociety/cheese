import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "required_ci", Path(__file__).with_name("required-ci.py")
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "required-ci.yml"


class SelectTest(unittest.TestCase):
    """Which suites a change selects, per tier."""

    def test_documentation_still_runs_guards(self):
        selected = gate.select(["docs/architecture.md"])
        self.assertEqual({k for k, v in selected.items() if v}, {"guards"})

    def test_backend_change_selects_backend_and_e2e(self):
        selected = gate.select(["backend/app/api/rooms.py"])
        self.assertTrue(selected["backend"] and selected["e2e"])
        self.assertFalse(selected["frontend"])

    def test_shared_wire_fixture_checks_both_languages(self):
        selected = gate.select(["backend/tests/fixtures/wire/heartbeat.json"])
        self.assertTrue(selected["backend"] and selected["cli"])

    def test_gate_edit_exercises_all_suites(self):
        self.assertTrue(
            all(gate.select([".github/workflows/required-ci.yml"]).values())
        )

    def test_the_pr_tier_leaves_the_post_merge_suites_to_main(self):
        # A path of each post-merge suite, and the gate itself, which selects
        # everything: on a ready pull request none of them runs, while the
        # suites a pull request does run stay selected. A push to main runs
        # them.
        for path in (
            "backend/app/api/rooms.py",
            "e2e/tests/login.spec.ts",
            "scripts/remote_execution/room_fixture.py",
            "scripts/remote_execution/mcp_contract.py",
            "scripts/remote_execution/equivalence.py",
            ".github/scripts/required-ci.py",
        ):
            with self.subTest(path=path):
                pr = gate.select([path], "pr")
                full = gate.select([path], "full")
                for suite in gate.POST_MERGE_SUITES:
                    self.assertFalse(pr[suite], suite)
                self.assertTrue(any(full[suite] for suite in gate.POST_MERGE_SUITES))
                self.assertTrue(pr["guards"])
                for suite in gate.PATHS:
                    if suite not in gate.POST_MERGE_SUITES:
                        self.assertEqual(pr[suite], full[suite], suite)

    def test_a_draft_selects_no_suite(self):
        for path in (".github/scripts/required-ci.py", "backend/app/api/rooms.py",
                     "docs/architecture.md"):
            with self.subTest(path=path):
                self.assertFalse(any(gate.select([path], "draft").values()))


#: The commit scope names as the diff's base; a main push with no anchor has "".
BASE = "0" * 40


def needs_for(paths, tier):
    """A happy-path ``toJSON(needs)``: every job the workflow runs for ``paths``
    at ``tier`` succeeded and every other one was skipped.

    The scope outputs are what ``scope`` writes for that tier. The ``api`` job,
    where due, saw the OpenAPI document stay put. This is the shape the judge
    must accept, and the shape every negative test perturbs.
    """
    selected = gate.select(list(paths), tier)
    outputs = {k: str(v).lower() for k, v in selected.items()}
    outputs["tier"] = tier
    outputs["base"] = BASE
    api_due = gate.api_selected(outputs, tier)
    return {
        "scope": {"result": "success", "outputs": outputs},
        "api": ({"result": "success", "outputs": {"moved": "false"}} if api_due
                else {"result": "skipped"}),
        **{k: {"result": "success" if v else "skipped"} for k, v in selected.items()},
    }


#: A change that selects suites a pull request runs and post-merge suites both.
WIDE_CHANGE = ("backend/app/api/rooms.py", "e2e/tests/login.spec.ts",
               "scripts/remote_execution/equivalence.py")


class TierForTest(unittest.TestCase):
    """The event decides the tier: draft and ready pull requests, and the rest."""

    def test_a_ready_pull_request_is_pr(self):
        # Whatever the action, the payload's draft flag decides.
        for action in ("opened", "synchronize", "reopened", "ready_for_review"):
            with self.subTest(action=action):
                event = {"action": action, "pull_request": {"draft": False}}
                self.assertEqual(gate.tier_for("pull_request", event), "pr")

    def test_a_draft_pull_request_is_draft(self):
        for action in ("opened", "synchronize", "reopened", "converted_to_draft"):
            with self.subTest(action=action):
                event = {"action": action, "pull_request": {"draft": True}}
                self.assertEqual(gate.tier_for("pull_request", event), "draft")

    def test_non_pull_request_events_are_full(self):
        # A push to main is the post-submit gate deployment reads; no payload
        # field can talk any other event down to a pull request's tier.
        for event_name in ("push", "merge_group", "workflow_dispatch", "schedule", ""):
            for draft in (True, False):
                with self.subTest(event_name=event_name, draft=draft):
                    self.assertEqual(gate.tier_for(
                        event_name, {"pull_request": {"draft": draft}}), "full")

    def test_an_unreadable_event_fails_closed_to_full(self):
        for event_name, event in (
            ("pull_request", {}),  # no flag
            ("pull_request", {"pull_request": None}),
            ("pull_request", {"pull_request": {}}),  # flag absent
            ("pull_request", {"pull_request": {"draft": None}}),  # flag null
            ("pull_request", {"pull_request": {"draft": "false"}}),  # not a bool
            ("pull_request", None),  # payload not a dict
            ("pull_request", "not-a-dict"),
            ("workflow_dispatch", {"ref": "main"}),
            ("schedule", {}),
            ("", {}),
        ):
            with self.subTest(event_name=event_name, event=event):
                self.assertEqual(gate.tier_for(event_name, event), "full")


class AggregationTierTest(unittest.TestCase):
    """The aggregating check accepts a run only at the tier its event demands."""

    def test_a_draft_passes_with_every_suite_skipped(self):
        needs = needs_for(WIDE_CHANGE, "draft")
        self.assertTrue(all(needs[suite]["result"] == "skipped" for suite in gate.PATHS))
        self.assertEqual(gate.failures(needs, "draft"), [])

    def test_a_draft_blocks_if_any_suite_ran(self):
        # A green suite under the draft's name would be evidence the draft tier
        # never produces; the judge refuses it rather than read it as a pass.
        for suite in (*gate.PATHS, "api"):
            with self.subTest(suite=suite):
                needs = needs_for(WIDE_CHANGE, "draft")
                needs[suite] = {"result": "success", "outputs": {"moved": "false"}}
                self.assertTrue(gate.failures(needs, "draft"))

    def test_a_ready_pr_passes_with_its_selected_suites_green(self):
        needs = needs_for(WIDE_CHANGE, "pr")
        self.assertEqual(needs["backend"]["result"], "success")
        for suite in gate.POST_MERGE_SUITES:
            self.assertEqual(needs[suite]["result"], "skipped", suite)
        self.assertEqual(gate.failures(needs, "pr"), [])

    def test_a_ready_pr_blocks_when_a_selected_suite_did_not_pass(self):
        for suite in ("backend", "guards"):
            for result in ("failure", "cancelled", "skipped", "missing"):
                with self.subTest(suite=suite, result=result):
                    needs = needs_for(WIDE_CHANGE, "pr")
                    needs[suite]["result"] = result
                    self.assertTrue(gate.failures(needs, "pr"))

    def test_a_ready_pr_blocks_when_a_post_merge_suite_ran(self):
        # Whatever scope says about it: a post-merge suite never runs on a pull
        # request, so a result from one means the run is not the tier it claims.
        for suite in gate.POST_MERGE_SUITES:
            for selected in ("true", "false"):
                with self.subTest(suite=suite, selected=selected):
                    needs = needs_for(WIDE_CHANGE, "pr")
                    needs["scope"]["outputs"][suite] = selected
                    needs[suite]["result"] = "success"
                    self.assertTrue(gate.failures(needs, "pr"))

    def test_a_full_run_requires_the_post_merge_suites_it_selected(self):
        needs = needs_for(WIDE_CHANGE, "full")
        self.assertEqual(gate.failures(needs, "full"), [])
        for suite in gate.POST_MERGE_SUITES:
            if needs["scope"]["outputs"][suite] != "true":
                continue
            for result in ("failure", "cancelled", "skipped", "missing"):
                with self.subTest(suite=suite, result=result):
                    broken = needs_for(WIDE_CHANGE, "full")
                    broken[suite]["result"] = result
                    self.assertTrue(gate.failures(broken, "full"))

    def test_scope_tier_disagreeing_with_the_event_blocks(self):
        # Scope outputs that claim a looser tier than the event: the suites
        # match the claim, and the judge still refuses it.
        for claimed, event_tier in (("draft", "pr"), ("pr", "full"), ("draft", "full")):
            with self.subTest(claimed=claimed, event=event_tier):
                self.assertTrue(gate.failures(needs_for(WIDE_CHANGE, claimed), event_tier))

    def test_missing_or_invalid_scope_tier_blocks(self):
        for tier in (None, "banana", "", "quick"):
            with self.subTest(tier=tier):
                needs = needs_for(WIDE_CHANGE, "full")
                needs["scope"]["outputs"]["tier"] = tier
                self.assertTrue(gate.failures(needs, "full"))
        needs = needs_for(WIDE_CHANGE, "full")
        del needs["scope"]["outputs"]["tier"]
        self.assertTrue(gate.failures(needs, "full"))

    def test_intentionally_skipped_suites_do_not_block(self):
        self.assertEqual(gate.failures(needs_for(["backend/app/api/rooms.py"], "full"), "full"), [])

    def test_failed_cancelled_skipped_or_missing_selected_suite_blocks(self):
        for result in ("failure", "cancelled", "skipped", "missing"):
            with self.subTest(result=result):
                needs = needs_for(["backend/app/api/rooms.py"], "full")
                needs["backend"]["result"] = result
                self.assertTrue(gate.failures(needs, "full"))
        # A selected suite whose job never reported into needs is missing too.
        needs = needs_for(["backend/app/api/rooms.py"], "full")
        del needs["backend"]
        self.assertTrue(gate.failures(needs, "full"))

    def test_failed_scope_or_missing_output_blocks(self):
        needs = needs_for(["backend/app/api/rooms.py"], "full")
        needs["scope"]["result"] = "failure"
        self.assertTrue(gate.failures(needs, "full"))
        needs = needs_for(["backend/app/api/rooms.py"], "full")
        del needs["scope"]["outputs"]["backend"]
        self.assertTrue(gate.failures(needs, "full"))
        needs = needs_for(["backend/app/api/rooms.py"], "full")
        needs["scope"]["outputs"]["backend"] = "banana"
        self.assertTrue(gate.failures(needs, "full"))


class IntegrationPlanTest(unittest.TestCase):
    """Which backend integration tests a run executes, on how many shards.

    Only a ready pull request narrows the layer, only with a coverage map, and
    only when every path that selected the backend suite is under backend/;
    anything else runs it whole. A whole layer keeps its five shards and its
    case floor; a narrowed one gets the shards its file count needs.
    """

    TREE = {
        "app/__init__.py": "",
        "app/rooms.py": "def open_room():\n    return 1\n\n\ndef close_room():\n    return 2\n",
        "app/billing.py": "def charge():\n    return 1\n",
        "tests/__init__.py": "",
        "tests/integration/__init__.py": "",
        "tests/integration/test_rooms.py": "def test_open():\n    pass\n",
        "tests/integration/test_close.py": "def test_close():\n    pass\n",
        "tests/integration/test_other.py": "z = 1\n",
    }
    #: What the traced run saw: HTTP-only tests, so no import edge says it.
    MAP = {"commit": "a" * 40, "unmapped": [], "files": {
        "app/rooms.py": {"open_room": ["tests/integration/test_rooms.py"],
                         "close_room": ["tests/integration/test_close.py"]},
        "app/billing.py": {"charge": ["tests/integration/test_other.py"]},
    }}
    WHOLE_SHARDS = [f"{i}/5" for i in range(5)]

    class FakeDiff:
        def __init__(self, backend, lines):
            self.backend, self.lines = backend, lines

        def read_base(self, path):
            return self.read_head(path)

        def read_head(self, path):
            file = self.backend.parent / path
            return file.read_text() if file.exists() else None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.backend = Path(tmp.name) / "backend"
        for rel, text in self.TREE.items():
            path = self.backend / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def plan(self, paths, tier="pr", lines=None, cmap=MAP):
        diff = self.FakeDiff(self.backend, lines or {})
        return gate.integration_plan(list(paths), tier, self.backend, cmap=cmap, diff=diff)

    def assert_whole(self, plan):
        files, shards, floor, _reason = plan
        self.assertIsNone(files)
        self.assertEqual(shards, self.WHOLE_SHARDS)
        # Held to the whole layer's floor, never the narrowed one.
        self.assertGreater(floor, 1)

    def test_only_a_ready_pull_request_narrows(self):
        for tier in ("full", "draft"):
            with self.subTest(tier=tier):
                self.assert_whole(self.plan(["backend/app/rooms.py"], tier))

    def test_without_a_coverage_map_the_layer_runs_whole(self):
        self.assert_whole(self.plan(["backend/app/rooms.py"], cmap=None))

    def test_a_ready_pr_runs_the_tests_that_executed_the_changed_function(self):
        # Line 2 is close_room's body: the test that called it over HTTP runs,
        # the one that only called open_room does not.
        files, shards, floor, _reason = self.plan(
            ["backend/app/rooms.py"], lines={"backend/app/rooms.py": ({5}, {5})})
        self.assertEqual(files, ["tests/integration/test_close.py"])
        self.assertEqual(shards, ["0/1"])
        self.assertEqual(floor, 0)

    def test_a_change_to_the_gate_or_the_selection_runs_the_whole_layer(self):
        # A change to what decides the selection cannot be judged by it.
        for path in (".github/workflows/required-ci.yml", ".github/scripts/required-ci.py",
                     ".github/scripts/test_required_ci.py", ".github/scripts/backend_imports.py",
                     ".github/scripts/coverage_map.py", "backend/scripts/ci_shard.py"):
            with self.subTest(path=path):
                self.assert_whole(self.plan(["backend/app/billing.py", path]))

    def test_a_backend_selecting_path_outside_backend_runs_the_whole_layer(self):
        for path in (".github/workflows/test.yml", ".pre-commit-config.yaml",
                     "frontend/src/i18n/messages/zh-CN/global.json",
                     "deploy/metering-proxy/cheese_billing_core.py"):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path], "pr")["backend"])
                self.assert_whole(self.plan(["backend/app/billing.py", path]))

    def test_a_change_every_test_shares_runs_the_whole_layer(self):
        for path in ("backend/uv.lock", "backend/tests/conftest.py"):
            with self.subTest(path=path):
                self.assert_whole(self.plan([path]))

    def test_a_selection_that_fails_runs_the_whole_layer(self):
        with mock.patch.object(gate.coverage_map, "select", side_effect=KeyError("files")):
            self.assert_whole(self.plan(["backend/app/rooms.py"]))

    def test_a_narrowed_selection_takes_the_shards_its_size_needs(self):
        # 144 files a shard (the layer's 718 over its five), never more than
        # the whole layer's five.
        for count, shards in ((1, 1), (144, 1), (145, 2), (288, 2), (289, 3),
                              (577, 5), (5000, 5)):
            selected = [f"tests/integration/test_{i}.py" for i in range(count)]
            with self.subTest(count=count), mock.patch.object(
                    gate.coverage_map, "select", return_value=(selected, ["stubbed"])):
                files, got, floor, _reason = self.plan(["backend/app/rooms.py"])
                self.assertEqual(files, selected)
                self.assertEqual(got, [f"{i}/{shards}" for i in range(shards)])
                self.assertEqual(floor, 0)


class ApiSurfaceTest(unittest.TestCase):
    """A backend change that moves the OpenAPI document runs the frontend suite.

    The frontend compiles against types generated from that document, so a
    change that moves it and lands without the frontend suite has shipped an
    API surface nobody compiled the frontend against. No path says when the
    document moved; the ``api`` job says it, and the required check must hold
    the frontend suite to that answer.
    """

    def needs(self, paths=("backend/app/api/rooms.py",), api=None, frontend=None):
        selected = gate.select(list(paths))
        outputs = {k: str(v).lower() for k, v in selected.items()}
        outputs["tier"] = "full"
        outputs["base"] = BASE
        needs = {
            "scope": {"result": "success", "outputs": outputs},
            **{k: {"result": "success" if v else "skipped"} for k, v in selected.items()},
        }
        if api is not None:
            needs["api"] = api
        if frontend is not None:
            needs["frontend"] = {"result": frontend}
        return needs

    def test_a_moved_surface_without_the_frontend_suite_is_red(self):
        needs = self.needs(api={"result": "success", "outputs": {"moved": "true"}},
                           frontend="skipped")
        self.assertTrue(gate.failures(needs, "full"))

    def test_an_unmoved_surface_leaves_the_frontend_suite_out(self):
        needs = self.needs(api={"result": "success", "outputs": {"moved": "false"}},
                           frontend="skipped")
        self.assertEqual(gate.failures(needs, "full"), [])

    def test_a_backend_change_with_no_answer_from_api_is_red(self):
        # A job that did not report whether the surface moved has not excused
        # the frontend suite it would have started.
        for api in ({"result": "failure"}, {"result": "cancelled"}, {"result": "skipped"},
                    None, {"result": "success"},
                    {"result": "success", "outputs": {"moved": ""}},
                    {"result": "success", "outputs": {"moved": "maybe"}}):
            with self.subTest(api=api):
                self.assertTrue(gate.failures(self.needs(api=api, frontend="skipped"), "full"))

    def test_a_change_to_both_halves_still_asks_api(self):
        # Whether the change breaks pages built against the base is asked of
        # every backend change, the frontend suite selected by paths or not.
        paths = ("backend/app/api/rooms.py", "frontend/src/main.ts")
        self.assertTrue(gate.failures(self.needs(paths, api={"result": "skipped"}), "full"))
        needs = self.needs(paths, api={"result": "success", "outputs": {"moved": "true"}})
        self.assertEqual(gate.failures(needs, "full"), [])

    def test_a_main_push_with_no_base_does_not_ask_api(self):
        # Nothing to print a base document from: every path is in scope, so
        # the frontend suite runs on the paths, and api stays out.
        needs = needs_for(["backend/app/api/rooms.py", "frontend/src/main.ts"], "full")
        needs["scope"]["outputs"]["base"] = ""
        needs["api"] = {"result": "skipped"}
        self.assertEqual(gate.failures(needs, "full"), [])
        needs["api"] = {"result": "failure"}
        self.assertTrue(gate.failures(needs, "full"))

    def test_a_draft_never_asks_api(self):
        needs = needs_for(["backend/app/api/rooms.py"], "draft")
        self.assertEqual(gate.failures(needs, "draft"), [])
        needs["api"] = {"result": "success", "outputs": {"moved": "true"}}
        self.assertTrue(gate.failures(needs, "draft"))

    def test_a_ready_pr_holds_the_frontend_to_the_api_answer(self):
        # The frontend suite is not post-merge: a ready pull request whose
        # backend change moved the document runs it, as a main push does.
        needs = needs_for(["backend/app/api/rooms.py"], "pr")
        self.assertEqual(gate.failures(needs, "pr"), [])
        needs["api"] = {"result": "success", "outputs": {"moved": "true"}}
        self.assertTrue(gate.failures(needs, "pr"))
        needs["frontend"] = {"result": "success"}
        self.assertEqual(gate.failures(needs, "pr"), [])
        for api in ({"result": "skipped"}, {"result": "failure"}, {"result": "success"}):
            with self.subTest(api=api):
                needs = needs_for(["backend/app/api/rooms.py"], "pr")
                needs["api"] = api
                self.assertTrue(gate.failures(needs, "pr"))

    @staticmethod
    def _holds(expression, outputs, moved, needs_succeeded):
        """Whether an ``if:`` over scope outputs and ``needs.api.outputs.moved``
        holds, given whether every job it needs succeeded.

        A condition with no status function gets GitHub's implicit
        ``success()``: a skipped or failed need skips the job whatever the rest
        says. Only the operators these two conditions use are read; anything
        else raises, so a rewritten condition fails here instead of passing."""
        expr = expression.strip()
        match = re.fullmatch(r"\$\{\{(.*)\}\}", expr, re.S)
        expr = (match.group(1) if match else expr).strip()
        if not re.search(r"\b(?:success|failure|always|cancelled)\(\)", expr):
            expr = f"{needs_succeeded!r} && ({expr})"
        expr = re.sub(r"needs\.scope\.outputs\.(\w+)",
                      lambda m: repr(outputs.get(m.group(1), "")), expr)
        expr = expr.replace("needs.api.outputs.moved", repr(moved))
        expr = expr.replace("!cancelled()", "True")
        expr = expr.replace("&&", " and ").replace("||", " or ")
        if re.search(r"[&|]|![^=]|needs\.", expr):
            raise AssertionError(f"unsupported condition: {expression!r} -> {expr}")
        return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307

    def test_the_workflow_conditions_produce_what_the_judge_requires(self):
        # Run every combination through the suite jobs' `if:`s exactly as
        # written in required-ci.yml, give each job the result GitHub would,
        # and ask the judge. A condition that drifted from the judge — a suite
        # started on a draft, a post-merge suite started on a pull request, api
        # asked when the paths already chose the frontend, or the frontend left
        # out when api saw the surface move — leaves a combination it refuses.
        jobs = jobs_of(WORKFLOW.read_text())
        for tier in gate.TIERS:
            for backend in ("true", "false"):
                for frontend in ("true", "false"):
                    for moved in ("true", "false"):
                        for others in ("true", "false"):
                            for base in (BASE, ""):
                                with self.subTest(tier=tier, backend=backend, frontend=frontend,
                                                  moved=moved, others=others, base=base):
                                    self._judge_agrees(jobs, tier, backend, frontend, moved, others, base)

    def _judge_agrees(self, jobs, tier, backend, frontend, moved, others, base=BASE):
        # Scope's outputs as written; `others` is every remaining suite,
        # post-merge ones included, so a pull request whose scope says e2e is
        # selected still has to leave e2e out.
        outputs = {suite: others for suite in gate.PATHS}
        outputs.update(tier=tier, backend=backend, frontend=frontend, base=base)
        api_runs = self._holds(jobs["api"]["if"], outputs, "", True)
        self.assertEqual(api_runs, gate.api_selected(outputs, tier))
        # A skipped job's outputs are empty strings.
        api_moved = moved if api_runs else ""
        needs = {
            "scope": {"result": "success", "outputs": outputs},
            "api": ({"result": "success", "outputs": {"moved": moved}}
                    if api_runs else {"result": "skipped"}),
        }
        for suite in gate.PATHS:
            # frontend needs scope (succeeded) and api, which succeeded only
            # if it ran; every other suite needs only scope.
            if suite == "frontend":
                runs = self._holds(jobs[suite]["if"], outputs, api_moved, api_runs)
            else:
                runs = self._holds(jobs[suite]["if"], outputs, api_moved, True)
            if tier == "draft" or (tier == "pr" and suite in gate.POST_MERGE_SUITES):
                self.assertFalse(runs, f"{suite} starts on a {tier} run")
            needs[suite] = {"result": "success" if runs else "skipped"}
        self.assertEqual(gate.failures(needs, tier), [])


def run_bodies(text):
    """The body of every ``run:`` in a workflow, block or inline."""
    lines, i = text.splitlines(), 0
    while i < len(lines):
        match = re.match(r"^(\s*)run:(.*)$", lines[i])
        if not match:
            i += 1
            continue
        indent, inline, body = match.group(1), match.group(2).strip(), []
        if inline and inline != "|" and inline != ">":
            body.append(inline)
        i += 1
        while i < len(lines) and (not lines[i].strip()
                                  or len(lines[i]) - len(lines[i].lstrip()) > len(indent)):
            body.append(lines[i])
            i += 1
        yield "\n".join(body)


def jobs_of(text):
    """Every job of a workflow: ``{job key: {"name": ..., "if": ..., "text": ...}}``.

    Just enough of the format for the job-level keys these guards read — a job is
    a key indented two spaces under ``jobs:``, and its own keys are indented four,
    with a folded (``>-``) value joined back onto one line. No third-party YAML
    parser is imported: this suite runs on a bare ``python3``, the same way the
    scripts it covers do. An unsupported shape shows up as a missing key in the
    assertions below, not as a silent pass.
    """
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.rstrip() == "jobs:"), None
    )
    if start is None:
        raise AssertionError("workflow has no jobs: mapping")
    jobs, current, i = {}, None, start + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() and not line.startswith(" "):
            break
        job = re.match(r"^  (\S+):\s*$", line)
        if job:
            current = jobs[job.group(1)] = {"text": []}
        elif current is not None:
            current["text"].append(line)
            key = re.match(r"^    ([\w-]+):[ \t]*(.*)$", line)
            if key:
                value = key.group(2)
                if value in (">", "|", ">-", "|-", ">+", "|+"):
                    block, j = [], i + 1
                    while j < len(lines) and (not lines[j].strip()
                                              or lines[j].startswith("      ")):
                        block.append(lines[j].strip())
                        j += 1
                    value, i = " ".join(part for part in block if part), j - 1
                current[key.group(1)] = value
        i += 1
    for job in jobs.values():
        job["text"] = "\n".join(job["text"])
    return jobs


def needs_of(job):
    """The job keys a ``jobs_of`` job lists under ``needs:``."""
    value = job.get("needs", "").strip()
    if value.startswith("[") and value.endswith("]"):
        return [part.strip() for part in value[1:-1].split(",") if part.strip()]
    return [value] if value else []


def condition_holds(expression, event_name, draft, cancelled=False):
    """Whether a workflow ``if:`` holds on a plain event.

    So the tier guards are checked by which event each job answers, not by how
    the condition is spelled. Only the subset this workflow uses is supported;
    anything else raises, so a guard rewritten into a shape this cannot read
    fails loudly instead of quietly falling out of the assertions.
    """
    match = re.fullmatch(r"\$\{\{(.*)\}\}", expression.strip(), re.S)
    if not match:
        raise AssertionError(f"not a workflow expression: {expression!r}")
    py = match.group(1).strip()
    py = py.replace("github.event.pull_request.draft", repr(draft))
    py = py.replace("github.event_name", repr(event_name))
    py = py.replace("!cancelled()", "not cancelled")
    py = py.replace("&&", " and ").replace("||", " or ").replace("!(", "not (")
    if re.search(r"[!&|]", py):
        raise AssertionError(f"unsupported operator in {expression!r}: {py}")
    return bool(eval(py, {"__builtins__": {}}, {"cancelled": cancelled}))  # noqa: S307


def run_body_containing(text, needle):
    """The single ``run:`` body whose shell contains ``needle``.

    Steps are located by a token their command must contain, then their real
    shell is executed by the behavioural tests below — nothing is asserted about
    how the line is spelled, only about what it does when run.
    """
    bodies = [body for body in run_bodies(text) if needle in body]
    if len(bodies) != 1:
        raise AssertionError(f"expected exactly one run body with {needle!r}, got {len(bodies)}")
    return bodies[0]


class WorkflowTest(unittest.TestCase):
    """Workflow facts that are behaviour: which job an event starts, and what it waits on."""

    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_the_two_aggregators_are_two_jobs_with_literal_names(self):
        # Each aggregates in its own job, and each job's `name` is written out
        # literally. A computed `name:` is only rendered once GitHub evaluates
        # it, so a run nobody evaluated it for shows the expression's own text
        # as the check's name — a draft run cancelled by the next push left
        # `github.event_name == 'pull_request' && ...` on the PR page, where a
        # cancelled check with a name like that reads as a red.
        jobs = jobs_of(self.text)
        aggregators = {
            job["name"]: job
            for job in jobs.values()
            if job.get("name") and "required-ci.py check" in job["text"]
        }
        # Both names, and only these: a run under the draft name or a third
        # aggregation job would change which check branch protection reads.
        self.assertEqual(
            sorted(aggregators), ["CI required", "CI required (draft quick)"]
        )
        for name, job in aggregators.items():
            # One judge, every tier: the job hands its `needs` over and
            # required-ci.py recomputes the tier. A job left out of `needs` is
            # `missing` to the judge, never `skipped`, so both aggregators
            # need every job the judge reads — and a draft, which runs none of
            # them, is judged on all of them having skipped.
            self.assertEqual(set(needs_of(job)), {"scope", "api", *gate.PATHS}, name)
        # Which job runs is the event's own draft flag, never a scope output:
        # a failed `scope` leaves its `tier` empty, and the draft run must
        # still not fall through to `CI required`. condition_holds raises on a
        # condition that reads anything else.
        full, draft = aggregators["CI required"], aggregators["CI required (draft quick)"]
        for event_name, is_draft, running in (
            ("pull_request", True, "draft"),
            ("pull_request", False, "full"),
            ("push", None, "full"),
        ):
            self.assertEqual(
                {label for label, job in (("full", full), ("draft", draft))
                 if condition_holds(job["if"], event_name, is_draft)},
                {running},
                f"wrong aggregator on {event_name} draft={is_draft}",
            )
        # A cancelled run starts neither: the aggregator reports what the suites
        # did, and a cancelled one has nothing to report.
        self.assertFalse(condition_holds(draft["if"], "pull_request", True, True))
        self.assertFalse(condition_holds(full["if"], "push", None, True))

    def test_the_workflow_runs_on_pull_requests_and_main_pushes_only(self):
        # The triggers are the tiers' inputs: a push to main (full) and a pull
        # request to main, including the moment it is marked ready (pr) or
        # turned back into a draft. No merge queue: nothing produces a run there.
        on = self.text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertRegex(on, r"(?m)^  push:\n    branches: \[main\]$")
        self.assertRegex(on, r"(?m)^  pull_request:\n    branches: \[main\]$")
        types = re.search(r"(?m)^    types: \[(.*)\]$", on).group(1)
        self.assertLessEqual({"opened", "synchronize", "reopened", "ready_for_review",
                              "converted_to_draft"}, {t.strip() for t in types.split(",")})
        self.assertNotIn("merge_group", on)

    def test_the_integration_layer_runs_unless_the_plan_is_empty(self):
        # test.yml's integration job, given what scope writes for each plan:
        # json.dumps of the file list, or of "all" for the whole layer.
        condition = jobs_of((WORKFLOW.parent / "test.yml").read_text())["integration"]["if"]
        expr = re.fullmatch(r"\$\{\{(.*)\}\}", condition.strip()).group(1)
        for files, due in (([], False), ("all", True), (["tests/integration/test_a.py"], True)):
            with self.subTest(files=files):
                value = json.dumps(files)
                py = expr.replace("inputs.integration-files", repr(value))
                self.assertNotIn("inputs.", py, condition)
                self.assertEqual(bool(eval(py, {"__builtins__": {}}, {})), due)  # noqa: S307


class TitleRecheckTest(unittest.TestCase):
    """Run the real recheck shell; only GitHub responses are substituted."""

    def recheck(self, *, completed=False, required="failure", required_name="CI required",
                status="completed", attempt=1, same_title=False, newer=False,
                pull_changes=None, source_changes=None, renames=None,
                job_started="2026-10-18T10:00:00Z", latest_attempt=None):
        head = "a" * 40
        pull = {"number": 7, "state": "open", "draft": False,
                "title": 'fix: corrected title $(touch pwned)',
                "head": {"sha": head, "repo": {"full_name": "owner/repo"}},
                "base": {"ref": "main"}}
        event_pull = json.loads(json.dumps(pull))
        pull.update(pull_changes or {})
        source = {"id": 99, "head_sha": head, "event": "pull_request",
                  "conclusion": "failure", "run_attempt": attempt,
                  "head_repository": {"full_name": "owner/repo"},
                  "display_title": pull["title"] if same_title else "bad title"}
        source.update(source_changes or {})
        event = {"pull_request": event_pull, "workflow_run": source}
        if renames is None:
            renames = [{"event": "renamed", "created_at": "2026-10-18T10:01:00Z",
                        "rename": {"from": "bad title", "to": pull["title"]}}]
        data = {"pull": pull, "events": renames,
                "runs": [{"databaseId": 100 if newer else 99, "status": status,
                          "attempt": attempt if latest_attempt is None else latest_attempt}],
                # A failed suite beside the aggregator: a title edit re-runs the
                # title's check, never a suite.
                "jobs": [{"name": "backend / static", "databaseId": 101, "id": 101,
                          "conclusion": "failure", "started_at": job_started},
                         {"name": required_name, "databaseId": 202, "id": 202,
                          "conclusion": required, "started_at": job_started}]}
        text = WORKFLOW.with_name("pr-title.yml").read_text()
        body = textwrap.dedent(run_body_containing(text, "gh"))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            stub = bindir / "gh"
            stub.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
                import json, os, sys
                from pathlib import Path
                args = sys.argv[1:]
                data = json.loads(Path(os.environ["STUB_DATA"]).read_text())
                with open(os.environ["STUB_CALLS"], "a") as log:
                    print(json.dumps(args), file=log)
                if args[:2] == ["run", "list"]:
                    answer = data["runs"]
                elif args[0] == "api":
                    if "/jobs" in args[1]:
                        assert "--paginate" in args and "--slurp" in args, args
                        attempt = data["runs"][0]["attempt"]
                        assert f"/attempts/{attempt}/jobs" in args[1], args
                        answer = [{"jobs": data["jobs"][:1]}, {"jobs": data["jobs"][1:]}]
                    elif "/events" in args[1]:
                        assert "--paginate" in args and "--slurp" in args, args
                        answer = [data["events"][:1], data["events"][1:]]
                    else:
                        answer = [data["pull"]] if "/commits/" in args[1] else data["pull"]
                elif args[:2] == ["run", "rerun"]:
                    raise SystemExit(0)
                else:
                    raise AssertionError(args)
                print(json.dumps(answer))
                '''))
            stub.chmod(0o755)
            (tmp / "event.json").write_text(json.dumps(event))
            (tmp / "data.json").write_text(json.dumps(data))
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "GITHUB_REPOSITORY": "owner/repo", "HEAD_SHA": head,
                   "GITHUB_EVENT_NAME": "workflow_run" if completed else "pull_request",
                   "GITHUB_EVENT_PATH": str(tmp / "event.json"),
                   "STUB_DATA": str(tmp / "data.json"), "STUB_CALLS": str(tmp / "calls")}
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", body],
                                    cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((tmp / "pwned").exists())
            calls = [json.loads(line) for line in (tmp / "calls").read_text().splitlines()] if (tmp / "calls").exists() else []
            return [call[call.index("--job") + 1] for call in calls if call[:2] == ["run", "rerun"]]

    def test_recheck_job_accepts_title_edits_and_failed_pr_completions(self):
        condition = jobs_of(WORKFLOW.with_name("pr-title.yml").read_text())["recheck"]["if"]
        for event_name, draft, fork, action, title_changed, conclusion, attempt, expected in (
            ("pull_request", False, False, "edited", True, "", 1, True),
            ("pull_request", False, False, "edited", False, "", 1, False),
            ("pull_request", True, False, "edited", True, "", 1, False),
            ("pull_request", False, True, "edited", True, "", 1, False),
            ("pull_request", False, False, "ready_for_review", False, "", 1, True),
            ("workflow_run", False, False, "completed", False, "failure", 1, True),
            ("workflow_run", False, False, "completed", False, "failure", 2, True),
            ("workflow_run", False, False, "completed", False, "success", 1, False),
            ("workflow_run", False, True, "completed", False, "failure", 1, False),
        ):
            repo = "fork/repo" if fork else "owner/repo"
            context = {"event_name": event_name, "repository": "owner/repo", "event": {
                "action": action, "changes": {"title": title_changed},
                "pull_request": {"draft": draft, "head": {"repo": {"full_name": repo}}},
                "workflow_run": {"event": "pull_request", "conclusion": conclusion,
                                 "run_attempt": attempt, "head_repository": {"full_name": repo}}}}

            def value(match):
                found = context
                for key in match.group().split(".")[1:]:
                    found = found[key]
                return repr(found)

            expression = re.sub(r"github(?:\.\w+)+", value, condition)
            expression = expression.replace("&&", " and ").replace("||", " or ").replace("!", "not ")
            with self.subTest(context=context):
                self.assertEqual(bool(eval(expression, {"__builtins__": {}}, {})), expected)  # noqa: S307

    def test_a_skipped_completion_cannot_cancel_a_running_recheck(self):
        # Required CI completes for every conclusion, so the draft run that
        # ready_for_review supersedes starts this workflow too, with a
        # `cancelled` completion. Its job is skipped; held as a workflow-level
        # group, the run still cancelled the title-edit recheck for the same
        # head, and that CANCELLED `recheck` check stayed on the head as its
        # last word — a red rollup after every later attempt passed (#4573).
        # Job-level concurrency is entered only by a job that will run.
        text = WORKFLOW.with_name("pr-title.yml").read_text()
        self.assertNotRegex(text, r"(?m)^concurrency:")
        recheck = jobs_of(text)["recheck"]
        self.assertEqual(recheck["concurrency"], "")
        self.assertIn("group: pr-title-", recheck["text"])

    def test_title_edit_reruns_only_ci_required(self):
        self.assertEqual(self.recheck(), ["202"])

    def test_a_draft_runs_aggregator_is_never_rerun(self):
        # A draft's aggregator judged no suite and checks no title.
        for completed in (False, True):
            with self.subTest(completed=completed):
                self.assertEqual(self.recheck(completed=completed,
                                              required_name="CI required (draft quick)"), [])

    def test_title_edit_during_any_attempt_is_recovered_once_per_new_edit(self):
        self.assertEqual(self.recheck(status="in_progress"), [])
        for attempt in (1, 2, 7):
            with self.subTest(attempt=attempt):
                self.assertEqual(self.recheck(completed=True, attempt=attempt), ["202"])
                self.assertEqual(self.recheck(completed=True, attempt=attempt + 1,
                                             job_started="2026-10-18T10:02:00Z"), [])
        self.assertEqual(self.recheck(completed=True, newer=True), [])
        self.assertEqual(self.recheck(completed=True, latest_attempt=2), [])

    def test_restoring_the_original_title_after_the_job_started_is_a_new_edit(self):
        title = 'fix: corrected title $(touch pwned)'
        events = [{"event": "renamed", "created_at": "2026-10-18T10:00:01Z",
                   "rename": {"from": title, "to": "invalid intermediate title"}},
                  {"event": "renamed", "created_at": "2026-10-18T10:01:00Z",
                   "rename": {"from": "invalid intermediate title", "to": title}}]
        self.assertEqual(self.recheck(completed=True, same_title=True, renames=events), ["202"])
        self.assertEqual(self.recheck(completed=True, same_title=True, renames=events,
                                     attempt=2, job_started="2026-10-18T10:02:00Z"), [])

    def test_completion_requires_a_real_rename_to_the_current_title(self):
        self.assertEqual(self.recheck(completed=True, renames=[]), [])
        for change in ({"event": "commented"}, {"rename": {"to": "another title"}},
                       {"created_at": "2026-10-18T09:59:59Z"}):
            event = {"event": "renamed", "created_at": "2026-10-18T10:01:00Z",
                     "rename": {"to": 'fix: corrected title $(touch pwned)'}}
            event.update(change)
            with self.subTest(change=change):
                self.assertEqual(self.recheck(completed=True, renames=[event]), [])
        # GitHub timestamps have second resolution; preserve an edit in the
        # job's start second rather than silently treating it as older.
        self.assertEqual(self.recheck(completed=True, job_started="2026-10-18T10:01:00Z"), ["202"])

    def test_completion_cannot_restart_main_or_other_repositories(self):
        for changes in ({"event": "push"}, {"conclusion": "success"},
                        {"head_repository": {"full_name": "fork/repo"}}):
            with self.subTest(changes=changes):
                self.assertEqual(self.recheck(completed=True, source_changes=changes), [])

    def test_only_the_current_ready_same_repository_pr_can_be_rechecked(self):
        for completed in (False, True):
            for changes in ({"state": "closed"}, {"draft": True}, {"base": {"ref": "release"}},
                            {"head": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}}},
                            {"head": {"sha": "a" * 40, "repo": {"full_name": "fork/repo"}}}):
                with self.subTest(completed=completed, changes=changes):
                    self.assertEqual(self.recheck(completed=completed, pull_changes=changes), [])
        for required in ("skipped", "cancelled"):
            with self.subTest(required=required):
                self.assertEqual(self.recheck(required=required), [])


class MainCliTest(unittest.TestCase):
    """The ``scope``/``check`` wiring that the runner actually invokes."""

    def _event_file(self, tmp, payload):
        path = Path(tmp) / "event.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def _pr_repo(self, tmp, files, base_files=None):
        """A throwaway repo with the gate's scripts where a checkout has them,
        a base commit holding ``base_files`` and one commit writing ``files``
        (each path -> text).

        Required CI runs scope on a depth=1 checkout, so the test builds its own
        two-commit history instead of relying on the checkout's depth. The gate
        reads the backend's import graph from the ``backend/`` beside its own
        ``.github/scripts``, so the fixture tree is the graph it judges — small,
        because this suite runs in the scope job ahead of every suite.
        Returns ``(script, base)``.
        """
        scripts = WORKFLOW.parent.parent / "scripts"
        target = tmp / ".github" / "scripts"
        target.mkdir(parents=True)
        for name in ("required-ci.py", "required-ci-paths.json", "backend_imports.py",
                         "coverage_map.py"):
            shutil.copy(scripts / name, target / name)

        def git(*args):
            subprocess.run(
                ["git", "-c", "user.email=test@example.com", "-c", "user.name=test",
                 "-c", "commit.gpgsign=false", *args],
                cwd=tmp, check=True, capture_output=True, text=True)

        def write(tree):
            for rel, text in tree.items():
                path = tmp / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                git("add", rel)

        git("init", "-q", "-b", "main")
        write({"base.txt": "base\n", **(base_files or {})})
        git("commit", "-qm", "base")
        base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
        write(files)
        git("commit", "-qm", "the change")
        return target / "required-ci.py", base

    def _pr_scope(self, tmp, script, base, draft):
        out = tmp / "github_output"
        out.write_text("")
        env = {**{k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"},
               "GITHUB_EVENT_NAME": "pull_request",
               "GITHUB_EVENT_PATH": self._event_file(tmp, {"pull_request": {"draft": draft}}),
               "GITHUB_OUTPUT": str(out)}
        proc = subprocess.run([sys.executable, str(script), "scope", base],
                              env=env, cwd=tmp, check=True, capture_output=True, text=True)
        return proc, dict(line.split("=", 1) for line in out.read_text().splitlines()), env

    #: A backend module, an integration test that imports it, and one that
    #: does not.
    BACKEND = {
        "backend/app/__init__.py": "",
        "backend/app/rooms.py": "x = 1\n",
        "backend/tests/__init__.py": "",
        "backend/tests/integration/__init__.py": "",
        "backend/tests/integration/test_rooms.py": "from app import rooms\n",
        "backend/tests/integration/test_other.py": "y = 2\n",
    }
    #: The change under test: an edit to that module.
    CHANGE = {"backend/app/rooms.py": "x = 2\n"}

    def test_a_draft_scope_selects_no_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            script, base = self._pr_repo(tmp, self.CHANGE, self.BACKEND)
            _proc, written, _env = self._pr_scope(tmp, script, base, draft=True)
        self.assertEqual(written["tier"], "draft")
        self.assertEqual({suite: written[suite] for suite in gate.PATHS},
                         {suite: "false" for suite in gate.PATHS})

    def test_a_ready_pr_scope_runs_what_it_selects(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            changed = {**self.CHANGE, "e2e/tests/login.spec.ts": "x\n"}
            script, base = self._pr_repo(tmp, changed, self.BACKEND)
            proc, written, env = self._pr_scope(tmp, script, base, draft=False)

            self.assertEqual(written["tier"], "pr")
            self.assertEqual(written["backend"], "true")
            self.assertEqual(written["guards"], "true")
            for suite in gate.POST_MERGE_SUITES:
                self.assertEqual(written[suite], "false", suite)
            # The api job compares the document against the commit this diff
            # was taken from, so the two cannot be answering different questions.
            self.assertEqual(written["base"], base)
            # No coverage map can be read here (no token), so the layer runs
            # whole rather than narrowing on a guess.
            self.assertEqual(json.loads(written["integration_files"]), "all")
            self.assertEqual(json.loads(written["integration_shards"]),
                             [f"{i}/5" for i in range(5)])
            self.assertEqual(written["integration_floor"], "2400")

            # The judge accepts the run those outputs describe.
            needs = {"scope": {"result": "success", "outputs": written},
                     "api": {"result": "success", "outputs": {"moved": "false"}},
                     **{k: {"result": "success" if written[k] == "true" else "skipped"}
                        for k in gate.PATHS}}
            check = subprocess.run([sys.executable, str(script), "check"],
                                   env={**env, "CI_NEEDS": json.dumps(needs)},
                                   cwd=tmp, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def _push_repo(self, tmp):
        """A throwaway repo with ``origin`` pointing at itself, so the push
        pipeline can fetch its own refs the way the workflow does."""

        def git(*args):
            subprocess.run(
                ["git", "-c", "user.email=test@example.com", "-c", "user.name=test",
                 "-c", "commit.gpgsign=false", *args],
                cwd=tmp, check=True, capture_output=True, text=True)

        git("init", "-q", "-b", "main")
        git("remote", "add", "origin", str(tmp))
        (tmp / "base.txt").write_text("base\n")
        git("add", "base.txt")
        git("commit", "-qm", "base")
        return git

    def _push_scope(self, tmp, event_path):
        scripts = WORKFLOW.parent.parent / "scripts"
        out = tmp / "github_output"
        proc = subprocess.run(
            [sys.executable, str(scripts / "required-ci.py"), "scope", ""],
            env={**os.environ, "GITHUB_EVENT_NAME": "push",
                 "GITHUB_EVENT_PATH": event_path, "GITHUB_OUTPUT": str(out)},
            cwd=tmp, check=True, capture_output=True, text=True,
        )
        return proc, dict(line.split("=", 1) for line in out.read_text().splitlines())

    def test_a_main_push_diffs_from_the_green_anchor(self):
        # The batch base is the commit the gate last passed on: the previous tip
        # would drop the commits of a run that a newer push superseded.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            git = self._push_repo(tmp)
            git("update-ref", "refs/heads/main-green", "HEAD")
            anchor = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
            changed = tmp / "backend/app/api/rooms.py"
            changed.parent.mkdir(parents=True)
            changed.write_text("x = 1\n")
            git("add", "backend/app/api/rooms.py")
            git("commit", "-qm", "change a backend file")
            # A second commit, so the anchor is not the previous tip: with one
            # commit between them, an implementation that diffs from HEAD~1 (or
            # from github.event.before) returns the anchor's sha by accident and
            # drops exactly the commits a superseded run left behind.
            (tmp / "frontend/src/main.ts").parent.mkdir(parents=True)
            (tmp / "frontend/src/main.ts").write_text("y\n")
            git("add", "frontend/src/main.ts")
            git("commit", "-qm", "change a frontend file")

            proc, written = self._push_scope(tmp, self._event_file(tmp, {}))
            previous_tip = subprocess.check_output(
                ["git", "rev-parse", "HEAD~1"], cwd=tmp, text=True).strip()
            self.assertEqual(written["base"], anchor)
            self.assertNotEqual(written["base"], previous_tip,
                                "the push used the previous tip, not the green anchor")
            self.assertEqual(written["tier"], "full")
            self.assertEqual(written["backend"], "true")
            self.assertEqual(written["frontend"], "true")
            self.assertIn("last green gate result", proc.stdout)

    def test_an_anchor_off_the_pushed_history_widens_the_scope(self):
        # The ref is only ever written from a main push run, so this takes a
        # rewritten ref. A diff against a commit that is not on this history is
        # not a diff of what landed, and can miss a change whose content happens
        # to match — so the run must widen instead of trusting it.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            git = self._push_repo(tmp)
            git("checkout", "-qb", "side")
            (tmp / "side.txt").write_text("x\n")
            git("add", "side.txt")
            git("commit", "-qm", "a commit that never landed on main")
            side = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
            git("checkout", "-q", "main")
            git("update-ref", "refs/heads/main-green", side)
            changed = tmp / "backend/app/api/rooms.py"
            changed.parent.mkdir(parents=True)
            changed.write_text("x = 1\n")
            git("add", "backend/app/api/rooms.py")
            git("commit", "-qm", "change a backend file")

            proc, written = self._push_scope(tmp, self._event_file(tmp, {}))
            self.assertEqual(written["base"], "")
            self.assertIn("is not an ancestor", proc.stdout)
            self.assertEqual(written["backend"], "true")
            self.assertEqual(written["e2e"], "true")

    def test_a_main_push_without_an_anchor_scopes_every_tracked_path(self):
        # The first push after the gate moved to main has no anchor to diff
        # from; a narrower guess would let a commit no run covered go untested.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            git = self._push_repo(tmp)
            changed = tmp / "e2e/tests/login.spec.ts"
            changed.parent.mkdir(parents=True)
            changed.write_text("x\n")
            git("add", "e2e/tests/login.spec.ts")
            git("commit", "-qm", "touch e2e")

            proc, written = self._push_scope(tmp, self._event_file(tmp, {}))
            self.assertEqual(written["base"], "")
            # Read off the tree, not off a diff: the e2e path is in scope even
            # though nothing says it changed, and paths outside the tree are not.
            self.assertEqual(written["e2e"], "true")
            self.assertEqual(written["frontend"], "false")
            self.assertIn("no usable refs/heads/main-green anchor", proc.stdout)

    def test_check_mode_fails_closed_when_the_event_is_unreadable(self):
        missing = tempfile.TemporaryDirectory()
        self.addCleanup(missing.cleanup)
        env = {**{k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"},
               "GITHUB_EVENT_NAME": "pull_request",
               "GITHUB_EVENT_PATH": str(Path(missing.name) / "no-such-event.json"),
               "CI_NEEDS": json.dumps({"scope": {"result": "success",
                                                 "outputs": {k: "false" for k in gate.PATHS} | {"tier": "draft"}}})}
        # No payload → full tier; a draft scope output then disagrees → red.
        result = subprocess.run(
            [sys.executable, str(WORKFLOW.parent.parent / "scripts" / "required-ci.py"), "check"],
            env=env, cwd=WORKFLOW.parent.parent.parent, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the event", result.stderr)


class MergeRefBaseTest(unittest.TestCase):
    """``pr-base`` names the base a pull request's merge ref was built on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def commit(self, name, text):
        (self.repo / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-qm", f"add {name}")
        return self.git("rev-parse", "HEAD")

    def merged_repo(self):
        """A base, a PR branch off it, and main moving on before the merge.

        Returns (head commit, main tip at merge time, merge commit). The local
        ``commit.gpgsign=false`` keeps a host whose global config signs commits
        (and has no working signer) from turning the fixture red.
        """
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")
        self.commit("base.txt", "base\n")
        self.git("checkout", "-qb", "pr")
        head = self.commit("pr.txt", "pr\n")
        self.git("checkout", "-q", "main")
        base_at_merge = self.commit("main.txt", "main\n")
        self.git("merge", "--no-ff", "-qm", "merge pr", "pr")
        return head, base_at_merge, self.git("rev-parse", "HEAD")

    def test_a_merge_ref_answers_its_first_parent(self):
        head, base_at_merge, merge = self.merged_repo()
        self.assertEqual(
            gate.merge_ref_base("f" * 40, merge, cwd=self.repo), base_at_merge)
        # A ref that is not a merge keeps the event's answer.
        self.assertEqual(gate.merge_ref_base("f" * 40, head, cwd=self.repo), "f" * 40)

    def test_the_cli_prints_the_gates_answer(self):
        _, base_at_merge, _ = self.merged_repo()
        script = WORKFLOW.parent.parent / "scripts" / "required-ci.py"
        result = subprocess.run(
            [sys.executable, str(script), "pr-base", "f" * 40],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), base_at_merge)


if __name__ == "__main__":
    unittest.main()
