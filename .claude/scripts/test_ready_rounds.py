"""Delivery follows the PR's stamped review, independent of local cache order."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReadyRounds(unittest.TestCase):
    def ready(self, body, *, head="b" * 40):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            binary = home / "gh"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "if any('check-runs' in a for a in sys.argv):\n"
                " print(json.dumps({'conclusion':'success'}))\n"
                "elif '--jq' in sys.argv:\n"
                " print(os.environ['PR_HEAD'] + '\\tfalse')\n"
                "else:\n"
                " print(json.dumps({'head':{'sha':os.environ['PR_HEAD']},"
                "'base':{'ref':'main'},'draft':False,'body':os.environ['PR_BODY']}))\n"
            )
            binary.chmod(0o755)
            # An old blocked round exists in the cache; a newer unfinished round
            # exists under the explicit root. Neither overrides the stamped result.
            for root, sha, result in (
                (home / ".cheese/review-cache", "a" * 12,
                 "独立 review：head aaaaaaaa 结论 有 2 条｜档位 B\n"),
                (home / "rounds", "b" * 12, ""),
            ):
                directory = root / f"pr-review-7-{sha}.input"
                directory.mkdir(parents=True)
                (directory / "meta").write_text("pr=7\nbase=main\n")
                (directory / "REVIEW-RESULT.md").write_text(result)
            return subprocess.run(
                ["bash", str(ROOT / ".claude/scripts/pr-review.sh"), "ready", "7"],
                env={**os.environ, "HOME": str(home),
                     "PATH": str(home) + os.pathsep + os.environ["PATH"],
                     "PR_REVIEW_DIR": str(home / "rounds"),
                     "PR_HEAD": head, "PR_BODY": body},
                capture_output=True, text=True,
            )

    def test_new_clean_delta_supersedes_old_blockers_and_unfinished_directory(self):
        result = self.ready(
            "独立 review：head aaaaaaaa 结论 有 2 条｜档位 B\n\n"
            "独立 review：head bbbbbbbb（增量自 aaaaaaaa）结论 无 blocker/major｜档位 B\n"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("review bbbbbbbb", result.stdout)

    def test_new_blocked_delta_cannot_use_an_older_clean_conclusion(self):
        result = self.ready(
            "独立 review：head aaaaaaaa 结论 无 blocker/major｜档位 B\n"
            "独立 review：head bbbbbbbb（增量自 aaaaaaaa）结论 有 1 条｜档位 B\n"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 条 blocker/major", result.stdout)

    def test_unstamped_local_result_does_not_deliver_the_pr(self):
        result = self.ready("No stamped conclusion.\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("没有审查结论", result.stdout)


if __name__ == "__main__":
    unittest.main()
