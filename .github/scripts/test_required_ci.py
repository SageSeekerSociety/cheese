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
            "backend/app/domain/agent/harness/claude_code/remote_execution/runtime_core.py",
            "backend/app/domain/agent/harness/claude_code/remote_execution/runtime_commands.py",
            "backend/app/domain/agent/harness/claude_code/remote_execution/runtime_context.py",
            "backend/app/domain/agent/harness/claude_code/remote_execution/runtime_calls.py",
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

    def test_the_catalogs_the_backend_reads_select_backend(self):
        # The backend reads these three at import time (sentences.py), so a
        # change to any of them has to run the backend suite. roomNotice and
        # apiError are each split across a shard directory now: the patterns
        # have to reach inside them, and the frontend-only namespaces must not
        # drag backend along.
        for path in (
            "frontend/src/i18n/messages/zh-CN/roomNotice/agent.json",
            "frontend/src/i18n/messages/en/roomNotice/agent.json",
            "frontend/src/i18n/messages/zh-CN/global.json",
            "frontend/src/i18n/messages/zh-CN/apiError/usage.json",
            "frontend/src/i18n/messages/en/apiError/usage.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["backend"])
        for path in (
            "frontend/src/i18n/messages/zh-CN/work/room/doc/index.json",
            "frontend/src/i18n/messages/zh-CN/account/index.json",
        ):
            with self.subTest(path=path):
                selected = gate.select([path])
                self.assertTrue(selected["frontend"])
                self.assertFalse(selected["backend"])

    def test_frontend_ratchet_scripts_run_the_frontend_suite(self):
        # lint:scenes and lint:scene-debt run these scripts against the real tree
        # only in the frontend suite; guards alone never runs them there.
        for path in (
            ".claude/scripts/frontend_grade.py",
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
        # the task entry that wires it, and the entry point `--pre-push`
        # documents and its tests execute.
        for path in (
            ".cheese/pre-push",
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

    def test_e2e_runs_only_what_its_own_job_executes(self):
        # e2e.yml starts the app from backend/app, migrates with alembic, seeds
        # the fusion demo, and drives the app through the browser. Two places
        # outside app/ and alembic/ are executed by that job and by nobody else:
        # `backend/tests/forgejo/**` — the backend suite passes
        # `--ignore=tests/forgejo` (test.yml), so e2e is where those run — and
        # the seed script the job names. A rule that dropped them would skip the
        # Playwright shards on a change to the forge fixture they exercise.
        for path in (
            "backend/app/api/rooms.py",
            "backend/alembic/versions/abc123_add_column.py",
            "backend/alembic.ini",
            "backend/tests/forgejo/test_pull_ci.py",
            "backend/tests/forgejo/pull-ci.sh",
            "backend/scripts/seed_fusion_demo.py",
            "backend/sandbox/cheese",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "backend/.python-version",
            "frontend/src/main.ts",
            "e2e/tests/login.spec.ts",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["e2e"])

    def test_a_change_that_only_its_other_suites_read_skips_the_browser(self):
        # These are files e2e never executes: unit and integration tests, the
        # repository's check scripts, and the import-linter contract. Running
        # five Playwright shards for a change none of them can see is what this
        # rule stops doing — and the linters that DO read them stay selected.
        for path in (
            "backend/tests/unit/test_runtime.py",
            "backend/tests/integration/test_archive_retires_storage.py",
            "backend/tests/conftest.py",
            "backend/scripts/check_boundaries.py",
            "backend/scripts/test_gateway_conformance.py",
            "backend/.importlinter",
            "backend/deferred-import-baseline.json",
        ):
            with self.subTest(path=path):
                selected = gate.select([path])
                self.assertFalse(selected["e2e"])
                self.assertTrue(selected["backend"] and selected["guards"])

    def test_a_remote_change_selects_what_its_jobs_execute(self):
        # remote-execution.yml builds backend/sandbox/Dockerfile.private, runs a
        # pytest selection whose files load tests/conftest.py (and, through it,
        # tests/isolation.py and tests/support/*), and names two scripts on the
        # command line. Anything under one of those roots has to select it —
        # including alembic.ini, which conftest's `python -m alembic upgrade`
        # reads from backend/ when a DB-backed test builds its schema.
        for path in (
            "backend/app/domain/agent/executor_transport.py",
            "backend/alembic/env.py",
            "backend/alembic.ini",
            "backend/sandbox/Dockerfile.private",
            "backend/tests/conftest.py",
            "backend/tests/support/processes.py",
            "backend/tests/unit/test_private_chat.py",
            "backend/tests/integration/test_llm_proxy.py",
            "backend/scripts/assert_suite_ran.py",
            "backend/scripts/device_connection_lifecycle_acceptance.py",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "backend/.python-version",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["remote"])

    def test_a_change_no_remote_job_executes_skips_the_acceptance(self):
        # The two jobs do not run the repository's check scripts: a change to
        # one of them alters no file the acceptance launches.
        for path in (
            "backend/scripts/check_boundaries.py",
            "backend/scripts/gateway_conformance.py",
            "backend/scripts/seat_throughput.py",
            "backend/.importlinter",
            "backend/deferred-import-baseline.json",
        ):
            with self.subTest(path=path):
                selected = gate.select([path])
                self.assertFalse(selected["remote"])
                self.assertTrue(selected["backend"] and selected["guards"])

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
            # A backend change the paths do not send to the frontend: the api
            # job answers whether the surface moved, and here it did not.
            "api": {"result": "success", "outputs": {"moved": "false"}},
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
            "api": {"result": "skipped"},
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

    def test_pull_request_skips_e2e_and_remote_even_when_paths_match(self):
        for path in (
            "backend/app/api/rooms.py",
            "e2e/tests/login.spec.ts",
            "scripts/remote_execution/room_fixture.py",
            ".github/scripts/required-ci.py",
        ):
            with self.subTest(path=path):
                pr_selected = gate.select([path], "pull_request")
                self.assertFalse(pr_selected["e2e"])
                self.assertFalse(pr_selected["remote"])
                self.assertTrue(pr_selected["guards"])
        mg_selected = gate.select(
            ["e2e/tests/login.spec.ts", "scripts/remote_execution/room_fixture.py"],
            "merge_group",
        )
        self.assertTrue(mg_selected["e2e"])
        self.assertTrue(mg_selected["remote"])

    def test_check_accepts_skipped_e2e_and_remote_on_pull_request_only(self):
        pr_needs = self.needs()
        pr_needs["scope"]["outputs"]["branch_heavy"] = "false"
        for suite in ("e2e", "remote"):
            pr_needs["scope"]["outputs"][suite] = "false"
            pr_needs[suite]["result"] = "skipped"
        self.assertEqual(gate.failures(pr_needs, "full", "pull_request"), [])
        raw_skipped = self.needs()
        for suite in ("e2e", "remote"):
            raw_skipped[suite]["result"] = "skipped"
        self.assertEqual(gate.failures(raw_skipped, "full", "pull_request"), [])
        mismatch = self.needs()
        mismatch["scope"]["outputs"]["branch_heavy"] = "true"
        for suite in ("e2e", "remote"):
            mismatch[suite]["result"] = "skipped"
        self.assertTrue(gate.failures(mismatch, "full", "pull_request"))
        for suite in ("e2e", "remote"):
            with self.subTest(suite=suite):
                mg_needs = self.needs()
                mg_needs["scope"]["outputs"]["branch_heavy"] = "true"
                mg_needs[suite]["result"] = "skipped"
                self.assertTrue(gate.failures(mg_needs, "full", "merge_group"))


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
            # A backend change the paths do not send to the frontend: the api
            # job answers whether the surface moved, and here it did not.
            "api": {"result": "success", "outputs": {"moved": "false"}},
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
            "api": {"result": "skipped"},
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
        needs = {
            "scope": {"result": "success", "outputs": outputs},
            "fast": {"result": "skipped"},
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

    def test_a_moved_surface_with_the_frontend_suite_green_passes(self):
        needs = self.needs(api={"result": "success", "outputs": {"moved": "true"}},
                           frontend="success")
        self.assertEqual(gate.failures(needs, "full"), [])

    def test_a_moved_surface_with_a_red_frontend_suite_is_red(self):
        needs = self.needs(api={"result": "success", "outputs": {"moved": "true"}},
                           frontend="failure")
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

    def test_api_runs_only_where_the_paths_left_the_question_open(self):
        # A frontend change already runs the suite, and a change outside the
        # backend cannot move the document: neither asks api.
        for paths in (("frontend/src/main.ts",),
                      ("backend/app/api/rooms.py", "frontend/src/main.ts"),
                      ("docs/workflows.md",)):
            with self.subTest(paths=paths):
                ran = self.needs(paths, api={"result": "success", "outputs": {"moved": "false"}})
                self.assertTrue(gate.failures(ran, "full"))
                skipped = self.needs(paths, api={"result": "skipped"})
                self.assertEqual(gate.failures(skipped, "full"), [])

    def test_a_draft_never_asks_api(self):
        needs = self.needs(api={"result": "success", "outputs": {"moved": "true"}})
        needs["scope"]["outputs"]["tier"] = "quick"
        needs["fast"]["result"] = "success"
        for suite in gate.PATHS:
            needs[suite] = {"result": "skipped"}
        self.assertTrue(gate.failures(needs, "quick"))
        needs["api"] = {"result": "skipped"}
        self.assertEqual(gate.failures(needs, "quick"), [])

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
        # Run every combination through the two `if:`s exactly as written in
        # required-ci.yml, give each job the result GitHub would, and ask the
        # judge. A condition that drifted from the judge — api asked when the
        # paths already chose the frontend, or the frontend left out when api
        # saw the surface move — leaves a combination the judge refuses.
        jobs = jobs_of(WORKFLOW.read_text())
        api_if, frontend_if = jobs["api"]["if"], jobs["frontend"]["if"]
        for tier in ("full", "quick"):
            for backend in ("true", "false"):
                for frontend in ("true", "false"):
                    for moved in ("true", "false"):
                        with self.subTest(tier=tier, backend=backend,
                                          frontend=frontend, moved=moved):
                            outputs = {suite: "false" for suite in gate.PATHS}
                            outputs.update(tier=tier, backend=backend, frontend=frontend)
                            api_runs = self._holds(api_if, outputs, "", True)
                            self.assertEqual(api_runs, gate.api_selected(outputs, tier))
                            # A skipped job's outputs are empty strings.
                            api_moved = moved if api_runs else ""
                            # frontend needs scope (succeeded) and api, which
                            # succeeded only if it ran.
                            frontend_runs = self._holds(frontend_if, outputs, api_moved,
                                                        api_runs)
                            needs = {
                                "scope": {"result": "success", "outputs": outputs},
                                "fast": {"result": "success" if tier == "quick" else "skipped"},
                                "api": ({"result": "success", "outputs": {"moved": moved}}
                                        if api_runs else {"result": "skipped"}),
                            }
                            for suite in gate.PATHS:
                                runs = (frontend_runs if suite == "frontend"
                                        else tier == "full" and outputs[suite] == "true")
                                needs[suite] = {"result": "success" if runs else "skipped"}
                            self.assertEqual(gate.failures(needs, tier), [])

    def test_the_api_output_is_the_one_the_compare_step_writes(self):
        # The frontend's `if:` reads `needs.api.outputs.moved`; the job maps it
        # from a step's output. A renamed step id or output key leaves it empty,
        # and an empty `moved` is a judge-red run on every backend change.
        text = jobs_of(WORKFLOW.read_text())["api"]["text"]
        mapped = re.search(r"^      moved: \$\{\{ steps\.([\w-]+)\.outputs\.(\w+) \}\}$",
                           text, re.M)
        self.assertIsNotNone(mapped, "the api job does not map `moved` from a step output")
        step_id, key = mapped.groups()
        self.assertEqual(key, "moved")
        self.assertRegex(text, rf"(?m)^        id: {re.escape(step_id)}$")
        step = text[text.index(f"id: {step_id}"):]
        self.assertIn("export_openapi --stdout", step.split("\n      - ")[0])

    def run_compare(self, head_doc, base_doc, base_exit=0):
        """Run the api job's real comparison shell against stub git and uv.

        The stub uv prints ``head_doc`` from the change's backend/ and
        ``base_doc`` from the base worktree's, and exits ``base_exit`` there.
        Returns the process and what the step wrote to $GITHUB_OUTPUT.
        """
        body = run_body_containing(WORKFLOW.read_text(), "export_openapi --stdout")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "repo" / "backend").mkdir(parents=True)
            bindir = tmp / "bin"
            bindir.mkdir()
            _stub(bindir, "git",
                  'if [ "$1" = worktree ]; then mkdir -p "${@: -2:1}/backend"; fi\n')
            # Prints only from the two backend/ directories the step must run
            # it in; anywhere else is a step that changed directory wrongly.
            _stub(bindir, "uv",
                  'case "$PWD" in\n'
                  '  "$RUNNER_TEMP/base/backend") printf "%s" "$BASE_DOC"; exit "$BASE_EXIT";;\n'
                  '  */repo/backend) printf "%s" "$HEAD_DOC";;\n'
                  '  *) echo "uv run from $PWD" >&2; exit 3;;\n'
                  'esac\n')
            output = tmp / "github_output"
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "RUNNER_TEMP": str(tmp / "runner"), "GITHUB_OUTPUT": str(output),
                   "BASE_SHA": "0" * 40, "HEAD_DOC": head_doc, "BASE_DOC": base_doc,
                   "BASE_EXIT": str(base_exit)}
            (tmp / "runner").mkdir()
            # `bash -e`: the shell GitHub gives a Linux `run:` step.
            proc = subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp / "repo",
                                  capture_output=True, text=True)
            written = output.read_text() if output.exists() else ""
        return proc, written

    def test_the_compare_step_reports_a_moved_document(self):
        proc, written = self.run_compare('{"paths": {"/a": {}}}\n', '{"paths": {}}\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(written.splitlines(), ["moved=true"])

    def test_the_compare_step_reports_an_unmoved_document(self):
        proc, written = self.run_compare('{"paths": {}}\n', '{"paths": {}}\n')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(written.splitlines(), ["moved=false"])

    def test_a_base_that_cannot_print_its_document_fails_the_step(self):
        # No document at the base is no answer: the step must fail (which the
        # judge reads as red) rather than call an empty file "moved" or not.
        proc, written = self.run_compare('{"paths": {}}\n', "", base_exit=1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("moved=", written)


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


def steps_of(job_text):
    """The top-level steps and scalar keys of a workflow job."""
    lines = job_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == "    steps:"), None)
    if start is None:
        raise AssertionError("job has no steps: mapping")
    steps = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < 6:
            break
        item = re.match(r"^      - ([\w-]+):[ \t]*(.*)$", line)
        if item:
            steps.append({item.group(1): item.group(2)})
        elif steps:
            key = re.match(r"^        ([\w-]+):[ \t]*(.*)$", line)
            if key:
                steps[-1][key.group(1)] = key.group(2)
    return steps


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


def _stub(bindir, name, script):
    """Write an executable stub command into ``bindir`` and return its path."""
    path = bindir / name
    path.write_text("#!/usr/bin/env bash\n" + script)
    path.chmod(0o755)
    return path


def run_draft_step(text, tmp, ci_fast_exit=0, report=None, gh_exit=0, jq=None):
    """Run the draft job's real shell against stub commands, and return the process.

    ``ci_fast_exit`` is the status the stubbed ci-fast leaves behind, and
    ``report`` the report it writes to the file its own ``--report`` argument
    names (absent means it reached no verdict at all, which is what a bad
    argument or a killed process looks like). ``jq``, when given, replaces the
    report renderer. The stubbed ``python3`` delegates ``-c`` to the real
    interpreter, because the step reads the report's own status with one line
    of Python and that read is part of what is under test.

    The stub writes to the path it is handed and nowhere else, because that is
    what ci-fast does and the two halves are only wired together if they agree
    on the path: ci-fast's own default is the repository's git dir, so a step
    that stopped passing ``--report`` would put the report somewhere the
    step's read never looks, ``|| true`` would swallow the miss, and the draft
    would redden as "no verdict". Planting the fixture's report at the path
    the step reads instead would keep every test green through exactly that.
    """
    tmp = Path(tmp)
    bindir = tmp / "bin"
    bindir.mkdir()
    _stub(bindir, "gh",
          f'printf "%s" \'{{"title": "feat: x", "draft": true}}\'\nexit {gh_exit}\n')
    _stub(bindir, "python3",
          f'if [ "$1" = "-c" ]; then exec "{sys.executable}" "$@"; fi\n'
          f'printf "%s\\0" "$@" >> "$STUB_ARGV"\n'
          f'prev=""; for a in "$@"; do\n'
          f'  if [ "$prev" = "--report" ] && [ -n "$STUB_REPORT" ]; then\n'
          f'    printf "%s" "$STUB_REPORT" > "$a"\n'
          f'  fi\n'
          f'  prev="$a"\n'
          f'done\n'
          f'exit {ci_fast_exit}\n')
    if jq is not None:
        _stub(bindir, "jq", jq)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "RUNNER_TEMP": str(tmp), "GITHUB_REPOSITORY": "owner/repo", "PR_NUMBER": "1",
           "GH_TOKEN": "x", "STUB_ARGV": str(tmp / "argv"),
           "STUB_REPORT": "" if report is None else json.dumps(report),
           "GITHUB_STEP_SUMMARY": str(tmp / "summary")}
    # `-e` is the shell the runner gives a `run:` step (GitHub's implicit
    # default for a Linux job is `bash -e`), and two of the ways this step can
    # redden a draft are unguarded commands failing under it. Without it here
    # the guards for those are invisible to the test.
    return subprocess.run(["bash", "-e", "-c", run_body_containing(text, "ci-fast.py")],
                          env=env, cwd=tmp, capture_output=True, text=True)




