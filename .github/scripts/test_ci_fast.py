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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).with_name("ci-fast.py")

ALL_HOOKS = [
    "ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork",
    "eslint", "stylelint", "boundary-check-frontend", "scene-ratchet", "catalog-ratchet",
    "repo-rules", "action-pins", "manual-anchors", "file-size", "doc-references",
    "pyright", "frontend-typecheck",
]
BACKEND_HOOKS = {"ruff", "ruff-format", "boundary-check", "deferred-imports", "migration-fork"}
FRONTEND_HOOKS = {"eslint", "stylelint", "boundary-check-frontend", "scene-ratchet", "catalog-ratchet"}
GUARD_HOOKS = {"repo-rules", "action-pins", "manual-anchors", "file-size", "doc-references"}


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _cores():
    """The core count the code under test will see.

    Not the host's online cores: `ci_fast_checks._cores` prefers the process's
    own affinity/CPU count. A skip decided on a different number would run a
    core-capped assertion on a machine whose cgroup or cpuset is narrower than
    the host, where it can only fail.
    """
    from ci_fast_checks import _cores as cores
    return cores()


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
        (self.repo / ".gitignore").write_text(".ci-fast-ran\n")
        (self.repo / "backend/.venv").mkdir(parents=True)
        (self.repo / "frontend/node_modules").mkdir(parents=True)
        (self.repo / "backend/app").mkdir(parents=True)
        (self.repo / "frontend/src").mkdir(parents=True)

    def write_hooks(self, failing=(), omit=(), slow=(), extra_entries=""):
        entries = []
        for hook_id in ALL_HOOKS:
            if hook_id in omit:
                continue
            exit_code = 1 if hook_id in failing else 0
            tail = "sleep 30; " if hook_id in slow else ""
            entries.append(
                f"""      - id: {hook_id}
        name: {hook_id} stub
        entry: bash -c 'echo {hook_id} >> .ci-fast-ran; {tail}echo {hook_id}-output; exit {exit_code}'
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
            [sys.executable, str(SCRIPT), "--tier", "static", *extra],
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

    # --- a stray SKIP in the caller's shell must NOT silently skip a nested
    # --- check: the runner scrubs verdict-altering variables, so the hook
    # --- really runs and the verdict reflects the hook, not the caller's env
    def test_outer_skip_env_does_not_skip_nested_check(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast(env_extra={"SKIP": "ruff"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("跳过", proc.stdout)
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertEqual(results.get("ruff"), "pass")
        self.assertIn("ruff", self.ran_hooks())  # the stub really executed
        self.assertEqual(self.report()["status"], "pass")

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

    # --- same for the color override: PRE_COMMIT_COLOR is scrubbed too, so a
    # --- forced-colour caller cannot flip the verdict; the hook really runs
    def test_colored_override_does_not_change_verdict(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast(env_extra={"PRE_COMMIT_COLOR": "always"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        results = {r["id"]: r["result"] for r in self.report()["ran"]}
        self.assertEqual(results.get("ruff"), "pass")
        self.assertIn("ruff", self.ran_hooks())
        self.assertEqual(self.report()["status"], "pass")

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

    # --- prose is not an unknown path: a docs-only diff must not pull in the
    # product suites. The map leaves README.md/AGENTS.md/SKILL.md unnamed, so
    # before this they widened the scope to the full suite — which a quick run
    # cannot fit, so a docs-only draft answered "unknown" (2026-10-12).
    def test_docs_only_diff_does_not_widen_scope(self):
        self.write_hooks()
        self.make_base_and_head("README.md")
        for path in ("AGENTS.md", "docs/manual/dev/ci.md", ".claude/skills/x/SKILL.md"):
            self.commit_file(path)
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(FRONTEND_HOOKS & self.ran_hooks())
        self.assertFalse(BACKEND_HOOKS & self.ran_hooks())
        self.assertTrue(GUARD_HOOKS <= self.ran_hooks())
        self.assertEqual(self.report()["unknown_paths"], [])

    # --- ...but prose alongside a path the map cannot place does not excuse it
    def test_docs_next_to_an_unknown_path_still_widens(self):
        self.write_hooks()
        self.make_base_and_head("README.md")
        self.commit_file("strange-dir/thing.xyz")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(FRONTEND_HOOKS & self.ran_hooks())
        self.assertTrue(BACKEND_HOOKS & self.ran_hooks())
        self.assertEqual(self.report()["unknown_paths"], ["strange-dir/thing.xyz"])

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
            [sys.executable, str(SCRIPT), "--tier", "static"],
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

    def quick_tools(self, passed=2, skipped=0, missing_title=False, preamble=""):
        tools = self.tmp / "tools"
        tools.mkdir()
        body = f'''#!{sys.executable}
import json, pathlib, sys
args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == "gh":
    print({preamble!r})  # a platform/credential notice before the title
    print("feat: actual PR title")
    sys.exit({1 if missing_title else 0})
if "--outputFile" in args:
    target = args[args.index("--outputFile") + 1]
    json.dump({{"numPassedTests": {passed}, "numPendingTests": {skipped},
               "numFailedTests": 0, "testResults": [
               {{"name": str(pathlib.Path.cwd() / "src/i18n/catalog.spec.ts")}},
               {{"name": str(pathlib.Path.cwd() / "src/i18n/english.spec.ts")}}]}}, open(target, "w"))
if "--project" in args:
    config = json.load(open(args[args.index("--project") + 1]))
    assert config["references"] == []
    assert config["extends"].endswith("/frontend/tsconfig.json")
    assert any(p.endswith("/frontend/env.d.ts") for p in config["include"])
    print(json.dumps(config))
'''
        for tool in ("pnpm", "node", "gh"):
            target = tools / tool
            target.write_text(body)
            target.chmod(0o755)
        return {"PATH": str(tools) + os.pathsep + self.env["PATH"], "CI_FAST_PRECOMMIT": "uvx pre-commit"}

    def test_quick_runs_related_i18n_and_narrow_types(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: verify local checks", env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rows = {r["id"]: r for r in self.report()["ran"]}
        self.assertEqual(rows["frontend-unit"]["evidence"]["passed"], 2)
        self.assertIn("related", rows["frontend-unit"]["command"])
        self.assertIn("--run", rows["frontend-unit"]["command"])
        self.assertIn("--maxWorkers=1", rows["frontend-unit"]["command"])
        self.assertIn("frontend-i18n", rows)
        config_path = rows["frontend-typecheck-changed"]["command"][-2]
        self.assertFalse(Path(config_path).exists(), "temporary project must be cleaned")

    @unittest.skipIf(_cores() < 3, "the pool is core-capped on this machine")
    def test_quick_pool_follows_a_runner_sized_ceiling(self):
        # The draft job hands the runner's ceiling down, so the frontend pool
        # must come from it instead of staying at the local default of one.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: size the pool",
                                "--memory-mib", "9216", env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rep = self.report()
        self.assertEqual(rep["memory_limit_mib"], 9216)
        rows = {r["id"]: r for r in rep["ran"]}
        workers = next(a.split("=", 1)[1] for a in rows["frontend-unit"]["command"]
                       if a.startswith("--maxWorkers="))
        self.assertGreater(int(workers), 1)
        self.assertIn(f"--maxWorkers={workers}", rows["frontend-i18n"]["command"])

    def test_quick_empty_tests_are_unknown(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: check emptiness", env_extra=self.quick_tools(passed=0))
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertEqual(self.report()["status"], "unknown")

    def test_quick_partial_skips_are_unknown(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: check skip", env_extra=self.quick_tools(skipped=1))
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_quick_title_does_not_substitute_head_for_pr(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", env_extra=self.quick_tools(missing_title=True))
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIsNone(self.report()["title"]["value"])
        proc = self.run_ci_fast("--tier", "quick", env_extra={"PATH": str(self.tmp / "tools") + os.pathsep + self.env["PATH"]})
        self.assertEqual(proc.returncode, 2, proc.stdout)

    def test_quick_uses_actual_pr_title_when_no_proposal(self):
        # No --title: the title is read from gh. A real, conforming PR title
        # must green the run and the report must record its source.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.report()["title"]["source"], "actual PR title")
        self.assertEqual(self.report()["title"]["value"], "feat: actual PR title")

    def backend_tools(self, covered="tests/unit/test_probe.py", folder=None):
        # A stub `uv` that stands in for pytest. It writes both the JUnit report
        # (classname only, as pytest 9 does — no file attribute) and the in-run
        # node audit that the -p plugin would produce, for the given covered
        # file. Everything else in the quick tier is real (pre-commit runs the
        # fixture's stub hooks from PATH).
        tools = folder or (self.tmp / "btools")
        tools.mkdir(parents=True, exist_ok=True)
        body = f'''#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
covered = {covered!r}
module = covered[:-3].replace("/", ".")
node = covered + "::test_it"  # pytest nodeids carry the path, not the module
if "--junitxml" in args:
    pathlib.Path(args[args.index("--junitxml") + 1]).write_text(
        '<testsuites><testsuite><testcase classname="' + module + '" name="test_it"/>'
        '</testsuite></testsuites>')
if os.environ.get("CI_FAST_NODES"):
    json.dump({{"selected": [{{"node": node, "layer": "pure"}}], "deselected": [],
               "executed": [{{"node": node, "when": "call", "outcome": "passed"}}]}},
              open(os.environ["CI_FAST_NODES"], "w"))
sys.exit(0)
'''
        target = tools / "uv"
        target.write_text(body)
        target.chmod(0o755)
        return {"PATH": str(tools) + os.pathsep + self.env["PATH"], "CI_FAST_PRECOMMIT": "uvx pre-commit"}

    def test_quick_backend_black_box(self):
        # End to end through main(): the plan selects the changed unit test,
        # the report must show it executed, and the run is green. The report
        # carries only a classname (pytest 9), so this also proves the
        # required-file match works without a file attribute.
        self.write_hooks()
        self.make_base_and_head("backend/tests/unit/test_probe.py")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: run backend pure", env_extra=self.backend_tools())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        row = next(r for r in self.report()["ran"] if r["id"] == "backend-pure")
        self.assertEqual(row["result"], "pass")
        self.assertIn("tests/unit/test_probe.py", row["evidence"]["required"])
        self.assertIn("-m", row["command"])
        self.assertIn("pure", row["command"])
        self.assertIn("ci_fast_pytest_plugin", row["command"])

    def test_quick_backend_missing_target_is_unknown(self):
        # A green report that omits the file depending on the changed module
        # proves nothing about the change: the run must be unknown, not green.
        self.write_hooks()
        self.make_base_and_head("backend/tests/unit/test_probe.py")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: miss coverage",
                                env_extra=self.backend_tools(covered="tests/unit/test_something_else.py"))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.report()["status"], "unknown")

    def test_i18n_message_change_runs_backend_guards_locally(self):
        # A frontend catalog edit under src/i18n/messages/** is read back by the
        # backend refusal/sentences guards, but the shared CI path map names only
        # three catalogs. This tier must widen backend scope locally (leaving the
        # shared map alone) so those guards still run here.
        self.write_hooks()
        self.make_base_and_head("frontend/src/i18n/messages/zh/newCatalog.json")
        env = self.quick_tools()
        self.backend_tools(folder=self.tmp / "tools")  # add the pytest stub beside pnpm/node
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: widen i18n scope", env_extra=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = self.report()
        self.assertTrue(report["i18n_backend_guard"])
        self.assertTrue(report["scope"]["backend"])
        self.assertTrue(any(r["id"] == "backend-pure" for r in report["ran"]))

    def test_shared_i18n_catalog_needs_no_local_widening(self):
        # roomNotice.json already sets backend scope in the shared map, so the
        # local guard must not claim it did anything.
        self.write_hooks()
        self.make_base_and_head("frontend/src/i18n/messages/zh/roomNotice.json")
        env = self.quick_tools()
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: shared catalog", env_extra=env)
        report = self.report()
        self.assertFalse(report["i18n_backend_guard"])
        self.assertTrue(report["scope"]["backend"])

    def test_actual_pr_title_ignores_tool_preamble(self):
        # The credential wrapper can print a notice before the title; the
        # single-line PR title must still be read correctly, not the blob.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick",
                                env_extra=self.quick_tools(preamble="notice: accessing the forge"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.report()["title"]["value"], "feat: actual PR title")

    def test_quick_invalid_title_fails(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", "--title", "a bad title", env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(self.report()["title"]["value"], "a bad title")

    def test_global_budget_records_all_remaining_checks(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--budget", "0.5")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertLess(self.report()["seconds"], 3)
        self.assertTrue(any(r["result"] in ("budget", "timeout") for r in self.report()["ran"]))

    def test_memory_limit_above_the_default_is_accepted(self):
        # The work machine's 3072 MiB session cap is the default, not a hard
        # ceiling: a runner that can back more may raise it, and past the cap
        # the value is refused rather than clamped. The cap is read out of
        # --help so raising it does not have to be mirrored here.
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        cap = int(re.search(r"ceiling, 1\.\.(\d+)", self.run_ci_fast("--help").stdout).group(1))
        self.assertGreater(cap, 3072)
        proc = self.run_ci_fast("--memory-mib", str(cap))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.report()["memory_limit_mib"], cap)
        proc = self.run_ci_fast("--memory-mib", str(cap + 1))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn(str(cap), proc.stderr)

    def test_js_hook_without_budget_is_unknown_not_run(self):
        # A node hook started on a nearly empty budget would spend its life
        # being killed instead of judged; report it unknown, never a red it did
        # not observe. The budget is spent by a real (slow) stub hook first, so
        # nothing here is stubbed away.
        self.write_hooks(slow={"manual-anchors"})
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast("--budget", "3", "--timeout", "2")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        rows = {r["id"]: r for r in self.report()["ran"]}
        for identifier in ("eslint", "stylelint", "boundary-check-frontend"):
            self.assertEqual(rows[identifier]["result"], "unknown", identifier)
        self.assertNotIn("eslint", self.ran_hooks())
        # The two ratchets are python scripts costing seconds, far too cheap for
        # a fixed floor to judge: they are left to their own per-check timeout,
        # so a budget that ran out must still never read as their failure.
        for identifier in ("scene-ratchet", "catalog-ratchet"):
            self.assertNotEqual(rows.get(identifier, {}).get("result"), "fail", identifier)


class SelectionEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def test_backend_follows_imports_not_names(self):
        from ci_fast_backend import select_backend
        self.put("backend/app/invoice.py", "VALUE = 1\n")
        self.put("backend/app/adapter.py", "from .invoice import VALUE\n")
        self.put("backend/tests/unit/test_unrelated_name.py", "def test_it():\n    from app.adapter import VALUE\n")
        self.put("backend/tests/unit/test_invoice_named_but_unrelated.py", "def test_it(): pass\n")
        paths, reason, targets = select_backend(self.root, ["backend/app/invoice.py"])
        self.assertIn("tests/unit/test_unrelated_name.py", paths)
        self.assertNotIn("tests/unit/test_invoice_named_but_unrelated.py", paths)
        self.assertIn("tests/unit/test_sentences.py", paths)
        self.assertIn("transitive", reason)
        # the pinpointed consumer is reported as a required target (proves the
        # changed module has coverage), not just as one file among the guards
        self.assertIn("tests/unit/test_unrelated_name.py", targets)
        self.assertNotIn("tests/unit/test_sentences.py", targets)

    def test_backend_deleted_module_keeps_consumers(self):
        from ci_fast_backend import select_backend
        self.put("backend/tests/unit/test_any_name.py", "from app.gone import value\n")
        paths, _, _ = select_backend(self.root, ["backend/app/gone.py"])
        self.assertIn("tests/unit/test_any_name.py", paths)

    def test_backend_unknown_module_and_meta_widen(self):
        from ci_fast_backend import select_backend
        self.put("backend/tests/unit/test_a.py", "def test_it(): pass\n")
        self.put("backend/tests/unit/test_b.py", "def test_it(): pass\n")
        for changed in (["backend/app/new.py"], ["backend/tests/conftest.py"], ["backend/pyproject.toml"]):
            paths, reason, targets = select_backend(self.root, changed)
            self.assertIn("tests/unit/test_a.py", paths)
            self.assertIn("tests/unit/test_b.py", paths)
            self.assertIn("fallback", reason)
            self.assertEqual(targets, [], "a whole-selection fallback pinpoints nothing")

    def test_backend_literal_module_import_is_selected(self):
        from ci_fast_backend import select_backend
        self.put("backend/app/special.py", "VALUE = 1\n")
        self.put("backend/tests/unit/test_indirect.py", 'import importlib\nx = importlib.import_module("app.special")\n')
        paths, _, _ = select_backend(self.root, ["backend/app/special.py"])
        self.assertIn("tests/unit/test_indirect.py", paths)

    def test_frontend_delete_keeps_related_when_a_root_survives(self):
        # A deletion no longer expands to the suite: the surviving root still
        # seeds the related graph. The deleted module does force the full
        # typecheck, which is the only project that can see dangling importers.
        from ci_fast_checks import test_plan
        self.put("frontend/src/new.vue", "<template><p>Hello</p></template>")
        plan, _ = test_plan(self.root, ["frontend/src/old.vue", "frontend/src/new.vue"], {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "related")
        self.assertEqual(unit["evidence"]["roots"], ["src/new.vue"])
        self.assertTrue(any(p["id"] == "frontend-i18n" for p in plan))
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))
        plan, _ = test_plan(self.root, ["frontend/src/new.vue"], {"frontend": True}, self.root)
        types = next(p for p in plan if p["id"] == "frontend-typecheck-changed")
        config = json.loads(Path(types["command"][3]).read_text())
        self.assertEqual(config["references"], [])
        self.assertIn(str(self.root / "frontend/src/new.vue"), config["files"])
        self.assertIn(str(self.root / "frontend/env.d.ts"), config["include"])

    def test_junit_empty_partial_skip_and_errors_do_not_green(self):
        from ci_fast_checks import validate_evidence
        xml = self.root / "results.xml"
        evidence = {"kind": "junit", "path": str(xml)}
        for body, expected in (("", "unknown"), ('<testcase><skipped/></testcase><testcase/>', "unknown"),
                               ('<testcase><error/></testcase>', "fail")):
            xml.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>")
            self.assertEqual(validate_evidence(evidence)["result"], expected)
        xml.write_text('<testsuites><testsuite><testcase/></testsuite></testsuites>')
        self.assertEqual(validate_evidence(evidence)["evidence"]["passed"], 1)

    def test_junit_required_target_must_actually_execute(self):
        # A green suite is not enough: the file that depends on the changed
        # module must appear in the report, or the change has no coverage.
        from ci_fast_checks import validate_evidence
        xml = self.root / "results.xml"
        evidence = {"kind": "junit", "path": str(xml)}
        xml.write_text('<testsuites><testsuite>'
                       '<testcase file="tests/unit/test_executed.py" classname="tests.unit.test_executed"/>'
                       '</testsuite></testsuites>')
        covered = {**evidence, "required": ["tests/unit/test_executed.py"]}
        self.assertEqual(validate_evidence(covered)["result"], "pass")
        missing = {**evidence, "required": ["tests/unit/test_never_ran.py"]}
        verdict = validate_evidence(missing)
        self.assertEqual(verdict["result"], "unknown")
        self.assertEqual(verdict["evidence"]["missing"], ["tests/unit/test_never_ran.py"])

    def test_junit_classname_matches_required_file_by_module_prefix(self):
        # pytest 9 writes no `file` attribute and appends the class to
        # `classname` (tests.unit.test_x.TestC). The required `.py` path must
        # still match — a passing class-based file is coverage, not a miss.
        from ci_fast_checks import _covers
        self.assertTrue(_covers("tests.unit.test_x", "tests/unit/test_x.py"))
        self.assertTrue(_covers("tests.unit.test_x.TestC", "tests/unit/test_x.py"))
        self.assertTrue(_covers("tests/unit/test_x.py", "tests/unit/test_x.py"))
        self.assertTrue(_covers("repo/tests/unit/test_x.py", "tests/unit/test_x.py"))
        self.assertFalse(_covers("tests.unit.test_x_extra", "tests/unit/test_x.py"))
        self.assertFalse(_covers("tests.unit.test_y", "tests/unit/test_x.py"))

    def test_junit_class_based_report_greens_required_file(self):
        from ci_fast_checks import validate_evidence
        xml = self.root / "avatars.xml"
        xml.write_text('<testsuites><testsuite>'
                       '<testcase classname="tests.unit.test_avatars_service.TestAvatarToDto" name="test_a"/>'
                       '<testcase classname="tests.unit.test_avatars_service.TestAvatarToDto" name="test_b"/>'
                       '</testsuite></testsuites>')
        evidence = {"kind": "junit", "path": str(xml),
                    "required": ["tests/unit/test_avatars_service.py"]}
        self.assertEqual(validate_evidence(evidence)["result"], "pass")

    def node_audit(self, selected=(), deselected=(), executed=()):
        path = self.root / "nodes.json"
        path.write_text(json.dumps({
            "selected": [{"node": n, "layer": l} for n, l in selected],
            "deselected": [{"node": n, "layer": l} for n, l in deselected],
            "executed": [{"node": n, "when": w, "outcome": o} for n, w, o in executed]}))
        return str(path)

    def audit_evidence(self, nodes, required=None):
        xml = self.root / "empty.xml"
        xml.write_text("<testsuites><testsuite/></testsuites>")
        evidence = {"kind": "junit", "path": str(xml), "nodes": nodes}
        if required is not None:
            evidence["required"] = required
        return evidence

    def test_pure_audit_requires_every_selected_pure_node_to_execute(self):
        # The JUnit report alone cannot show what a filter removed; the in-run
        # audit must. A selected pure node that never ran, or a pure node a -k
        # hid, is unknown — never green.
        from ci_fast_checks import validate_evidence
        ok = self.audit_evidence(self.node_audit(
            selected=[("tests/unit/test_x.py::test_a", "pure")],
            executed=[("tests/unit/test_x.py::test_a", "call", "passed")]))
        self.assertEqual(validate_evidence(ok)["result"], "pass")
        unrun = self.audit_evidence(self.node_audit(
            selected=[("tests/unit/test_x.py::test_a", "pure"), ("tests/unit/test_x.py::test_b", "pure")],
            executed=[("tests/unit/test_x.py::test_a", "call", "passed")]))
        verdict = validate_evidence(unrun)
        self.assertEqual(verdict["result"], "unknown")
        self.assertEqual(verdict["evidence"]["unrun"], ["tests/unit/test_x.py::test_b"])
        hidden = self.audit_evidence(self.node_audit(
            deselected=[("tests/unit/test_x.py::test_a", "pure")]))
        self.assertEqual(validate_evidence(hidden)["result"], "unknown")

    def test_pure_audit_excludes_integration_without_going_unknown(self):
        # The pure layer is defined to exclude Postgres tests; that deselection
        # is the tier working, so it must not force a permanent unknown.
        from ci_fast_checks import validate_evidence
        evidence = self.audit_evidence(self.node_audit(
            selected=[("tests/unit/test_x.py::test_a", "pure")],
            deselected=[("tests/integration/test_db.py::test_it", "integration")],
            executed=[("tests/unit/test_x.py::test_a", "call", "passed")]))
        self.assertEqual(validate_evidence(evidence)["result"], "pass")

    def test_pure_audit_integration_only_target_is_not_run_not_unknown(self):
        # A target the static graph widened to, whose tests are all integration,
        # is not this layer's job: it is recorded not_run, not required. A change
        # whose modules have no pure consumer at all is the unknown case.
        from ci_fast_checks import validate_evidence
        both = self.audit_evidence(self.node_audit(
            selected=[("tests/unit/test_consumer.py::test_c", "pure")],
            deselected=[("tests/unit/test_wide.py::test_w", "integration")],
            executed=[("tests/unit/test_consumer.py::test_c", "call", "passed")]),
            required=["tests/unit/test_consumer.py", "tests/unit/test_wide.py"])
        verdict = validate_evidence(both)
        self.assertEqual(verdict["result"], "pass")
        self.assertEqual(verdict["evidence"]["not_run_pure"], ["tests/unit/test_wide.py"])
        none = self.audit_evidence(self.node_audit(
            selected=[("tests/unit/test_guards.py::test_g", "pure")],
            deselected=[("tests/unit/test_target.py::test_it", "integration")],
            executed=[("tests/unit/test_guards.py::test_g", "call", "passed")]),
            required=["tests/unit/test_target.py"])
        self.assertEqual(validate_evidence(none)["result"], "unknown")

    def test_pure_audit_reports_failures(self):
        from ci_fast_checks import validate_evidence
        evidence = self.audit_evidence(self.node_audit(
            selected=[("tests/unit/test_x.py::test_a", "pure")],
            executed=[("tests/unit/test_x.py::test_a", "call", "failed")]))
        self.assertEqual(validate_evidence(evidence)["result"], "fail")

    def test_full_frontend_run_requires_both_i18n_specs(self):
        # The full Vitest run must be shown to have executed the guards, not
        # merely to have matched them.
        from ci_fast_checks import validate_evidence
        path = self.root / "vitest.json"
        evidence = {"kind": "vitest", "path": str(path),
                    "required_files": ["src/i18n/catalog.spec.ts", "src/i18n/english.spec.ts"]}
        path.write_text(json.dumps({"numPassedTests": 1, "numFailedTests": 0, "numPendingTests": 0,
                                    "testResults": [{"name": "/repo/frontend/src/i18n/catalog.spec.ts"}]}))
        self.assertEqual(validate_evidence(evidence)["result"], "unknown")
        path.write_text(json.dumps({"numPassedTests": 2, "numFailedTests": 0, "numPendingTests": 0,
                                    "testResults": [{"name": "/repo/frontend/src/i18n/catalog.spec.ts"},
                                                    {"name": "/repo/frontend/src/i18n/english.spec.ts"}]}))
        self.assertEqual(validate_evidence(evidence)["result"], "pass")

    def test_related_run_holding_only_the_seeds_is_an_empty_selection(self):
        # The seeds run whatever changed, so a report that holds only them says
        # nothing about the change: the graph selected no test file for the
        # changed root, which is what main reported unknown. Vitest cannot say
        # it here — --no-passWithNoTests only fires on a run with no test file
        # at all, and the seeds keep the run non-empty.
        from ci_fast_checks import validate_evidence
        path = self.root / "vitest.json"
        seed = "/repo/frontend/src/styles/docBlocks.spec.ts"
        evidence = {"kind": "vitest", "path": str(path), "mode": "related",
                    "required_files": ["src/styles/docBlocks.spec.ts"],
                    "roots": ["src/components/base/NewThing.vue"]}
        path.write_text(json.dumps({"numPassedTests": 3, "numFailedTests": 0, "numPendingTests": 0,
                                    "testResults": [{"name": seed}]}))
        verdict = validate_evidence(evidence)
        self.assertEqual(verdict["result"], "unknown")
        self.assertEqual(verdict["evidence"]["passed"], 3)
        # One test file the seeds do not account for is the change's coverage.
        path.write_text(json.dumps({"numPassedTests": 4, "numFailedTests": 0, "numPendingTests": 0,
                                    "testResults": [{"name": seed},
                                                    {"name": "/repo/frontend/src/views/x.spec.ts"}]}))
        self.assertEqual(validate_evidence(evidence)["result"], "pass")
        # A full run judges by its own rule: everything it ran is a selection.
        self.assertEqual(validate_evidence({**evidence, "mode": "full"})["result"], "pass")
        # A changed spec is selected by being run, even though it is also a seed
        # — a spec changed on its own seeds the graph with itself.
        changed = {**evidence, "roots": ["src/styles/docBlocks.spec.ts"]}
        path.write_text(json.dumps({"numPassedTests": 3, "numFailedTests": 0, "numPendingTests": 0,
                                    "testResults": [{"name": seed}]}))
        self.assertEqual(validate_evidence(changed)["result"], "pass")

    def test_frontend_scope_widens_to_full_only_when_unjudgeable(self):
        # The full run is the fallback for a change nothing can narrow. A
        # deletion is judged by the related graph (seeded by the surviving
        # roots) and the surviving file list (for types), so it stays narrow.
        from ci_fast_checks import test_plan
        self.put("frontend/src/keep.ts", "export const x = 1\n")
        for changed, conservative in (
            (["frontend/src/keep.ts"], True),                                   # no merge base / unknown
            (["frontend/src/test/harness.ts", "frontend/src/keep.ts"], False),   # test harness
            (["frontend/package.json", "frontend/src/keep.ts"], False),         # config edit
            ([".github/workflows/ci-fast.yml", "frontend/src/keep.ts"], False),  # .github edit
            # Not because they are config — because they are the whole change
            # and seed no root, so nothing can narrow them. Either file beside a
            # surviving src root stays `related`.
            (["frontend/vite.board-proto.config.mts"], False),                   # config outside FRONTEND_META, alone
            (["frontend/collab/vitest.config.ts"], False),                       # a second Vitest config, alone
        ):
            plan, _ = test_plan(self.root, changed, {"frontend": True}, self.root, conservative)
            unit = next(p for p in plan if p["id"] == "frontend-unit")
            self.assertEqual(unit["evidence"]["mode"], "full", changed)
            self.assertFalse(any(p["id"] == "frontend-i18n" for p in plan), changed)
        plan, _ = test_plan(self.root, ["frontend/src/gone.ts", "frontend/src/keep.ts"],
                            {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "related")
        # A deleted module is exactly what the full typecheck is for.
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))

    def test_frontend_types_only_input_widens_types_not_the_test_run(self):
        # A backend route change regenerates openapi.json and the types-only
        # src/api/openapi.d.ts. Nothing in frontend/src reads the document at
        # runtime, so the suite would be checking nothing new; the type check is
        # what has to widen.
        from ci_fast_checks import test_plan
        self.put("frontend/src/api/openapi.d.ts", "export interface paths {}\n")
        self.put("frontend/src/use.ts", "export const x = 1\n")
        plan, omissions = test_plan(
            self.root, ["backend/openapi.json", "frontend/src/api/openapi.d.ts", "frontend/src/use.ts"],
            {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "related")
        # A declaration file cannot seed the graph, so it is not a root.
        self.assertEqual(unit["evidence"]["roots"], ["src/use.ts"])
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))
        self.assertEqual(omissions, [])
        # Alone, the regenerated declaration runs no test at all — that is a
        # stated omission, not a suite-wide run and not an unknown.
        plan, omissions = test_plan(self.root, ["backend/openapi.json", "frontend/src/api/openapi.d.ts"],
                                    {"frontend": True}, self.root)
        self.assertFalse(any(p["id"] == "frontend-unit" for p in plan))
        self.assertTrue(any(p["id"] == "frontend-i18n" for p in plan))
        self.assertEqual(omissions[0]["id"], "frontend-unit")
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))

    def test_frontend_deleted_spec_is_not_a_dangling_import(self):
        # A deleted spec leaves no importer behind, so the narrow project
        # judges the change; the full typecheck is not called for.
        from ci_fast_checks import test_plan
        self.put("frontend/src/keep.ts", "export const x = 1\n")
        plan, _ = test_plan(self.root, ["frontend/src/old.spec.ts", "frontend/src/keep.ts"],
                            {"frontend": True}, self.root)
        self.assertTrue(any(p["id"] == "frontend-typecheck-changed" for p in plan))
        self.assertFalse(any(p["id"] == "frontend-typecheck-full" for p in plan))

    def test_frontend_deletion_a_surviving_file_imports_widens(self):
        # The narrowed run is seeded from the changed files, so the spec of an
        # unchanged file that imports the deleted one is never selected — and
        # the typecheck cannot stand in, because the app declares *.vue and Vite
        # declares *.css, so vue-tsc resolves an import of a file that is gone.
        from ci_fast_checks import test_plan
        self.put("frontend/src/keep.ts", "import Gone from '@/gone'\nexport const x = Gone\n")
        self.put("frontend/src/keep.css", "@import './gone.css';\n")
        for named in ("frontend/src/gone.vue", "frontend/src/gone.css"):
            plan, _ = test_plan(self.root, [named, "frontend/src/keep.ts", "frontend/src/keep.css"],
                                {"frontend": True}, self.root)
            unit = next(p for p in plan if p["id"] == "frontend-unit")
            self.assertEqual(unit["evidence"]["mode"], "full", named)
        # A basename in prose is not a reference. This tree discusses its own
        # files in comments constantly, and reading those as references would
        # run the whole suite for a rename nobody left dangling.
        self.put("frontend/src/mentioned.ts", "// see old-thing.vue for the old shape\n")
        plan, _ = test_plan(self.root, ["frontend/src/old-thing.vue", "frontend/src/mentioned.ts"],
                            {"frontend": True}, self.root)
        self.assertEqual(next(p for p in plan if p["id"] == "frontend-unit")["evidence"]["mode"],
                         "related")

    def test_frontend_deletion_of_a_helper_imported_without_its_suffix_widens(self):
        # A dot in a specifier says nothing about where the file name ends:
        # `./helper.spec.kit` names helper.spec.kit.ts, and the resolver appends
        # the module kinds it knows. Reading `.kit` as the whole name matched
        # nothing, so deleting a helper that surviving specs import left the run
        # narrowed and green — the failure is at import resolution, and no type
        # check stands in for it.
        from ci_fast_checks import test_plan
        self.put("frontend/src/keep.ts", "export const x = 1\n")
        self.put("frontend/src/uses.spec.ts",
                 "import { h } from './helper.spec.kit'\nexport const y = h\n")
        plan, _ = test_plan(
            self.root, ["frontend/src/helper.spec.kit.ts", "frontend/src/keep.ts"],
            {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "full")
        # The deleted helper is a module, not a spec: `spec` in the middle of a
        # name does not make the check that sees its importers unnecessary.
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))

    def test_frontend_deleted_helper_named_like_a_spec_widens_types_alone(self):
        # With no importer left behind the run stays narrowed, but the deleted
        # helper is still a module the whole-project typecheck is for; only a
        # file that itself ends as a spec or a test leaves nothing dangling.
        from ci_fast_checks import test_plan
        self.put("frontend/src/keep.ts", "export const x = 1\n")
        plan, _ = test_plan(self.root, ["frontend/src/helper.spec.kit.ts", "frontend/src/keep.ts"],
                            {"frontend": True}, self.root)
        self.assertEqual(next(p for p in plan if p["id"] == "frontend-unit")["evidence"]["mode"],
                         "related")
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))

    def test_frontend_scope_is_the_paths_the_map_routed_there(self):
        # The path map routes backend/openapi.json into frontend scope, so a
        # backend change enters this branch through it. Read from the whole
        # change, its own .py files counted as frontend runtime content and the
        # suite ran in full for a document no test can load.
        from ci_fast_checks import test_plan
        self.put("frontend/src/api/openapi.d.ts", "export interface paths {}\n")
        changed = ["backend/app/api/projects.py", "backend/openapi.json",
                   "frontend/src/api/openapi.d.ts"]
        plan, omissions = test_plan(self.root, changed, {"frontend": True}, self.root,
                                    frontend_scope=["backend/openapi.json",
                                                    "frontend/src/api/openapi.d.ts"])
        self.assertEqual([o["id"] for o in omissions], ["frontend-unit"])
        self.assertFalse(any(p["id"] == "frontend-unit" for p in plan))
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))
        # With no scope from the caller the whole change is read, as before.
        plan, _ = test_plan(self.root, changed, {"frontend": True}, self.root)
        self.assertEqual(next(p for p in plan if p["id"] == "frontend-unit")["evidence"]["mode"],
                         "full")

    def test_read_off_disk_specs_matches_the_specs_that_read_the_tree(self):
        # The tuple is written by hand, and a stale entry is filtered out of
        # both the seeds and required_files by `is_file()` without a word. This
        # is what notices a spec starting to read the tree, or one renamed. The
        # read is matched by the specifier it writes, not by the substring
        # `node:fs`: `from 'fs'` and `from 'fs/promises'` are the same read, and
        # a tuple that missed one would stop seeding it without a word.
        from ci_fast_checks import I18N_SPECS, OFF_DISK_SPECS
        src = SCRIPT.parents[2] / "frontend" / "src"
        reads_the_tree = re.compile(r"""["'](?:node:)?fs(?:/promises)?["']""")
        readers = {"src/" + p.relative_to(src).as_posix()
                   for p in src.rglob("*.spec.ts")
                   if reads_the_tree.search(p.read_text(encoding="utf-8", errors="ignore"))}
        # src/collab/ is outside this Vitest project (frontend/vite.config.ts
        # excludes it, it has its own config); the i18n guards are required by
        # frontend-i18n instead.
        self.assertEqual(readers - set(I18N_SPECS) - {"src/collab/guide.spec.ts"},
                         set(OFF_DISK_SPECS))

    def test_frontend_seedless_change_widens_unless_it_is_prose(self):
        # A seedless change that carries runtime content cannot be judged
        # narrow, and the guards are not what judges it: a catalog, a ratchet
        # baseline and a stylesheet are each read by a filesystem scan rather
        # than through an import edge, so no test file is selected for them and
        # the guard specs never look at them — a broken rule in docBlocks.css
        # passes both guards and fails only src/styles/docBlocks.spec.ts, which
        # reads the stylesheet off disk. The suite is what answers for the
        # edit. A deletion with no surviving root is the same case.
        from ci_fast_checks import test_plan
        self.put("frontend/src/i18n/messages/en/tasks.json", "{}\n")
        self.put("frontend/scene-baseline.json", "{}\n")
        for changed in (["frontend/src/i18n/messages/en/tasks.json"],
                        ["frontend/scene-baseline.json"],
                        ["frontend/src/styles/docBlocks.css"],
                        ["frontend/src/gone.ts"]):
            plan, omissions = test_plan(self.root, changed, {"frontend": True}, self.root)
            unit = next(p for p in plan if p["id"] == "frontend-unit")
            self.assertEqual(unit["evidence"]["mode"], "full", changed)
            self.assertNotIn("frontend-unit", [o["id"] for o in omissions], changed)
            self.assertFalse(any(p["id"] == "frontend-i18n" for p in plan), changed)
        # Prose is judged by the doc checks and a declaration file compiles to
        # no runtime, so both leave the suite nothing to judge: the guards run
        # and frontend-unit is stated as not run rather than expanded to.
        self.put("frontend/src/api/openapi.d.ts", "export {}\n")
        for changed in (["frontend/README.md"], ["frontend/src/api/openapi.d.ts"]):
            plan, omissions = test_plan(self.root, changed, {"frontend": True}, self.root)
            self.assertFalse(any(p["id"] == "frontend-unit" for p in plan), changed)
            self.assertTrue(any(p["id"] == "frontend-i18n" for p in plan), changed)
            self.assertEqual(omissions[0]["id"], "frontend-unit", changed)
            self.assertIn("no changed frontend source", omissions[0]["reason"], changed)

    def test_frontend_locale_edit_beside_a_root_stays_narrow(self):
        # The ordinary frontend change: a component and the catalog entries it
        # uses. The component seeds the related run; the catalog is the guard
        # specs' subject, and they run beside it in the same tier.
        from ci_fast_checks import test_plan
        self.put("frontend/src/views/Index.vue", "<template><p>x</p></template>")
        self.put("frontend/src/i18n/messages/en/tasks.json", "{}\n")
        plan, omissions = test_plan(
            self.root, ["frontend/src/views/Index.vue", "frontend/src/i18n/messages/en/tasks.json"],
            {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "related")
        self.assertEqual(unit["evidence"]["roots"], ["src/views/Index.vue"])
        self.assertTrue(any(p["id"] == "frontend-i18n" for p in plan))
        self.assertNotIn("frontend-unit", [o["id"] for o in omissions])

    def test_frontend_related_run_carries_the_read_off_disk_specs(self):
        # `related` follows import edges, and a spec that judges a file by
        # reading it with `node:fs` has no edge to it — the component whose
        # template it asserts, the stylesheet whose rule it checks. Those specs
        # are seeded into the narrowed run by name and required in its report,
        # so a selection that silently drops one is unknown, not green.
        from ci_fast_checks import OFF_DISK_SPECS, test_plan
        self.put("frontend/src/keep.ts", "export const x = 1\n")
        for p in OFF_DISK_SPECS:
            self.put("frontend/" + p, "export {}\n")
        plan, _ = test_plan(self.root, ["frontend/src/keep.ts"], {"frontend": True},
                            Path(tempfile.mkdtemp()))
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "related")
        self.assertEqual(unit["evidence"]["required_files"], list(OFF_DISK_SPECS))
        for p in OFF_DISK_SPECS:
            self.assertIn(p, unit["command"], p)
        # Deleting one of them is a change to a spec the graph also cannot see:
        # it is not required back from a run that no longer contains it.
        os.remove(self.root / "frontend" / OFF_DISK_SPECS[0])
        plan, _ = test_plan(self.root, ["frontend/" + OFF_DISK_SPECS[0], "frontend/src/keep.ts"],
                            {"frontend": True}, Path(tempfile.mkdtemp()))
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["required_files"], list(OFF_DISK_SPECS[1:]))
        self.assertNotIn(OFF_DISK_SPECS[0], unit["command"])

    def test_vitest_workers_follow_the_memory_ceiling(self):
        # The arithmetic bounds the black-box path below cannot reach: the core
        # count capping a huge ceiling, and the pool never spending a ceiling
        # entirely on workers. The wiring (ceiling -> flag) is black-box tested
        # by test_quick_pool_follows_a_runner_sized_ceiling.
        from ci_fast_checks import MIB_PER_VITEST_WORKER, vitest_flags, vitest_workers
        # One fork per 3 GiB is frontend/vite.config.ts's own rule (a fork
        # peaks at 1.7–2.1 GB), so the two stay in step on the same machine.
        self.assertEqual(MIB_PER_VITEST_WORKER, 3 * 1024)
        # A tiny ceiling leaves room for one fork and the pool never drops
        # below that, whatever the machine has.
        self.assertEqual(vitest_workers(3072), 1)
        self.assertEqual(vitest_workers(1024), 1)
        # A ceiling that pays for more forks than the machine has cores is
        # capped by the cores: the memory term (341 forks) cannot bind here, so
        # the core count is the only variable, and the floor still applies.
        self.assertEqual(vitest_workers(1024 * 1024), max(1, min(16, _cores() - 1)))
        if _cores() >= 3:
            # The boundary itself: exactly two forks' worth of ceiling buys two
            # forks on a machine with three or more cores.
            self.assertEqual(vitest_workers(6 * 1024), 2)
            self.assertEqual(vitest_workers(8 * 1024), 2)
        if _cores() >= 4:
            # The draft job's ceiling on the 4 vCPU runner: what vite.config.ts
            # computes for that box, i.e. the pool a frontend change is meant to
            # get there instead of the single worker.
            self.assertEqual(vitest_workers(9216), 3)
        flags = vitest_flags(8 * 1024)
        self.assertIn(f"--maxWorkers={vitest_workers(8 * 1024)}", flags)
        if _cores() >= 3:
            # A ceiling that buys more than one fork must show more than that in
            # the flags, not the single-worker pin this replaced.
            self.assertNotIn("--maxWorkers=1", flags)
        self.assertIn("--no-passWithNoTests", flags)
        # A single worker keeps the files in the process that started Vitest —
        # the form the tier had while its pool was pinned to one, and the local
        # default under the work machine's own 3 GiB cap. Once the ceiling backs
        # a pool, the pool is what bounds memory and the serial-file pin goes.
        self.assertIn("--no-file-parallelism", vitest_flags(3072))
        if _cores() >= 3:
            self.assertNotIn("--no-file-parallelism", flags)

    def test_frontend_meta_and_ambient_force_full_typecheck(self):
        from ci_fast_checks import test_plan
        self.put("frontend/src/new.vue", "<template><p>x</p></template>")
        self.put("frontend/tsconfig.app.json", "{}")
        plan, _ = test_plan(self.root, ["frontend/tsconfig.app.json", "frontend/src/new.vue"],
                            {"frontend": True}, self.root)
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))
        self.assertFalse(any(p["id"] == "frontend-typecheck-changed" for p in plan))

    def test_narrow_type_project_bridges_node_modules_and_paths(self):
        # The temp project must resolve the real tsconfig's types/paths, or
        # vue-tsc exits 2 (cannot judge) without checking anything.
        from ci_fast_checks import test_plan
        self.put("frontend/src/new.vue", "<template><p>x</p></template>")
        plan, _ = test_plan(self.root, ["frontend/src/new.vue"], {"frontend": True}, self.root)
        types = next(p for p in plan if p["id"] == "frontend-typecheck-changed")
        config_path = Path(types["command"][3])
        config = json.loads(config_path.read_text())
        self.assertTrue((config_path.parent / "node_modules").is_symlink())
        self.assertEqual(config["compilerOptions"]["baseUrl"], str(self.root / "frontend"))
        self.assertEqual(config["compilerOptions"]["paths"], {"@/*": ["src/*"]})
        self.assertEqual(config["references"], [])
        self.assertEqual(config["files"], [str(self.root / "frontend/src/new.vue")])


