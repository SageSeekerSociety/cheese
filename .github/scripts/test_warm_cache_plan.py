#!/usr/bin/env python3
"""Contracts of the warm-cache planner and of the workflow that runs it.

The planner is driven as a process against throwaway repositories: what it
reads is a push's commit range, and a test that hands it a list of paths would
skip the part that turns a range into one.

The workflow is read as text. There is no YAML library on the runners and this
is the only file in the tree that needs to judge a workflow's own conditions:
the gate that decides whether a domain warms, and the gate that decides
whether an exact cache hit still installs, are executed here in the small
expression subset they use — a term this file does not understand fails the
test rather than passing it.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
PLANNER = HERE / "warm-cache-plan.py"
WORKFLOW = HERE.parent / "workflows" / "warm-caches.yml"

JOBS = ("backend", "harness-bins", "guards", "remote-execution-npm", "go", "pnpm")
OUTPUTS = {job: job.replace("-", "_") for job in JOBS}

# Every path in the workflow's push filter, and the jobs that read it. The
# suite that restores each key is named in the workflow's header comment.
DEPENDENCIES = (
    ("backend/uv.lock", {"backend", "harness-bins"}),
    (".pre-commit-config.yaml", {"guards"}),
    (
        "backend/app/domain/agent/harness/claude_code/device_launch.py",
        {"backend", "harness-bins"},
    ),
    (
        "backend/app/domain/agent/harness/claude_code/remote_execution/client.py",
        {"backend", "harness-bins"},
    ),
    ("backend/app/domain/agent/harness/codex/pin.py", {"backend", "harness-bins"}),
    ("backend/app/domain/agent/harness/pi/launch.py", {"harness-bins"}),
    (
        "scripts/remote_execution/package-lock.json",
        {"remote-execution-npm"},
    ),
    ("cli/go.mod", {"go"}),
    ("cli/go.sum", {"go"}),
    ("frontend/pnpm-lock.yaml", {"pnpm"}),
    ("frontend/package.json", {"pnpm"}),
)

# Files that exist before the first push, so a delete and a rename have
# something to act on. `cli/go.mod` is deliberately absent: one rename moves a
# file into that path.
BASE_FILES = (
    ".pre-commit-config.yaml",
    "backend/uv.lock",
    "backend/app/domain/agent/harness/codex/pin.py",
    "backend/app/domain/agent/harness/pi/launch.py",
    "cli/go.sum",
    "frontend/pnpm-lock.yaml",
    "scripts/remote_execution/package-lock.json",
    "docs/notes.md",
)

# The jobs whose production command is skipped on an exact cache hit, and the
# command itself, so the gate is proven to sit on the real install.
GATED_INSTALLS = {
    "remote-execution-npm": (
        "Install scripts/remote_execution the way mcp-contract.yml does",
        "npm ci --prefix scripts/remote_execution",
    ),
    "go": (
        "Build the module, its vet targets and its test binaries",
        "go build ./...",
    ),
    "pnpm": (
        "Install dependencies the way frontend.yml does",
        "pnpm install --frozen-lockfile",
    ),
}


def load_planner():
    """The planner as a module, for the questions a process cannot ask."""
    spec = importlib.util.spec_from_file_location("warm_cache_plan", PLANNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlannerTests(unittest.TestCase):
    """A push is a commit range, so the fixture is a real repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")
        for path in BASE_FILES:
            self.write(path, "base\n")
        self.base = self.commit("base")

    def git(self, *args, check=True):
        result = subprocess.run(
            ("git", *args), cwd=self.repo, capture_output=True, text=True
        )
        if check and result.returncode:
            raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
        return result

    def write(self, path, text="change\n"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.write_text(target.read_text() + text)
        else:
            target.write_text(text)

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def push(self, *paths, delete=()):
        """Commit one push on top of the fixture's base, and name it."""
        self.git("reset", "--hard", "--quiet", self.base)
        self.git("clean", "-q", "-fd")
        for path in delete:
            self.git("rm", "-q", path)
        for path in paths:
            self.write(path)
        return self.commit("push")

    def push_and_plan(self, *paths, delete=()):
        """Plan a one-commit push from the fixture's base touching paths."""
        return self.plan(self.base, current=self.push(*paths, delete=delete))

    def plan(
        self,
        base,
        *,
        event="push",
        ref="refs/heads/main",
        current="HEAD",
        cwd=None,
        output=None,
    ):
        env = dict(os.environ)
        env.pop("GITHUB_OUTPUT", None)
        env.update(
            {
                "EVENT_NAME": event,
                "REF": ref,
                "BASE_SHA": base,
                "CURRENT_SHA": current,
            }
        )
        if output is not None:
            env["GITHUB_OUTPUT"] = str(output)
        result = subprocess.run(
            [sys.executable, "-I", str(PLANNER)],
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = output.read_text() if output is not None else result.stdout
        values = {}
        for line in text.splitlines():
            key, _, value = line.partition("=")
            values[key] = value
        self.assertEqual(sorted(values), sorted(OUTPUTS.values()), text)
        for value in values.values():
            self.assertIn(value, ("true", "false"), text)
        return {job for job, key in OUTPUTS.items() if values[key] == "true"}

    # -- which dependency selects which jobs -----------------------------

    def test_each_dependency_selects_exactly_its_jobs(self):
        for path, wanted in DEPENDENCIES:
            with self.subTest(path=path):
                self.assertEqual(self.push_and_plan(path), wanted)

    def test_one_push_selects_the_union_of_its_dependencies(self):
        self.assertEqual(
            self.push_and_plan("cli/go.sum", "frontend/pnpm-lock.yaml"),
            {"go", "pnpm"},
        )

    def test_a_push_touching_no_dependency_selects_nothing(self):
        self.assertEqual(self.push_and_plan("docs/notes.md"), set())

    # -- deletions and renames -------------------------------------------

    def test_deleting_a_dependency_still_warms_its_jobs(self):
        self.assertEqual(
            self.push_and_plan(delete=["backend/uv.lock"]),
            {"backend", "harness-bins"},
        )

    def test_a_rename_counts_on_the_side_it_left(self):
        self.git("reset", "--hard", "--quiet", self.base)
        self.git("clean", "-q", "-fd")
        self.git(
            "mv",
            "backend/app/domain/agent/harness/codex/pin.py",
            "docs/pin.py",
        )
        after = self.commit("move the codex pin out")
        self.assertEqual(
            self.plan(self.base, current=after), {"backend", "harness-bins"}
        )

    def test_a_push_covers_every_commit_since_the_base(self):
        """Two commits, one dependency in each: HEAD^ would see only the last."""
        self.git("reset", "--hard", "--quiet", self.base)
        self.git("clean", "-q", "-fd")
        self.write("backend/uv.lock")
        self.commit("bump the lock")
        self.write("frontend/pnpm-lock.yaml")
        after = self.commit("bump the frontend lock")
        self.assertEqual(
            self.plan(self.base, current=after), {"backend", "harness-bins", "pnpm"}
        )

    def test_a_dependency_changed_before_the_last_commit_still_counts(self):
        self.git("reset", "--hard", "--quiet", self.base)
        self.git("clean", "-q", "-fd")
        self.write("cli/go.sum")
        self.commit("bump go.sum")
        self.write("docs/notes.md")
        after = self.commit("a note that touches no dependency")
        self.assertEqual(self.plan(self.base, current=after), {"go"})

    def test_a_rename_counts_on_the_side_it_joined(self):
        self.git("reset", "--hard", "--quiet", self.base)
        self.git("clean", "-q", "-fd")
        self.git("mv", "docs/notes.md", "cli/go.mod")
        after = self.commit("move a note onto the go module")
        self.assertEqual(self.plan(self.base, current=after), {"go"})

    # -- bases and events it cannot establish ----------------------------

    def test_a_first_push_with_no_base_warms_everything(self):
        after = self.push("cli/go.sum")
        self.assertEqual(self.plan("0" * 40, current=after), set(JOBS))

    def test_a_base_the_checkout_does_not_have_warms_everything(self):
        after = self.push("cli/go.sum")
        self.assertEqual(self.plan("f" * 40, current=after), set(JOBS))

    def test_no_base_at_all_warms_everything(self):
        after = self.push("cli/go.sum")
        self.assertEqual(self.plan("", current=after), set(JOBS))

    def test_a_directory_that_is_not_a_repository_warms_everything(self):
        plain = pathlib.Path(self.tmp.name) / "plain"
        plain.mkdir()
        self.assertEqual(self.plan(self.base, cwd=plain), set(JOBS))

    def test_a_scheduled_run_warms_everything(self):
        self.assertEqual(self.plan(self.base, event="schedule"), set(JOBS))

    def test_a_manual_run_warms_everything(self):
        self.assertEqual(self.plan(self.base, event="workflow_dispatch"), set(JOBS))

    def test_a_push_to_another_branch_warms_everything(self):
        after = self.push("cli/go.sum")
        self.assertEqual(
            self.plan(self.base, ref="refs/heads/feature", current=after), set(JOBS)
        )

    def test_the_selector_and_its_test_warm_everything(self):
        for path in (
            ".github/workflows/warm-caches.yml",
            ".github/scripts/warm-cache-plan.py",
            ".github/scripts/test_warm_cache_plan.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.push_and_plan(path), set(JOBS))

    # -- the output the workflow consumes --------------------------------

    def test_github_output_receives_the_plan(self):
        after = self.push("cli/go.sum")
        target = pathlib.Path(self.tmp.name) / "output"
        self.assertEqual(self.plan(self.base, current=after, output=target), {"go"})
        written = target.read_text()
        self.assertIn("go=true", written)
        self.assertIn("pnpm=false", written)

    def test_every_path_the_workflow_watches_has_a_rule(self):
        planner = load_planner()
        for path in push_paths(WORKFLOW.read_text()):
            sample = path.replace("/**", "/sample.py")
            with self.subTest(path=path):
                self.assertNotEqual(
                    planner.select_jobs([sample]),
                    set(),
                    f"{path} triggers the workflow but selects no job",
                )


def push_paths(text):
    """The paths under `on.push`."""
    section = text.split("  push:", 1)[1].split("  schedule:", 1)[0]
    return re.findall(r"^      - (\S+)$", section, re.M)


def job_block(text, name):
    """One job of the workflow, found by the indentation of `jobs:`."""
    lines = text.splitlines()
    header = f"  {name}:"
    try:
        start = lines.index(header)
    except ValueError:
        raise AssertionError(f"the workflow has no job {name!r}") from None
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def steps(job):
    """The job's steps, split at their `      - ` markers."""
    lines = job.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("      - ")]
    blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]))
    return blocks


