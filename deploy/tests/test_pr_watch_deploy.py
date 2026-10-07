#!/usr/bin/env python3
"""Follow a merged PR to the dev deploy that releases it, against a fake GitHub."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WATCH = ROOT / ".claude/scripts/pr-watch.sh"

# Commits on main, oldest first. The PR merged as MERGE.
EARLIER, MIDDLE, MERGE, LATER = ("1" * 40, "2" * 40, "3" * 40, "4" * 40)
ORDER = [EARLIER, MIDDLE, MERGE, LATER]

FAKE_GH = r"""#!/usr/bin/env bash
# Answers the calls pr-watch.sh makes, from files in $FAKE.
query=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in -q|--jq) query="${args[$((i + 1))]}" ;; esac
done
case "$1 $2" in
  "api graphql") cat "$FAKE/pr.json" ;;
  "run list")
    # The first listing may come from runs-first.json; later ones from runs.json.
    if [ -f "$FAKE/runs-first.json" ] && [ ! -f "$FAKE/listed" ]; then
      touch "$FAKE/listed"; jq -r "$query" "$FAKE/runs-first.json"
    else
      jq -r "$query" "$FAKE/runs.json"
    fi ;;
  "run view") jq -r "$query" "$FAKE/jobs-$3.json" ;;
  api\ repos/*/compare/*)
    pair="${2##*/compare/}"
    jq -r --arg base "${pair%%...*}" --arg head "${pair##*...}" \
      '.[$base + "..." + $head] // "diverged"' "$FAKE/compare.json" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""


def run(run_id, status, conclusion, title, deploy="success"):
    """A deploy-dev run as `gh run list` reports it, and its deploy job."""
    return {"databaseId": run_id, "status": status, "conclusion": conclusion,
            "displayTitle": title, "deploy": deploy}


class DeployRunForAMergedPR(unittest.TestCase):
    def watch(self, runs, live, first=None):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            (fake / "gh").write_text(FAKE_GH)
            (fake / "curl").write_text(f"#!/usr/bin/env bash\necho '{json.dumps({'data': {'sha': live}})}'\n")
            for program in ("gh", "curl"):
                (fake / program).chmod(0o755)
            (fake / "pr.json").write_text(json.dumps({"data": {"repository": {"pullRequest": {
                "state": "MERGED", "mergeCommit": {"oid": MERGE}, "mergeQueueEntry": None,
                "timelineItems": {"nodes": []}}}}}))
            (fake / "runs.json").write_text(json.dumps(runs))
            if first is not None:
                (fake / "runs-first.json").write_text(json.dumps(first))
            for entry in runs:
                (fake / f"jobs-{entry['databaseId']}.json").write_text(json.dumps(
                    {"jobs": [{"name": "eligibility", "conclusion": "success"},
                              {"name": "deploy", "conclusion": entry["deploy"]}]}))
            compare = {}
            for i, base in enumerate(ORDER):
                for j, head in enumerate(ORDER):
                    compare[f"{base}...{head}"] = "identical" if i == j else ("ahead" if j > i else "behind")
            (fake / "compare.json").write_text(json.dumps(compare))
            result = subprocess.run(
                ["bash", str(WATCH), "--deploy", "3055"], capture_output=True, text=True, timeout=20,
                env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "FAKE": str(fake),
                     "PR_WATCH_INTERVAL": "0"})
        return result

    def test_a_cancelled_deploy_of_an_earlier_build_is_not_this_prs_deploy(self):
        # Every run below fired while MERGE was main's tip, so GitHub gives all
        # three MERGE as their head SHA; only the newest releases MERGE.
        runs = [
            run(3, "completed", "success", f"Deploy {MERGE}"),
            run(2, "completed", "success", f"Deploy {MIDDLE}"),
            run(1, "completed", "cancelled", f"Deploy {EARLIER}", deploy="cancelled"),
        ]
        result = self.watch(runs, live=MERGE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dev deploy run 3: completed success", result.stdout)
        self.assertIn(f"live on dev at {MERGE[:9]}", result.stdout)
        self.assertNotIn("run 1", result.stdout)
        self.assertNotIn("run 2", result.stdout)

    def test_a_later_commits_deploy_counts_because_it_contains_the_merge(self):
        runs = [run(5, "completed", "success", f"Deploy {LATER}"),
                run(4, "completed", "cancelled", f"Deploy {MERGE}", deploy="cancelled")]
        result = self.watch(runs, live=LATER)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dev deploy run 5: completed success", result.stdout)

    def test_a_failed_deploy_of_this_merge_fails_the_watch(self):
        runs = [run(7, "completed", "failure", f"Deploy {MERGE}", deploy="failure"),
                run(6, "completed", "success", f"Deploy {MIDDLE}")]
        result = self.watch(runs, live=MIDDLE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("dev deploy run 7: completed failure", result.stdout)

    def test_a_deploy_still_running_is_reported_before_it_finishes(self):
        # GitHub lists a run that has not finished with an empty conclusion.
        running = [run(8, "in_progress", "", f"Deploy {MERGE}", deploy="")]
        finished = [run(8, "completed", "success", f"Deploy {MERGE}")]
        result = self.watch(finished, live=MERGE, first=running)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dev deploy run 8: in_progress none", result.stdout)
        self.assertIn("dev deploy run 8: completed success", result.stdout)

    def test_deploy_runs_are_named_after_the_commit_they_release(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-dev.yml").read_text())
        check = next(step for step in workflow["jobs"]["eligibility"]["steps"]
                     if step.get("name") == "Require images and tests for the same commit")
        candidate = check["env"]["CANDIDATE_SHA"]
        self.assertEqual(workflow["run-name"], f"Deploy {candidate}")


if __name__ == "__main__":
    unittest.main()
