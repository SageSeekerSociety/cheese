"""Run the ratchet collection step the way GitHub runs it: `bash -e`.

The step that collects the snapshot exists to leave evidence. A run where the
collector never started must produce a file saying so, not an absence with a
red X — that is what the step's `|| code=$?` and its failure record are for.

The first version of the step read `code=$?` on the line after the collector,
which cannot work: GitHub runs the body of a `run:` step as `bash -e`, so a
non-zero collector ends the step on that line and everything after it —
including the record — is skipped. These cases run the step's own text under
`bash -e`, which is why they fail on that version and pass on this one.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/arch-metrics.yml"
BASH = shutil.which("bash") or "/bin/bash"

# Stands in for the collector: writes a snapshot only when asked, then exits
# with whatever the case under test needs. Nothing here reads the repository.
STUB = """\
import os
import sys

argv = sys.argv[1:]
target = None
for index, argument in enumerate(argv):
    if argument == "--out":
        target = argv[index + 1]
if os.environ["STUB_WRITE"] == "1":
    with open(target, "w") as handle:
        handle.write("{}\\n")
sys.exit(int(os.environ["STUB_EXIT"]))
"""


class RatchetCollectionTest(unittest.TestCase):
    def setUp(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.steps = workflow["jobs"]["board"]["steps"]
        self.collect = next(
            step
            for step in self.steps
            if step.get("name") == "Collect the ratchet snapshot"
        )
        self.summary = next(
            step for step in self.steps if step.get("name") == "Job summary"
        )
        self.upload = next(
            step
            for step in self.steps
            if step.get("with", {}).get("name") == "ratchet-snapshot"
        )

    def run_step(
        self,
        step,
        env_path,
        exit_code=0,
        writes=False,
        board=None,
        snapshot=None,
    ):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        repo = root / "repo"
        stub = repo / ".claude/scripts/ratchet-snapshot.py"
        stub.parent.mkdir(parents=True)
        stub.write_text(STUB)
        out = root / "out"
        out.mkdir()
        if board is not None:
            (out / "arch-metrics.md").write_text(board)
        if snapshot is not None:
            (out / "ratchet-snapshot.json").write_text(snapshot)
        # The body is the text CI runs, with GitHub's own expressions filled
        # in; nothing else about it is rewritten.
        body = root / "step.sh"
        body.write_text(re.sub(r"\$\{\{[^}]*\}\}", "filled-in", step["run"]))
        summary = out / "summary.md"
        summary.write_text("")
        result = subprocess.run(
            [BASH, "-e", str(body)],
            cwd=repo,
            env={
                "PATH": env_path,
                "OUT": str(out),
                "GITHUB_STEP_SUMMARY": str(summary),
                "STUB_EXIT": str(exit_code),
                "STUB_WRITE": "1" if writes else "0",
            },
            capture_output=True,
            text=True,
        )
        return result, out, summary

    def test_an_interpreter_that_cannot_start_still_leaves_the_record(self):
        with tempfile.TemporaryDirectory() as empty:
            result, out, _ = self.run_step(self.collect, empty)
        self.assertEqual(result.returncode, 127, result.stderr)
        record = out / "ratchet-snapshot.failed.txt"
        self.assertTrue(record.exists(), result.stdout + result.stderr)
        self.assertIn("exit code: 127", record.read_text())

    def test_a_collector_that_wrote_nothing_still_leaves_the_record(self):
        result, out, _ = self.run_step(self.collect, os.environ["PATH"], exit_code=2)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        record = out / "ratchet-snapshot.failed.txt"
        self.assertTrue(record.exists(), result.stdout + result.stderr)
        self.assertIn("exit code: 2", record.read_text())

    def test_a_successful_collection_leaves_no_failure_record(self):
        result, out, _ = self.run_step(
            self.collect, os.environ["PATH"], writes=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((out / "ratchet-snapshot.json").exists())
        self.assertFalse((out / "ratchet-snapshot.failed.txt").exists())

    def test_the_failed_collection_is_not_swallowed_and_is_uploaded(self):
        self.assertFalse(self.collect.get("continue-on-error", False))
        self.assertFalse(self.upload.get("continue-on-error", False))
        self.assertEqual(self.upload["with"]["if-no-files-found"], "error")
        self.assertIn("ratchet-snapshot.failed.txt", self.upload["with"]["path"])

    def test_the_summary_reports_a_collection_that_did_not_happen(self):
        result, _, summary = self.run_step(self.summary, os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        written = summary.read_text()
        self.assertIn("The ratchet snapshot was NOT collected", written)
        self.assertIn("The board was not measured", written)

    def test_the_summary_carries_the_board_when_there_is_one(self):
        result, _, summary = self.run_step(
            self.summary,
            os.environ["PATH"],
            board="# the board\n",
            snapshot="{}\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        written = summary.read_text()
        self.assertIn("# the board", written)
        self.assertNotIn("NOT collected", written)
        self.assertNotIn("was not measured", written)


if __name__ == "__main__":
    unittest.main()
