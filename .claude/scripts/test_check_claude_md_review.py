#!/usr/bin/env python3
"""Offline behavioural tests for the instruction-file review gate."""

import contextlib
import importlib.util
import io
import shlex
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("check-claude-md-review.py")
spec = importlib.util.spec_from_file_location("review_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "claude-md-review.yml"


def review(login="reviewer", state="APPROVED", kind="User"):
    return {"user": {"login": login, "type": kind}, "state": state}


def _entry(lines, key, indent):
    """The inline value of `key:` at `indent` columns, and its block body."""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(line.lstrip()) != indent:
            continue
        if not stripped.startswith(f"{key}:"):
            continue
        inline = stripped[len(key) + 1 :].strip()
        body = []
        for following in lines[index + 1 :]:
            text = following.strip()
            if not text or text.startswith("#"):
                continue
            if len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        return inline, body
    return "", []


def _scalars(text):
    """The string values of a flow sequence, or of a single scalar."""
    if text.startswith("[") and text.endswith("]"):
        return [
            item.strip().strip("'\"")
            for item in text[1:-1].split(",")
            if item.strip()
        ]
    return [text.strip("'\"")] if text else []


def pull_request_paths(workflow=WORKFLOW):
    """The `paths:` filter that makes this workflow run on a pull request."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    _, trigger = _entry(lines, "on", 0)
    _, pull_request = _entry(trigger, "pull_request", 2)
    inline, block = _entry(pull_request, "paths", 4)
    if inline:
        return _scalars(inline)
    return [
        line.strip()[2:].strip().strip("'\"")
        for line in block
        if line.strip().startswith("- ")
    ]


class ReviewGateTests(unittest.TestCase):
    def judge(self, files, labels=(), reviews=()):
        if files and isinstance(files[0], str):
            files = [{"filename": name} for name in files]

        def api(path):
            if path == "pulls/42":
                return {
                    "user": {"login": "author"},
                    "labels": [{"name": name} for name in labels],
                }
            if path.startswith("pulls/42/files?"):
                rows = files
            elif path.startswith("pulls/42/reviews?"):
                rows = reviews
            else:
                self.fail(f"unexpected API request: {path}")
            # Accept the initial request with or without an explicit first page.
            page = int(path.rsplit("&page=", 1)[1]) if "&page=" in path else 1
            return list(rows[(page - 1) * 100 : page * 100])

        output = io.StringIO()
        with (
            patch.object(gate, "_api", side_effect=api),
            patch.object(gate.subprocess, "run") as run,
            contextlib.redirect_stdout(output),
        ):
            result = gate.judge(42)
            run.assert_not_called()
        return result, output.getvalue()

    def test_unrelated_and_nested_files_do_not_require_signoff(self):
        result, _ = self.judge(["README.md", "frontend/AGENTS.md", "docs/CLAUDE.md"])
        self.assertEqual(result, 0)

    def test_gate_script_and_self_test_changes_need_no_signoff(self):
        # These files must run the gate, not be guarded by it: requiring a
        # sign-off to fix the gate would leave it unfixable in exactly the
        # situation where it is wrong.
        result, output = self.judge(
            [
                ".claude/scripts/check-claude-md-review.py",
                ".claude/scripts/test_check_claude_md_review.py",
            ]
        )
        self.assertEqual(result, 0)
        self.assertNotIn("::error::", output)

    def test_gate_script_pr_passes_through_the_cli(self):
        files = [
            {"filename": ".claude/scripts/check-claude-md-review.py"},
            {"filename": ".claude/scripts/test_check_claude_md_review.py"},
        ]

        def api(path):
            self.assertEqual(path, "pulls/42/files?per_page=100&page=1")
            return files

        with (
            patch.object(gate, "_api", side_effect=api),
            patch.object(gate.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(gate.main(["--pr", "42"]), 0)
            run.assert_not_called()

    def test_each_root_instruction_file_requires_signoff(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(name=name):
                result, output = self.judge([name], labels=["docs"])
                self.assertEqual(result, 1)
                self.assertIn(name, output)
                self.assertIn("::error::", output)

    def test_label_and_independent_human_approval_each_allow(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(name=name):
                self.assertEqual(self.judge([name], labels=["claude-md-ok"])[0], 0)
                self.assertEqual(self.judge([name], reviews=[review()])[0], 0)

    def test_bot_or_author_approval_cannot_open_gate(self):
        for approvals in (
            [review("author")],
            [review("robot", kind="Bot")],
            [review("robot[bot]")],
            [review("author"), review("robot", kind="Bot")],
        ):
            with self.subTest(approvals=approvals):
                self.assertEqual(self.judge(["AGENTS.md"], reviews=approvals)[0], 1)

    def test_latest_verdict_wins_but_comments_do_not_revoke(self):
        for last_state, expected in (
            ("CHANGES_REQUESTED", 1),
            ("DISMISSED", 1),
            ("COMMENTED", 0),
        ):
            with self.subTest(state=last_state):
                self.assertEqual(
                    self.judge(
                        ["AGENTS.md"], reviews=[review(), review(state=last_state)]
                    )[0],
                    expected,
                )
        self.assertEqual(
            self.judge(
                ["AGENTS.md"], reviews=[review(state="CHANGES_REQUESTED"), review()]
            )[0],
            0,
        )

    def test_later_pages_and_renames_cannot_hide_guarded_changes(self):
        files = [f".config/{i}.txt" for i in range(100)] + ["CLAUDE.md"]
        self.assertEqual(self.judge(files)[0], 1)
        self.assertEqual(
            self.judge([{"filename": "docs/old.md", "previous_filename": "AGENTS.md"}])[
                0
            ],
            1,
        )
        reviews = (
            [review()]
            + [review("author") for _ in range(99)]
            + [review(state="DISMISSED")]
        )
        self.assertEqual(self.judge(["AGENTS.md"], reviews=reviews)[0], 1)

    def test_failure_prints_executable_repo_qualified_repair(self):
        with patch.object(gate, "REPO", "owner/project"):
            result, output = self.judge(["AGENTS.md"])
        self.assertEqual(result, 1)
        commands = [
            shlex.split(line.strip())
            for line in output.splitlines()
            if line.strip().startswith("gh ")
        ]
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command[:2], ["gh", "api"])
        self.assertIn("repos/owner/project/issues/42/labels", command)
        self.assertIn("POST", command)
        self.assertIn("labels[]=claude-md-ok", command)
        self.assertIn("--apply-label 42", output)

    def test_explicit_apply_adds_label_without_replacing_others(self):
        output = io.StringIO()
        with (
            patch.object(gate, "REPO", "owner/project"),
            patch.object(
                gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as run,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(gate.main(["--apply-label", "42"]), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["gh", "api"])
        self.assertIn("POST", command)
        self.assertNotIn("PUT", command)
        self.assertIn("repos/owner/project/issues/42/labels", command)
        self.assertIn("labels[]=claude-md-ok", command)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_apply_failures_are_not_reported_as_success(self):
        for failure in (FileNotFoundError("gh"), subprocess.TimeoutExpired("gh", 60)):
            with (
                self.subTest(failure=failure),
                patch.object(gate.subprocess, "run", side_effect=failure),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertNotEqual(gate.main(["--apply-label", "42"]), 0)
        with (
            patch.object(
                gate.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertNotEqual(gate.main(["--apply-label", "42"]), 0)

    def test_api_failure_is_closed_and_never_applies_label(self):
        for failure in (
            urllib.error.HTTPError(
                "https://example.invalid", 403, "Forbidden", {}, None
            ),
            urllib.error.URLError("offline"),
        ):
            with (
                self.subTest(failure=failure),
                patch.object(gate, "_api", side_effect=failure),
                patch.object(gate.subprocess, "run") as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(gate.main(["--pr", "42"]), 1)
                run.assert_not_called()

    def test_cli_rejects_invalid_or_ambiguous_actions(self):
        for args in (
            [],
            ["--pr"],
            ["--pr", "0"],
            ["--apply-label", "-1"],
            ["--apply-label", "oops"],
            ["--self-test", "--apply-label", "42"],
        ):
            with self.subTest(args=args):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), *args],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)


class WorkflowTriggerTests(unittest.TestCase):
    """A change to the gate must run the gate's own proof.

    The workflow runs `--self-test` before it judges a PR, but only for the
    paths it triggers on. A gate whose own script or tests can change without
    that self-test running is a gate that can stop judging correctly in
    silence — the failure mode this whole workflow exists to prevent.
    """

    REQUIRED = (
        "AGENTS.md",
        ".claude/scripts/check-claude-md-review.py",
        ".claude/scripts/test_check_claude_md_review.py",
    )

    def test_workflow_triggers_on_the_gate_and_its_self_test(self):
        paths = pull_request_paths()
        for path in self.REQUIRED:
            with self.subTest(path=path):
                self.assertIn(path, paths, f"{path} must run the gate")

    def test_every_guarded_file_that_exists_triggers_the_workflow(self):
        # CLAUDE.md is guarded where it exists; a root file the repo does not
        # have cannot be edited, so it needs no trigger.
        paths = pull_request_paths()
        for name in gate.GUARDED:
            with self.subTest(name=name):
                if (ROOT / name).exists():
                    self.assertIn(name, paths)


if __name__ == "__main__":
    unittest.main()
