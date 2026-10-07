"""Black-box tests for ci-fast.py.

These drive the script as a subprocess against real git repositories built in
tmp dirs, and assert on observable outcomes: exit codes, stdout, the JSON
report, and which stub hooks actually executed (recorded by the stubs
themselves). They never import ci-fast or re-assert its internals — a test
that mirrors the implementation cannot catch the implementation being wrong.

Stub hooks are `language: system` entries in the fixture's own
.pre-commit-config.yaml: the command table still comes from a config file,
and pre-commit is the real scheduler. Set CI_FAST_PRECOMMIT to a pre-commit
invocation available on the machine (CI provides it via uv — `uvx pre-commit`
— in every workflow whose discover picks this file up).
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("ci-fast.py")

ALL_HOOKS = [
    "ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork",
    "eslint", "stylelint", "boundary-check-frontend", "scene-ratchet", "catalog-ratchet",
    "repo-rules", "action-pins", "manual-anchors", "file-size",
    "pyright", "frontend-typecheck",
]
BACKEND_HOOKS = {"ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork"}
FRONTEND_HOOKS = {"eslint", "stylelint", "boundary-check-frontend", "scene-ratchet", "catalog-ratchet"}
GUARD_HOOKS = {"repo-rules", "action-pins", "manual-anchors", "file-size"}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class CiFastTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ci-fast-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # pre-commit keeps a sqlite store under PRE_COMMIT_HOME (default
        # ~/.cache/pre-commit). Merge-queue runs execute on the shared
        # self-hosted runners as one user, several queue entries at a time, and
        # this file's runs have failed with "database is locked" on that one
        # store. The hooks here are local stubs that install nothing, so each
        # test gets a store of its own.
        self.env = {**os.environ, "PRE_COMMIT_HOME": str(self.tmp / "pre-commit-home")}
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "backend/.venv").mkdir(parents=True)
        (self.repo / "frontend/node_modules").mkdir(parents=True)
        (self.repo / "backend/app").mkdir(parents=True)
        (self.repo / "frontend/src").mkdir(parents=True)

    def write_hooks(self, failing=(), omit=(), extra_entries=""):
        entries = []
        for hook_id in ALL_HOOKS:
            if hook_id in omit:
                continue
            exit_code = 1 if hook_id in failing else 0
            entries.append(
                f"""      - id: {hook_id}
        name: {hook_id} stub
        entry: bash -c 'echo {hook_id} >> .ci-fast-ran; echo {hook_id}-output; exit {exit_code}'
        language: system
        pass_filenames: false
        always_run: true
