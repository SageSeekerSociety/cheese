#!/usr/bin/env python3
"""Exercise commit-file.sh with real Git, isolated from user signing/hooks/config.

Run: python3 .claude/scripts/test_commit_file.py
An alternate script path lets a mutation prove these tests reject a broken guard:
    python3 .claude/scripts/test_commit_file.py /tmp/broken-commit-file.sh
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("commit-file.sh").resolve()
if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
    SCRIPT = Path(sys.argv.pop(1)).resolve()


class CommitFileTest(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix="test-commit-file-")
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.temp = self.root / "messages"
        self.temp.mkdir()
        self.hooks = self.root / "hooks"
        self.hooks.mkdir()
        # Strip inherited repository/config selectors, including indexed -c values.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_AUTHOR_NAME="Commit File Test",
            GIT_AUTHOR_EMAIL="commit-file@example.invalid",
            GIT_COMMITTER_NAME="Commit File Test",
            GIT_COMMITTER_EMAIL="commit-file@example.invalid",
            GIT_TERMINAL_PROMPT="0",
            GIT_EDITOR="false",
            TMPDIR=str(self.temp),
        )
        self.git("init", "--quiet", "--template=", "--initial-branch=main")
        self.git("config", "core.hooksPath", str(self.hooks))
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "commit.cleanup", "strip")
        (self.repo / "tracked").write_text("first\n")
        self.git("add", "tracked")

    def run_process(self, args, message=None):
        return subprocess.run(
            args, cwd=self.repo, env=self.env, input=message, capture_output=True
        )

    def git(self, *args):
        result = self.run_process(["git", *args])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout

    def commit(self, message=None, *args):
        return self.run_process(["bash", str(SCRIPT), *args], message)

    def assert_message(self, expected):
        # `git log --format=%B` adds a record newline; remove exactly that byte.
        self.assertEqual(self.git("log", "-1", "--format=%B")[:-1], expected)

    def assert_rejected(self, message, *args):
        before = self.run_process(["git", "rev-parse", "--verify", "HEAD"])
        index = self.git("write-tree")
        result = self.commit(message, *args)
        self.assertNotEqual(result.returncode, 0, result.stdout.decode())
        after = self.run_process(["git", "rev-parse", "--verify", "HEAD"])
        self.assertEqual((before.returncode, before.stdout), (after.returncode, after.stdout))
        self.assertEqual(self.git("write-tree"), index)
        self.assertEqual(list(self.temp.iterdir()), [])
        return result

    def test_quoted_heredoc_preserves_literal_bytes(self):
        message = (
            b"fix(example): preserve `foo` and $bar\n\n"
            b"Literal $(touch SHOULD_NOT_EXIST), `touch ALSO_NOT_CREATED`.  \n"
            b"# Keep this comment and trailing whitespace.  \n\n"
        )
        command = 'bash "$1" <<\'MESSAGE\'\n' + message.decode() + "MESSAGE\n"
        result = self.run_process(["bash", "-c", command, "heredoc", str(SCRIPT)])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_message(message)
        self.assertFalse((self.repo / "SHOULD_NOT_EXIST").exists())
        self.assertFalse((self.repo / "ALSO_NOT_CREATED").exists())
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_file_path_and_private_snapshot(self):
        message = "fix(example): 保留 `foo()` 与 $bar\n".encode()
        source = self.repo / "-message with spaces"
        source.write_bytes(message)
        # The private snapshot exists during Git's hook and is removed afterwards.
        hook = self.hooks / "prepare-commit-msg"
        hook.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, stat, sys\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "files = list(pathlib.Path(os.environ['TMPDIR']).iterdir())\n"
            "assert len(files) == 1\n"
            "assert stat.S_IMODE(files[0].stat().st_mode) == 0o600\n"
            "assert files[0].read_bytes() == p.read_bytes()\n"
            "pathlib.Path('hook-ran').write_bytes(p.read_bytes())\n"
        )
        hook.chmod(0o755)
        result = self.commit(None, "--", source.name)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_message(message)
        self.assertEqual(source.read_bytes(), message)
        self.assertEqual((self.repo / "hook-ran").read_bytes(), message)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_explicit_stdin_and_safe_flags(self):
        result = self.commit(b"fix(example): original\n", "-")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        result = self.commit(b"fix(example): amended `foo`\n", "--amend", "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_message(b"fix(example): amended `foo`\n")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), b"1\n")
        result = self.commit(b"fix(example): empty tree change\n", "--allow-empty", "-q")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), b"2\n")

    def test_empty_backticks_refuse_commit(self):
        result = self.assert_rejected(b"fix(example): restore `` after expansion\n")
        self.assertIn(b"quoted heredoc", result.stderr)

    def test_malformed_messages_refuse_commit(self):
        for message in (
            b"fix(example): restore `foo\n",
            b"fix(example): restore ` `\n",
            b"fix(example): restore ()\n",
            b"fix(example): restore ( \t)\n",
            b"fix(example): `valid` then ()\n",
            b"fix(example): empty\x00hidden\n",
            b"fix(example): invalid \xff\n",
            b" \n\t",
        ):
            with self.subTest(message=message):
                self.assert_rejected(message)

    def test_code_examples_allow_literal_syntax(self):
        message = (
            b"fix(example): retain `foo()` and `$()`\n\n"
            b"Quote a backtick with `` ` ``.\n\n"
            b"```sh\nfoo()\nprintf '%s' `foo`\n```\n"
        )
        result = self.commit(message)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_message(message)

    def test_reject_before_amending_existing_commit(self):
        result = self.commit(b"fix(example): original\n")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assert_rejected(b"fix(example): lost ``\n", "--amend")
        self.assert_message(b"fix(example): original\n")

    def test_disallowed_arguments_cannot_replace_validated_message(self):
        for args in (
            ("-m", "replacement"),
            ("-F", "elsewhere"),
            ("--file=elsewhere",),
            ("--no-edit",),
            ("--edit",),
            ("--reuse-message=HEAD",),
            ("--fixup=HEAD",),
            ("--no-verify",),
            ("--cleanup=strip",),
            ("-a",),
            ("one", "two"),
            ("--",),
        ):
            with self.subTest(args=args):
                self.assert_rejected(b"fix(example): valid\n", *args)

    def test_missing_input_file_fails_and_cleans_up(self):
        self.assert_rejected(None, "missing-message")

    def test_git_hooks_keep_callers_umask(self):
        hook = self.hooks / "pre-commit"
        hook.write_text("#!/bin/sh\n: > hook-output\n")
        hook.chmod(0o755)
        result = self.run_process(
            ["bash", "-c", 'umask 022; exec bash "$1"', "umask-test", str(SCRIPT)],
            b"fix(example): keep hook output readable\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual((self.repo / "hook-output").stat().st_mode & 0o777, 0o644)

    def test_git_failure_propagates_and_cleans_up(self):
        hook = self.hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nprintf 'hook rejected\\n' >&2\nexit 1\n")
        hook.chmod(0o755)
        result = self.assert_rejected(b"fix(example): valid\n")
        self.assertIn(b"hook rejected", result.stderr)


if __name__ == "__main__":
    unittest.main()
