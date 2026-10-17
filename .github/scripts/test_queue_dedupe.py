"""Which Required CI runs a new merge queue entry may cancel, and which never."""

import importlib.util
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "queue_dedupe", Path(__file__).with_name("queue-dedupe.py")
)
dedupe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dedupe
spec.loader.exec_module(dedupe)

NEW_SHA = "b56df5e7" + "0" * 32
OLD_SHA = "4490d2b2" + "0" * 32
HEAD_REF = f"refs/heads/gh-readonly-queue/main/pr-4097-{NEW_SHA}"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def run(run_id, branch, sha, minute=50, event="merge_group", status="in_progress"):
    return {
        "id": run_id,
        "event": event,
        "status": status,
        "head_branch": branch,
        "head_sha": sha,
        "created_at": f"2026-10-17T11:{minute:02d}:00Z",
    }


def picked(runs, head_ref=HEAD_REF, head_sha=NEW_SHA, before=NOW):
    return [r["id"] for r in dedupe.superseded_runs(runs, head_ref, head_sha, before)]


class ParseRefTests(unittest.TestCase):
    def test_full_ref_and_branch_name_give_the_same_entry(self):
        self.assertEqual(dedupe.parse_queue_ref(HEAD_REF), ("main", 4097))
        self.assertEqual(
            dedupe.parse_queue_ref(f"gh-readonly-queue/main/pr-4097-{OLD_SHA}"),
            ("main", 4097),
        )

    def test_anything_else_is_not_a_queue_ref(self):
        for ref in ("", None, "refs/heads/main", "refs/pull/4097/merge",
                    "gh-readonly-queue/main/pr-x-abc"):
            with self.subTest(ref=ref):
                self.assertIsNone(dedupe.parse_queue_ref(ref))


class SupersededRunTests(unittest.TestCase):
    def test_same_pr_on_an_older_sha_is_cancelled(self):
        old = run(1, f"gh-readonly-queue/main/pr-4097-{OLD_SHA}", OLD_SHA)
        self.assertEqual(picked([old]), [1])

    def test_this_entrys_own_run_is_never_cancelled(self):
        own = run(2, f"gh-readonly-queue/main/pr-4097-{NEW_SHA}", NEW_SHA)
        self.assertEqual(picked([own]), [])

    def test_another_prs_entry_is_never_cancelled(self):
        for number in (4096, 40970, 409):
            with self.subTest(number=number):
                other = run(3, f"gh-readonly-queue/main/pr-{number}-{OLD_SHA}", OLD_SHA)
                self.assertEqual(picked([other]), [])

    def test_a_queue_for_another_base_is_never_cancelled(self):
        other = run(4, f"gh-readonly-queue/release/pr-4097-{OLD_SHA}", OLD_SHA)
        self.assertEqual(picked([other]), [])

    def test_a_run_that_is_not_merge_group_is_never_cancelled(self):
        pr_run = run(5, f"gh-readonly-queue/main/pr-4097-{OLD_SHA}", OLD_SHA,
                     event="pull_request")
        self.assertEqual(picked([pr_run]), [])

    def test_a_finished_run_is_left_alone(self):
        done = run(6, f"gh-readonly-queue/main/pr-4097-{OLD_SHA}", OLD_SHA,
                   status="completed")
        self.assertEqual(picked([done]), [])

    def test_a_run_created_after_this_entry_is_left_alone(self):
        # A newer entry's run, seen by a late-delivered event of an older entry.
        newer = run(7, f"gh-readonly-queue/main/pr-4097-{OLD_SHA}", OLD_SHA)
        before = datetime(2026, 10, 17, 11, 40, tzinfo=UTC)
        self.assertEqual(picked([newer], before=before), [])

    def test_an_unparseable_entry_cancels_nothing(self):
        old = run(8, f"gh-readonly-queue/main/pr-4097-{OLD_SHA}", OLD_SHA)
        self.assertEqual(picked([old], head_ref="refs/heads/main"), [])


if __name__ == "__main__":
    unittest.main()
