"""The review-pin rule, run the way CI runs it: a PR file, a comments file, an exit code."""

from pathlib import Path
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("check-review-pin.py")
RENDER = Path(__file__).resolve().parents[2] / ".claude" / "pr-review" / "render.py"
HEAD = "a" * 40
OLD = "b" * 40


def review(sha, *severities, marker=True):
    """A pin comment body the way .claude/pr-review/render.py writes it."""
    lines = [f"<!-- pr-review sha={sha} -->" if marker else "no marker", "**Independent review**", ""]
    for i, severity in enumerate(severities, 1):
        lines += [
            f"### {i}. [{severity}] something is wrong {i}",
            f"`a.py:{i}` · runtime · evidence rung 4 (verified)",
            "",
        ]
    return "\n".join(lines)


def rendered(sha, *severities):
    """The pin comment body from the real renderer, so a change to its format turns this red."""
    spec = importlib.util.spec_from_file_location("pr_review_render", RENDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    findings = [
        {
            "severity": severity,
            "title": f"something is wrong {i}",
            "file": "a.py",
            "line": i,
            "kind": "runtime",
            "rung": 4,
            "failure": "x -> y",
            "evidence": "ran it",
            "confirm_with": "",
        }
        for i, severity in enumerate(severities, 1)
    ]
    result = {
        "summary": "a summary",
        "candidates_found": len(findings),
        "candidates_refuted": 0,
        "findings": findings,
        "not_covered": [],
    }
    return module.render(result, sha, "c" * 40, "")


def comment(body, *, who="MEMBER", at="2026-10-13T10:00:00Z", id=1, login="someone", kind="User"):
    return {
        "id": id,
        "body": body,
        "author_association": who,
        "created_at": at,
        "user": {"login": login, "type": kind},
    }


def app_comment(body, **kwargs):
    """What GitHub reports for a comment posted with the App's installation token."""
    return comment(body, who="CONTRIBUTOR", login="cheesex-app[bot]", kind="Bot", **kwargs)


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

    def test_reads_the_findings_the_real_renderer_writes(self):
        self.assertEqual(run([comment(rendered(HEAD))]).returncode, 0)
        self.assertEqual(run([comment(rendered(HEAD, "minor"))]).returncode, 0)
        for severity in ("blocker", "major"):
            with self.subTest(severity=severity):
                result = run([comment(rendered(HEAD, "minor", severity))])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"[{severity}] something is wrong 2", result.stdout)

    def test_prose_that_quotes_a_heading_is_not_a_finding(self):
        body = review(HEAD) + "\nThe earlier round had:\n### 1. [major] quoted\nnot a finding\n"
        self.assertEqual(run([comment(body)]).returncode, 0)

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

    def test_every_role_that_can_push_here_may_pin(self):
        for who in ("OWNER", "MEMBER", "COLLABORATOR"):
            with self.subTest(who=who):
                self.assertEqual(run([comment(review(HEAD), who=who)]).returncode, 0)
                self.assertEqual(run([comment(review(OLD), who=who)]).returncode, 1)

    def test_a_pin_posted_through_the_app_counts_but_only_the_apps(self):
        self.assertEqual(run([app_comment(review(HEAD))]).returncode, 0)
        stale = run([app_comment(review(OLD))])
        self.assertEqual(stale.returncode, 1)
        self.assertIn(OLD[:12], stale.stdout)
        self.assertEqual(run([app_comment(review(HEAD, "major"))]).returncode, 1)
        other_bot = comment(review(HEAD), who="NONE", login="coderabbitai[bot]", kind="Bot")
        self.assertEqual(run([other_bot]).returncode, 1)
        look_alike = comment(review(HEAD), who="CONTRIBUTOR", login="cheesex-app[bot]", kind="User")
        self.assertEqual(run([look_alike]).returncode, 1)
        app_good = app_comment(review(HEAD), at="2026-10-13T09:00:00Z", id=1)
        other_junk = comment(review(OLD, "major"), who="NONE", login="coderabbitai[bot]", kind="Bot", at="2026-10-13T10:00:00Z", id=2)
        self.assertEqual(run([app_good, other_junk]).returncode, 0)

    def test_a_marker_in_the_middle_of_a_comment_is_not_a_pin(self):
        quoted = comment("see\n" + review(HEAD))
        self.assertEqual(run([quoted]).returncode, 1)
        real = comment(review(HEAD), at="2026-10-13T09:00:00Z", id=1)
        later_quote = comment("this one is stale:\n" + review(OLD, "major"), at="2026-10-13T10:00:00Z", id=2)
        self.assertEqual(run([real, later_quote]).returncode, 0)

    def test_a_marker_without_a_readable_commit_is_red(self):
        for line in ["<!-- pr-review sha=abc -->", "<!-- pr-review sha= -->", f"<!-- pr-review sha={HEAD.upper()} -->"]:
            with self.subTest(line=line):
                result = run([comment(line)])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("unreadable pin line", result.stdout)

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
        result = run([comment(review(HEAD))], head="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no head sha", result.stdout)


if __name__ == "__main__":
    unittest.main()
