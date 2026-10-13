"""The review-pin rule, run the way CI runs it: a PR file, a comments file, an exit code."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("check-review-pin.py")
HEAD = "a" * 40
OLD = "b" * 40


def review(sha, *severities, marker=True):
    """A pin comment body the way .claude/pr-review/render.py writes it."""
    lines = [f"<!-- pr-review sha={sha} -->" if marker else "no marker", "**Independent review**", ""]
    for i, severity in enumerate(severities, 1):
        lines += [f"### {i}. [{severity}] something is wrong {i}", "`a.py:1` · runtime", ""]
    return "\n".join(lines)


def comment(body, *, who="MEMBER", at="2026-10-13T10:00:00Z", id=1):
    return {"id": id, "body": body, "author_association": who, "created_at": at}


def run(comments, *, head=HEAD, draft=False, pages=None):
    with tempfile.TemporaryDirectory() as tmp:
        pull = Path(tmp, "pull.json")
        pull.write_text(json.dumps({"number": 7, "draft": draft, "head": {"sha": head}}))
        listing = Path(tmp, "comments.json")
        listing.write_text(pages if pages is not None else json.dumps(comments))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--pull-request", str(pull), "--comments", str(listing)],
            capture_output=True,
            text=True,
        )


class ReviewPinTest(unittest.TestCase):
    def test_a_clean_review_of_the_head_passes(self):
        for severities in [(), ("minor",), ("minor", "minor")]:
            with self.subTest(severities=severities):
                result = run([comment(review(HEAD, *severities))])
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_no_review_at_all_is_red(self):
        for comments in [[], [comment("looks good to me")]]:
            with self.subTest(comments=comments):
                result = run(comments)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("no independent review is pinned", result.stdout)

    def test_a_review_of_an_older_head_is_red_and_names_both_commits(self):
        result = run([comment(review(OLD))])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(OLD[:12], result.stdout)
        self.assertIn(HEAD[:12], result.stdout)
        self.assertIn("pr-review.sh", result.stdout)

    def test_a_blocker_or_major_on_the_head_is_red_but_a_minor_is_not(self):
        for severity in ("blocker", "major"):
            with self.subTest(severity=severity):
                result = run([comment(review(HEAD, "minor", severity))])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"[{severity}]", result.stdout)

    def test_only_the_newest_pin_counts(self):
        stale_then_good = [
            comment(review(OLD, "major"), at="2026-10-13T09:00:00Z", id=1),
            comment(review(HEAD), at="2026-10-13T10:00:00Z", id=2),
        ]
        self.assertEqual(run(stale_then_good).returncode, 0)
        good_then_stale = [
            comment(review(HEAD), at="2026-10-13T09:00:00Z", id=1),
            comment(review(OLD), at="2026-10-13T10:00:00Z", id=2),
        ]
        self.assertEqual(run(good_then_stale).returncode, 1)
        good_then_major = [
            comment(review(HEAD), at="2026-10-13T09:00:00Z", id=1),
            comment(review(HEAD, "major"), at="2026-10-13T10:00:00Z", id=2),
        ]
        self.assertEqual(run(good_then_major).returncode, 1)

    def test_a_pin_from_someone_with_no_role_here_counts_for_nothing(self):
        for who in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"):
            with self.subTest(who=who):
                self.assertEqual(run([comment(review(HEAD), who=who)]).returncode, 1)
        trusted_stale = comment(review(OLD), at="2026-10-13T09:00:00Z", id=1)
        outsider_fresh = comment(review(HEAD), who="NONE", at="2026-10-13T10:00:00Z", id=2)
        self.assertEqual(run([trusted_stale, outsider_fresh]).returncode, 1)
        trusted_good = comment(review(HEAD), at="2026-10-13T09:00:00Z", id=1)
        outsider_junk = comment(review(OLD, "major"), who="NONE", at="2026-10-13T10:00:00Z", id=2)
        self.assertEqual(run([trusted_good, outsider_junk]).returncode, 0)

    def test_a_marker_in_the_middle_of_a_comment_is_not_a_pin(self):
        quoted = comment("see\n" + review(HEAD))
        self.assertEqual(run([quoted]).returncode, 1)

    def test_a_marker_without_a_readable_commit_is_red(self):
        for line in ["<!-- pr-review sha=abc -->", "<!-- pr-review sha= -->", f"<!-- pr-review sha={HEAD.upper()} -->"]:
            with self.subTest(line=line):
                result = run([comment(line)])
                self.assertEqual(result.returncode, 1, result.stdout)

    def test_a_draft_is_not_judged(self):
        result = run([], draft=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("draft", result.stdout)

    def test_reads_what_gh_api_paginate_prints(self):
        page_one = json.dumps([comment(review(OLD), id=1, at="2026-10-13T09:00:00Z")])
        page_two = json.dumps([comment(review(HEAD), id=2, at="2026-10-13T10:00:00Z")])
        self.assertEqual(run(None, pages=page_one + page_two).returncode, 0)
        self.assertEqual(run(None, pages=page_two + "\n" + page_one).returncode, 0)
        slurped = json.dumps([json.loads(page_one), json.loads(page_two)])
        self.assertEqual(run(None, pages=slurped).returncode, 0)
        self.assertEqual(run(None, pages=page_one).returncode, 1)

    def test_a_pull_request_without_a_head_is_red(self):
        self.assertEqual(run([comment(review(HEAD))], head="").returncode, 1)


if __name__ == "__main__":
    unittest.main()
