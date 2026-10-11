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
import time
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
        (self.repo / ".gitignore").write_text(".ci-fast-ran\n")
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

    def test_frontend_delete_forces_full_and_temp_project_inherits(self):
        from ci_fast_checks import test_plan
        self.put("frontend/src/new.vue", "<template><p>Hello</p></template>")
        plan, _ = test_plan(self.root, ["frontend/src/old.vue", "frontend/src/new.vue"], {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "full")
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

    def test_frontend_scope_widens_to_full_on_config_delete_or_unknown(self):
        from ci_fast_checks import test_plan
        self.put("frontend/src/keep.ts", "export const x = 1\n")
        for changed, conservative in (
            (["frontend/src/keep.ts"], True),                                   # no merge base / unknown
            (["frontend/src/gone.ts"], False),                                  # deleted root
            (["frontend/package.json", "frontend/src/keep.ts"], False),         # config edit
            ([".github/workflows/ci-fast.yml", "frontend/src/keep.ts"], False),  # .github edit
        ):
            plan, _ = test_plan(self.root, changed, {"frontend": True}, self.root, conservative)
            unit = next(p for p in plan if p["id"] == "frontend-unit")
            self.assertEqual(unit["evidence"]["mode"], "full", changed)

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


if __name__ == "__main__":
    unittest.main()
