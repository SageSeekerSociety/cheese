#!/usr/bin/env python3
"""Follow a reviewed head through a direct merge, main CI and its dev deployment."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WATCH = ROOT / ".claude/scripts/pr-watch.sh"

# Commits on main, oldest first. The PR merged as MERGE.
EARLIER, MIDDLE, MERGE, LATER, LATEST = ("1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40)
ORDER = [EARLIER, MIDDLE, MERGE, LATER, LATEST]

FAKE_GH = r"""#!/usr/bin/env bash
# Answers the calls pr-watch.sh makes, from files in $FAKE.
query=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[$i]}" in -q|--jq) query="${args[$((i + 1))]}" ;; esac
done
case "$1 $2" in
  "api graphql")
    # Poll n reads pr-n.json while one exists, then the last one; else pr.json.
    if [ -f "$FAKE/pr-0.json" ]; then
      n=$(cat "$FAKE/polls" 2>/dev/null || echo 0); echo $((n + 1)) > "$FAKE/polls"
      while [ "$n" -gt 0 ] && [ ! -f "$FAKE/pr-$n.json" ]; do n=$((n - 1)); done
      cat "$FAKE/pr-$n.json"
    else
      cat "$FAKE/pr.json"
    fi ;;
  "pr checks") printf 'CI required\tpass\t1m\thttps://example.invalid\n' ;;
  "pr merge")
    printf '%s\n' "$*" >> "$FAKE/merge-called"
    [ ! -f "$FAKE/reject-merge" ] ;;
  "run list")
    if [[ "$*" == *required-ci.yml* ]]; then
      if [ -f "$FAKE/ci-first.json" ] && [ ! -f "$FAKE/ci-listed" ]; then
        touch "$FAKE/ci-listed"; jq -r "$query" "$FAKE/ci-first.json"
      else
        jq -r "$query" "$FAKE/ci-runs.json"
      fi
      exit
    fi
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
    def watch(self, runs, live, first=None, ci=None, ci_first=None, red_wait="0"):
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
            (fake / "ci-runs.json").write_text(json.dumps(ci if ci is not None else [
                {"databaseId": 100, "status": "completed", "conclusion": "success", "headSha": MERGE}]))
            if ci_first is not None:
                (fake / "ci-first.json").write_text(json.dumps(ci_first))
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
                     "PR_WATCH_INTERVAL": "0", "PR_WATCH_MAIN_RED_WAIT": red_wait})
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
        self.assertNotIn("dev deploy run 1:", result.stdout)
        self.assertNotIn("dev deploy run 2:", result.stdout)

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

    def test_an_earlier_successful_deploy_ends_the_watch_while_a_newer_one_runs(self):
        # Run 9 released MERGE and dev runs it; run 10, for a later commit, is
        # still going. The PR is live, so the watch ends without waiting on 10.
        runs = [run(10, "in_progress", "", f"Deploy {LATER}", deploy=""),
                run(9, "completed", "success", f"Deploy {MERGE}")]
        result = self.watch(runs, live=MERGE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dev deploy run 9: completed success", result.stdout)
        self.assertIn(f"live on dev at {MERGE[:9]}", result.stdout)

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

    def test_a_red_main_is_reported_even_when_a_deploy_run_succeeded(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {MERGE}")], live=MERGE,
                            ci=[{"databaseId": 101, "status": "completed", "conclusion": "failure",
                                 "headSha": MERGE}])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("main Required CI run 101: completed failure", result.stdout)
        self.assertNotIn("live on dev", result.stdout)

    def test_main_ci_is_followed_until_it_finishes(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {MERGE}")], live=MERGE,
                            ci_first=[{"databaseId": 100, "status": "in_progress", "conclusion": "",
                                       "headSha": MERGE}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("main Required CI run 100: in_progress none", result.stdout)
        self.assertIn("main Required CI run 100: completed success", result.stdout)

    def test_a_superseding_main_batch_covers_a_cancelled_merge_run(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {LATER}")], live=LATER,
                            ci=[{"databaseId": 102, "status": "completed", "conclusion": "success",
                                 "headSha": LATER},
                                {"databaseId": 100, "status": "completed", "conclusion": "cancelled",
                                 "headSha": MERGE}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("main Required CI run 102: completed success", result.stdout)

    def test_a_later_red_batch_does_not_revoke_an_earlier_green_containing_batch(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {LATER}")], live=LATER,
                            ci=[{"databaseId": 103, "status": "completed", "conclusion": "failure",
                                 "headSha": LATEST},
                                {"databaseId": 102, "status": "completed", "conclusion": "success",
                                 "headSha": LATER},
                                {"databaseId": 100, "status": "completed", "conclusion": "cancelled",
                                 "headSha": MERGE}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("main Required CI run 102: completed success", result.stdout)
        self.assertIn(f"live on dev at {LATER[:9]}", result.stdout)

    def test_a_newer_green_batch_passes_a_merge_whose_own_main_run_failed(self):
        # Another PR in the merge's batch broke main; the next batch but one fixed
        # it and was released, while the deploys of the red commits were skipped.
        ci = [{"databaseId": 104, "status": "completed", "conclusion": "success", "headSha": LATEST},
              {"databaseId": 103, "status": "completed", "conclusion": "failure", "headSha": LATER},
              {"databaseId": 101, "status": "completed", "conclusion": "failure", "headSha": MERGE},
              {"databaseId": 100, "status": "completed", "conclusion": "cancelled", "headSha": EARLIER}]
        runs = [run(12, "completed", "success", f"Deploy {LATEST}"),
                run(11, "completed", "skipped", f"Deploy {LATER}", deploy="skipped"),
                run(10, "completed", "skipped", f"Deploy {MERGE}", deploy="skipped")]
        result = self.watch(runs, live=LATEST, ci=ci)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("main Required CI run 104: completed success", result.stdout)
        self.assertIn(f"live on dev at {LATEST[:9]}", result.stdout)

    def test_a_red_main_after_the_merge_waits_for_the_next_batch(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {LATER}")], live=LATER, red_wait="3600",
                            ci_first=[{"databaseId": 101, "status": "completed", "conclusion": "failure",
                                       "headSha": MERGE}],
                            ci=[{"databaseId": 102, "status": "completed", "conclusion": "success",
                                 "headSha": LATER},
                                {"databaseId": 101, "status": "completed", "conclusion": "failure",
                                 "headSha": MERGE}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"main is red after merge {MERGE[:9]} (run 101)", result.stdout)
        self.assertIn("main Required CI run 102: completed success", result.stdout)
        self.assertNotIn("stopped watching", result.stdout)

    def test_a_red_main_with_no_newer_batch_fails_once_the_wait_runs_out(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {MERGE}")], live=MERGE, red_wait="2",
                            ci=[{"databaseId": 101, "status": "completed", "conclusion": "failure",
                                 "headSha": MERGE}])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"main is red after merge {MERGE[:9]} (run 101)", result.stdout)
        self.assertIn("stopped watching: main Required CI failed after merge", result.stdout)

    def test_a_newer_red_batch_that_still_contains_the_merge_fails_the_watch(self):
        result = self.watch([run(9, "completed", "success", f"Deploy {MERGE}")], live=MERGE,
                            ci=[{"databaseId": 102, "status": "completed", "conclusion": "failure",
                                 "headSha": LATER},
                                {"databaseId": 101, "status": "completed", "conclusion": "failure",
                                 "headSha": MERGE}])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("main Required CI run 102: completed failure", result.stdout)
        self.assertIn(f"stopped watching: main Required CI failed after merge {MERGE[:9]} (run 102",
                      result.stdout)
        self.assertNotIn("live on dev", result.stdout)

    def test_a_cancelled_deploy_waits_for_the_next_converging_release(self):
        result = self.watch([run(10, "completed", "success", f"Deploy {LATER}")], live=LATER,
                            first=[run(9, "completed", "cancelled", f"Deploy {MERGE}", deploy="cancelled")])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dev deploy run 10: completed success", result.stdout)


HEAD, PUSHED = "a" * 40, "b" * 40
def open_pr(mergeable="MERGEABLE", auto=False, head=HEAD):
    """An open PR as the watcher's GraphQL query returns it."""
    return {"data": {"repository": {"pullRequest": {
        "state": "OPEN", "headRefOid": head, "mergeable": mergeable, "mergeStateStatus": "BLOCKED",
        "autoMergeRequest": {"enabledAt": "2026-10-12T06:15:50Z"} if auto else None,
        "mergeCommit": None}}}}


CLOSED = {"data": {"repository": {"pullRequest": {
    "state": "CLOSED", "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN", "autoMergeRequest": None,
    "mergeCommit": None}}}}

MERGED = {"data": {"repository": {"pullRequest": {
    "state": "MERGED", "headRefOid": HEAD, "mergeCommit": {"oid": MERGE}}}}}


# `pr-review.sh ready <PR>`, answering in turn from review-<n>.txt/.exit in $FAKE.
FAKE_REVIEW = r"""#!/usr/bin/env bash
[ "$1 $2" = "ready 3426" ] || { echo "usage: ready DIR|PR" >&2; exit 2; }
n=$(cat "$FAKE/review-calls" 2>/dev/null || echo 0); echo $((n + 1)) > "$FAKE/review-calls"
while [ "$n" -gt 0 ] && [ ! -f "$FAKE/review-$n.txt" ]; do n=$((n - 1)); done
cat "$FAKE/review-$n.txt"; exit "$(cat "$FAKE/review-$n.exit")"
"""


class AnOpenPRWaitingToMerge(unittest.TestCase):
    def watch(self, polls, quiet="3600", reviews=(("可递：head aaaaaaaa｜CI required success", True),),
              reject=False):
        """Run the watcher with --enqueue over successive GitHub answers; return it and whether it merged.

        `reviews` are what successive `pr-review.sh ready 3426` calls print and whether each says
        可递; the last one repeats."""
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory)
            (fake / "gh").write_text(FAKE_GH)
            (fake / "gh").chmod(0o755)
            # The watcher asks the pr-review.sh beside it, so it runs from a copy with a fake one there.
            shutil.copy(WATCH, fake / "pr-watch.sh")
            for n, (text, done) in enumerate(reviews):
                (fake / f"review-{n}.txt").write_text(text + "\n")
                (fake / f"review-{n}.exit").write_text("0" if done else "1")
            (fake / "pr-review.sh").write_text(FAKE_REVIEW)
            (fake / "pr-review.sh").chmod(0o755)
            for n, answer in enumerate(polls):
                (fake / f"pr-{n}.json").write_text(json.dumps(answer))
            (fake / "ci-runs.json").write_text(json.dumps([
                {"databaseId": 100, "status": "completed", "conclusion": "success", "headSha": MERGE}]))
            if reject:
                (fake / "reject-merge").touch()
            result = subprocess.run(
                ["bash", str(fake / "pr-watch.sh"), "--enqueue", "3426"], capture_output=True, text=True, timeout=20,
                env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "FAKE": str(fake),
                     "PR_WATCH_INTERVAL": "0", "PR_WATCH_QUIET": quiet})
            calls = (fake / "merge-called").read_text() if (fake / "merge-called").exists() else ""
            return result, calls

    def test_a_pr_that_conflicts_with_main_ends_the_watch_and_says_so(self):
        result, _ = self.watch([open_pr(mergeable="CONFLICTING")])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("conflicts with its base branch", result.stdout)

    def test_existing_auto_merge_is_reported_as_unguarded(self):
        result, calls = self.watch([open_pr(auto=True)])
        self.assertFalse(calls)
        self.assertIn("existing auto-merge is not guarded by ready", result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_a_green_pr_whose_review_is_not_done_is_not_merged_and_says_what_is_missing(self):
        result, merged = self.watch([open_pr(), open_pr(), CLOSED], quiet="0",
                                    reviews=[("还不能递\n缺增量审查", False)])
        self.assertFalse(merged, result.stdout)
        self.assertIn("not merged, review is not done: 还不能递; 缺增量审查", result.stdout)
        self.assertIn("not ready: 还不能递; 缺增量审查", result.stdout)

    def test_a_pr_is_merged_once_its_review_is_done_with_nothing_else_changed(self):
        waiting = open_pr()
        result, merged = self.watch([waiting, waiting, waiting, waiting, MERGED],
                                    reviews=[("还不能递", False), ("还不能递", False),
                                             ("可递：head aaaaaaaa｜CI required success", True)])
        self.assertTrue(merged, result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("squash merge requested", result.stdout)
        self.assertIn("--squash", merged)
        self.assertIn(f"--match-head-commit {HEAD}", merged)
        self.assertNotIn("--auto", merged)

    def test_a_push_between_ready_and_merge_prevents_the_merge(self):
        result, calls = self.watch([open_pr(), open_pr(head=PUSHED), CLOSED])
        self.assertFalse(calls, result.stdout)
        self.assertIn("head or mergeability changed after ready", result.stdout)

    def test_ready_for_a_different_head_never_merges(self):
        result, calls = self.watch([open_pr(head=PUSHED), CLOSED])
        self.assertFalse(calls, result.stdout)
        self.assertIn("not merged", result.stdout)

    def test_exit_zero_without_a_ready_line_never_merges(self):
        result, calls = self.watch([open_pr(), CLOSED], reviews=[("unknown", True)])
        self.assertFalse(calls, result.stdout)

    def test_a_push_after_the_last_read_is_guarded_by_the_server(self):
        result, calls = self.watch([open_pr(), open_pr(), CLOSED], reject=True)
        self.assertIn(f"--match-head-commit {HEAD}", calls)
        self.assertIn("merge refused", result.stdout)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