def step_named(job, name):
    for block in steps(job):
        if re.search(rf"^\s+- name: {re.escape(name)}$", block, re.M):
            return block
    raise AssertionError(f"no step named {name!r} in job:\n{job}")


def cache_step_ids(job):
    """The `id:` of every actions/cache step in the job, in order."""
    ids = []
    for block in steps(job):
        if "uses: actions/cache@" not in block:
            continue
        match = re.search(r"^\s+id: (\S+)$", block, re.M)
        if match:
            ids.append(match.group(1))
    return ids


def job_field(job, key):
    """A property of the job itself: four spaces, not a step's eight."""
    match = re.search(rf"^    {re.escape(key)}: (.+)$", job, re.M)
    if match is None:
        raise AssertionError(f"the job has no {key!r}:\n{job}")
    return match.group(1).strip()


def concurrency_field(job, key):
    """A property under the job's `concurrency:`, one level further in."""
    match = re.search(rf"^      {re.escape(key)}: (.+)$", job, re.M)
    if match is None:
        raise AssertionError(f"the job's concurrency has no {key!r}:\n{job}")
    return match.group(1).strip()


def step_field(step, key):
    match = re.search(rf"^\s+{re.escape(key)}: (.+)$", step, re.M)
    if match is None:
        raise AssertionError(f"the step has no {key!r}:\n{step}")
    return match.group(1).strip()