"""
            )
        (self.repo / ".pre-commit-config.yaml").write_text(
            "repos:\n  - repo: local\n    hooks:\n" + "".join(entries) + extra_entries
        )

    def commit_file(self, path, content="x"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", f"touch {path}")

    def make_base_and_head(self, head_path="backend/app/x.py"):
        self.commit_file("backend/app/base.py")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit_file(head_path)

    def run_ci_fast(self, *extra, env_extra=None):
        env = dict(self.env)
        env.setdefault("CI_FAST_PRECOMMIT", "pre-commit")
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra],
            cwd=self.repo, capture_output=True, text=True, env=env, timeout=120,
        )

    def ran_hooks(self):
        marker = self.repo / ".ci-fast-ran"
        return set(marker.read_text().split()) if marker.exists() else set()

    def report(self):
        path = self.repo / ".git/ci-fast-report.json"
        return json.loads(path.read_text()) if path.exists() else None

    # --- the happy path: scope selects, exit 0, report says what did not run
    def test_pass_scopes_backend_change(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("已执行静态检查 通过", proc.stdout)
        self.assertTrue(BACKEND_HOOKS <= self.ran_hooks())
        self.assertTrue(GUARD_HOOKS <= self.ran_hooks())
        self.assertFalse(FRONTEND_HOOKS & self.ran_hooks())
        rep = self.report()
        self.assertEqual(rep["status"], "pass")
        self.assertEqual(rep["exit"], 0)
        self.assertEqual(rep["dirty"]["count"], 0)
        self.assertTrue(rep["base"]["merge_base"])
        not_run = {n["id"] for n in rep["not_run"]}
        self.assertIn("pytest", not_run)
        self.assertIn("typecheck", not_run)
        self.assertIn("不代表完整 CI 通过", proc.stdout)

    # --- a failed hook must fail the run and surface the hook's own output
    def test_failing_hook_exit_1(self):
        self.write_hooks(failing={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("失败", proc.stdout)
        self.assertIn("ruff-output", proc.stdout)  # not swallowed
        self.assertEqual(self.report()["status"], "fail")

    # --- missing toolchain is blocked (3), not a pass and not a check failure
    def test_blocked_without_venv(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        shutil.rmtree(self.repo / "backend/.venv")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("task be:deps:sync", proc.stdout)
        self.assertIn("cd backend && uv sync", proc.stdout)  # task-free equivalent
        self.assertEqual(self.report()["status"], "blocked")
        self.assertEqual(self.ran_hooks(), set())

    def test_blocked_without_precommit(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast(env_extra={"CI_FAST_PRECOMMIT": "/nonexistent/pre-commit"})
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)

    # --- no merge base: selector may widen to all, but the run is NOT a pass
    def test_no_merge_base_falls_back_but_unknown(self):
        self.write_hooks()
        self.commit_file("backend/app/x.py")  # no refs/remotes/origin/main
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("无法验证", proc.stdout)
        rep = self.report()
        self.assertEqual(rep["status"], "unknown")
        self.assertTrue(rep["fallback"])
        # everything ran except the checks that themselves need the merge base
        self.assertTrue((BACKEND_HOOKS - {"migration-fork"}) | FRONTEND_HOOKS <= self.ran_hooks())
        self.assertNotIn("file-size", self.ran_hooks())
        self.assertNotIn("migration-fork", self.ran_hooks())
        skipped = {n["id"] for n in rep["not_run"]}
        self.assertIn("file-size", skipped)
        self.assertIn("migration-fork", skipped)

    # --- a custom --base cannot be honored by the base-judging hooks (they
    # --- always judge origin/main), so the run must NOT pass — exit 2
    def test_custom_base_cannot_verify_base_hooks(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        git(self.repo, "update-ref", "refs/heads/release", "HEAD~1")
        proc = self.run_ci_fast("--base", "release")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("无法验证", proc.stdout)
        self.assertNotIn("file-size", self.ran_hooks())
        self.assertNotIn("migration-fork", self.ran_hooks())
        self.assertIn("ruff", self.ran_hooks())  # base-independent hooks still ran
        rep = self.report()
        self.assertEqual(rep["status"], "unknown")
        skipped = {n["id"] for n in rep["not_run"]}
        self.assertIn("file-size", skipped)
        self.assertIn("migration-fork", skipped)

    # --- SKIP must not green a run: pre-commit returns 0 for a skipped hook
    # --- WITHOUT running it; that is recorded as skipped, never as pass
    def test_skip_env_is_not_a_pass(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast(env_extra={"SKIP": "ruff"})
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("跳过", proc.stdout)
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertEqual(results.get("ruff"), "skipped")
        self.assertNotIn("ruff", self.ran_hooks())  # the stub never executed
        self.assertEqual(self.report()["status"], "unknown")

    # --- no matching files is legitimately not_applicable: reported as such,
    # --- never counted as an execution; the other hooks really ran, so the
    # --- run still passes without pretending this one did
    def test_no_files_is_not_applicable(self):
        self.write_hooks(extra_entries="""      - id: scene-ratchet
        name: scene-ratchet filtered stub
        entry: bash -c 'echo scene-ratchet >> .ci-fast-ran'
        language: system
        files: '\\.xyz$'
""", omit={"scene-ratchet"})
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("scene-ratchet", self.ran_hooks())
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertNotIn("scene-ratchet", results)
        not_run = {n["id"]: n.get("reason", "") for n in self.report()["not_run"]}
        self.assertIn("scene-ratchet", not_run)
        self.assertIn("无相关文件", not_run["scene-ratchet"])

    # --- a color-forced SKIP must still be caught: the invocation pins
    # --- --color never (beats PRE_COMMIT_COLOR=always) and ANSI is stripped
    def test_colored_skip_is_not_a_pass(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast(env_extra={"PRE_COMMIT_COLOR": "always", "SKIP": "ruff"})
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertEqual(results.get("ruff"), "skipped")
        self.assertNotIn("ruff", self.ran_hooks())  # the stub never executed
        self.assertEqual(self.report()["status"], "unknown")

    # --- same for the no-files case under forced color: still not_applicable
    def test_colored_no_files_is_not_applicable(self):
        self.write_hooks(extra_entries="""      - id: scene-ratchet
        name: scene-ratchet filtered stub
        entry: bash -c 'echo scene-ratchet >> .ci-fast-ran'
        language: system
        files: '\\.xyz$'
