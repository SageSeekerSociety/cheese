"""Which Required CI runs a new merge queue entry may cancel, and which never."""

import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
import unittest.mock
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


ENV = {
    "GITHUB_REPOSITORY": "o/r",
    "GITHUB_RUN_ID": "99",
    "HEAD_REF": HEAD_REF,
    "HEAD_SHA": NEW_SHA,
}


class FakeApi:
    """GitHub as `gh api` would answer: listed runs, and the cancels asked for."""

    def __init__(self, runs, fail_cancel=(), fail_list=False):
        self.runs = runs
        self.fail_cancel = set(fail_cancel)
        self.fail_list = fail_list
        self.cancelled = []

    def __call__(self, *args):
        path = args[-1]
        if args[:2] == ("-X", "POST"):
            run_id = int(path.split("/")[-2])
            if run_id in self.fail_cancel:
                raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 409")
            self.cancelled.append(run_id)
            return None
        if path.endswith("/runs/99"):
            return {"created_at": "2026-10-17T12:00:00Z"}
        if self.fail_list:
            raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")
        query = dict(part.split("=") for part in path.split("?")[1].split("&"))
        matching = [r for r in self.runs if r["status"] == query["status"]]
        size, page = int(query["per_page"]), int(query["page"])
        return {"workflow_runs": matching[(page - 1) * size : page * size]}


def old_branch(number=4097):
    return f"gh-readonly-queue/main/pr-{number}-{OLD_SHA}"


class CancelTests(unittest.TestCase):
    def test_only_the_superseded_run_is_cancelled(self):
        api = FakeApi([
            run(1, old_branch(), OLD_SHA),
            run(2, f"gh-readonly-queue/main/pr-4097-{NEW_SHA}", NEW_SHA, minute=59),
            run(3, old_branch(4096), OLD_SHA),
        ])
        dedupe.cancel_superseded(api, ENV)
        self.assertEqual(api.cancelled, [1])

    def test_a_run_newer_than_this_entrys_required_ci_run_is_left_alone(self):
        # This job started late; the other run came from a later queue rebuild.
        api = FakeApi([
            run(2, f"gh-readonly-queue/main/pr-4097-{NEW_SHA}", NEW_SHA, minute=40),
            run(1, old_branch(), OLD_SHA, minute=50),
        ])
        dedupe.cancel_superseded(api, ENV)
        self.assertEqual(api.cancelled, [])

    def test_superseded_runs_beyond_the_first_page_are_cancelled(self):
        others = [run(100 + i, old_branch(5000 + i), OLD_SHA) for i in range(150)]
        api = FakeApi([*others, run(1, old_branch(), OLD_SHA)])
        dedupe.cancel_superseded(api, ENV)
        self.assertEqual(api.cancelled, [1])

    def test_a_failed_cancel_does_not_stop_the_others(self):
        api = FakeApi(
            [run(1, old_branch(), OLD_SHA), run(2, old_branch(), "c" * 40, status="queued")],
            fail_cancel={1, 2},
        )
        dedupe.cancel_superseded(api, ENV)
        self.assertEqual(api.cancelled, [])
        api.fail_cancel = {2}
        dedupe.cancel_superseded(api, ENV)
        api.fail_cancel = {1}
        dedupe.cancel_superseded(api, ENV)
        self.assertEqual(sorted(api.cancelled), [1, 2])

    def test_the_job_exits_zero_when_github_cannot_be_read(self):
        api = FakeApi([run(1, old_branch(), OLD_SHA)], fail_list=True)
        with unittest.mock.patch.object(dedupe, "gh_api", api), \
                unittest.mock.patch.dict(dedupe.os.environ, ENV), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dedupe.main(), 0)
        self.assertEqual(api.cancelled, [])

    def test_the_job_exits_zero_on_a_run_it_cannot_parse(self):
        broken = run(1, old_branch(), OLD_SHA)
        del broken["created_at"]
        api = FakeApi([broken])
        with unittest.mock.patch.object(dedupe, "gh_api", api), \
                unittest.mock.patch.dict(dedupe.os.environ, ENV), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dedupe.main(), 0)
        self.assertEqual(api.cancelled, [])


class RepoWideListingTests(unittest.TestCase):
    """queue-dedupe.yml adds a second, always-green run to every merge_group
    event, so a tool that lists merge_group runs repo-wide counts it as an
    entry's result. Listings have to name the workflow they read."""

    def test_no_tool_lists_merge_group_runs_across_workflows(self):
        root = Path(__file__).resolve().parents[2]
        offenders = []
        for tree in (".github", ".claude/scripts", "deploy", "scripts"):
            for path in (root / tree).rglob("*"):
                if path.suffix not in (".py", ".sh", ".yml") or path == Path(__file__):
                    continue
                if "actions/runs?event=merge_group" in path.read_text(errors="ignore"):
                    offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