def evaluate_gate(expression, *, cancelled, outputs, needs_ok=True):
    """Evaluate the job condition in the subset it is written in.

    `outputs` is the plan job's outputs; a failed plan job leaves every one of
    them empty, which is how its dependants still run — but only because the
    condition calls `always()`. Without it GitHub skips a job whose `needs`
    failed, whatever the rest of the expression says.
    """
    terms = [part.strip() for part in expression.split(" && ")]
    if not needs_ok and "always()" not in terms:
        return False
    values = []
    for term in terms:
        if term == "always()":
            values.append(True)
        elif term == "!cancelled()":
            values.append(not cancelled)
        else:
            match = re.fullmatch(
                r"needs\.plan\.outputs\.([a-z_]+) != 'false'", term
            )
            if match is None:
                raise AssertionError(f"unfamiliar gate term {term!r}")
            values.append(outputs.get(match.group(1), "") != "false")
    return all(values)


def evaluate_cache_gate(expression, cache_hit):
    """Evaluate a step condition that reads one `cache-hit` output."""
    match = re.fullmatch(
        r"steps\.([A-Za-z0-9_-]+)\.outputs\.cache-hit != 'true'",
        expression.strip(),
    )
    if match is None:
        raise AssertionError(f"unfamiliar cache gate {expression!r}")
    return cache_hit != "true"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_the_plan_job_publishes_every_job(self):
        plan = job_block(self.text, "plan")
        for key in OUTPUTS.values():
            self.assertIn(
                f"{key}: ${{{{ steps.plan.outputs.{key} }}}}",
                plan,
                f"the plan job does not publish {key}",
            )
        for block in steps(plan):
            if "uses: actions/checkout@" in block:
                break
        else:
            self.fail("the plan job does not check out the range")
        self.assertTrue(step_field(block, "fetch-depth").startswith("1"))

    def test_the_plan_fetches_the_push_base_rather_than_head_parent(self):
        plan = job_block(self.text, "plan")
        fetch = step_named(plan, "Fetch the push's base commit")
        self.assertEqual(step_field(fetch, "continue-on-error"), "true")
        self.assertIn("BASE_SHA: ${{ github.event.before }}", fetch)
        self.assertIn("git fetch", fetch)
        runner = step_named(plan, "Plan the dependency domains this push invalidated")
        self.assertIn("BASE_SHA: ${{ github.event.before }}", runner)
        self.assertIn("CURRENT_SHA: ${{ github.sha }}", runner)
        self.assertIn("python3 .github/scripts/warm-cache-plan.py", runner)

    def test_a_domain_warms_unless_the_plan_says_false(self):
        for name in JOBS:
            with self.subTest(job=name):
                job = job_block(self.text, name)
                self.assertEqual(job_field(job, "needs"), "plan")
                expression = job_field(job, "if")
                key = re.search(r"needs\.plan\.outputs\.([a-z_]+)", expression).group(1)
                self.assertEqual(key, OUTPUTS[name])
                self.assertTrue(
                    evaluate_gate(expression, cancelled=False, outputs={key: "true"})
                )
                self.assertFalse(
                    evaluate_gate(expression, cancelled=False, outputs={key: "false"})
                )
                # The plan job failed: its outputs are empty, so the domain
                # must still warm. Nothing may be skipped on a plan failure.
                self.assertTrue(
                    evaluate_gate(
                        expression, cancelled=False, outputs={}, needs_ok=False
                    )
                )
                # A cancelled run dispatches nothing.
                self.assertFalse(
                    evaluate_gate(expression, cancelled=True, outputs={})
                )

    def test_each_job_cancels_only_its_own_domain(self):
        self.assertIsNone(
            re.search(r"^concurrency:", self.text, re.M),
            "a workflow-wide group cancels other domains",
        )
        for name in JOBS:
            with self.subTest(job=name):
                job = job_block(self.text, name)
                self.assertEqual(
                    concurrency_field(job, "group"),
                    f"warm-caches-{name}-${{{{ github.ref }}}}",
                )
                self.assertEqual(concurrency_field(job, "cancel-in-progress"), "false")

    def test_an_exact_cache_hit_skips_the_install_and_nothing_else_does(self):
        for name, (step_name, command) in GATED_INSTALLS.items():
            with self.subTest(job=name):
                job = job_block(self.text, name)
                cache_ids = cache_step_ids(job)
                self.assertEqual(
                    len(cache_ids), 1, f"{name} should read one cache step"
                )
                install = step_named(job, step_name)
                self.assertIn(command, install)
                condition = step_field(install, "if")
                self.assertEqual(
                    condition,
                    f"steps.{cache_ids[0]}.outputs.cache-hit != 'true'",
                )
                calls = [
                    int(evaluate_cache_gate(condition, hit))
                    for hit in ("", "false", "true")
                ]
                self.assertEqual(
                    calls, [1, 1, 0], f"{name}: partial and miss install, exact does not"
                )

    def test_the_arm_job_still_syncs_python(self):
        job = job_block(self.text, "harness-bins")
        install = step_named(job, "Install Python and dependencies")
        self.assertIsNone(
            re.search(r"^\s+if:", install, re.M),
            "ARM must sync Python on every run",
        )
        self.assertIn("uv sync --frozen --all-extras", install)
        # The condition below reads this id. Without the id on the cache step
        # the hit is always empty, the unpack runs every time, and the rule
        # this job exists to keep would be broken while the step still looks
        # conditional.
        self.assertIn("harness-bins", cache_step_ids(job))
        unpack = step_named(job, "Unpack the pinned harness binaries")
        self.assertEqual(
            step_field(unpack, "if"),
            "steps.harness-bins.outputs.cache-hit != 'true'",
        )

    def test_the_push_filter_covers_the_selector_and_its_test(self):
        watched = push_paths(self.text)
        self.assertIn(".github/workflows/warm-caches.yml", watched)
        self.assertIn(".github/scripts/warm-cache-plan.py", watched)
        self.assertIn(".github/scripts/test_warm_cache_plan.py", watched)


