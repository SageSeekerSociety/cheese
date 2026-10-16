#!/usr/bin/env python3
"""Black-box post-pull checks: python3 .claude/scripts/test_post_pull.py."""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("post-pull.sh")
UV_LOCK = "backend/uv.lock"
PNPM_LOCK = "frontend/pnpm-lock.yaml"
BASELINES = ("frontend/import-boundary-baseline.json", "frontend/a11y-baseline.json")


class PostPullTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="post-pull-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "POST_PULL_"))
        }
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="Post Pull Test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Post Pull Test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            POST_PULL_TEST_LOG=str(self.log),
        )
        for tool in ("uv", "pnpm", "curl"):
            self.stub(self.bin / tool, tool)
        self.git("init", "--quiet")
        for path in (UV_LOCK, PNPM_LOCK, *BASELINES, "backend/pyproject.toml", "README.md"):
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("initial\n")
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "initial")

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, env=self.env, text=True, stderr=subprocess.PIPE
        ).strip()

    def stub(self, path: Path, tool: str) -> None:
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "with open(os.environ['POST_PULL_TEST_LOG'], 'a') as stream:\n"
            f"    stream.write(json.dumps([{tool!r}, sys.argv[1:], os.getcwd()]) + '\\n')\n"
            f"if os.environ.get('POST_PULL_TEST_FAIL') == {tool!r}:\n"
            "    sys.exit(23)\n"
        )
        path.chmod(0o755)

    def advance(self, paths: tuple[str, ...]) -> None:
        before = self.git("rev-parse", "HEAD")
        for path in paths:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as stream:
                stream.write("changed\n")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", "pull result")
        self.git("update-ref", "ORIG_HEAD", before)

    def run_script(self, *, success: bool = True) -> tuple[subprocess.CompletedProcess[str], list]:
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.repo / "frontend",
            env=self.env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result, calls

    def assert_effects(self, paths: tuple[str, ...]) -> None:
        result, calls = self.run_script()
        expected = [["uv", ["run", "alembic", "upgrade", "head"], str(self.repo / "backend")]]
        if UV_LOCK in paths or "backend/pyproject.toml" in paths:
            expected.append(["uv", ["sync"], str(self.repo / "backend")])
        if PNPM_LOCK in paths:
            expected.append(["pnpm", ["--dir", "frontend", "install", "--frozen-lockfile"], str(self.repo)])
        expected.append(["curl", ["-sf", "http://localhost:8081/healthz"], str(self.repo / "backend")])
        self.assertEqual(calls, expected)
        warnings = [line for line in result.stdout.splitlines() if "WARNING:" in line]
        changed_baselines = [path for path in BASELINES if path in paths]
        self.assertEqual(len(warnings), int(bool(changed_baselines)))
        for baseline in BASELINES:
            self.assertEqual(baseline in "\n".join(warnings), baseline in paths)

    def test_changes_and_nonchanges_are_independent(self) -> None:
        watched = (UV_LOCK, PNPM_LOCK, *BASELINES)
        for flags in itertools.product((False, True), repeat=len(watched)):
            paths = tuple(path for path, changed in zip(watched, flags) if changed)
            with self.subTest(paths=paths):
                self.advance(paths)
                self.assert_effects(paths)

    def test_unrelated_and_similarly_named_files_do_not_trigger(self) -> None:
        paths = (
            "README.md",
            "other/uv.lock",
            "other/pyproject.toml",
            "desktop/pnpm-lock.yaml",
            "frontend/pnpm-lock.yaml.backup",
            "frontend/import-boundary-baseline.json.backup",
            "frontend/a11y-baseline.json.backup",
            "backend/.boundary-baseline.json",
            "frontend/.a11y-baseline.json",
        )
        self.advance(paths)
        self.assert_effects(paths)

    def test_backend_manifest_still_syncs(self) -> None:
        paths = ("backend/pyproject.toml",)
        self.advance(paths)
        self.assert_effects(paths)

    def test_uses_orig_head_not_previous_reflog_entry(self) -> None:
        before = self.git("rev-parse", "HEAD")
        self.advance((UV_LOCK, PNPM_LOCK, *BASELINES))
        self.advance(("README.md",))
        self.git("update-ref", "ORIG_HEAD", before)
        self.assert_effects((UV_LOCK, PNPM_LOCK, *BASELINES))

    def test_command_overrides_are_executable_paths(self) -> None:
        for tool in ("uv", "pnpm"):
            override = self.root / f"override {tool}"
            self.stub(override, tool)
            self.env[f"POST_PULL_{tool.upper()}"] = str(override)
            (self.bin / tool).write_text("#!/bin/sh\nexit 99\n")
        paths = (UV_LOCK, PNPM_LOCK)
        self.advance(paths)
        self.assert_effects(paths)

    def test_deleted_baseline_is_reported(self) -> None:
        before = self.git("rev-parse", "HEAD")
        (self.repo / BASELINES[0]).unlink()
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "remove baseline")
        self.git("update-ref", "ORIG_HEAD", before)
        self.assert_effects((BASELINES[0],))

    def test_renamed_watched_files_are_still_detected(self) -> None:
        paths = (UV_LOCK, PNPM_LOCK, *BASELINES)
        before = self.git("rev-parse", "HEAD")
        for path in paths:
            self.git("mv", path, f"{path}.bak")
        self.git("commit", "--quiet", "-m", "rename watched files")
        self.git("update-ref", "ORIG_HEAD", before)
        self.assert_effects(paths)

    def test_migration_notice_lists_changed_paths(self) -> None:
        path = "backend/alembic/versions/new_revision.py"
        self.advance((path,))
        result, _ = self.run_script()
        self.assertIn(path, result.stdout)
        self.assertNotIn("No new migrations", result.stdout)

    def test_migration_failure_stops_later_checks(self) -> None:
        self.advance((PNPM_LOCK,))
        self.env["POST_PULL_TEST_FAIL"] = "uv"
        result, calls = self.run_script(success=False)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertEqual([call[0] for call in calls], ["uv"])
        self.assertNotIn("=== Done ===", result.stdout)

    def test_unavailable_health_endpoint_is_nonfatal(self) -> None:
        self.advance(())
        self.env["POST_PULL_TEST_FAIL"] = "curl"
        result, calls = self.run_script()
        self.assertEqual(calls[-1][0], "curl")
        self.assertIn("SKIP:", result.stdout)
        self.assertIn("=== Done ===", result.stdout)

    def test_missing_orig_head_is_not_reported_as_no_changes(self) -> None:
        result, calls = self.run_script(success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ORIG_HEAD", result.stdout + result.stderr)
        self.assertEqual(calls, [])

    def test_install_failure_stops_with_nonzero_status(self) -> None:
        self.advance((PNPM_LOCK,))
        self.env["POST_PULL_TEST_FAIL"] = "pnpm"
        result, calls = self.run_script(success=False)
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("pnpm", [call[0] for call in calls])
        self.assertNotIn("=== Done ===", result.stdout)


if __name__ == "__main__":
    unittest.main()