""", omit={"scene-ratchet"})
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast(env_extra={"PRE_COMMIT_COLOR": "always"})
        self.assertEqual(proc.returncode, 0, proc.stdout)
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertNotIn("scene-ratchet", results)
        not_run = {n["id"]: n.get("reason", "") for n in self.report()["not_run"]}
        self.assertIn("无相关文件", not_run["scene-ratchet"])

    # --- an unknown path widens the scope; it must not shrink to guards-only
    def test_unknown_path_widens_scope(self):
        self.write_hooks()
        self.make_base_and_head("strange-dir/thing.xyz")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(FRONTEND_HOOKS & self.ran_hooks())
        self.assertTrue(BACKEND_HOOKS & self.ran_hooks())
        self.assertIn("strange-dir/thing.xyz", self.report()["unknown_paths"])

    # --- staged + unstaged + untracked all count, and the report proves it
    def test_dirty_worktree_selected_and_fingerprinted(self):
        self.write_hooks()
        self.make_base_and_head("README.md")
        (self.repo / "frontend/src/staged.ts").write_text("x")
        git(self.repo, "add", "frontend/src/staged.ts")
        (self.repo / "backend/app/base.py").write_text("edited")  # unstaged
        (self.repo / "backend/app/untracked.py").write_text("x")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(FRONTEND_HOOKS & self.ran_hooks())
        self.assertTrue(BACKEND_HOOKS & self.ran_hooks())
        rep = self.report()
        self.assertEqual(rep["dirty"]["count"], 3)
        self.assertEqual(len(rep["dirty"]["fingerprint"]), 12)

    # --- the fingerprint binds CONTENT: same path, new bytes, new fingerprint
    def test_fingerprint_changes_with_content(self):
        self.write_hooks()
        self.make_base_and_head("README.md")
        probe = self.repo / "backend/app/base.py"
        probe.write_text("version one")
        first = self.run_ci_fast()
        self.assertEqual(first.returncode, 0, first.stdout)
        fp_one = self.report()["dirty"]["fingerprint"]
        probe.write_text("version two")
        second = self.run_ci_fast()
        self.assertEqual(second.returncode, 0, second.stdout)
        fp_two = self.report()["dirty"]["fingerprint"]
        self.assertNotEqual(fp_one, fp_two)

    # --- green requires the report on disk: an unwritable --report path
    # --- downgrades a passing run to 2 instead of exiting 0 with no report
    def test_unwritable_report_never_green(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        blocker = self.repo / "blocker"
        blocker.write_text("a file, not a directory")
        proc = self.run_ci_fast("--report", str(blocker / "report.json"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("报告写入失败", proc.stderr)
        self.assertFalse((blocker / "report.json").exists())

    # --- full-tree hooks read the worktree: untracked content is CHECKED,
    # --- not merely selected on (a content-reading stub hook proves it)
    def test_untracked_content_is_checked(self):
        self.write_hooks(extra_entries="""      - id: repo-rules
        name: content-reading stub
        entry: bash -c 'echo repo-rules >> .ci-fast-ran; if grep -rq LINTPROBE backend/; then echo found-probe; exit 1; fi'
        language: system
        pass_filenames: false
        always_run: true
""", omit={"repo-rules"})
        self.make_base_and_head("README.md")
        (self.repo / "backend/app/probe.py").write_text("# LINTPROBE untracked")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("found-probe", proc.stdout)

    # --- deletion and rename move both the old and the new path into scope
    def test_delete_and_rename_hit_both_sides(self):
        self.write_hooks()
        self.commit_file("backend/app/old.py")
        self.commit_file("frontend/src/old.ts")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.repo, "rm", "-q", "backend/app/old.py")
        git(self.repo, "mv", "frontend/src/old.ts", "frontend/src/new.ts")
        git(self.repo, "commit", "-m", "delete and rename")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(BACKEND_HOOKS & self.ran_hooks())
        self.assertTrue(FRONTEND_HOOKS & self.ran_hooks())

    # --- a stale report is deleted up front: a failing run cannot leave "pass"
    def test_stale_report_never_survives(self):
        self.write_hooks(failing={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        report_path = self.repo / ".git/ci-fast-report.json"
        report_path.write_text(json.dumps({"status": "pass", "exit": 0}))
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(self.report()["status"], "fail")

    # --- a hung hook is a timeout (unknown), not a pass and not a hang
    def test_hook_timeout_is_unknown(self):
        self.write_hooks(extra_entries="""      - id: repo-rules
        name: slow stub
        entry: bash -c 'echo repo-rules >> .ci-fast-ran; sleep 10'
        language: system
        pass_filenames: false
        always_run: true
""", omit={"repo-rules"})
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast("--timeout", "2")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("超时", proc.stdout)
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertEqual(results.get("repo-rules"), "timeout")

    # --- config drift between the hook map and .pre-commit-config.yaml is
    # --- unknown (2), loudly — never a silent skip
    def test_hook_id_missing_from_config(self):
        self.write_hooks(omit={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("ruff", proc.stdout)
        self.assertEqual(self.report()["status"], "unknown")

    # --- --types opts into the manual-stage type checks
    def test_types_flag_runs_pyright(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast("--types")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("pyright", self.ran_hooks())

    # --- linked worktrees: .git is a file there, the report must still land
    # --- in the real git dir and the run must pass (agents live in worktrees)
    def test_linked_worktree_run(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        worktree = self.tmp / "linked"
        git(self.repo, "worktree", "add", "--detach", str(worktree), "HEAD")
        # a fresh worktree has no venv/node_modules (untracked state is not
        # shared) — the preflight must see them, exactly like after setup
        (worktree / "backend/.venv").mkdir(parents=True)
        (worktree / "frontend/node_modules").mkdir(parents=True)
        # the worktree shares refs, including refs/remotes/origin/main
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=worktree, capture_output=True, text=True,
            env={**self.env, "CI_FAST_PRECOMMIT": os.environ.get("CI_FAST_PRECOMMIT", "pre-commit")},
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        gitdir = subprocess.check_output(
            ["git", "rev-parse", "--git-dir"], cwd=worktree, text=True
        ).strip()
        rep = json.loads((Path(gitdir) / "ci-fast-report.json").read_text())
        self.assertEqual(rep["status"], "pass")


if __name__ == "__main__":
    unittest.main()