class HarnessCacheIdentityTest(unittest.TestCase):
    """A verified build is reusable after a code-only launcher change."""

    def jobs(self):
        tests = WORKFLOW.with_name("test.yml").read_text()
        warm = WORKFLOW.read_text()
        return [
            job_block(warm, "harness-bins"),
            *[
                block
                for name in re.findall(r"^  ([\w-]+):$", tests, re.M)
                if "id: harness-bins" in (block := job_block(tests, name))
            ],
        ]

    def key(
        self,
        job,
        *,
        source="original launcher",
        pins=("2.1.299", "0.154.0", "1.0.0"),
        arch="ARM64",
    ):
        cache = next(block for block in steps(job) if "id: harness-bins" in block)
        expression = step_field(cache, "key")
        outputs = {}
        for block in steps(job):
            if "id: harness-pins" not in block:
                continue
            # Execute the workflow's pin reader against declared versions.
            # Nothing here reproduces how that reader constructs its output.
            code = re.search(r"<<'PY'[^\n]*\n(.*?)\n          PY", block, re.S)
            self.assertIsNotNone(code, "the pin reader has no Python body")
            matrix = ModuleType("app.domain.agent.capability.matrix")
            harness = ModuleType("app.domain.agent.harness")
            harness.CLAUDE_CODE, harness.CODEX, harness.PI = "claude", "codex", "pi"
            matrix.written = lambda: {
                name: SimpleNamespace(pinned_version=version)
                for name, version in zip(("claude", "codex", "pi"), pins)
            }
            printed = io.StringIO()
            with (
                patch.dict(
                    sys.modules, {matrix.__name__: matrix, harness.__name__: harness}
                ),
                contextlib.redirect_stdout(printed),
            ):
                exec(textwrap.dedent(code.group(1)), {})
            outputs = dict(
                line.split("=", 1) for line in printed.getvalue().splitlines()
            )

        def value(match):
            term = match.group(1).strip()
            if term == "runner.os":
                return "Linux"
            if term == "runner.arch":
                return arch
            if term.startswith("hashFiles("):
                # hashFiles changes when launcher code changes even if its pin does not.
                return hashlib.sha256((source + str(pins)).encode()).hexdigest()
            if term.startswith("steps.harness-pins.outputs."):
                return outputs[term.rsplit(".", 1)[-1]]
            self.fail(f"unjudged cache key expression: {term}")

        return re.sub(r"\$\{\{(.*?)\}\}", value, expression)

    def test_launcher_edits_do_not_discard_verified_pinned_builds(self):
        for job in self.jobs():
            with self.subTest(job=job.splitlines()[0]):
                self.assertEqual(
                    self.key(job), self.key(job, source="launcher refactored")
                )

    def test_each_pin_and_the_architecture_select_a_new_build(self):
        for job in self.jobs():
            original = self.key(job)
            for pins in [
                ("2.1.300", "0.154.0", "1.0.0"),
                ("2.1.299", "0.155.0", "1.0.0"),
                ("2.1.299", "0.154.0", "1.0.1"),
            ]:
                self.assertNotEqual(original, self.key(job, pins=pins))
            self.assertNotEqual(original, self.key(job, arch="X64"))

    def test_shards_restore_the_same_build_the_warmer_saved(self):
        keys = {self.key(job) for job in self.jobs()}
        self.assertEqual(len(keys), 1, keys)


if __name__ == "__main__":
    unittest.main()
