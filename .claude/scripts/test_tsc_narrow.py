#!/usr/bin/env python3
"""test_tsc_narrow.py — prove tsc-narrow.sh reports what it claims to.

Run directly:  python3 .claude/scripts/test_tsc_narrow.py

The script exists because the whole-tree `vue-tsc` does not fit a work
machine's memory and the ratchet reports only a count per file. What the tests
below pin down is the part a reader cannot see from the output: that a target
compiles to nothing but its own diagnostics, that a name used only in a type
annotation with no import is caught (the case eslint, the bundler and the tests
all miss), that the exit code separates "the targets have errors" from "this
run could not judge", and that the throwaway project is gone on every exit —
a narrow check that quietly leaves a tsconfig in the tree, or reports a git
that could not compute the diff as "nothing changed", would mislead exactly the
person reading it to find out why the type gate is red.

Argument handling, file selection and the exit-code map are tested here without
vue-tsc, against a throwaway repository whose `frontend/node_modules` holds a
stub binary. The stub records the project it was handed and exits with the
status the test names, so the config the script builds and every branch of the
status map run for real; only the compilation itself is replaced. The class
below it runs the real compiler and needs the frontend toolchain, so it skips
where that is not installed — but it no longer takes these tests down with it.

Not collected by repo-guards.yml: that job names each `.claude/scripts`
self-test individually rather than globbing them, so this file runs nowhere in
CI today. Wiring it in is one line there — the `guards` scope already covers
`.claude/scripts/**` — and the selection tests need no frontend toolchain, so
they run on any runner. Until then, run it by hand.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().with_name("tsc-narrow.sh")
VUE_TSC = REPO_ROOT / "frontend" / "node_modules" / ".bin" / "vue-tsc"

#: A repository whose global config signs commits or points GIT_DIR elsewhere
#: would break every fixture here, and the breakage would look like the
#: assertion under test rather than like a broken fixture.
CLEAN_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
CLEAN_GIT_ENV.pop("GIT_DIR", None)


class SelectionTest(unittest.TestCase):
    """What the script reads, refuses and chooses — no compiler involved."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="tsc-narrow-selftest-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / "repo"
        (self.root / ".claude" / "scripts").mkdir(parents=True)
        (self.root / "frontend" / "node_modules" / ".bin").mkdir(parents=True)
        (self.root / "frontend" / "src").mkdir()
        (self.root / "frontend" / "env.d.ts").write_text("")
        self.script = self.root / ".claude" / "scripts" / "tsc-narrow.sh"
        self.script.write_bytes(SCRIPT.read_bytes())
        stub = self.root / "frontend" / "node_modules" / ".bin" / "vue-tsc"
        stub.write_text(
            "#!/bin/sh\n"
            "# Stand-in for vue-tsc: hand the test the project it was given, then\n"
            "# exit with the status the test asked for.\n"
            'if [ -n "${TSC_NARROW_STUB_CAPTURE:-}" ]; then\n'
            '  prev=""\n'
            '  for arg in "$@"; do\n'
            '    if [ "$prev" = "-p" ]; then cp "$arg" "$TSC_NARROW_STUB_CAPTURE"; fi\n'
            '    prev="$arg"\n'
            "  done\n"
            "fi\n"
            'exit "${TSC_NARROW_STUB_EXIT:-0}"\n'
        )
        stub.chmod(0o755)
        self.tmpdir = Path(self.scratch.name) / "tmp"
        self.tmpdir.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        # A local ref is enough; the script only asks git to resolve the name.
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=tsc-narrow self-test",
                "-c",
                "user.email=self-test@invalid",
                *args,
            ],
            cwd=self.root,
            env=CLEAN_GIT_ENV,
            check=True,
            capture_output=True,
            text=True,
        )

    def commit(self, message: str):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def base_at(self, revision: str = "HEAD"):
        """Move origin/main, the base the default file set is read from."""
        self.git("update-ref", "refs/remotes/origin/main", revision)

    def run_script(
        self,
        *args: str,
        generate: bool = False,
        env: dict | None = None,
        tmpdir: Path | None = None,
    ):
        argv = ["bash", str(self.script)]
        if not generate:
            argv.append("--no-generate")
        return subprocess.run(
            [*argv, *args],
            cwd=self.root,
            env={
                **CLEAN_GIT_ENV,
                "TMPDIR": str(self.tmpdir if tmpdir is None else tmpdir),
                **(env or {}),
            },
            capture_output=True,
            text=True,
        )

    def checked(self, result: subprocess.CompletedProcess) -> list[str]:
        """The files the run says it is about to check."""
        lines = result.stderr.splitlines()
        start = next(i for i, line in enumerate(lines) if "checking " in line)
        return [line.strip() for line in lines[start + 1 :] if line.startswith("  ")]

    def assert_temp_clean(self):
        """The throwaway project must not outlive the run, pass or fail."""
        self.assertEqual(
            list(self.tmpdir.iterdir()),
            [],
            "the script left files in TMPDIR instead of removing its project",
        )

    def test_a_deleted_path_is_skipped_and_the_rest_still_checked(self):
        # A branch that deletes or renames a frontend source: the diff names the
        # old path, which has no diagnostic to report. Aborting on it would
        # leave the files that do have one unchecked.
        (self.root / "frontend" / "src" / "gone.ts").write_text("export const a = 1\n")
        self.commit("add gone.ts")
        self.base_at()
        (self.root / "frontend" / "src" / "gone.ts").unlink()
        (self.root / "frontend" / "src" / "keep.ts").write_text("export const b = 2\n")
        self.commit("remove gone.ts, add keep.ts")

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.checked(result), ["frontend/src/keep.ts"])
        self.assert_temp_clean()

    def test_the_default_set_is_the_branch_changed_frontend_sources(self):
        for name in ("a.ts", "b.tsx", "c.vue", "d.spec.ts", "notes.md", "e.json"):
            (self.root / "frontend" / "src" / name).write_text("export const x = 1\n")
        (self.root / "frontend" / "env.d.ts").write_text("declare const y: number\n")
        self.commit("branch work")

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            sorted(self.checked(result)),
            [
                "frontend/src/a.ts",
                "frontend/src/b.tsx",
                "frontend/src/c.vue",
                "frontend/src/d.spec.ts",
            ],
        )

    def test_a_diff_that_cannot_be_computed_is_not_a_pass(self):
        # Shallow or unrelated history: `origin/main...HEAD` has no merge base.
        # Reading that as "this branch changed nothing" would report a clean
        # selection from a comparison that never happened.
        self.git("checkout", "-q", "--orphan", "other")
        self.git("commit", "-q", "--allow-empty", "-m", "unrelated")
        self.base_at()
        self.git("checkout", "-q", "main")
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        self.commit("branch work")

        result = self.run_script()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("failed", result.stderr)
        self.assertNotIn("no frontend sources", result.stderr)
        self.assert_temp_clean()

    def test_a_branch_with_no_frontend_change_is_a_pass(self):
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        self.commit("branch work")
        self.base_at()

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no frontend sources", result.stderr)

    def test_generation_runs_unless_it_is_turned_off(self):
        # The API types are generated, not committed, and a stale or missing
        # copy turns into TS2307 noise on every target that imports the API
        # layer — so the switch that suppresses it has to be the switch that is
        # wired, not an inverted or ignored one. It is watched here rather than
        # through whether the file lands, because generating it needs a backend.
        (self.root / "frontend" / "scripts").mkdir()
        (self.root / "frontend" / "scripts" / "gen-api-types.mjs").write_text("")
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")

        on = self.run_script("frontend/src/a.ts", generate=True)
        self.assertIn("generating frontend/src/api/openapi.d.ts", on.stderr)
        off = self.run_script("frontend/src/a.ts")
        self.assertNotIn("generating frontend/src/api/openapi.d.ts", off.stderr)

    def test_nothing_is_written_into_the_tree(self):
        (self.root / "frontend" / "scripts").mkdir()
        (self.root / "frontend" / "scripts" / "gen-api-types.mjs").write_text("")
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        self.commit("branch work")
        before = self.git("status", "--porcelain").stdout
        self.assertFalse(before.strip(), before)

        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("status", "--porcelain").stdout, before)
        self.assertFalse((self.root / "frontend" / "src" / "api" / "openapi.d.ts").exists())

    def test_a_named_file_that_does_not_exist_is_refused(self):
        # Named paths are the caller's to get wrong, unlike a path the branch
        # deleted — this one cannot be silently skipped or the caller gets a
        # pass for a typo.
        result = self.run_script("frontend/src/absent.ts")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no such file", result.stderr)

    def test_a_named_file_of_another_kind_is_refused(self):
        (self.root / "frontend" / "src" / "notes.md").write_text("hi\n")
        result = self.run_script("frontend/src/notes.md")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("not a TypeScript source file", result.stderr)

    def test_an_unknown_option_is_refused(self):
        result = self.run_script("--wat")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unknown option", result.stderr)

    def test_a_missing_toolchain_is_cannot_judge_not_a_failure(self):
        (self.root / "frontend" / "node_modules" / ".bin" / "vue-tsc").unlink()
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        result = self.run_script("frontend/src/a.ts")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("not installed", result.stderr)

    def test_the_project_compiles_the_named_files_and_their_ambient_types(self):
        # What the throwaway project has to say: exactly the targets as `files`,
        # the ambient declarations as `include`, and the real `baseUrl`/`paths`
        # so `@/*` resolves. Miss any and the run reports errors the tree does
        # not have.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        captured = Path(self.scratch.name) / "captured-tsconfig.json"
        result = self.run_script(
            "frontend/src/a.ts", env={"TSC_NARROW_STUB_CAPTURE": str(captured)}
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = json.loads(captured.read_text())
        frontend = self.root.resolve() / "frontend"
        self.assertEqual([Path(p).name for p in config["files"]], ["a.ts"])
        self.assertTrue(all(Path(p).is_absolute() for p in config["files"]))
        self.assertEqual(config["extends"], f"{frontend}/tsconfig.json")
        self.assertEqual(config["compilerOptions"]["baseUrl"], str(frontend))
        self.assertEqual(config["compilerOptions"]["paths"], {"@/*": ["src/*"]})
        self.assertIn(f"{frontend}/env.d.ts", config["include"])

    def test_compiler_diagnostics_are_a_failure(self):
        # The one compiler status that is a verdict on the targets. vue-tsc
        # answers 2 for a run that produced diagnostics.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        result = self.run_script("frontend/src/a.ts", env={"TSC_NARROW_STUB_EXIT": "2"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_temp_clean()

    def test_a_compiler_that_cannot_read_the_project_is_cannot_judge(self):
        # vue-tsc answers 1 for a config or options it could not read: no
        # diagnostics, no verdict.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        result = self.run_script("frontend/src/a.ts", env={"TSC_NARROW_STUB_EXIT": "1"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("not be read", result.stderr)
        self.assert_temp_clean()

    def test_a_killed_compiler_is_cannot_judge_not_a_failure(self):
        # The machine ran out of memory and the process died on a signal
        # (status >= 128). Blaming the targets for the machine's limit is the
        # failure this mapping exists to prevent.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        result = self.run_script("frontend/src/a.ts", env={"TSC_NARROW_STUB_EXIT": "134"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("killed", result.stderr)
        self.assert_temp_clean()

    def test_a_generation_failure_is_cannot_judge_not_a_type_error(self):
        # A checkout without the backend toolchain: the generator fails, and a
        # run that compiled on would report TS2307 for types that were never
        # generated — a verdict on code that was never read.
        (self.root / "frontend" / "scripts").mkdir()
        (self.root / "frontend" / "scripts" / "gen-api-types.mjs").write_text(
            "process.exit(3)\n"
        )
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        result = self.run_script("frontend/src/a.ts", generate=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("could not be generated", result.stderr)
        self.assert_temp_clean()

    def test_no_origin_main_is_cannot_judge(self):
        # The default file set is read from origin/main...HEAD. Without the ref
        # there is nothing to compare against, and an empty selection would read
        # as a branch that changed nothing.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        result = self.run_script()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("origin/main is not present", result.stderr)

    def test_an_unwritable_tmpdir_is_cannot_judge_not_a_failure(self):
        # mktemp's own failure status is 1, which this script reserves for "the
        # targets have type errors": a run that never started must not borrow it.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        missing = Path(self.scratch.name) / "no-such-tmpdir"
        named = self.run_script("frontend/src/a.ts", tmpdir=missing)
        self.assertEqual(named.returncode, 2, named.stdout + named.stderr)
        self.assertIn("mktemp", named.stderr)
        discovered = self.run_script(tmpdir=missing)
        self.assertEqual(discovered.returncode, 2, discovered.stdout + discovered.stderr)
        self.assertIn("mktemp", discovered.stderr)

    def test_a_python_that_fails_to_resolve_targets_is_cannot_judge(self):
        # `set -e` does not reach a command inside a process substitution, so a
        # python3 that died leaves the target array empty. Left unguarded, the
        # run compiles nothing and reports nothing found — a pass for a check
        # that never resolved its own arguments.
        (self.root / "frontend" / "src" / "a.ts").write_text("export const x = 1\n")
        bindir = Path(self.scratch.name) / "bin"
        bindir.mkdir()
        broken = bindir / "python3"
        broken.write_text("#!/bin/sh\nexit 3\n")
        broken.chmod(0o755)
        result = self.run_script(
            "frontend/src/a.ts",
            env={"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"},
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("could not resolve the target paths", result.stderr)

    def test_help_prints_the_script_header(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage: tsc-narrow.sh", result.stdout)
        self.assertIn("Exit codes", result.stdout)


@unittest.skipUnless(VUE_TSC.exists(), f"frontend toolchain missing: {VUE_TSC}")
class NarrowRunTest(unittest.TestCase):
    """A real narrow compile, against the repository's own frontend project."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="tsc-narrow-selftest-")
        self.addCleanup(self.scratch.cleanup)
        self.samples = Path(self.scratch.name) / "samples"
        self.samples.mkdir()
        self.tmpdir = Path(self.scratch.name) / "tmp"
        self.tmpdir.mkdir()

    def sample(self, name: str, text: str) -> Path:
        path = self.samples / name
        path.write_text(text)
        return path

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SCRIPT), "--no-generate", *args],
            cwd=REPO_ROOT,
            env={**os.environ, "TMPDIR": str(self.tmpdir)},
            capture_output=True,
            text=True,
        )

    def assert_temp_clean(self):
        self.assertEqual(
            list(self.tmpdir.iterdir()),
            [],
            "the script left files in TMPDIR instead of removing its project",
        )

    def test_a_valid_file_reports_nothing_and_succeeds(self):
        target = self.sample("fine.ts", "export const answer: number = 42\n")
        result = self.run_script(str(target))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("error TS", result.stdout)
        self.assert_temp_clean()

    def test_a_name_used_only_in_an_annotation_is_reported(self):
        # A refactor that drops `import type { CatalogEntry }` still leaves
        # every value-site reference resolvable; only the type check sees this.
        target = self.sample(
            "split.ts",
            "interface Row { id: string }\n"
            "export function pick(rows: Row[]): MissingName {\n"
            "  return rows[0] as MissingName\n"
            "}\n",
        )
        result = self.run_script(str(target))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("TS2304", result.stdout + result.stderr)
        self.assert_temp_clean()

    def test_the_worktree_is_not_touched_by_a_run(self):
        target = self.sample("fine.ts", "export const answer: number = 42\n")
        api_types = REPO_ROOT / "frontend" / "src" / "api" / "openapi.d.ts"
        before = api_types.stat().st_mtime_ns if api_types.exists() else None
        changed_before = self._changed_paths()
        self.run_script(str(target))
        self.assertEqual(self._changed_paths(), changed_before)
        after = api_types.stat().st_mtime_ns if api_types.exists() else None
        self.assertEqual(after, before, "--no-generate rewrote the API types")

    def _changed_paths(self) -> list[str]:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        ).stdout
        return [line for line in out.splitlines() if "tsc-narrow-selftest" not in line]


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
