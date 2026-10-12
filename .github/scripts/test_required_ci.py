import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "required_ci", Path(__file__).with_name("required-ci.py")
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / "required-ci.yml"


class RequiredCITest(unittest.TestCase):
    def test_main_parent_preserves_standalone_push_selections(self):
        # Representative files from every former standalone main-push pattern.
        previous = {
            "backend": [
                "backend/app/main.py",
                "deploy/metering-proxy/proxy.py",
                ".pre-commit-config.yaml",
                ".github/scripts/ensure-apt.sh",
                ".github/workflows/test.yml",
            ],
            "frontend": ["frontend/src/main.ts", ".github/workflows/frontend.yml"],
            "e2e": [
                "backend/app/main.py",
                "frontend/src/main.ts",
                "e2e/tests/login.ts",
                ".github/scripts/ci-test-evidence.py",
                ".github/workflows/e2e.yml",
            ],
            "cli": [
                "cli/main.go",
                "backend/tests/fixtures/wire/frame.json",
                ".github/workflows/cli.yml",
            ],
            "guards": [
                "backend/app/main.py",
                "frontend/src/main.ts",
                "deploy/run.sh",
                ".claude/scripts/check.sh",
                ".pre-commit-config.yaml",
                ".github/workflows/test.yml",
                ".github/scripts/check.py",
            ],
            "deploy": [
                "deploy/run.sh",
                ".github/workflows/deploy-scripts-test.yml",
                ".github/workflows/deploy-dev.yml",
                ".github/workflows/deploy-drift.yml",
                ".github/workflows/build.yml",
                ".github/scripts/plan-image-builds.sh",
                ".github/scripts/ensure-apt.sh",
                ".claude/scripts/pr-watch.sh",
                ".github/workflows/desktop.yml",
                ".github/scripts/test-plan-image-builds.sh",
                "backend/scripts/gateway_supply_probe.py",
                "backend/scripts/test_gateway_supply_probe.py",
            ],
            "harness": [
                "backend/uv.lock",
                "backend/app/domain/agent/harness/claude_code/device_launch.py",
                "backend/app/domain/agent/harness/codex/host.py",
                ".github/workflows/harness-contract.yml",
            ],
            "mcp": [
                "scripts/remote_execution/package-lock.json",
                ".github/workflows/mcp-contract.yml",
                "scripts/remote_execution/headless_contract.py",
            ],
        }
        for suite, paths in previous.items():
            for path in paths:
                with self.subTest(suite=suite, path=path):
                    self.assertTrue(gate.select([path])[suite])

    def test_remote_execution_plugin_runs_the_build_contracts(self):
        # headless_contract.py launches -p through the plugin and the executor,
        # with the runner's own arguments (cli.py).
        for path in (
            "backend/app/domain/agent/harness/claude_code/cli.py",
            "backend/app/domain/agent/harness/claude_code/remote_execution/proxy.js",
            "backend/app/domain/agent/harness/claude_code/remote_execution/client.py",
            "backend/app/domain/agent/executor_transport.py",
            "scripts/remote_execution/acceptance.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["mcp"])

    def test_what_the_equivalence_run_launches_selects_it(self):
        # equivalence.py imports headless_contract.py, which launches the build
        # with custom_mcp.py; client.py and release.py ship the executor's
        # files, the sandbox CLI and the project hooks to the machine.
        for path in (
            "scripts/remote_execution/equivalence.py",
            "scripts/remote_execution/custom_mcp.py",
            "backend/app/domain/agent/harness/claude_code/remote_execution/runtime.py",
            "backend/app/domain/agent/project_hooks.py",
            "backend/sandbox/cheese",
            ".github/workflows/mcp-equivalence.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["equivalence"])

    def test_the_other_build_contracts_leave_equivalence_alone(self):
        for path in (
            "deploy/metering-proxy/cheese_billing_core.py",
            "scripts/remote_execution/refresh_contract.py",
            "scripts/remote_execution/mcp_contract.py",
            "scripts/remote_execution/acceptance.py",
            ".github/workflows/mcp-contract.yml",
        ):
            with self.subTest(path=path):
                selected = gate.select([path])
                self.assertTrue(selected["mcp"])
                self.assertFalse(selected["equivalence"])

    def test_docs_site_sources_build_the_site_before_merge(self):
        # Everything the docs build reads: pages, the generator, the files its
        # CLI, settings and CI references are generated from, and any code a
        # developer page names in ``covers`` (a vanished path fails the build).
        # The build runs in guards, which every merge diff selects.
        for path in (
            "backend/app/domain/chat/service.py",
            "frontend/src/main.ts",
            "docs/manual/quickstart.md",
            "docs/manual/dev/turn.md",
            "docs/site/build.mjs",
            "backend/sandbox/cheese",
            "backend/app/core/config.py",
            ".github/workflows/test.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["guards"])

    def test_documentation_still_runs_guards(self):
        selected = gate.select(["docs/architecture.md"])
        self.assertEqual({k for k, v in selected.items() if v}, {"guards"})

    def test_remote_dependencies_select_both_acceptance_jobs(self):
        for path in (
            "backend/uv.lock",
            "cli/internal/client.go",
            "scripts/remote_execution/room_fixture.py",
            ".dockerignore",
            ".github/scripts/allow-user-namespaces.sh",
            ".github/scripts/ensure-apt.sh",
            ".github/scripts/ci-test-evidence.py",
            ".github/workflows/remote-execution.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["remote"])

    def test_remote_failure_cannot_leave_the_required_check_green(self):
        for result in ("failure", "cancelled", "skipped", "missing"):
            with self.subTest(result=result):
                needs = self.needs()
                needs["remote"]["result"] = result
                self.assertTrue(gate.failures(needs, "full"))

    def test_backend_change_selects_backend_and_e2e(self):
        selected = gate.select(["backend/app/api/rooms.py"])
        self.assertTrue(selected["backend"] and selected["e2e"])
        self.assertFalse(selected["frontend"])

    def test_frontend_ratchet_scripts_run_the_frontend_suite(self):
        # lint:catalog and lint:scenes run these scripts against the real tree
        # only in the frontend suite; guards alone never runs them there.
        for path in (
            ".claude/scripts/frontend_grade.py",
            ".claude/scripts/catalog-ratchet.py",
            ".claude/scripts/scene-ratchet.py",
            ".claude/scripts/scene-debt-ratchet.py",
            ".claude/scripts/test_scene_debt_ratchet.py",
            ".claude/scripts/ratchet_report.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["frontend"])
        self.assertFalse(gate.select([".claude/scripts/check.sh"])["frontend"])

    def test_combined_merge_group_checks_every_changed_area(self):
        selected = gate.select(["frontend/src/main.ts", "cli/main.go"])
        self.assertTrue(all(selected[k] for k in ("frontend", "cli", "e2e")))

    def test_gate_edit_exercises_all_suites(self):
        self.assertTrue(
            all(gate.select([".github/workflows/required-ci.yml"]).values())
        )

    def test_cifast_inputs_select_the_cifast_suite(self):
        # The complete input set of ci-fast: its script and tests, the shared
        # selector and path map it reuses, the hook command table it invokes,
        # and the task entry that wires it.
        for path in (
            ".github/scripts/ci-fast.py",
            ".github/scripts/ci_fast_pytest_plugin.py",
            ".github/scripts/test_ci_fast.py",
            ".github/scripts/required-ci.py",
            ".github/scripts/required-ci-paths.json",
            ".pre-commit-config.yaml",
            "Taskfile.yml",
            ".github/workflows/ci-fast.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["cifast"])

    def test_unrelated_change_does_not_select_cifast(self):
        self.assertFalse(gate.select(["cli/main.go"])["cifast"])

    def test_failed_cancelled_or_missing_selected_cifast_blocks(self):
        for result in ("failure", "cancelled", "skipped", "missing"):
            with self.subTest(result=result):
                needs = self.needs()
                needs["scope"]["outputs"]["cifast"] = "true"
                needs["cifast"]["result"] = result
                self.assertTrue(gate.failures(needs, "full"))
        # A selected suite whose job never reported into needs is missing too.
        needs = self.needs()
        needs["scope"]["outputs"]["cifast"] = "true"
        del needs["cifast"]
        self.assertTrue(gate.failures(needs, "full"))

    def test_missing_or_invalid_cifast_scope_output_blocks(self):
        needs = self.needs()
        del needs["scope"]["outputs"]["cifast"]
        self.assertTrue(gate.failures(needs, "full"))
        needs = self.needs()
        needs["scope"]["outputs"]["cifast"] = "banana"
        self.assertTrue(gate.failures(needs, "full"))

    def test_gateway_health_changes_select_their_behavior_suite(self):
        for path in (
            "backend/scripts/gateway_supply_probe.py",
            "backend/scripts/test_gateway_supply_probe.py",
            ".github/workflows/deploy-drift.yml",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["deploy"])

    def test_shared_wire_fixture_checks_both_languages(self):
        selected = gate.select(["backend/tests/fixtures/wire/frame.json"])
        self.assertTrue(selected["backend"] and selected["cli"])

    def test_backend_hook_edits_run_the_hooks_that_consume_them(self):
        self.assertTrue(gate.select([".pre-commit-config.yaml"])["backend"])

    def test_shared_package_installer_runs_its_consumers(self):
        selected = gate.select([".github/scripts/ensure-apt.sh"])
        self.assertTrue(selected["backend"] and selected["deploy"])

    def test_clean_evidence_helper_runs_its_e2e_consumer(self):
        selected = gate.select([".github/scripts/ci-test-evidence.py"])
        self.assertTrue(selected["e2e"])

    def needs(self):
        """A happy-path ``toJSON(needs)`` for a non-draft (FULL) run.

        A backend change selects backend, e2e, guards and remote; every other
        suite is skipped. ``fast`` is skipped — it only runs on a draft — and
        ``scope.outputs.tier`` is ``"full"``. This is the shape the aggregation
        must accept, and the shape every negative test perturbs.
        """
        selected = gate.select(["backend/app/api/rooms.py"])
        outputs = {k: str(v).lower() for k, v in selected.items()}
        outputs["tier"] = "full"
        return {
            "scope": {"result": "success", "outputs": outputs},
            "fast": {"result": "skipped"},
            **{
                k: {"result": "success" if v else "skipped"}
                for k, v in selected.items()
            },
        }

    def quick_needs(self):
        """A happy-path ``toJSON(needs)`` for a DRAFT (QUICK) run.

        ``scope`` keeps the raw select flags — the fast job reads them to decide
        what to install — but the tier is ``"quick"``, so NO product suite may
        run: every one is skipped and only ``fast`` succeeds.
        """
        selected = gate.select(["backend/app/api/rooms.py"])
        outputs = {k: str(v).lower() for k, v in selected.items()}
        outputs["tier"] = "quick"
        return {
            "scope": {"result": "success", "outputs": outputs},
            "fast": {"result": "success"},
            **{k: {"result": "skipped"} for k in selected},
        }

    def test_intentionally_skipped_suites_do_not_block(self):
        self.assertEqual(gate.failures(self.needs(), "full"), [])

    def test_failed_cancelled_skipped_or_missing_selected_suite_blocks(self):
        for result in ("failure", "cancelled", "skipped", "missing"):
            with self.subTest(result=result):
                needs = self.needs()
                needs["backend"]["result"] = result
                self.assertTrue(gate.failures(needs, "full"))

    def test_failed_scope_or_missing_output_blocks(self):
        needs = self.needs()
        needs["scope"]["result"] = "failure"
        self.assertTrue(gate.failures(needs, "full"))
        needs = self.needs()
        del needs["scope"]["outputs"]["backend"]
        self.assertTrue(gate.failures(needs, "full"))


class TierForTest(unittest.TestCase):
    """The event → tier table, the single source of the whole scheme's safety.

    Only a DRAFT pull_request is quick. Everything else — merge queue, a
    non-draft PR, and any event this cannot read — is the full gate.
    """

    def test_merge_group_is_always_full(self):
        # The queue entry is the run the production deploy chain reads
        # (deploy/check-auto-deploy.py); it must be the complete gate.
        self.assertEqual(gate.tier_for("merge_group", {}), "full")
        self.assertEqual(
            gate.tier_for("merge_group", {"pull_request": {"draft": True}}), "full"
        )

    def test_a_non_draft_pull_request_is_full(self):
        # Includes ready_for_review: promoting a draft starts a full run.
        for action in ("opened", "synchronize", "reopened", "ready_for_review"):
            with self.subTest(action=action):
                event = {"action": action, "pull_request": {"draft": False}}
                self.assertEqual(gate.tier_for("pull_request", event), "full")

    def test_only_a_draft_pull_request_is_quick(self):
        for action in ("opened", "synchronize", "converted_to_draft"):
            with self.subTest(action=action):
                event = {"action": action, "pull_request": {"draft": True}}
                self.assertEqual(gate.tier_for("pull_request", event), "quick")

    def test_the_draft_flag_not_the_action_is_authoritative(self):
        # Same action, opposite flag: the flag wins, so a stale/renamed action
        # can never promote a draft to the full gate (or vice versa).
        for action in ("opened", "ready_for_review", "synchronize", "reopened"):
            with self.subTest(action=action, draft=False):
                self.assertEqual(
                    gate.tier_for(
                        "pull_request", {"action": action, "pull_request": {"draft": False}}
                    ),
                    "full",
                )
            with self.subTest(action=action, draft=True):
                self.assertEqual(
                    gate.tier_for(
                        "pull_request", {"action": action, "pull_request": {"draft": True}}
                    ),
                    "quick",
                )

    def test_an_unreadable_event_fails_closed_to_full(self):
        for event_name, event in (
            ("pull_request", {}),  # no flag
            ("pull_request", {"pull_request": None}),
            ("pull_request", {"pull_request": {}}),  # flag absent
            ("pull_request", {"pull_request": {"draft": None}}),  # flag null
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

    def needs(self):
        selected = gate.select(["backend/app/api/rooms.py"])
        outputs = {k: str(v).lower() for k, v in selected.items()}
        outputs["tier"] = "full"
        return {
            "scope": {"result": "success", "outputs": outputs},
            "fast": {"result": "skipped"},
            **{
                k: {"result": "success" if v else "skipped"}
                for k, v in selected.items()
            },
        }

    def quick_needs(self):
        selected = gate.select(["backend/app/api/rooms.py"])
        outputs = {k: str(v).lower() for k, v in selected.items()}
        outputs["tier"] = "quick"
        return {
            "scope": {"result": "success", "outputs": outputs},
            "fast": {"result": "success"},
            **{k: {"result": "skipped"} for k in selected},
        }

    def test_quick_run_passes_with_only_the_fast_job_green(self):
        self.assertEqual(gate.failures(self.quick_needs(), "quick"), [])

    def test_quick_run_blocks_if_a_product_suite_ran(self):
        # A draft must SKIP every full suite: a green product suite under the
        # draft's name would be full evidence masquerading as the quick tier.
        needs = self.quick_needs()
        needs["backend"]["result"] = "success"
        self.assertTrue(gate.failures(needs, "quick"))

    def test_quick_run_blocks_if_fast_is_missing_or_not_success(self):
        for result in ("failure", "cancelled", "skipped", "missing"):
            with self.subTest(result=result):
                needs = self.quick_needs()
                needs["fast"]["result"] = result
                self.assertTrue(gate.failures(needs, "quick"))
        needs = self.quick_needs()
        del needs["fast"]
        self.assertTrue(gate.failures(needs, "quick"))

    def test_full_run_blocks_a_green_fast_job(self):
        # In the full tier the fast job must be skipped: if it ran, something
        # other than the selected full suite set produced the required green.
        needs = self.needs()
        needs["fast"]["result"] = "success"
        self.assertTrue(gate.failures(needs, "full"))

    def test_a_full_run_does_not_satisfy_a_quick_event(self):
        # The draft's required name is `CI required (draft quick)`; a full-tier
        # payload must never be accepted as its answer.
        self.assertTrue(gate.failures(self.needs(), "quick"))

    def test_a_quick_run_does_not_satisfy_a_full_event(self):
        self.assertTrue(gate.failures(self.quick_needs(), "full"))

    def test_scope_tier_disagreeing_with_the_event_blocks(self):
        needs = self.needs()
        needs["scope"]["outputs"]["tier"] = "quick"
        self.assertTrue(gate.failures(needs, "full"))
        needs = self.quick_needs()
        needs["scope"]["outputs"]["tier"] = "full"
        self.assertTrue(gate.failures(needs, "quick"))

    def test_missing_or_invalid_scope_tier_blocks(self):
        for tier in (None, "banana", ""):
            with self.subTest(tier=tier):
                needs = self.needs()
                needs["scope"]["outputs"]["tier"] = tier
                self.assertTrue(gate.failures(needs, "full"))
        needs = self.needs()
        del needs["scope"]["outputs"]["tier"]
        self.assertTrue(gate.failures(needs, "full"))


def jobs_of(text):
    """Split a workflow into ``{job: body}`` by two-space top-level keys."""
    marks = list(re.finditer(r"(?m)^  ([A-Za-z0-9_]+):$", text))
    return {m.group(1): text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            for i, m in enumerate(marks)}


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


class WorkflowTextTest(unittest.TestCase):
    """Static guards over required-ci.yml text, to back claims made here.

    Invariants the workflow copy must keep, checked against the file itself so a
    later edit cannot quietly undo them.
    """

    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_no_shell_command_interpolates_a_workflow_expression(self):
        # Untrusted text (a PR title off the API) must reach a script as data,
        # never as a workflow expression spliced into a shell line: `${{ ... }}`
        # in a `run:` is command-injection surface and, for a multiline value,
        # corrupts whatever follows. Feed it through `env:` and expand the shell
        # variable instead. (Expressions in `env:`/`if:`/`name:` are fine and are
        # why this checks run bodies, not every line with `${{`.)
        for body in run_bodies(self.text):
            self.assertNotIn(
                "${{", body,
                f"expression spliced into a shell command: {body.strip()[:80]}",
            )
        # The one place the title is consumed expands the shell variable.
        self.assertIn('--title "$title"', self.text)

    def test_the_draft_job_installs_both_dependency_sets_unconditionally(self):
        # ci-fast widens its own scope beyond the raw select() flags (unknown
        # paths → backend+frontend, i18n → backend), so a scope-gated install
        # could omit a dependency ci-fast then needs, blocking the draft. The
        # install must name both installers and gate neither on a scope output.
        match = re.search(r"(?ms)^\s*- name: Install dependencies\n(.*?)(?=^\s*- name:)", self.text)
        self.assertIsNotNone(match, "no 'Install dependencies' step in the workflow")
        install = match.group(0)
        self.assertIn("uv sync", install)
        self.assertIn("pnpm install", install)
        self.assertNotIn("needs.scope.outputs", install)
        self.assertNotIn("if:", install)


class MainCliTest(unittest.TestCase):
    """The ``scope``/``check`` wiring that the runner actually invokes."""

    def _event_file(self, tmp, payload):
        path = Path(tmp) / "event.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def test_scope_mode_reports_the_selection_and_the_tier(self):
        # Required CI runs this on a depth=1 checkout, which has no parent
        # commit to reach for. Build a throwaway repo with two explicit commits
        # and run the CLI inside it, so the test never depends on the checkout's
        # history depth (and the workflow checkout stays as shallow as it is).
        scripts = WORKFLOW.parent.parent / "scripts"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ("required-ci.py", "required-ci-paths.json"):
                shutil.copy(scripts / name, tmp / name)

            def git(*args):
                subprocess.run(
                    ["git", "-c", "user.email=test@example.com", "-c", "user.name=test",
                     "-c", "commit.gpgsign=false", *args],
                    cwd=tmp, check=True, capture_output=True, text=True)

            git("init", "-q")
            (tmp / "base.txt").write_text("base\n")
            git("add", "base.txt")
            git("commit", "-qm", "base")
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
            changed = tmp / "backend/app/api/rooms.py"
            changed.parent.mkdir(parents=True)
            changed.write_text("x = 1\n")
            git("add", "backend/app/api/rooms.py")
            git("commit", "-qm", "change a backend file")

            out = tmp / "github_output"
            env = {
                **os.environ,
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_EVENT_PATH": self._event_file(tmp, {"pull_request": {"draft": True}}),
                "GITHUB_OUTPUT": str(out),
            }
            subprocess.run([sys.executable, str(tmp / "required-ci.py"), "scope", base],
                           env=env, cwd=tmp, check=True, capture_output=True, text=True)
            written = dict(line.split("=", 1) for line in out.read_text().splitlines())
            # The changed backend file selects backend (and e2e/remote/guards);
            # a draft event makes it the quick tier.
            self.assertEqual(written["tier"], "quick")
            self.assertEqual(written["backend"], "true")
            self.assertEqual(written["cifast"], "false")
            self.assertTrue(all(v in ("true", "false") for k, v in written.items() if k != "tier"))

    def test_check_mode_fails_closed_when_the_event_is_unreadable(self):
        env = {**os.environ, "GITHUB_EVENT_NAME": "pull_request",
               "GITHUB_EVENT_PATH": "/nonexistent/event.json",
               "CI_NEEDS": json.dumps({"scope": {"result": "success",
                                                 "outputs": {k: "false" for k in gate.PATHS} | {"tier": "quick"}}})}
        # No payload → full tier; a quick scope output then disagrees → red.
        result = subprocess.run(
            [sys.executable, str(WORKFLOW.parent.parent / "scripts" / "required-ci.py"), "check"],
            env=env, cwd=WORKFLOW.parent.parent.parent, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the event", result.stderr)


if __name__ == "__main__":
    unittest.main()
