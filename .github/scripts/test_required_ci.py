import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "required_ci", Path(__file__).with_name("required-ci.py")
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class RequiredCITest(unittest.TestCase):
    def test_main_parent_preserves_standalone_push_selections(self):
        # Representative files from every former standalone main-push pattern.
        previous = {
            "backend": ["backend/app/main.py", "deploy/metering-proxy/proxy.py",
                        ".pre-commit-config.yaml", ".github/scripts/ensure-apt.sh",
                        ".github/workflows/test.yml"],
            "frontend": ["frontend/src/main.ts", ".github/workflows/frontend.yml"],
            "e2e": ["backend/app/main.py", "frontend/src/main.ts", "e2e/tests/login.ts",
                    ".github/scripts/ci-test-evidence.py", ".github/workflows/e2e.yml"],
            "cli": ["cli/main.go", "backend/tests/fixtures/wire/frame.json",
                    ".github/workflows/cli.yml"],
            "guards": ["backend/app/main.py", "frontend/src/main.ts", "deploy/run.sh",
                       ".claude/scripts/check.sh", ".pre-commit-config.yaml",
                       ".github/workflows/test.yml", ".github/scripts/check.py"],
            "deploy": ["deploy/run.sh", ".github/workflows/deploy-scripts-test.yml",
                       ".github/workflows/deploy-dev.yml", ".github/workflows/deploy-drift.yml",
                       ".github/workflows/build.yml", ".github/scripts/plan-image-builds.sh",
                       ".github/scripts/ensure-apt.sh", ".github/scripts/test-plan-image-builds.sh",
                       "backend/scripts/gateway_supply_probe.py",
                       "backend/scripts/test_gateway_supply_probe.py"],
            "harness": ["backend/uv.lock", "backend/app/domain/agent/harness/claude_code/device_launch.py",
                        "backend/app/domain/agent/harness/codex/host.py",
                        ".github/workflows/harness-contract.yml"],
            "mcp": ["scripts/remote_execution/package-lock.json", ".github/workflows/mcp-contract.yml",
                    "scripts/remote_execution/headless_contract.py"],
        }
        for suite, paths in previous.items():
            for path in paths:
                with self.subTest(suite=suite, path=path):
                    self.assertTrue(gate.select([path])[suite])

    def test_remote_execution_plugin_runs_the_build_contracts(self):
        # headless_contract.py launches -p through the plugin and the executor.
        for path in (
            "backend/app/domain/agent/harness/claude_code/remote_execution/proxy.js",
            "backend/app/domain/agent/harness/claude_code/remote_execution/client.py",
            "backend/app/domain/agent/executor_transport.py",
            "scripts/remote_execution/acceptance.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.select([path])["mcp"])

    def test_documentation_still_runs_guards(self):
        selected = gate.select(["docs/architecture.md"])
        self.assertEqual({k for k, v in selected.items() if v}, {"guards"})

    def test_backend_change_selects_backend_and_e2e(self):
        selected = gate.select(["backend/app/api/rooms.py"])
        self.assertTrue(selected["backend"] and selected["e2e"])
        self.assertFalse(selected["frontend"])

    def test_combined_merge_group_checks_every_changed_area(self):
        selected = gate.select(["frontend/src/main.ts", "cli/main.go"])
        self.assertTrue(all(selected[k] for k in ("frontend", "cli", "e2e")))

    def test_gate_edit_exercises_all_suites(self):
        self.assertTrue(all(gate.select([".github/workflows/required-ci.yml"]).values()))

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
        selected = gate.select(["backend/app/api/rooms.py"])
        return {
            "scope": {"result": "success", "outputs": {
                k: str(v).lower() for k, v in selected.items()
            }},
            **{k: {"result": "success" if v else "skipped"} for k, v in selected.items()},
        }

    def test_intentionally_skipped_suites_do_not_block(self):
        self.assertEqual(gate.failures(self.needs()), [])

    def test_failed_cancelled_skipped_or_missing_selected_suite_blocks(self):
        for result in ("failure", "cancelled", "skipped", "missing"):
            with self.subTest(result=result):
                needs = self.needs()
                needs["backend"]["result"] = result
                self.assertTrue(gate.failures(needs))

    def test_failed_scope_or_missing_output_blocks(self):
        needs = self.needs()
        needs["scope"]["result"] = "failure"
        self.assertTrue(gate.failures(needs))
        needs = self.needs()
        del needs["scope"]["outputs"]["backend"]
        self.assertTrue(gate.failures(needs))


if __name__ == "__main__":
    unittest.main()