class BoundedProcessTest(unittest.TestCase):
    def test_timeout_reaps_created_group(self):
        from ci_fast_process import execute
        with tempfile.TemporaryDirectory() as tmp:
            result = execute([sys.executable, "-c", "import time; time.sleep(10)"], tmp, 0.3)
        self.assertEqual(result["result"], "timeout")
        self.assertLess(result["seconds"], 2)

    def test_signal_and_missing_binary_are_not_check_failures(self):
        from ci_fast_process import execute
        result = execute([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"], "/tmp", 2)
        self.assertEqual(result["result"], "unknown")
        self.assertEqual(execute(["/no-such-ci-fast-command"], "/tmp", 2)["result"], "blocked")
        self.assertEqual(execute([sys.executable], "/tmp", 0)["result"], "budget")

    def test_memory_guard_uses_group_rss(self):
        from ci_fast_process import execute
        result = execute([sys.executable, "-c", "import time; x=bytearray(32*1024*1024); time.sleep(5)"], "/tmp", 2, memory_mib=24)
        self.assertEqual(result["result"], "memory")
        self.assertGreaterEqual(result["peak_rss_mib"], 24)

    def test_a_missed_sample_is_not_blocked(self):
        # The sampler missing its window is not "this machine lacks ps". A check
        # that ran and exited 0 must read pass. Every sample is made to miss, so
        # nothing here depends on the wall clock or on how busy the host is —
        # this is the shape that turned an expected exit 2 (unknown) into
        # blocked's 3 twice on #3476 on 2026-10-12.
        import ci_fast_process
        from ci_fast_process import Unmeasured

        def miss(pgid, timeout=1.0):
            raise Unmeasured("no sample")

        original, ci_fast_process.group_rss = ci_fast_process.group_rss, miss
        self.addCleanup(setattr, ci_fast_process, "group_rss", original)
        result = ci_fast_process.execute([sys.executable, "-c", "pass"], "/tmp", 5)
        self.assertEqual(result["result"], "pass", result)

    def test_a_sample_that_landed_clears_the_misses(self):
        # Misses are ridden out per stretch, not counted over the check's whole
        # life: two misses, one sample that lands, then two more still leaves the
        # memory ceiling enforced. Counting them for the whole check would let a
        # long, heavily loaded run quietly stop being bounded.
        import ci_fast_process
        from ci_fast_process import Unmeasured

        samples = [Unmeasured("no sample"), Unmeasured("no sample"), 12.0,
                   Unmeasured("no sample"), Unmeasured("no sample")]

        def flaky(pgid, timeout=1.0):
            # Past the scripted misses every sample lands, so what stops the
            # measuring can only be the misses being counted in a run.
            got = samples.pop(0) if samples else 12.0
            if isinstance(got, Exception):
                raise got
            return got

        original, ci_fast_process.group_rss = ci_fast_process.group_rss, flaky
        self.addCleanup(setattr, ci_fast_process, "group_rss", original)
        result = ci_fast_process.execute(
            [sys.executable, "-c", "import time; time.sleep(1)"], "/tmp", 5
        )
        self.assertEqual(result["result"], "pass", result)
        self.assertFalse(result["unmeasured"], result)
        self.assertGreaterEqual(result["peak_rss_mib"], 12.0)

    def test_a_machine_without_ps_is_blocked(self):
        # The one case that is genuinely blocked: ps is not installed, so no
        # memory ceiling can be enforced at all.
        import ci_fast_process
        from ci_fast_process import MissingPs

        def gone(pgid, timeout=1.0):
            raise MissingPs("no ps")

        original, ci_fast_process.group_rss = ci_fast_process.group_rss, gone
        self.addCleanup(setattr, ci_fast_process, "group_rss", original)
        result = ci_fast_process.execute([sys.executable, "-c", "pass"], "/tmp", 5)
        self.assertEqual(result["result"], "blocked", result)

    def test_cleanup_reaps_children_of_a_finished_wrapper(self):
        # The wrapper exits 0 but leaves a long-lived child behind; the runner
        # must kill the whole group so the child does not linger after "pass".
        from ci_fast_process import execute
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            code = ("import subprocess, sys, time; "
                    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                    f"open({str(pid_file)!r}, 'w').write(str(p.pid)); sys.exit(0)")
            result = execute([sys.executable, "-c", code], tmp, 5)
            self.assertEqual(result["result"], "pass", result)
            child = int(pid_file.read_text())
            for _ in range(40):
                if not Path(f"/proc/{child}").exists():
                    break
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{child}").exists(), "leftover child was not reaped")


class PytestPluginTest(unittest.TestCase):
    """Drive the real audit plugin, not a hand-written sidecar.

    The black-box tests stand a stub `uv` in for pytest, so the plugin itself
    never runs there. This loads the real module and calls its own hooks with
    fake item/report objects, then judges the sidecar it wrote through
    validate_evidence — the recording functions are half the honesty claim, so
    they get their own tests. Stdlib only: the plugin tolerates a missing pytest
    (the CI lane has only python/uv), so no pytest is needed here either.
    """

    class Item:
        def __init__(self, node, layer):
            self.nodeid = node
            self._layer = layer

        def get_closest_marker(self, name):
            return object() if name == self._layer else None

    class Report:
        def __init__(self, node, when, outcome):
            self.nodeid = node
            self.when = when
            self.outcome = outcome

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        import ci_fast_pytest_plugin as plugin
        self.plugin = plugin
        self.sidecar = self.root / "nodes.json"
        plugin._OUT = str(self.sidecar)
        plugin._STATE = {"selected": [], "deselected": [], "executed": []}

    def drive(self, selected=(), deselected=(), reports=()):
        self.plugin.pytest_collection_modifyitems(None, None, [self.Item(n, l) for n, l in selected])
        self.plugin.pytest_deselected([self.Item(n, l) for n, l in deselected])
        for node, when, outcome in reports:
            self.plugin.pytest_runtest_logreport(self.Report(node, when, outcome))
        self.plugin.pytest_sessionfinish(None, 0)
        return json.loads(self.sidecar.read_text())

    def evidence(self, sidecar_data, required=None):
        # a JUnit file as pytest 9 writes it: classname only, class appended
        xml = self.root / "r.xml"
        cases = "".join(
            f'<testcase classname="{e["node"].split("::")[0][:-3].replace("/", ".")}" name="x"/>'
            for e in sidecar_data["executed"])
        xml.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
        evidence = {"kind": "junit", "path": str(xml), "nodes": str(self.sidecar)}
        if required is not None:
            evidence["required"] = required
        return evidence

    def test_records_layers_and_greens_a_class_based_pure_run(self):
        from ci_fast_checks import validate_evidence
        data = self.drive(
            selected=[("tests/unit/test_x.py::TestC::test_a", "pure")],
            reports=[("tests/unit/test_x.py::TestC::test_a", "call", "passed")])
        self.assertEqual(data["selected"], [{"node": "tests/unit/test_x.py::TestC::test_a", "layer": "pure"}])
        self.assertEqual(data["deselected"], [])
        self.assertEqual(data["executed"],
                         [{"node": "tests/unit/test_x.py::TestC::test_a", "when": "call", "outcome": "passed"}])
        self.assertEqual(validate_evidence(self.evidence(data, ["tests/unit/test_x.py"]))["result"], "pass")

    def test_integration_deselection_is_recorded_not_hidden(self):
        from ci_fast_checks import validate_evidence
        data = self.drive(
            selected=[("tests/unit/test_x.py::test_a", "pure")],
            deselected=[("tests/integration/test_db.py::test_it", "integration")],
            reports=[("tests/unit/test_x.py::test_a", "call", "passed")])
        self.assertEqual(data["deselected"],
                         [{"node": "tests/integration/test_db.py::test_it", "layer": "integration"}])
        self.assertEqual(validate_evidence(self.evidence(data, ["tests/unit/test_x.py"]))["result"], "pass")

    def test_hidden_pure_node_is_unknown(self):
        from ci_fast_checks import validate_evidence
        data = self.drive(deselected=[("tests/unit/test_x.py::test_a", "pure")])
        self.assertEqual(data["deselected"], [{"node": "tests/unit/test_x.py::test_a", "layer": "pure"}])
        verdict = validate_evidence(self.evidence(data))
        self.assertEqual(verdict["result"], "unknown")
        self.assertEqual(verdict["evidence"]["hidden"], ["tests/unit/test_x.py::test_a"])

    def test_skipped_pure_node_is_unknown(self):
        from ci_fast_checks import validate_evidence
        data = self.drive(
            selected=[("tests/unit/test_x.py::test_a", "pure")],
            reports=[("tests/unit/test_x.py::test_a", "setup", "skipped")])
        self.assertEqual(data["executed"],
                         [{"node": "tests/unit/test_x.py::test_a", "when": "setup", "outcome": "skipped"}])
        verdict = validate_evidence(self.evidence(data, ["tests/unit/test_x.py"]))
        self.assertEqual(verdict["result"], "unknown")
        self.assertEqual(verdict["evidence"]["unrun"], ["tests/unit/test_x.py::test_a"])

    def test_teardown_failure_fails(self):
        from ci_fast_checks import validate_evidence
        data = self.drive(
            selected=[("tests/unit/test_x.py::test_a", "pure")],
            reports=[("tests/unit/test_x.py::test_a", "call", "passed"),
                     ("tests/unit/test_x.py::test_a", "teardown", "failed")])
        self.assertEqual(validate_evidence(self.evidence(data))["result"], "fail")


if __name__ == "__main__":
    unittest.main()
