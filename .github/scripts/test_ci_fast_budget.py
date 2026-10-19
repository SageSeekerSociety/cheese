"""Exercise budget selection through the CLI and its content-bound report."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("ci-fast.py")


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ci-fast-budget-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "CI_FAST_", "PYTHON_CPU_COUNT"))}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Test", GIT_COMMITTER_NAME="Test",
                        GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_EMAIL="test@example.com")
        self.git("init", "-b", "main")
        hooks = ("repo-rules", "action-pins", "manual-anchors", "doc-references")
        (self.repo / ".pre-commit-config.yaml").write_text(
            "\n".join(f"- id: {hook}" for hook in hooks))
        # A scheduler stub makes the selected guard checks instantaneous. All
        # report writing, CPU detection, budget validation and deadlines are real.
        stub = self.repo / "scheduler.py"
        stub.write_text("print('guard passed')\n")
        self.env["CI_FAST_PRECOMMIT"] = shlex.join([sys.executable, str(stub)])
        self.git("add", ".pre-commit-config.yaml", "scheduler.py")
        self.git("commit", "-m", "fixture")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.repo / "README.md").write_text("Changed prose.\n")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                       check=True, capture_output=True)

    def invoke(self, *args, override=None, cores=None):
        env = dict(self.env)
        if override is not None:
            env["CI_FAST_BUDGET_SECONDS"] = override
        command = [sys.executable, str(SCRIPT), "--tier", "static", *args]
        if cores is not None:
            available = sorted(os.sched_getaffinity(0))
            if len(available) < cores:
                self.skipTest(f"requires {cores} available CPUs")
            # Restrict the child, not the runner or the other parallel tests.
            command = [sys.executable, "-I", "-c",
                       "import os,sys; os.sched_setaffinity(0, "
                       f"{available[:cores]!r}); os.execve(sys.argv[1], sys.argv[1:], os.environ)",
                       *command]
        return subprocess.run(command, cwd=self.repo, env=env, capture_output=True,
                              text=True, timeout=20)

    def report(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads((self.repo / ".git/ci-fast-report.json").read_text())

    @unittest.skipUnless(hasattr(os, "sched_setaffinity"), "requires CPU affinity")
    def test_one_cpu_gets_more_time_without_reducing_checks(self):
        report = self.report(self.invoke(cores=1))
        self.assertEqual(report["budget_seconds"], 720)
        self.assertEqual({row["id"] for row in report["ran"]},
                         {"repo-rules", "action-pins", "manual-anchors", "doc-references"})
        self.assertEqual(report["budget_source"], "cpu default (1 available)")

    @unittest.skipUnless(hasattr(os, "sched_setaffinity"), "requires CPU affinity")
    def test_two_cpus_get_half_the_single_cpu_budget(self):
        self.assertEqual(self.report(self.invoke(cores=2))["budget_seconds"], 360)

    @unittest.skipUnless(hasattr(os, "sched_setaffinity"), "requires CPU affinity")
    def test_four_cpus_keep_the_180_second_floor(self):
        self.assertEqual(self.report(self.invoke(cores=4))["budget_seconds"], 180)

    def test_environment_overrides_default(self):
        report = self.report(self.invoke(override="543.5"))
        self.assertEqual(report["budget_seconds"], 543.5)
        self.assertEqual(report["budget_source"], "CI_FAST_BUDGET_SECONDS")

    def test_environment_cannot_lower_the_default_floor(self):
        self.assertEqual(self.report(self.invoke(override="12"))["budget_seconds"], 180)

    def test_explicit_budget_wins_even_over_invalid_environment(self):
        report = self.report(self.invoke("--budget", "20", override="not-a-number"))
        self.assertEqual(report["budget_seconds"], 20)
        self.assertEqual(report["budget_source"], "--budget")

    def test_extended_keeps_900_unless_overridden(self):
        self.assertEqual(self.report(self.invoke("--tier", "extended", "--title", "test: bounds"))
                         ["budget_seconds"], 900)
        self.assertEqual(self.report(self.invoke("--tier", "extended", "--title", "test: bounds",
                                                override="1200"))["budget_seconds"], 1200)

    def test_invalid_environment_is_a_usage_error(self):
        for value in ("", "no", "nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value):
                proc = self.invoke(override=value)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("CI_FAST_BUDGET_SECONDS must be finite and positive", proc.stderr)
                self.assertFalse((self.repo / ".git/ci-fast-report.json").exists())

    def test_nonfinite_explicit_bounds_are_rejected(self):
        for flag in ("--budget", "--timeout"):
            for value in ("nan", "inf", "-inf"):
                with self.subTest(flag=flag, value=value):
                    proc = self.invoke(f"{flag}={value}")
                    self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                    self.assertIn("finite positive time limits", proc.stderr)

    def test_explicit_tiny_budget_still_reports_unknown(self):
        proc = self.invoke("--budget", "0.000001")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        report = json.loads((self.repo / ".git/ci-fast-report.json").read_text())
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["budget_seconds"], 0.000001)


if __name__ == "__main__":
    unittest.main()
