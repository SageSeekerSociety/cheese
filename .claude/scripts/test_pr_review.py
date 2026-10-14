#!/usr/bin/env python3
"""pr-review.sh against a throwaway repository and a stand-in `gh`.

    python3 .claude/scripts/test_pr_review.py

The stand-in `gh` answers the few `gh api` reads prepare makes and appends every call to a
log, so the test can say what was and was not asked of GitHub. No network, no credentials.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PR = "7"
#: A second PR whose only change is a lock file: 档位 G, so nobody reviews it and the
#: card still carries a conclusion line saying so.
PR_MECHANICAL = "8"

GH = r"""#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$*" in
  *.head.sha*) case "$*" in *pulls/8*) echo "$PR8_HEAD" ;; *) echo "$PR_HEAD" ;; esac ;;
  *.base.ref*) echo main ;;
  *--paginate*/commits*)
    case "$*" in *pulls/8*) printf '%s\n' $PR8_COMMITS ;; *) printf '%s\n' $PR_COMMITS ;; esac ;;
  *check-runs*) printf '%s\n' "${CI_JSON:-}" ;;
  *'"# "'*) printf '# A title\n\nThe body.\n' ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def result_json(sha, *severities):
    return {
        "reviewed_sha": sha,
        "summary": "Summary.",
        "candidates_found": 2,
        "candidates_refuted": 1,
        "findings": [
            {
                "severity": sev,
                "title": f"a {sev} thing",
                "file": "f.txt",
                "line": 1,
                "kind": "runtime",
                "rung": 4,
                "failure": "it breaks",
                "evidence": "ran it",
                "confirm_with": "",
            }
            for sev in severities
        ],
        "not_covered": [],
    }


class PrReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pr-review-test-"))
        origin, work = cls.tmp / "origin.git", cls.tmp / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
            git(work, "config", k, v)
        (work / ".claude/scripts").mkdir(parents=True)
        shutil.copytree(REPO_ROOT / ".claude/pr-review", work / ".claude/pr-review")
        shutil.copy(REPO_ROOT / ".claude/scripts/pr-review.sh", work / ".claude/scripts/")
        (work / "f.txt").write_text("base\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "base")
        git(work, "remote", "add", "origin", str(origin))
        git(work, "push", "-q", "origin", "main")
        # The PR's commits live on their own branch: main is the base and stays there, the
        # way it does for a PR that has not been merged. A test that advanced main along
        # with the PR would leave the tier scripts no base to measure the change against.
        git(work, "checkout", "-q", "-b", "pr")
        cls.shas = []
        for n in (1, 2, 3):
            (work / "f.txt").write_text(f"change {n}\n")
            git(work, "commit", "-qam", f"change {n}")
            cls.shas.append(git(work, "rev-parse", "HEAD"))
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR}/head")
        # A commit of the PR that is not an ancestor of its head (a force-push left it behind).
        git(work, "checkout", "-q", "-b", "side", cls.shas[0])
        (work / "f.txt").write_text("side\n")
        git(work, "commit", "-qam", "side")
        cls.side = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/side")
        # A PR whose only change is to a lock file, off the same base: 档位 G. It branches
        # from main, not from the other PR's head, or the other PR's change would come with
        # it and the tier would be the other PR's tier.
        git(work, "checkout", "-q", "-b", "mechanical", "main")
        (work / "frontend/pnpm-lock.yaml").parent.mkdir(parents=True, exist_ok=True)
        (work / "frontend/pnpm-lock.yaml").write_text("lockfileVersion: 2\n")
        git(work, "add", "frontend/pnpm-lock.yaml")
        git(work, "commit", "-qm", "bump lock")
        cls.mech = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_MECHANICAL}/head")
        git(work, "checkout", "-q", "main")
        git(work, "fetch", "-q", "origin")
        cls.work = work

        cls.bin = cls.tmp / "bin"
        cls.bin.mkdir()
        (cls.bin / "gh").write_text(GH)
        (cls.bin / "gh").chmod(0o755)
        cls.log = cls.tmp / "gh.log"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_script(self, *args, env_extra=None):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "PR_HEAD": self.shas[-1],
            "PR_COMMITS": " ".join([*self.shas, self.side]),
            "PR8_HEAD": self.mech,
            "PR8_COMMITS": self.mech,
            "PR_REVIEW_DIR": str(self.tmp / "reviews"),
            **(env_extra or {}),
        }
        (self.tmp / "reviews").mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(self.work / ".claude/scripts/pr-review.sh"), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def gh_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def prepare(self, *args):
        out = self.run_script("prepare", PR, *args)
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        return review_md, review_md.read_text()

    def post(self, review_md, *severities, dry=False):
        d = review_md.parent
        meta = dict(
            line.split("=", 1) for line in (d / "meta").read_text().splitlines() if "=" in line
        )
        sha = meta["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(result_json(sha, *severities)))
        before = self.gh_calls()
        out = self.run_script("post", str(d), *(["--dry-run"] if dry else []))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.gh_calls(), before, "post must not call gh")
        return d, out.stdout

    def test_post_writes_the_result_and_never_calls_gh(self):
        review_md, text = self.prepare()
        self.assertNotIn("Incremental review", text)
        d, out = self.post(review_md, "minor")
        saved = (d / "REVIEW-RESULT.md").read_text()
        self.assertEqual(saved, out)
        self.assertEqual(
            saved.splitlines()[0],
            f"独立 review：head {self.shas[-1][:8]} 结论 无 blocker/major｜档位 B",
        )
        shown = self.run_script("show", str(d), "--line")
        self.assertEqual(shown.stdout, saved.splitlines()[0] + "\n")
        self.assertEqual(self.run_script("show", str(d)).stdout, saved)
        self.assertFalse(any("comment" in c for c in self.gh_calls()))

    def test_prepare_decides_the_tier_and_prints_its_criteria(self):
        _, text = self.prepare()
        self.assertIn("## Review tier", text)
        self.assertIn("**档位 B 实现代码**", text)
        self.assertIn("两路全新上下文找茬", text)
        self.assertIn("f.txt（实现代码）", text)

    def test_a_mechanical_change_is_tier_g_and_gets_a_result_without_a_review(self):
        # post runs straight on prepare's own result.json, without the seat writing one:
        # that is the point of the tier, so nothing writes over it here.
        review_md, _ = self.prepare_tier_g()
        before = self.gh_calls()
        out = self.run_script("post", str(review_md.parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.gh_calls(), before, "post must not call gh")
        self.assertEqual(
            out.stdout.splitlines()[0],
            f"独立 review：head {self.mech[:8]} 结论 无 blocker/major｜档位 G",
        )
        # The line must not read as a judgement somebody made: the body says so.
        self.assertIn("没有审查者读过", out.stdout)

    def prepare_tier_g(self):
        out = self.run_script("prepare", PR_MECHANICAL)
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        self.assertIn("档位 G", out.stderr)
        self.assertIn("**档位 G 机械改动**", review_md.read_text())
        # prepare wrote the result a reviewer would otherwise write: post runs straight away.
        result = json.loads((review_md.parent / "result.json").read_text())
        self.assertEqual(result["reviewed_sha"], self.mech)
        self.assertEqual(result["findings"], [])
        self.assertIn("免审", result["summary"])
        self.assertTrue(all("免审" in item for item in result["not_covered"]))
        return review_md, out

    def test_post_recomputes_the_tier_instead_of_trusting_meta(self):
        # A seat cannot promote its own change to a lighter tier by editing meta: post
        # recomputes from the tree, and the conclusion line follows that, not the file.
        review_md, _ = self.prepare()
        d = review_md.parent
        meta = (d / "meta").read_text().replace("tier=B", "tier=G")
        self.assertIn("tier=G", meta)
        (d / "meta").write_text(meta)
        _, out = self.post(review_md, "minor")
        self.assertIn("档位 B", out.splitlines()[0])
        self.assertNotIn("档位 G", out.splitlines()[0])

    #: A green "CI required" check run on the PR head, as `gh api … check-runs --jq` prints
    #: it: one JSON object per line.
    GREEN = '{"name": "CI required", "status": "completed", "conclusion": "success", ' \
            '"started_at": "2026-01-01T00:00:00Z"}'

    def ready(self, *args, ci=None):
        return self.run_script("ready", *args, env_extra={"CI_JSON": self.GREEN if ci is None else ci})

    def test_ready_says_可递_when_the_ci_is_green_and_the_review_covers_the_head(self):
        review_md, _ = self.prepare()
        d, _ = self.post(review_md, "minor")
        for arg in (str(d), PR):  # a prepare directory, or the PR it belongs to
            out = self.ready(arg)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(
                out.stdout.splitlines()[0],
                f"可递：head {self.shas[-1][:8]}｜CI required success"
                f"｜review {self.shas[-1][:8]} 结论 无 blocker/major｜档位 B",
            )

    def test_ready_says_what_is_missing(self):
        review_md, _ = self.prepare()
        d, _ = self.post(review_md, "minor")
        red = self.ready(d, ci='{"name": "CI required", "status": "completed", '
                               '"conclusion": "failure", "started_at": "2026-01-01T00:00:00Z"}')
        self.assertEqual(red.returncode, 1)
        self.assertIn("还不能递", red.stdout)
        self.assertIn("CI required 在 head 上是 failure", red.stdout)
        # No conclusion line in the directory yet: ready must not read CI-green as delivery.
        (d / "REVIEW-RESULT.md").unlink()
        gone = self.ready(d)
        self.assertEqual(gone.returncode, 1)
        self.assertIn("卡里没有审查结论", gone.stdout)
        # A check run that has not finished is not green either.
        pending = self.ready(d, ci='{"name": "CI required", "status": "in_progress", '
                                   '"conclusion": null, "started_at": "2026-01-01T00:00:00Z"}')
        self.assertEqual(pending.returncode, 1)

    def test_ready_does_not_deliver_a_review_with_a_blocker(self):
        review_md, _ = self.prepare()
        d, _ = self.post(review_md, "major")
        out = self.ready(d)
        self.assertEqual(out.returncode, 1)
        self.assertIn("审查结论有 1 条 blocker/major", out.stdout)

    def test_ready_on_a_tier_g_change_needs_no_reviewer(self):
        review_md, _ = self.prepare_tier_g()
        self.run_script("post", str(review_md.parent))
        out = self.run_script("ready", str(review_md.parent), env_extra={"CI_JSON": self.GREEN})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(f"review {self.mech[:8]} 结论 无 blocker/major｜档位 G", out.stdout)

    def test_ready_asks_for_an_increment_review_when_the_head_moved(self):
        # The head moved, and what moved it is a change of this PR's own: the review of the
        # older commit no longer covers the head, so ready points at the delta review.
        review_md, _ = self.prepare()
        d, _ = self.post(review_md, "minor")
        git(self.work, "checkout", "-q", "pr")
        (self.work / "f.txt").write_text("change 4\n")
        git(self.work, "commit", "-qam", "change 4")
        moved = git(self.work, "rev-parse", "HEAD")
        git(self.work, "checkout", "-q", "main")
        out = self.run_script("ready", str(d),
                              env_extra={"CI_JSON": self.GREEN, "PR_HEAD": moved})
        self.assertEqual(out.returncode, 1)
        self.assertIn("缺一次增量审", out.stdout)
        self.assertIn(f"--since {self.shas[-1][:8]}", out.stdout)

    def test_dry_run_prints_without_writing(self):
        review_md, _ = self.prepare()
        d, out = self.post(review_md, "major", dry=True)
        self.assertIn("有 1 条", out.splitlines()[0])
        self.assertFalse((d / "REVIEW-RESULT.md").exists())

    def test_incremental_review_is_scoped_to_the_increment(self):
        since = self.shas[0]
        review_md, text = self.prepare("--since", since[:10])
        self.assertIn("## Incremental review", text)
        self.assertIn(f"{since[:12]}..{self.shas[-1][:12]}", text)
        self.assertIn(f"git diff {since[:12]} HEAD", text)
        self.assertIn(f"delta since {since}", text)
        d, out = self.post(review_md, "blocker", "major", "minor")
        self.assertEqual(
            out.splitlines()[0],
            f"独立 review：head {self.shas[-1][:8]}（增量自 {since[:8]}）结论 有 2 条｜档位 B",
        )

    def test_incremental_review_picks_up_the_last_rounds_result(self):
        first, _ = self.prepare(self.shas[0])
        self.post(first, "major")
        _, text = self.prepare("--since", self.shas[0])
        self.assertTrue((first.parent.parent / f"pr-review-{PR}-{self.shas[-1][:12]}.input/previous.md").exists())
        self.assertIn("previous.md", text)

    def test_since_must_be_an_older_commit_on_the_same_line(self):
        for since, why in ((self.side, "not an ancestor"), (self.shas[-1], "already the head")):
            out = self.run_script("prepare", PR, "--since", since)
            self.assertEqual(out.returncode, 1)
            self.assertIn(why, out.stderr)

    def test_usage_errors(self):
        for args in (
            ("prepare", PR, "--since"),
            ("prepare", PR, "--bogus"),
            ("post",),
            ("show", "d", "--bogus"),
        ):
            self.assertEqual(self.run_script(*args).returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