class WorkflowTextTest(unittest.TestCase):
    """Behavioural guards over required-ci.yml.

    The step under test is read out of the workflow as its real ``run:`` shell
    and executed against stub commands, so the guards hold on the shell the
    runner would run. Nothing asserts a quoting spelling: ``"$title"`` and
    ``"${title}"`` both pass; only a step that actually corrupts the title,
    lets it reach the shell as code, or drops a dependency set fails.
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

    def test_the_draft_job_hands_ci_fast_the_pr_payload_verbatim(self):
        # A PR title is untrusted data. Run the step's real shell against a `gh`
        # stub that returns a hostile title and a `python3` stub that records the
        # argv it received, and check the API payload reached the script intact.
        body = run_body_containing(self.text, "ci-fast.py")
        title = 'He said "hi"\n$(touch pwned)\nline three'
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            argv_log = tmp / "argv"
            _stub(bindir, "gh", "printf '%s' \"$STUB_TITLE\"\n")
            _stub(bindir, "python3",
                  'for a in "$@"; do printf "%s\\0" "$a" >> "$ARGV_LOG"; done\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "STUB_TITLE": title, "ARGV_LOG": str(argv_log), "RUNNER_TEMP": str(tmp),
                   "GITHUB_REPOSITORY": "owner/repo", "PR_NUMBER": "1", "GH_TOKEN": "x"}
            subprocess.run(["bash", "-c", body], env=env, cwd=tmp, check=True,
                           capture_output=True, text=True)
            argv = argv_log.read_text().split("\0")[:-1]
            # The title is not an argument at all: ci-fast reads the title and
            # the draft flag out of the API payload, which the step writes to a
            # file. Nothing a title contains can word-split an argument, and
            # nothing multiline goes into argv.
            self.assertNotIn(title, argv, f"the title was passed as an argument: {argv}")
            self.assertNotIn("--title", argv, f"argv: {argv}")
            self.assertEqual(argv.count("--pull-request"), 1, f"argv: {argv}")
            at = argv.index("--pull-request")
            self.assertEqual(Path(argv[at + 1]).read_text(), title,
                             "the API payload did not reach ci-fast verbatim")
            self.assertFalse((tmp / "pwned").exists(), "title reached the shell as code")

    def test_the_draft_job_states_the_runner_bounds(self):
        # ci-fast's defaults (180s, a 3072 MiB sampled ceiling) are the WORK
        # MACHINE's own limits — 3072 MiB is its session cap. A draft runs on a
        # CI runner instead, and with those defaults nothing behind the
        # frontend suite was ever reached: every completed draft run in the
        # 10-12 03:56–06:30 window died in frontend-unit at 2771–3079 MiB
        # sampled, leaving every later check at 0s. So the job states bounds
        # sized for the runner: above the local defaults, still quick tier (a
        # draft must not be trimmed to the static hooks), and within what
        # ci-fast will accept.
        body = run_body_containing(self.text, "ci-fast.py")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            argv_log = tmp / "argv"
            _stub(bindir, "gh", "printf '%s' \"$STUB_TITLE\"\n")
            _stub(bindir, "python3",
                  'for a in "$@"; do printf "%s\\0" "$a" >> "$ARGV_LOG"; done\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "STUB_TITLE": "feat: x", "ARGV_LOG": str(argv_log), "RUNNER_TEMP": str(tmp),
                   "GITHUB_REPOSITORY": "owner/repo", "PR_NUMBER": "1", "GH_TOKEN": "x"}
            subprocess.run(["bash", "-c", body], env=env, cwd=tmp, check=True,
                           capture_output=True, text=True)
            argv = argv_log.read_text().split("\0")[:-1]
        flags = {flag: argv[i + 1] for i, flag in enumerate(argv) if flag.startswith("--")}
        self.assertEqual(flags.get("--tier"), "quick", f"argv: {argv}")
        for flag, default, prompt in (
            ("--budget", 180, "the work machine's budget"),
            ("--memory-mib", 3072, "the work machine's ceiling"),
        ):
            self.assertIn(flag, flags, f"draft job falls back to {prompt}: {argv}")
            self.assertGreater(float(flags[flag]), default, f"{flag} is not sized for a runner")
        help_text = subprocess.run(
            [sys.executable, str(WORKFLOW.parent.parent / "scripts" / "ci-fast.py"), "--help"],
            check=True, capture_output=True, text=True).stdout
        cap = int(re.search(r"ceiling, 1\.\.(\d+)", help_text).group(1))
        # A workflow asking for more than ci-fast accepts is a red draft run for
        # every PR, which is the failure this tier just came back from.
        self.assertLessEqual(int(flags["--memory-mib"]), cap, "ceiling ci-fast refuses")

    def test_only_a_verdict_judges_the_draft(self):
        # 0 and 1 are ci-fast's own verdicts and decide this draft. Every other
        # exit is "could not judge" only when ci-fast's report says unknown or
        # blocked; with no report the run never reached a verdict — argparse
        # exits 2, the same code UNKNOWN uses, and a killed process 130+ — and
        # the draft goes red rather than pass as a tier that judged nothing.
        for status, report, expected, marker in (
            (0, None, 0, None),
            (1, None, 1, "::error::"),
            (2, {"status": "unknown", "exit": 2}, 0, "::warning::"),
            (3, {"status": "blocked", "exit": 3}, 0, "::warning::"),
            (130, {"status": "unknown", "exit": 130}, 0, "::warning::"),
            (2, None, 1, "::error::"),
            (4, None, 1, "::error::"),
            (137, None, 1, "::error::"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                proc = run_draft_step(self.text, tmp, ci_fast_exit=status, report=report)
            self.assertEqual(proc.returncode, expected,
                             f"exit {status} report {report}: {proc.stdout}{proc.stderr}")
            if marker:
                self.assertIn(marker, proc.stdout, f"exit {status}: {proc.stdout}")
            else:
                self.assertNotIn("::warning::", proc.stdout, f"exit {status}")

    def test_the_draft_job_does_not_redden_for_a_pr_it_cannot_read(self):
        # A failing `gh api` used to abort the step — this shell runs with
        # errexit — and a dead step is a red draft, for the runner's own
        # network. The read is handed to ci-fast as an unreadable payload
        # instead, which is one of the unknowns it knows how to report.
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_draft_step(self.text, tmp, ci_fast_exit=2, gh_exit=1,
                                  report={"status": "unknown", "exit": 2})
            argv = (Path(tmp) / "argv").read_text().split("\0")[:-1]
            summary = (Path(tmp) / "summary").read_text()
            payload = Path(argv[argv.index("--pull-request") + 1])
            unread = payload.read_text()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("--pull-request", argv, f"ci-fast was not handed the payload: {argv}")
        self.assertIn("what it could not judge", summary)
        # What the guard hands over is the point of guarding the read. The flag
        # being present says ci-fast was pointed somewhere; it does not say the
        # file holds the guard's message rather than the empty file a failed
        # `gh api ... > pull.json` leaves behind (the redirect truncates before
        # the command fails), a path the fallback wrote elsewhere, or a
        # placeholder that parses as a PR — the last of which would report a
        # draft green on the runner's network, the failure this guard exists to
        # prevent.
        self.assertIn("could not read", unread,
                      f"ci-fast was handed a payload the guard did not write: {payload}")
        with self.assertRaises(json.JSONDecodeError,
                               msg=f"the unread payload parses as a PR: {unread}"):
            json.loads(unread)

    def test_the_draft_job_says_what_the_green_does_not_cover(self):
        # The green of this check is honest only next to the list of what was
        # not judged. The renderer is stubbed, so this pins what the step routes
        # into the summary — the report it was written from, and the sentence
        # that says what the green means — rather than jq's own formatting.
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_draft_step(self.text, tmp, ci_fast_exit=3,
                                  report={"status": "blocked", "exit": 3},
                                  jq='printf "RENDERED:%s\\n" "$3"')
            summary = (Path(tmp) / "summary").read_text()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("what it could not judge", summary)
        # The report is rendered in three groups — why it could not judge, what
        # did not pass, and what did not run — each of them against the report
        # file the step was told to write. Reading a group without the reasons
        # is the summary that says "could not judge" and nothing about why.
        self.assertEqual([line for line in summary.splitlines() if line.startswith("RENDERED:")],
                         [f"RENDERED:{Path(tmp) / 'ci-fast-report.json'}"] * 3)
        self.assertIn("::warning::", proc.stdout)

    def test_a_report_that_cannot_be_rendered_still_does_not_redden_the_draft(self):
        # The shell runs with errexit, so a failing renderer used to end the
        # step non-zero — a red draft for a report that was already judged
        # "could not judge". The verdict stands and the annotation says it.
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_draft_step(self.text, tmp, ci_fast_exit=2,
                                  report={"status": "unknown", "exit": 2},
                                  jq='exit 127\n')
            summary = (Path(tmp) / "summary").read_text()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("could not be rendered", summary)
        self.assertIn("::warning::", proc.stdout)

    def test_the_draft_job_installs_both_dependency_sets(self):
        # ci-fast widens its own scope beyond the raw select() flags (unknown
        # paths → backend+frontend, i18n → backend), so the draft's install must
        # provision BOTH toolchains, unconditionally, or the widened run fails on
        # a missing dependency. Run the step's shell and record what it invoked.
        body = run_body_containing(self.text, "uv sync")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "backend").mkdir()
            (tmp / "frontend").mkdir()
            bindir = tmp / "bin"
            bindir.mkdir()
            install_log = tmp / "install"
            for name in ("uv", "pnpm"):
                _stub(bindir, name,
                      f'printf "%s\\t%s\\n" "$(pwd)" "$*" >> "$INSTALL_LOG"\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "INSTALL_LOG": str(install_log)}
            subprocess.run(["bash", "-c", body], env=env, cwd=tmp, check=True,
                           capture_output=True, text=True)
            # Each line is "<cwd>\t<args>": the directory the installer ran in,
            # then what it was asked to do.
            calls = [line.split("\t", 1) for line in install_log.read_text().splitlines()]
            backend = [args for cwd, args in calls if cwd.endswith("/backend")]
            frontend = [args for cwd, args in calls if cwd.endswith("/frontend")]
            self.assertTrue(any("sync" in args for args in backend),
                            f"backend toolchain not installed: {calls}")
            self.assertTrue(any("install" in args for args in frontend),
                            f"frontend toolchain not installed: {calls}")

    def test_the_two_tiers_are_two_jobs_with_literal_names(self):
        # Each tier aggregates in its own job, and each job's `name` is written
        # out literally. A computed `name:` is only rendered once GitHub
        # evaluates it, so a run nobody evaluated it for shows the expression's
        # own text as the check's name — a draft-quick run cancelled by the next
        # push left `github.event_name == 'pull_request' && ...` on the PR page,
        # where a cancelled check with a name like that reads as a red.
        jobs = jobs_of(self.text)
        aggregators = {
            job["name"]: job
            for job in jobs.values()
            if job.get("name") and "required-ci.py check" in job["text"]
        }
        # Both names, and only these: a full run under the draft name or a third
        # aggregation job would change which check branch protection reads.
        self.assertEqual(
            sorted(aggregators), ["CI required", "CI required (draft quick)"]
        )
        for name, job in aggregators.items():
            self.assertNotIn("${{", name, f"the {name!r} job computes its name")
            # One judge, both tiers: the job only hands its `needs` over and
            # required-ci.py recomputes the tier. Two sets of rules could drift.
            for suite in gate.PATHS:
                self.assertIn(suite, job["text"], f"the {name!r} job ignores {suite}")
        # Which job runs is the event's own draft flag, never a job result or a
        # scope output: if `scope` fails its `tier` output is empty, and keying
        # off it would fall through to `CI required` on a draft run — the
        # collision the two names exist to prevent.
        for name, job in aggregators.items():
            self.assertIn("github.event.pull_request.draft", job["if"], name)
            self.assertNotIn("needs", job["if"], name)
        full, draft = aggregators["CI required"], aggregators["CI required (draft quick)"]
        for event_name, is_draft, running in (
            ("pull_request", True, "draft"),
            ("pull_request", False, "full"),
            ("merge_group", None, "full"),
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
        self.assertFalse(condition_holds(full["if"], "merge_group", None, True))

    @staticmethod
    def _concurrency_group(text, event_name, ref, sha):
        """The run's concurrency group, evaluated for one event."""
        group = re.search(r"(?m)^  group: (.*)$", text).group(1).strip()

        def evaluate(match):
            expression = (match.group(1).strip()
                          .replace("github.event_name", repr(event_name))
                          .replace("github.ref", repr(ref)).replace("github.sha", repr(sha))
                          .replace("&&", " and ").replace("||", " or "))
            return str(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307

        return re.sub(r"\$\{\{(.*?)\}\}", evaluate, group)

    def test_main_pushes_queue_in_one_group_while_pull_requests_cancel(self):
        # The batching is GitHub's: one run executing, at most one pending, and a
        # newer pending run replaces the older one — which only happens inside
        # one group. One group per sha (the old shape) would run every commit and
        # never queue; cancelling a push would drop a commit's coverage.
        self.assertRegex(self.text, r"(?m)^  push:\n    branches: \[main\]$")
        first = self._concurrency_group(self.text, "push", "refs/heads/main", "a" * 40)
        second = self._concurrency_group(self.text, "push", "refs/heads/main", "b" * 40)
        self.assertEqual(first, second,
                         "each main push got its own concurrency group, so nothing queues")
        self.assertNotEqual(
            self._concurrency_group(self.text, "pull_request", "refs/pull/1/merge", "a" * 40),
            self._concurrency_group(self.text, "pull_request", "refs/pull/2/merge", "b" * 40),
            "pull requests share one group, so a new one cancels the wrong run")
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}", self.text,
            "a push must not cancel an in-flight batch (cancel-in-progress must be pull-request only)")

    def test_the_anchor_moves_only_after_a_green_main_push(self):
        job = jobs_of(self.text)["mark-green"]
        self.assertIn("needs: required", job["text"])
        self.assertIn("github.event_name == 'push'", job["if"])
        # cancelled() in an if: drops GitHub's implicit success(): a failed
        # aggregator would otherwise still move the anchor forward.
        self.assertIn("needs.required.result == 'success'", job["if"])
        self.assertIn("contents: write", job["text"])
        self.assertIn("refs/heads/main-green", job["text"])

    def test_a_failed_anchor_push_does_not_fail_the_run(self):
        # The deploy gate reads this run's conclusion, so one network error on
        # this push would hold a commit off dev. A stale anchor is safe by
        # construction — the next run diffs from the older commit — so the step
        # is loud and green instead.
        job = jobs_of(self.text)["mark-green"]
        body = run_body_containing(job["text"], "refs/heads/main-green")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            _stub(bindir, "git", 'echo "fatal: unable to access" >&2\nexit 128\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "GITHUB_SHA": "a" * 40, "GITHUB_STEP_SUMMARY": str(tmp / "summary")}
            proc = subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             "a refused anchor push failed the run it must never hold up")
            self.assertIn("::warning::", proc.stdout)
            self.assertIn("anchor", (tmp / "summary").read_text())

    def _title_step(self, tmp, event_name, env_extra):
        """Run the commit-title step's real shell against a stubbed curl."""
        body = run_body_containing(self.text, "api()")
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        _stub(bindir, "curl", (
            'out=""\nprev=""\nfor a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
            'printf "%s" \'{"commits":[{"commit":{"message":"update stuff\\nfiller"}}]}\' > "$out"\n'
            'printf 200\n'
        ))
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
               "RUNNER_TEMP": str(tmp), "GITHUB_EVENT_NAME": event_name,
               "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "x",
               "GITHUB_SHA": "h" * 40, **env_extra}
        return subprocess.run(["bash", "-e", "-c", body],
                              cwd=WORKFLOW.parent.parent.parent,
                              env=env, capture_output=True, text=True)

    def test_a_landed_title_is_reported_and_does_not_fail_the_gate(self):
        # The title is already on main and nobody can retitle it, so a red here
        # would hold back a validated commit (and dev) over formatting.
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._title_step(tmp, "push", {"PUSH_BASE_SHA": "b" * 40})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("::warning::", proc.stdout)
        self.assertNotIn("::error::", proc.stdout)

    def test_a_landed_title_without_an_anchor_is_reported_the_same_way(self):
        # The first push after the gate moved to main has no anchor; it checks
        # the push payload's own commits, still without failing the run.
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / "event.json"
            payload.write_text('{"commits": [{"message": "update stuff\\nfiller"}]}')
            proc = self._title_step(tmp, "push", {"PUSH_BASE_SHA": "", "GITHUB_EVENT_PATH": str(payload)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("::warning::", proc.stdout)

    def test_a_title_about_to_land_is_still_fatal(self):
        # In the merge queue the author can still fix it: that one is refused.
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._title_step(tmp, "merge_group", {"BASE_SHA": "b" * 40, "HEAD_SHA": "h" * 40})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("::error::", proc.stdout)

    def test_a_moved_anchor_is_silent(self):
        job = jobs_of(self.text)["mark-green"]
        body = run_body_containing(job["text"], "refs/heads/main-green")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            _stub(bindir, "git", 'exit 0\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "GITHUB_SHA": "a" * 40, "GITHUB_STEP_SUMMARY": str(tmp / "summary")}
            proc = subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("::warning::", proc.stdout)

    def test_backend_integration_is_deferred_to_merge_group(self):
        jobs = jobs_of(self.text)
        self.assertIn("branch_heavy", jobs["scope"]["text"])
        self.assertIn(
            "integration: ${{ needs.scope.outputs.branch_heavy == 'true' }}",
            jobs["backend"]["text"],
        )
        test_yml = (WORKFLOW.parent / "test.yml").read_text()
        test_jobs = jobs_of(test_yml)
        self.assertIn("integration", test_jobs)
        self.assertEqual(
            test_jobs["integration"]["if"],
            "${{ inputs.integration && github.event_name != 'pull_request' }}",
        )
        collect_body = run_body_containing(test_yml, "backend-expected")
        compare_body = run_body_containing(test_yml, "assert_shards_complete")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            uv_log = tmp / "uv_args"
            _stub(bindir, "uv", 'printf "%s\\n" "$*" >> "$UV_LOG"\n')
            _stub(bindir, "python3", "exit 0\n")
            env = {
                **os.environ,
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(tmp),
                "UV_LOG": str(uv_log),
                "PLAN_ARTIFACT": "backend-expected",
                "TEST_RESULT": "success",
            }
            subprocess.run(
                ["bash", "-e", "-c", collect_body],
                env={**env, "EXPECT_INTEGRATION": "false"},
                cwd=tmp, check=True, capture_output=True, text=True,
            )
            self.assertIn("-m not integration", uv_log.read_text())
            uv_log.write_text("")
            subprocess.run(
                ["bash", "-e", "-c", collect_body],
                env={**env, "EXPECT_INTEGRATION": "true"},
                cwd=tmp, check=True, capture_output=True, text=True,
            )
            self.assertNotIn("not integration", uv_log.read_text())

            pr_ok = subprocess.run(
                ["bash", "-e", "-c", compare_body],
                env={**env, "EXPECT_INTEGRATION": "false", "INTEGRATION_RESULT": "skipped"},
                cwd=tmp, capture_output=True, text=True,
            )
            self.assertEqual(pr_ok.returncode, 0, pr_ok.stderr)
            mg_fail = subprocess.run(
                ["bash", "-e", "-c", compare_body],
                env={**env, "EXPECT_INTEGRATION": "true", "INTEGRATION_RESULT": "skipped"},
                cwd=tmp, capture_output=True, text=True,
            )
            self.assertNotEqual(mg_fail.returncode, 0)

    def test_the_title_step_asks_curl_for_a_retry_and_keeps_the_body(self):
        # #4144: one TLS reset on the API GET reddened a merge-queue entry. The
        # shell cannot retry the request itself, so it asks curl for a retry
        # budget; run the step's real shell against a curl stub that records its
        # arguments and succeeds, and the request must arrive with that budget
        # and the body must still reach the checker verbatim.
        body = run_body_containing(self.text, "api()")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            curl_args = tmp / "curl_args"
            _stub(bindir, "curl", (
                'out=""\n'
                'prev=""\n'
                'for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                'printf "%s\\n" "$*" >> "$CURL_ARGS"\n'
                'printf "%s" \'{"number":7}\' > "$out"\n'
                'printf 200\n'
            ))
            _stub(bindir, "python3", 'printf "%s\\n" "$*" >> "$PY_ARGS"\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "RUNNER_TEMP": str(tmp), "CURL_ARGS": str(curl_args),
                   "PY_ARGS": str(tmp / "py_args"), "GITHUB_REPOSITORY": "owner/repo",
                   "PR_NUMBER": "7", "GH_TOKEN": "x",
                   "GITHUB_EVENT_NAME": "pull_request"}
            subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp, check=True,
                           capture_output=True, text=True)
            args = curl_args.read_text()
            # --retry-all-errors alone retries nothing: curl's default retry
            # count is zero, so the count is what makes a TLS reset retry.
            self.assertRegex(args, r"--retry(?:=|\s+)[1-9]\d*",
                             f"no retry count, so nothing is retried: {args}")
            self.assertIn("--retry-all-errors", args, f"no all-errors switch: {args}")
            self.assertIn("--max-time", args, f"no bound on a hung read: {args}")
            self.assertEqual((tmp / "pull.json").read_text(), '{"number":7}',
                             "the response did not reach the checker verbatim")

    def test_the_title_step_does_not_hand_python_an_empty_body(self):
        # A read that really fails is reported where it happened and exits
        # there. The merge_group branch used to pipe curl straight into python,
        # which turned the network error into a JSONDecodeError on "".
        body = run_body_containing(self.text, "api()")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            _stub(bindir, "curl",
                  'printf "curl: (35) TLS connect error\\n" >&2\nexit 35\n')
            _stub(bindir, "python3", 'printf "%s\\n" "$*" >> "$PY_ARGS"\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "RUNNER_TEMP": str(tmp), "PY_ARGS": str(tmp / "py_args"),
                   "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "x",
                   "BASE_SHA": "b" * 40, "HEAD_SHA": "h" * 40,
                   "GITHUB_EVENT_NAME": "merge_group"}
            proc = subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp,
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0, "a failed read did not fail the step")
            self.assertFalse((tmp / "py_args").exists(),
                             "python was reached after a failed read")
            self.assertIn("curl exit 35", proc.stderr)
            self.assertIn("HTTP", proc.stderr)

    def test_the_scope_step_retries_a_failed_base_fetch(self):
        # The base fetch is the same single shot on the same pool, and every
        # selected suite waits on it: a transient network error there reddens
        # the whole run.
        body = run_body_containing(self.text, "required-ci.py scope")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            git_calls = tmp / "git_calls"
            _stub(bindir, "git", (
                'n=$(cat "$GIT_CALLS" 2>/dev/null || echo 0)\n'
                'n=$((n + 1))\n'
                'printf "%s" "$n" > "$GIT_CALLS"\n'
                '[ "$n" -ge 2 ] || { echo "fatal: unable to access" >&2; exit 128; }\n'
            ))
            _stub(bindir, "python3", "exit 0\n")
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "GIT_CALLS": str(git_calls), "EVENT_BASE": "b" * 40}
            proc = subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(git_calls.read_text(), "2",
                             "the base fetch was not retried")

    def test_a_main_push_does_not_fetch_a_base_the_event_does_not_name(self):
        # A push has no base worth fetching: github.event.before is only the
        # previous tip, and the script resolves `main-green` itself. A step that
        # still fetched the event base would pay a network call, and on an
        # anchor-less first push it would fail on an empty ref.
        body = run_body_containing(self.text, "required-ci.py scope")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            git_calls = tmp / "git_calls"
            _stub(bindir, "git", 'printf "%s\\n" "$*" >> "$GIT_CALLS"\n')
            _stub(bindir, "python3", 'printf "%s\\n" "$*" >> "$PY_CALLS"\n')
            env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                   "GIT_CALLS": str(git_calls), "PY_CALLS": str(tmp / "py_calls"),
                   "GITHUB_EVENT_NAME": "push", "EVENT_BASE": ""}
            proc = subprocess.run(["bash", "-e", "-c", body], env=env, cwd=tmp,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(git_calls.exists(), "a push fetched a base it cannot use")
            self.assertIn("scope", (tmp / "py_calls").read_text())


    def test_required_backend_download_retries_once_after_cleaning_a_failure(self):
        required = jobs_of(self.text)["required"]
        steps = steps_of(required["text"])
        downloads = [step for step in steps
                     if step.get("uses", "").startswith("actions/download-artifact@")]
        self.assertEqual(len(downloads), 2, "the failed download must get exactly one retry")
        first, retry = downloads
        self.assertEqual(first["id"], "download_backend_artifacts")
        self.assertEqual(first["if"], "needs.backend.result == 'success'")
        self.assertEqual(first["continue-on-error"], "true")
        self.assertNotIn("continue-on-error", retry,
                         "a failed retry must fail CI required")
        self.assertEqual(first["uses"], retry["uses"],
                         "the retry must keep the pinned download action")
        self.assertEqual(required["text"].count("pattern: backend-*"), 2)
        self.assertEqual(required["text"].count("path: ${{ runner.temp }}/backend-artifacts"), 2)

        clear = next(step for step in steps
                     if step.get("id") == "clear_backend_artifacts")
        self.assertEqual(clear["if"],
                         "steps.download_backend_artifacts.outcome == 'failure'")
        self.assertEqual(clear["run"], 'rm -rf "$RUNNER_TEMP/backend-artifacts"')
        self.assertNotIn("continue-on-error", clear,
                         "a failed cleanup must fail CI required")
        self.assertEqual(
            retry["if"],
            "steps.download_backend_artifacts.outcome == 'failure' && "
            "steps.clear_backend_artifacts.outcome == 'success'",
        )

        coverage = next(step for step in steps
                        if step.get("name") == "Backend coverage complete")
        self.assertLess(steps.index(first), steps.index(clear))
        self.assertLess(steps.index(clear), steps.index(retry))
        self.assertLess(steps.index(retry), steps.index(coverage))
        self.assertEqual(coverage["if"], "needs.backend.result == 'success'")
        self.assertNotIn("continue-on-error", coverage,
                         "incomplete shard coverage must fail CI required")

    def test_required_backend_artifact_cleanup_failure_is_a_hard_failure(self):
        steps = steps_of(jobs_of(self.text)["required"]["text"])
        clear = next(step for step in steps
                     if step.get("id") == "clear_backend_artifacts")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            _stub(bindir, "rm", "exit 1\n")
            proc = subprocess.run(
                ["bash", "-e", "-c", clear["run"]],
                env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
                     "RUNNER_TEMP": str(tmp)},
                cwd=tmp, capture_output=True, text=True,
            )
        self.assertNotEqual(proc.returncode, 0, "cleanup failure was swallowed")
        self.assertNotIn("continue-on-error", clear)

    def test_required_ci_checks_backend_shard_coverage_exactly_once(self):
        """Every collected backend case ran on exactly one shard — checked by
        test.yml's own `test-complete` job, or, when Required CI turns that job
        off, by `CI required` itself. Never by neither."""
        jobs = jobs_of(self.text)
        delegated = "coverage-job: false" in jobs["backend"]["text"]
        test_jobs = jobs_of((WORKFLOW.parent / "test.yml").read_text())
        self.assertIn("inputs.coverage-job", test_jobs["test-complete"]["if"])
        self.assertEqual(
            "assert_shards_complete" in jobs["required"]["text"], delegated,
            "CI required must run the shard-coverage check exactly when it "
            "turns test.yml's test-complete off",
        )
        if not delegated:
            return
        body = run_body_containing(self.text, "assert_shards_complete")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bindir = tmp / "bin"
            bindir.mkdir()
            args_log = tmp / "args"
            env = {
                **os.environ,
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(tmp),
                "ARGS_LOG": str(args_log),
            }
            for verdict, expect_ok in ((0, True), (1, False)):
                _stub(bindir, "python3", f'printf "%s\\n" "$*" > "$ARGS_LOG"\nexit {verdict}\n')
                done = subprocess.run(
                    ["bash", "-e", "-c", body], env=env, cwd=tmp,
                    capture_output=True, text=True,
                )
                self.assertEqual(done.returncode == 0, expect_ok, done.stderr)
                self.assertEqual(
                    args_log.read_text().split(),
                    ["-m", "scripts.assert_shards_complete",
                     f"{tmp}/backend-artifacts", "--plan", "backend-expected"],
                )


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
            proc = subprocess.run(
                [sys.executable, str(tmp / "required-ci.py"), "scope", base],
                env=env, cwd=tmp, check=True, capture_output=True, text=True,
            )
            written = dict(line.split("=", 1) for line in out.read_text().splitlines())
            # The changed backend file selects backend and guards on pull_request,
            # while backend integration, e2e and remote are left to merge_group;
            # a draft event makes it the quick tier.
            self.assertEqual(written["tier"], "quick")
            self.assertEqual(written["branch_heavy"], "false")
            self.assertEqual(written["backend"], "true")
            self.assertEqual(written["e2e"], "false")
            self.assertEqual(written["remote"], "false")
            self.assertEqual(written["cifast"], "false")
            self.assertIn("e2e/remote 留给落码后的 run", proc.stdout)
            # The api job compares the document against the commit this diff
            # was taken from, so the two cannot be answering different questions.
            self.assertEqual(written["base"], base)
            self.assertTrue(all(v in ("true", "false") for k, v in written.items()
                                if k not in ("tier", "base")))

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
        scripts = WORKFLOW.parent.parent / "scripts"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ("required-ci.py", "required-ci-paths.json"):
                shutil.copy(scripts / name, tmp / name)
            git = self._push_repo(tmp)
            git("update-ref", "refs/heads/main-green", "HEAD")
            anchor = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
            changed = tmp / "backend/app/api/rooms.py"
            changed.parent.mkdir(parents=True)
            changed.write_text("x = 1\n")
            git("add", "backend/app/api/rooms.py")
            git("commit", "-qm", "change a backend file")

            proc, written = self._push_scope(tmp, self._event_file(tmp, {}))
            self.assertEqual(written["base"], anchor)
            self.assertEqual(written["tier"], "full")
            self.assertEqual(written["branch_heavy"], "true")
            self.assertEqual(written["backend"], "true")
            self.assertIn("last green gate result", proc.stdout)

    def test_a_main_push_without_an_anchor_scopes_every_tracked_path(self):
        # The first push after the gate moved to main has no anchor to diff
        # from; a narrower guess would let a commit no run covered go untested.
        scripts = WORKFLOW.parent.parent / "scripts"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name in ("required-ci.py", "required-ci-paths.json"):
                shutil.copy(scripts / name, tmp / name)
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
            self.assertIn("no refs/heads/main-green anchor yet", proc.stdout)

    def test_pull_request_defers_e2e_and_remote_while_merge_group_requires_them(self):
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

            git("init", "-q", "-b", "main")
            git("remote", "add", "origin", str(tmp))
            (tmp / "base.txt").write_text("base\n")
            git("add", "base.txt")
            git("commit", "-qm", "base")
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()
            git("checkout", "-qb", "entry")
            for rel in ("backend/app/api/rooms.py", "e2e/tests/login.spec.ts"):
                target = tmp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x = 1\n")
                git("add", rel)
            git("commit", "-qm", "touch backend and e2e")

            pr_out = tmp / "pr_output"
            pr_env = {
                **os.environ,
                "GITHUB_EVENT_NAME": "pull_request",
                "GITHUB_EVENT_PATH": self._event_file(tmp, {"pull_request": {"draft": False}}),
                "GITHUB_OUTPUT": str(pr_out),
            }
            pr_scope = subprocess.run(
                [sys.executable, str(tmp / "required-ci.py"), "scope", base],
                env=pr_env, cwd=tmp, check=True, capture_output=True, text=True,
            )
            pr_written = dict(line.split("=", 1) for line in pr_out.read_text().splitlines())
            self.assertEqual(pr_written["tier"], "full")
            self.assertEqual(pr_written["branch_heavy"], "false")
            self.assertEqual(pr_written["backend"], "true")
            self.assertEqual(pr_written["e2e"], "false")
            self.assertEqual(pr_written["remote"], "false")
            self.assertIn("e2e/remote 留给落码后的 run", pr_scope.stdout)

            pr_needs = {
                "scope": {"result": "success", "outputs": pr_written},
                "fast": {"result": "skipped"},
                "api": {"result": "success", "outputs": {"moved": "false"}},
                **{
                    k: {"result": "success" if pr_written[k] == "true" else "skipped"}
                    for k in gate.PATHS
                },
            }
            pr_check = subprocess.run(
                [sys.executable, str(tmp / "required-ci.py"), "check"],
                env={**pr_env, "CI_NEEDS": json.dumps(pr_needs)},
                cwd=tmp, capture_output=True, text=True,
            )
            self.assertEqual(pr_check.returncode, 0, pr_check.stderr)

            mg_out = tmp / "mg_output"
            mg_env = {
                **os.environ,
                "GITHUB_EVENT_NAME": "merge_group",
                "GITHUB_EVENT_PATH": self._event_file(tmp, {}),
                "GITHUB_OUTPUT": str(mg_out),
            }
            mg_scope = subprocess.run(
                [sys.executable, str(tmp / "required-ci.py"), "scope", base],
                env=mg_env, cwd=tmp, check=True, capture_output=True, text=True,
            )
            mg_written = dict(line.split("=", 1) for line in mg_out.read_text().splitlines())
            self.assertEqual(mg_written["branch_heavy"], "true")
            self.assertEqual(mg_written["e2e"], "true")
            self.assertEqual(mg_written["remote"], "true")
            self.assertNotIn("e2e/remote 留给落码后的 run", mg_scope.stdout)

            mg_needs_skipped = {
                "scope": {"result": "success", "outputs": mg_written},
                "fast": {"result": "skipped"},
                "api": {"result": "success", "outputs": {"moved": "false"}},
                **{
                    k: {"result": "success" if mg_written[k] == "true" else "skipped"}
                    for k in gate.PATHS
                },
            }
            mg_needs_skipped["e2e"]["result"] = "skipped"
            mg_check_fail = subprocess.run(
                [sys.executable, str(tmp / "required-ci.py"), "check"],
                env={**mg_env, "CI_NEEDS": json.dumps(mg_needs_skipped)},
                cwd=tmp, capture_output=True, text=True,
            )
            self.assertNotEqual(mg_check_fail.returncode, 0)

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

    def test_check_mode_fails_closed_when_needs_is_not_an_object(self):
        # `toJSON(needs)` is always an object, so this is unreachable in a real
        # run — but a malformed payload must still be a clear failure, not a
        # traceback, and never a pass. (null and [] are the two honest shapes of
        # "no needs object".)
        script = WORKFLOW.parent.parent / "scripts" / "required-ci.py"
        for payload in ("null", "[]"):
            result = subprocess.run(
                [sys.executable, str(script), "check"],
                env={**os.environ, "GITHUB_EVENT_NAME": "merge_group", "CI_NEEDS": payload},
                cwd=WORKFLOW.parent.parent.parent, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, f"CI_NEEDS={payload} should fail")
            self.assertIn("needs is not an object", result.stderr, f"CI_NEEDS={payload}")
            self.assertNotIn("Traceback", result.stderr, f"CI_NEEDS={payload}")


if __name__ == "__main__":
    unittest.main()
