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
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

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

    # --- the pre-push entry point (the platform's task-worktree hook calls
    # --- this): the three hooks that spawn the node toolchain leave the
    # --- selection and are NAMED as not run, so a report that could not judge
    # --- lint never reads as a pass that covered it
    def test_pre_push_leaves_out_the_node_hooks_and_says_so(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast("--pre-push")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ran = self.ran_hooks()
        self.assertFalse({"eslint", "stylelint", "boundary-check-frontend"} & ran)
        self.assertTrue({"scene-ratchet", "catalog-ratchet"} <= ran)  # the rest of the tier
        not_run = {item["id"]: item["reason"] for item in self.report()["not_run"]}
        for hook_id in ("eslint", "stylelint", "boundary-check-frontend"):
            self.assertIn(hook_id, not_run)
            self.assertIn("node toolchain", not_run[hook_id])
        self.assertTrue(self.report()["pre_push"])
        self.assertIn("未执行 eslint", proc.stdout)

    # --- and each of those names the command that runs it by hand: the reader
    # --- did not ask for this report and is about to push either way, so a
    # --- "not run" with no way to run it is a dead end
    def test_pre_push_names_how_to_run_what_it_left_out(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast("--pre-push")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        for hook_id in ("eslint", "stylelint", "boundary-check-frontend"):
            self.assertRegex(proc.stdout, rf"推前没跑 {hook_id}.*run {hook_id} --all-files --hook-stage manual")

    # --- what DID fail is reproduced too, and the script's own exit code still
    # --- reports the failure — only the hook swallows it, and that is the
    # --- hook's decision (a refused push is a commit the platform never got)
    def test_pre_push_reproduces_failures_and_keeps_exit_1(self):
        self.write_hooks(failing={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast("--pre-push")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("只报告，不挡推送", proc.stdout)
        self.assertRegex(proc.stdout, r"复现 ruff（fail）.*run ruff --all-files --hook-stage manual")
        self.assertEqual(self.report()["exit"], 1)

    # --- the hooks and the tool have to run on one interpreter. `uvx` builds
    # --- its tool venv around an interpreter of its own and puts that venv's
    # --- bin first on the hooks' PATH, so a `language: system` hook whose
    # --- entry says `python3` lands on it: measured 2026-10-12, 3.9.25 there
    # --- against the 3.13.5 running the script, and `migration-fork` (3.10+
    # --- annotations) reported fail on every backend-scoped push while the
    # --- reproduce line it printed repeated that same machine quirk. The
    # --- default command carries the pin, and the line the reader is told to
    # --- run is asserted to be a prefix of the argv that ran.
    def test_pre_push_pins_the_interpreter_the_hooks_run_under(self):
        self.write_hooks(failing={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        tools = self.tmp / "tools"
        tools.mkdir()
        log = self.tmp / "uvx-argv"
        uvx = tools / "uvx"
        uvx.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$(basename "$0") $*" >> {shlex.quote(str(log))}\n'
            'case "$*" in\n'
            '  *--version*) echo "pre-commit 4.7.0"; exit 0 ;;\n'
            'esac\n'
            "exit 1\n"
        )
        uvx.chmod(0o755)
        env = {k: v for k, v in self.env.items() if k != "CI_FAST_PRECOMMIT"}
        env["PATH"] = str(tools) + os.pathsep + self.env["PATH"]
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--tier", "static", "--pre-push"],
            cwd=self.repo, capture_output=True, text=True, env=env, timeout=120,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        spawned = log.read_text().splitlines()
        pinned = f"uvx --python {sys.executable} pre-commit"
        self.assertTrue(spawned, proc.stdout + proc.stderr)
        for line in spawned:
            self.assertTrue(line.startswith(pinned), line)
        printed = re.search(r"复现 ruff（fail）: (.*)", proc.stdout)
        self.assertIsNotNone(printed, proc.stdout)
        ran_ruff = [line for line in spawned if line.startswith(pinned + " run ruff ")]
        self.assertTrue(ran_ruff, spawned)
        self.assertTrue(ran_ruff[-1].startswith(printed.group(1)),
                        f"printed {printed.group(1)!r} ran {ran_ruff[-1]!r}")

    # --- and the preflight follows what the run will spawn, not the raw scope:
    # --- with the three node hooks out of the selection, a frontend-scoped
    # --- push from a checkout that never ran `task fe:install` is judged by
    # --- the guards and the two python ratchets instead of reporting blocked
    # --- with nothing run. Without the flag the same checkout still blocks,
    # --- because those three are selected again.
    def test_pre_push_static_judges_without_node_modules(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/x.ts")
        shutil.rmtree(self.repo / "frontend/node_modules")
        proc = self.run_ci_fast("--pre-push")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ran = self.ran_hooks()
        self.assertTrue({"scene-ratchet", "catalog-ratchet"} <= ran)
        self.assertIn("file-size", ran)
        self.assertNotIn("blocked", proc.stdout)
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("node_modules", proc.stdout)

    # --- the footer names a pre-commit hook only for something that is one.
    # --- `commit-title` is invoked directly and the plan's checks are run by
    # --- this tool itself, so `pre-commit run commit-title` for those answers
    # --- "No hook with id ..." (reported 2026-10-12): they name the command
    # --- that ran, or the input they were missing.
    def test_pre_push_does_not_dress_plan_checks_as_hooks(self):
        self.write_hooks(failing={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        proc = self.run_ci_fast("--tier", "quick", "--pre-push",
                                "--pull-request", str(self.tmp / "absent.json"),
                                env_extra=self.quick_tools())
        self.assertRegex(proc.stdout, r"复现 ruff（fail）: .*run ruff --all-files --hook-stage manual")
        self.assertIn("复现 commit-title", proc.stdout)
        self.assertIn("cannot read --pull-request", proc.stdout)
        self.assertNotRegex(proc.stdout, r"复现 commit-title.*pre-commit run")
        # --- the plan's rows are the other branch of `reproduce` — the one
        # --- with a command of its own — so `commit-title` alone would leave a
        # --- regression there green. `backend-pure` runs in this very run, and
        # --- its line has to say where to run it: from the repository root the
        # --- argv cannot find its runner (reported 2026-10-12).
        self.assertIn("复现 backend-pure", proc.stdout)
        self.assertNotRegex(proc.stdout, r"复现 backend-pure.*pre-commit run")
        self.assertRegex(proc.stdout, r"复现 backend-pure（[^）]*）: cd \S*/backend && .*pytest")

    # --- leaving the node hooks out of the run is not a licence to lose them
    # --- from the config: the drift check reads the whole set before the
    # --- filter, so a config that no longer defines one is still unknown
    def test_pre_push_still_catches_config_drift(self):
        self.write_hooks(omit={"eslint"})
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast("--pre-push")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("eslint", proc.stdout)
        self.assertEqual(self.report()["status"], "unknown")

    # --- a plain run is untouched: the node hooks are selected and executed
    # --- exactly as before (the narrowing belongs to the flag, not the tool)
    def test_without_pre_push_the_node_hooks_still_run(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/x.ts")
        proc = self.run_ci_fast()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue({"eslint", "stylelint", "boundary-check-frontend"} <= self.ran_hooks())
        self.assertFalse(self.report()["pre_push"])

    # --- the spawn and the printed line read CI_FAST_PRECOMMIT at two
    # --- separate sites, and both workflows set it. The test above strips it,
    # --- so it covers only the default branch: with the variable set, a
    # --- footer that fell back to the default invocation would print a line
    # --- the run never used and stay green.
    def test_pre_push_prints_the_pre_commit_it_spawned_with_ci_fast_precommit(self):
        self.write_hooks(failing={"ruff"})
        self.make_base_and_head("backend/app/x.py")
        tools = self.tmp / "tools"
        tools.mkdir()
        log = self.tmp / "uvx-argv"
        uvx = tools / "uvx"
        uvx.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$(basename "$0") $*" >> {shlex.quote(str(log))}\n'
            'case "$*" in\n'
            '  *--version*) echo "pre-commit 4.7.0"; exit 0 ;;\n'
            'esac\n'
            "exit 1\n"
        )
        uvx.chmod(0o755)
        proc = self.run_ci_fast(
            "--pre-push",
            env_extra={"PATH": str(tools) + os.pathsep + self.env["PATH"],
                       "CI_FAST_PRECOMMIT": "uvx pre-commit"},
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        printed = re.search(r"复现 ruff（fail）: (.*)", proc.stdout)
        self.assertIsNotNone(printed, proc.stdout)
        self.assertTrue(printed.group(1).startswith("uvx pre-commit run ruff"),
                        f"printed {printed.group(1)!r}, but CI_FAST_PRECOMMIT says uvx pre-commit")
        ran_ruff = [line for line in log.read_text().splitlines() if " run ruff " in line]
        self.assertTrue(ran_ruff, proc.stdout)
        self.assertTrue(ran_ruff[-1].startswith(printed.group(1)),
                        f"printed {printed.group(1)!r} ran {ran_ruff[-1]!r}")

    # --- `--types` adds `frontend-typecheck`, the one node hook the pre-push
    # --- filter does not drop, so a static pre-push run that asked for types
    # --- still needs frontend/node_modules. Asking the selection rather than
    # --- the flag is what keeps this blocked instead of passing with the check
    # --- it selected never run.
    def test_pre_push_with_types_still_gates_on_node_modules(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/x.ts")
        shutil.rmtree(self.repo / "frontend/node_modules")
        proc = self.run_ci_fast("--pre-push", "--types")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("node_modules", proc.stdout)

    # --- the tier is the other half of the same question, and the only run
    # --- where it is the whole answer: off the static tier the pre-push filter
    # --- still drops the three node hooks, so the selection can say nothing
    # --- about node while the tier's own remaining checks still type-check the
    # --- frontend. Blocked for the missing dependency, not red by a toolchain
    # --- that was never installed.
    def test_pre_push_off_the_static_tier_still_gates_on_node_modules(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/x.ts")
        shutil.rmtree(self.repo / "frontend/node_modules")
        # `run_ci_fast` writes `--tier static` first and the last one wins.
        proc = self.run_ci_fast("--tier", "quick", "--pre-push")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("node_modules", proc.stdout)

    # --- a config that is not text is a blocked run with a sentence, not a
    # --- traceback: `UnicodeDecodeError` is a `ValueError`, so the `except
    # --- OSError` that reads like it covers an unreadable file does not, and
    # --- the footer raised out of the handler that was already reporting the
    # --- verdict (reported 2026-10-12).
    def test_pre_push_reports_an_unreadable_config_as_blocked(self):
        self.write_hooks()
        self.make_base_and_head("backend/app/x.py")
        (self.repo / ".pre-commit-config.yaml").write_bytes(
            b"repos: []\n# raw 0x80 follows: \x80\n"
        )
        proc = self.run_ci_fast("--pre-push")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("cannot read .pre-commit-config.yaml", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(self.report()["status"], "blocked")

    # --- the entry point is documented as runnable by hand, so the guard that
    # --- sends it away from outside a work tree has to actually fire: `cd ""`
    # --- returns 0 in dash and in bash, so testing `cd`'s status is not
    # --- testing that a directory was found, and the report would run against
    # --- the caller's directory while still exiting 0.
    def test_the_entry_point_stays_out_of_the_way_outside_a_work_tree(self):
        """Nothing runs when there is no work tree to check.

        Pins the behaviour, not either guard on its own: deleting the
        assignment's `|| exit 0` alone leaves this green, because the
        `[ -n "$root" ]` line under it answers the same state.
        """
        entry = SCRIPT.parents[2] / ".cheese/pre-push"
        tools = self.tmp / "tools"
        tools.mkdir()
        marker = self.tmp / "ran-ci-fast"
        python = tools / "python3"
        python.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 0\n")
        python.chmod(0o755)
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        proc = subprocess.run(
            [str(entry)], cwd=elsewhere, capture_output=True, text=True, timeout=60,
            env={"PATH": str(tools) + os.pathsep + self.env["PATH"]},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(marker.exists(), "the report ran outside a work tree")

    # --- a delivery push has no terminal: only what went wrong is worth a file
    def test_the_entry_point_leaves_a_report_only_from_a_run_that_failed(self):
        """The platform reads this file back and shows it in the room.

        On a delivery push the script's stdout goes to a pipe nobody reads, so
        the caller asks for the report by name instead. Only a run that did not
        pass writes it: a passing run has nothing to act on, and a room told
        about one would be told to look for a problem that is not there.

        The stub also records what it was asked to run, because the argv is the
        entry point's whole job: `--pre-push` is the flag that drops the three
        node-toolchain hooks, and `--tier static` is what the report calls the
        draft tier's scope. A stub that ignores its arguments would not notice
        the day either one changed.
        """
        entry = SCRIPT.parents[2] / ".cheese/pre-push"
        tools = self.tmp / "tools"
        tools.mkdir()
        work = self.tmp / "work"
        work.mkdir()
        git(work, "init", "-b", "main")
        report = self.tmp / "report.txt"
        argv = self.tmp / "argv"
        env = {
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "CI_FAST_PRE_PUSH_REPORT": str(report),
        }
        python = tools / "python3"
        python.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(argv))}\n"
            "echo 'ruff 报了一个错'\n"
            "exit 0\n"
        )
        python.chmod(0o755)

        passed = subprocess.run(
            [str(entry)], cwd=work, capture_output=True, text=True, timeout=60,
            env=env,
        )
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertIn("ruff 报了一个错", passed.stdout)
        self.assertFalse(
            report.exists(), "a passing run left a report for the room"
        )
        self.assertEqual(
            argv.read_text().splitlines(),
            [".github/scripts/ci-fast.py", "--tier", "static", "--pre-push"],
            "the entry point ran something other than the draft tier's checks",
        )

        python.write_text("#!/bin/sh\necho 'ruff 报了一个错'\nexit 1\n")
        failed = subprocess.run(
            [str(entry)], cwd=work, capture_output=True, text=True, timeout=60,
            env=env,
        )
        self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self.assertEqual(report.read_text(), "ruff 报了一个错\n")

    # --- the other half of "a report, not a gate": nobody asked for a report,
    # --- so the run has a reader and the status is what it is worth. The
    # --- documented way to use this by hand is to link it into your own
    # --- `.git/hooks/pre-push`, where git honours what it returns; the
    # --- platform's own push asks for the report by name, which is what caps
    # --- it at 0 above.
    def test_the_entry_point_hands_a_direct_run_the_checks_status(self):
        """Run by hand this is a hook; asked for a report, it is a report.

        `--pre-push`'s own promise (`ci-fast.md`: the exit code is unchanged,
        1 for a real failure, 2/3 for unknown/blocked) is a promise about the
        checks, and the direct run is where a caller reads it.
        """
        entry = SCRIPT.parents[2] / ".cheese/pre-push"
        tools = self.tmp / "tools"
        tools.mkdir()
        work = self.tmp / "work"
        work.mkdir()
        git(work, "init", "-b", "main")
        python = tools / "python3"
        env = {"PATH": str(tools) + os.pathsep + os.environ["PATH"]}

        python.write_text("#!/bin/sh\necho 'ruff 报了一个错'\nexit 0\n")
        python.chmod(0o755)
        passing = subprocess.run(
            [str(entry)], cwd=work, capture_output=True, text=True, timeout=60,
            env=env,
        )
        self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)

        python.write_text("#!/bin/sh\necho 'ruff 报了一个错'\nexit 1\n")
        failing = subprocess.run(
            [str(entry)], cwd=work, capture_output=True, text=True, timeout=60,
            env=env,
        )
        self.assertEqual(failing.returncode, 1, failing.stdout + failing.stderr)
        self.assertIn("ruff 报了一个错", failing.stdout)

    # --- the wall clock the header and `docs/workflows.md` both advertise, and
    # --- the one thing the tier's own budgets cannot cover: a machine that
    # --- cannot reach pre-commit's index hangs before any check starts. On the
    # --- delivery path that hang would eat the budget the push shares with the
    # --- hook, so the kill has to arrive long before it.
    def test_the_entry_point_cuts_a_run_that_outlasts_its_wall_clock(self):
        """A hung check must not become a hung push.

        The stub replaces itself with `sleep`, so the `TERM` the wall clock
        sends lands on the process the pipe is waiting for and the run really
        ends there — a shell that kept a grandchild alive would hold the
        command substitution open for the whole sleep.
        """
        entry = SCRIPT.parents[2] / ".cheese/pre-push"
        tools = self.tmp / "tools"
        tools.mkdir()
        work = self.tmp / "work"
        work.mkdir()
        git(work, "init", "-b", "main")
        python = tools / "python3"
        python.write_text("#!/bin/sh\nexec sleep 30\n")
        python.chmod(0o755)
        started = time.monotonic()
        proc = subprocess.run(
            [str(entry)], cwd=work, capture_output=True, text=True, timeout=60,
            env={
                "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                "CI_FAST_PRE_PUSH_REPORT": str(self.tmp / "report.txt"),
                "CI_FAST_PRE_PUSH_TIMEOUT": "1",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertLess(
            time.monotonic() - started, 15, "the wall clock did not fire"
        )

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

    def quick_tools(self, passed=2, skipped=0, missing_title=False, preamble="",
                    draft=False, gh_title="feat: actual PR title"):
        tools = self.tmp / "tools"
        tools.mkdir()
        # What `gh pr view --json title,draft --jq ...` prints: the draft flag
        # and the title, on one line, tab-separated.
        gh_line = ("true" if draft else "false") + "\t" + gh_title
        body = f'''#!{sys.executable}
import json, pathlib, sys
args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == "gh":
    print({preamble!r})  # a platform/credential notice before the fields
    print({gh_line!r})
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

    def test_quick_local_read_does_not_judge_a_draft_title(self):
        # `task ci:fast` passes no --pull-request, so the title and the draft
        # flag come from `gh pr view`. The rule is about the PR, not about
        # which argv carried the title in: a draft's placeholder is the
        # platform's, and judging it here reds the contributor's own run for
        # text they never wrote.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick",
                                env_extra=self.quick_tools(draft=True, gh_title="WIP: 修点什么"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rep = self.report()
        self.assertTrue(rep["title"]["draft"])
        self.assertIsNone(rep["title"]["value"])
        self.assertNotIn("commit-title", [row["id"] for row in rep["ran"]])
        self.assertIn("commit-title", [item["id"] for item in rep["not_run"]])

    def test_quick_local_read_judges_a_ready_title(self):
        # The same read with the PR marked ready: the title is the change's own
        # again, so a non-conforming one still fails.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick",
                                env_extra=self.quick_tools(gh_title="a bad title"))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(self.report()["title"]["value"], "a bad title")

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

    def test_quick_backend_does_not_judge_what_the_tier_cannot_run(self):
        # The pinned Claude Code/Codex/pi builds come from the environment; a
        # tier that does not install them gets FAILURES, not skips, from the
        # tests that reach one. They are reported not run and left out of the
        # pytest file list — while staying in required, so a green run still
        # means the change's pinpointed consumers ran.
        self.write_hooks()
        no_harness = {"CHEESE_TEST_CLAUDE": "", "CHEESE_TEST_CODEX": "", "CHEESE_TEST_PI": ""}
        # app/wheel.py is a frontend type-check input now — the API types are
        # generated from the app — so this change pulls the frontend types check
        # into a quick run. Put its stubs beside the pytest one, as a real
        # checkout would have them, so what the assertions below measure is the
        # harness split and not a missing pnpm.
        env = self.quick_tools()
        self.backend_tools(covered="tests/unit/test_spins.py", folder=self.tmp / "tools")
        self.commit_file("backend/tests/pinned_claude.py", "def claude_binary(): raise SystemExit\n")
        self.commit_file("backend/tests/unit/test_needs_pinned.py",
                         "from tests.pinned_claude import claude_binary\nfrom app.wheel import spin\n")
        self.commit_file("backend/tests/unit/test_spins.py", "from app.wheel import spin\n")
        self.commit_file("backend/app/wheel.py", "spin = 0\n")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit_file("backend/app/wheel.py", "spin = 1\n")
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: skip the harness",
                                env_extra={**env, **no_harness})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rep = self.report()
        row = next(r for r in rep["ran"] if r["id"] == "backend-pure")
        self.assertNotIn("tests/unit/test_needs_pinned.py", row["command"])
        self.assertIn("tests/unit/test_spins.py", row["command"])
        self.assertIn("tests/unit/test_needs_pinned.py", row["evidence"]["required"])
        self.assertEqual([item["id"] for item in rep["not_run"] if item["id"].endswith("pinned-harness")],
                         ["backend-pure:pinned-harness"])
        # The builds being present is what decides it, not the tier: the same
        # change runs that test again.
        with_harness = {v: sys.executable for v in no_harness}
        proc = self.run_ci_fast("--tier", "quick", "--title", "feat: run the harness",
                                env_extra={**env, **with_harness})
        row = next(r for r in self.report()["ran"] if r["id"] == "backend-pure")
        self.assertIn("tests/unit/test_needs_pinned.py", row["command"])

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

    def test_quick_draft_title_is_reported_not_run(self):
        # A draft carries the platform's placeholder title ("WIP: <task title>"),
        # which says nothing about the change: judging it fails a draft for text
        # it does not own. check-commit-title.py skips a draft the same way, and
        # the PR's title is checked once it is marked ready for review.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        payload = self.tmp / "pull.json"
        payload.write_text(json.dumps({"number": 7, "title": "WIP: 修点什么", "draft": True}))
        proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        rep = self.report()
        self.assertTrue(rep["title"]["draft"])
        self.assertIsNone(rep["title"]["value"])
        self.assertNotIn("commit-title", [row["id"] for row in rep["ran"]])
        self.assertIn("commit-title", [item["id"] for item in rep["not_run"]])

    def test_quick_pull_request_title_is_checked_when_ready(self):
        # The same payload with the PR marked ready: the title is the change's
        # own again, so a non-conforming one fails as before.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        payload = self.tmp / "pull.json"
        payload.write_text(json.dumps({"number": 7, "title": "a bad title", "draft": False}))
        proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(self.report()["title"]["value"], "a bad title")

    def test_quick_reads_the_title_from_an_event_payload(self):
        # A workflow can hand over the event JSON instead, which nests the PR.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        payload = self.tmp / "event.json"
        payload.write_text(json.dumps({"number": 7, "pull_request": {
            "title": "feat: from the event", "draft": False}}))
        proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.report()["title"]["value"], "feat: from the event")

    def test_quick_unreadable_pull_request_is_unknown(self):
        # A payload that is not JSON (gh writing its error into the file, say)
        # leaves both the title and the draft flag unread: that one check is
        # unknown, never a pass, and never a judgement on a title nobody read.
        # It is the title alone that is lost — the rest of the tier still runs,
        # which is what the rows below hold.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        payload = self.tmp / "pull.json"
        payload.write_text("gh: Not Found (HTTP 404)")
        proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 2, proc.stdout)
        rep = self.report()
        self.assertEqual(rep["status"], "unknown")
        rows = {row["id"]: row for row in rep["ran"]}
        self.assertEqual(rows["commit-title"]["result"], "unknown")
        self.assertIn("pull-request", rows["commit-title"]["reason"])
        self.assertIn("file-size", rows, "the tier judged nothing at all")

    def test_an_unreadable_pull_request_still_judges_the_change(self):
        # The same payload with a check that really fails: the failure must
        # reach the verdict. Aborting at the payload instead reported unknown
        # having executed nothing, so a failure in the same commit went unseen
        # and the draft was green — the false green this tier exists to remove.
        self.write_hooks(failing={"file-size"})
        self.make_base_and_head("frontend/src/probe.ts")
        payload = self.tmp / "pull.json"
        payload.write_text("gh: Not Found (HTTP 404)")
        proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(self.report()["status"], "fail")
        self.assertIn("file-size", self.ran_hooks())

    def test_quick_pull_request_that_is_not_an_object_is_unknown(self):
        # JSON that parses but is not a PR object — a scalar, a list, a
        # `pull_request` that is not an object. Reading a title out of it used
        # to raise, and an exception nobody catches exits 1: the caller reads
        # that as "this change is red", which is a red for a payload nobody
        # could read. No readable title is unknown, with the reason recorded.
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        tools = self.quick_tools()
        for text in ("null", "[]", '"feat: x"', "5"):
            payload = self.tmp / "pull.json"
            payload.write_text(text)
            proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                    env_extra=tools)
            self.assertEqual(proc.returncode, 2, f"{text}: {proc.stdout}{proc.stderr}")
            report = self.report()
            self.assertEqual(report["status"], "unknown", text)
            self.assertIn("pull-request", " ".join(report["reasons"]), text)
            self.assertIn("file-size", [row["id"] for row in report["ran"]], text)
        # ...and a PR object whose title is not a string is the same story: it
        # would reach check-commit-title.py as an argv element and raise there.
        payload = self.tmp / "pull.json"
        payload.write_text('{"title": 5, "draft": false}')
        proc = self.run_ci_fast("--tier", "quick", "--pull-request", str(payload),
                                env_extra=tools)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIsNone(self.report()["title"]["value"])

    def test_quick_invalid_title_fails(self):
        self.write_hooks()
        self.make_base_and_head("frontend/src/probe.ts")
        proc = self.run_ci_fast("--tier", "quick", "--title", "a bad title", env_extra=self.quick_tools())
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(self.report()["title"]["value"], "a bad title")

    def test_global_budget_records_all_remaining_checks(self):
        # The budget also covers the setup before the first check and the
        # readiness check (`pre-commit --version`) itself. On the shared
        # self-hosted runners both cost seconds — several queue entries run on
        # one machine, and `uvx pre-commit` resolves on a cold cache — so a
        # budget of 0.5s, and of 3s, turned red here for exactly that reason:
        # the run reported unknown having recorded no check at all, which is
        # what this test exists to catch. Warm the readiness command first and
        # give the budget room to reach the checks. It is then spent by a real
        # (slow) stub hook, so nothing here is stubbed away.
        self.write_hooks(slow={"file-size"})
        self.make_base_and_head("frontend/src/probe.ts")
        subprocess.run([*shlex.split(self.env.get("CI_FAST_PRECOMMIT", "pre-commit")), "--version"],
                       cwd=self.repo, capture_output=True, env=self.env, timeout=120)
        proc = self.run_ci_fast("--budget", "8", "--timeout", "30")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertLess(self.report()["seconds"], 20)
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
        paths, reason, targets, _ = select_backend(self.root, ["backend/app/invoice.py"])
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
        paths, _, _, _ = select_backend(self.root, ["backend/app/gone.py"])
        self.assertIn("tests/unit/test_any_name.py", paths)

    def test_backend_unknown_module_and_meta_widen(self):
        from ci_fast_backend import select_backend
        self.put("backend/tests/unit/test_a.py", "def test_it(): pass\n")
        self.put("backend/tests/unit/test_b.py", "def test_it(): pass\n")
        for changed in (["backend/app/new.py"], ["backend/tests/conftest.py"], ["backend/pyproject.toml"]):
            paths, reason, targets, _ = select_backend(self.root, changed)
            self.assertIn("tests/unit/test_a.py", paths)
            self.assertIn("tests/unit/test_b.py", paths)
            self.assertIn("fallback", reason)
            self.assertEqual(targets, [], "a whole-selection fallback pinpoints nothing")

    def test_backend_literal_module_import_is_selected(self):
        from ci_fast_backend import select_backend
        self.put("backend/app/special.py", "VALUE = 1\n")
        self.put("backend/tests/unit/test_indirect.py", 'import importlib\nx = importlib.import_module("app.special")\n')
        paths, _, _, _ = select_backend(self.root, ["backend/app/special.py"])
        self.assertIn("tests/unit/test_indirect.py", paths)

    def test_backend_pinned_harness_tests_are_named_not_run(self):
        # The pinned Claude Code / Codex / pi builds come from the environment
        # (CHEESE_TEST_*), installed by the full CI and by dev-db.sh; a test that
        # reaches one FAILS where they are absent, it does not skip. A tier that
        # does not have them must report such a test not run rather than let that
        # failure judge the change — while still requiring it, because a change
        # whose pinpointed consumers never executed was not judged.
        from ci_fast_backend import select_backend
        self.put("backend/tests/pinned_claude.py", "def claude_binary(): raise SystemExit\n")
        self.put("backend/app/wheel.py", "spin = 1\n")
        self.put("backend/tests/unit/test_spins_the_wheel.py", "from app.wheel import spin\n")
        self.put("backend/tests/unit/test_needs_the_build.py",
                 "from app.wheel import spin\nfrom tests.pinned_claude import claude_binary\n")
        self.put("backend/tests/unit/test_reads_the_variable.py",
                 'import os\nfrom app.wheel import spin\nVAR = os.environ.get("CHEESE_TEST_PI")\n')
        selected, reason, targets, omitted = select_backend(
            self.root, ["backend/app/wheel.py"], harness_gap="missing: CHEESE_TEST_PI")
        self.assertEqual(omitted, ["tests/unit/test_needs_the_build.py",
                                   "tests/unit/test_reads_the_variable.py"])
        for path in omitted:
            self.assertNotIn(path, selected)
            self.assertIn(path, targets, "a change nobody ran a test for is not judged")
        self.assertIn("tests/unit/test_spins_the_wheel.py", selected)
        self.assertIn("pinned-harness", reason)
        # Nothing is dropped where the builds are: the same change runs them.
        selected, _, _, omitted = select_backend(self.root, ["backend/app/wheel.py"])
        self.assertEqual(omitted, [])
        self.assertIn("tests/unit/test_needs_the_build.py", selected)

    def test_backend_harness_omission_follows_imports_only(self):
        # Naming a file is a selection dependency — the legacy-error ratchet
        # names every file it counts — but it is not a reason a test needs the
        # pinned build. Following it here would drop a test this tier can run.
        from ci_fast_backend import select_backend
        self.put("backend/tests/pinned_claude.py", "def claude_binary(): raise SystemExit\n")
        self.put("backend/tests/unit/test_runs_the_build.py",
                 "from tests.pinned_claude import claude_binary\n")
        self.put("backend/tests/unit/test_counts_a_file.py",
                 'COUNTED = "backend/tests/unit/test_runs_the_build.py"\n')
        selected, _, targets, omitted = select_backend(
            self.root, ["backend/tests/unit/test_runs_the_build.py"], harness_gap="missing: x")
        self.assertEqual(omitted, ["tests/unit/test_runs_the_build.py"])
        self.assertIn("tests/unit/test_counts_a_file.py", selected)
        self.assertIn("tests/unit/test_counts_a_file.py", targets)

    def test_backend_harness_omission_ignores_the_dynamic_widening(self):
        # A module whose import list is not fully known is a consumer of every
        # module for selection: app.main loads its routes that way. That
        # over-approximation must not answer "who needs the pinned harness" — it
        # would make app.main a harness consumer and every test that builds the
        # app through it a harness test, dropping most of the suite from a tier
        # that can run it.
        #
        # The fixture has to use the shape that widens. `import_module` with a
        # variable is what app/main.py does (`name = f"{routes_pkg...}";
        # importlib.import_module(name)`); a string literal is a *static* edge
        # in this selector, so a fixture written with one exercises no widening
        # at all and would pass whatever the harness question did with it. The
        # changed module is one nothing names statically, so the test below is
        # reached only through the widening.
        from ci_fast_backend import select_backend
        self.put("backend/tests/pinned_claude.py", "def claude_binary(): raise SystemExit\n")
        self.put("backend/app/plugin.py", "PLUGIN = 1\n")
        self.put("backend/app/main.py",
                 'import importlib\nname = "app.plugin"\napp = importlib.import_module(name)\n')
        self.put("backend/tests/unit/test_builds_the_app.py", "from app.main import app\n")
        selected, _, _, omitted = select_backend(
            self.root, ["backend/app/plugin.py"], harness_gap="missing: x")
        self.assertIn("tests/unit/test_builds_the_app.py", selected,
                      "the dynamic widening did not reach the test")
        self.assertEqual(omitted, [])

    def test_pinned_harness_gap_wants_files_not_names(self):
        from ci_fast_backend import pinned_harness_gap
        build = self.root / "bin" / "build"
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_text("#!/bin/sh\n")
        installed = {v: str(build) for v in
                     ("CHEESE_TEST_CLAUDE", "CHEESE_TEST_CODEX", "CHEESE_TEST_PI")}
        self.assertEqual(pinned_harness_gap(installed), "")
        self.assertEqual(pinned_harness_gap({}),
                         "missing: CHEESE_TEST_CLAUDE, CHEESE_TEST_CODEX, CHEESE_TEST_PI")
        # A variable pointing at a path nobody installed is not a build.
        self.assertEqual(pinned_harness_gap({**installed, "CHEESE_TEST_CODEX": str(self.root / "gone")}),
                         "missing: CHEESE_TEST_CODEX")
        # Nor is a path that exists but is not a file — a directory, say. The
        # build is what the tests execute, and a directory at that path would
        # only reach them as a spawn failure: `exists` would count it, and this
        # tier would then run the harness tests it cannot run.
        where = self.root / "bin" / "not-a-build"
        where.mkdir(parents=True, exist_ok=True)
        self.assertEqual(pinned_harness_gap({**installed, "CHEESE_TEST_PI": str(where)}),
                         "missing: CHEESE_TEST_PI")

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
        # A backend route change moves the API document the frontend types are
        # generated from, and with it the types-only src/api/openapi.d.ts.
        # Nothing in frontend/src reads the document at runtime, so the suite
        # would be checking nothing new; the type check is what has to widen.
        from ci_fast_checks import test_plan
        self.put("frontend/src/api/openapi.d.ts", "export interface paths {}\n")
        self.put("frontend/src/use.ts", "export const x = 1\n")
        route = "backend/app/api/routes/doctors.py"
        plan, omissions = test_plan(
            self.root, [route, "frontend/src/api/openapi.d.ts", "frontend/src/use.ts"],
            {"frontend": True}, self.root)
        unit = next(p for p in plan if p["id"] == "frontend-unit")
        self.assertEqual(unit["evidence"]["mode"], "related")
        # A declaration file cannot seed the graph, so it is not a root, and the
        # backend schema source is not frontend content a spec can execute.
        self.assertEqual(unit["evidence"]["roots"], ["src/use.ts"])
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))
        self.assertEqual(omissions, [])
        # Alone, the regenerated declaration and the schema source it was built
        # from run no test at all — that is a stated omission, not a suite-wide
        # run and not an unknown.
        plan, omissions = test_plan(self.root, [route, "frontend/src/api/openapi.d.ts"],
                                    {"frontend": True}, self.root)
        self.assertFalse(any(p["id"] == "frontend-unit" for p in plan))
        self.assertTrue(any(p["id"] == "frontend-i18n" for p in plan))
        self.assertEqual(omissions[0]["id"], "frontend-unit")
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))

    def test_backend_route_change_alone_requests_full_frontend_types(self):
        # The regression for the generated-schema change: the API document is no
        # longer committed, so its inputs stand in for it. A route or a model
        # the frontend's types are built from, the exporter, and the pins that
        # decide the exporter's FastAPI all have to reach the full type check,
        # under both readings of the change — the whole diff and the paths the
        # scope map routed into frontend scope.
        from ci_fast_checks import test_plan
        for kind, path in (("route", "backend/app/api/routes/doctors.py"),
                           ("model", "backend/app/models/doctor.py"),
                           ("exporter", "backend/scripts/export_openapi.py"),
                           ("pins", "backend/uv.lock"),
                           ("pyproject", "backend/pyproject.toml")):
            plan, omissions = test_plan(self.root, [path], {"frontend": True}, self.root)
            self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan), kind)
            # It seeds no frontend root and carries no runtime, so the suite is
            # stated as not run rather than expanded to.
            self.assertFalse(any(p["id"] == "frontend-unit" for p in plan), kind)
            self.assertEqual([o["id"] for o in omissions], ["frontend-unit"], kind)
            plan, _ = test_plan(self.root, [path], {"frontend": True}, self.root,
                                frontend_scope=[path])
            self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan), kind)
            self.assertFalse(any(p["id"] == "frontend-unit" for p in plan), kind)

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
        # The path map routes the backend schema sources into frontend scope, so
        # a backend change enters this branch through them. Whether the change
        # carries frontend runtime content is read from the paths the map routed
        # here, not from the whole change: an unrouted path beside them (a CLI
        # file the map never sends to frontend) would otherwise be read as
        # content the suite must execute and run it in full for a document no
        # test can load.
        from ci_fast_checks import test_plan
        self.put("frontend/src/api/openapi.d.ts", "export interface paths {}\n")
        routed = ["backend/app/api/routes/projects.py", "frontend/src/api/openapi.d.ts"]
        changed = routed + ["cli/cmd/root.go"]
        plan, omissions = test_plan(self.root, changed, {"frontend": True}, self.root,
                                    frontend_scope=routed)
        self.assertEqual([o["id"] for o in omissions], ["frontend-unit"])
        self.assertFalse(any(p["id"] == "frontend-unit" for p in plan))
        self.assertTrue(any(p["id"] == "frontend-typecheck-full" for p in plan))
        # With no scope from the caller the whole change is read, and the
        # unrouted path widens the run, as before.
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

    def test_a_check_that_never_measures_is_recorded_unmeasured(self):
        # The report has to say the sampling failed; a check read as bounded at
        # 0 MiB is the lie that flag exists to prevent. The check here outlives
        # the run of misses, so whether the flag is set is the whole test.
        import ci_fast_process
        from ci_fast_process import Unmeasured

        def miss(pgid, timeout=1.0):
            raise Unmeasured("no sample")

        original, ci_fast_process.group_rss = ci_fast_process.group_rss, miss
        self.addCleanup(setattr, ci_fast_process, "group_rss", original)
        result = ci_fast_process.execute(
            [sys.executable, "-c", "import time; time.sleep(2)"], "/tmp", 10
        )
        self.assertEqual(result["result"], "pass", result)
        self.assertTrue(result["unmeasured"], result)
        self.assertEqual(result["peak_rss_mib"], 0, result)

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
        # memory ceiling can be enforced at all. This is execute()'s half of
        # that; the sampler's half — that a missing ps raises this and not
        # Unmeasured — is the test below.
        import ci_fast_process
        from ci_fast_process import MissingPs

        def gone(pgid, timeout=1.0):
            raise MissingPs("no ps")

        original, ci_fast_process.group_rss = ci_fast_process.group_rss, gone
        self.addCleanup(setattr, ci_fast_process, "group_rss", original)
        result = ci_fast_process.execute([sys.executable, "-c", "pass"], "/tmp", 5)
        self.assertEqual(result["result"], "blocked", result)

    def test_ps_absent_is_told_apart_from_ps_that_did_not_answer(self):
        # Without this mapping a machine with no ps reads as a check that
        # passed with its ceiling skipped — the promise the exit-code table
        # makes, and the one thing that must stay blocked.
        import ci_fast_process

        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        with mock.patch.dict(os.environ, {"PATH": empty}):
            with self.assertRaises(ci_fast_process.MissingPs):
                ci_fast_process.group_rss(1)

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
