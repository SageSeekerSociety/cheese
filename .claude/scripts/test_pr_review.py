#!/usr/bin/env python3
"""pr-review.sh against a throwaway repository and a stand-in `gh`.

    python3 .claude/scripts/test_pr_review.py

The stand-in `gh` answers the few `gh api` reads prepare makes and appends every call to a
log, so the test can say what was and was not asked of GitHub. No network, no credentials.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PR = "7"
#: A second PR whose only change is a lock file: 档位 G, so nobody reviews it and the
#: card still carries a conclusion line saying so.
PR_MECHANICAL = "8"
#: A PR whose head commit is a lock bump but whose first commit is an auth change: 档位 A,
#: the tier of the whole change, not of the last commit.
PR_MIXED = "9"
#: A PR that rewrites the rule its own review would be handed, and changes a path that
#: rule is scoped to: the point of reading the rules at the default branch.
PR_RULE_EDIT = "10"
#: A PR whose reviewed commit was followed by a merge of the base branch and nothing else:
#: the increment §4 exempts, so ready must take the original conclusion line.
PR_BASE_MERGE = "11"

GH = r"""#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$*" in
  *.head.sha*) case "$*" in *pulls/8*) echo "$PR8_HEAD" ;; *pulls/9*) echo "$PR9_HEAD" ;;
    *pulls/10*) echo "$PR10_HEAD" ;; *pulls/11*) echo "$PR11_HEAD" ;; *) echo "$PR_HEAD" ;; esac ;;
  *.base.ref*) echo main ;;
  *--paginate*/commits*)
    case "$*" in *pulls/8*) printf '%s\n' $PR8_COMMITS ;;
      *pulls/9*) printf '%s\n' $PR9_COMMITS ;;
      *pulls/10*) printf '%s\n' $PR10_COMMITS ;;
      *pulls/11*) printf '%s\n' $PR11_COMMITS ;;
      *) printf '%s\n' $PR_COMMITS ;; esac ;;
  *check-runs*) printf '%s\n' "${CI_JSON:-}" ;;
  *'"# "'*) printf '# A title\n\nThe body.\n' ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""

#: A stand-in `git` that records the GIT_NO_LAZY_FETCH each call saw, then runs the real
#: one. The variable's presence is the thing under test — a normal clone never notices it —
#: so the log line is what says prepare stripped it and post set it.
GIT_WRAPPER = r"""#!/usr/bin/env bash
printf '%s\n' "${GIT_NO_LAZY_FETCH-<unset>}" >> "$GIT_LOG"
exec __REAL_GIT__ "$@"
"""


#: A stand-in for the render.py on main, until this branch merges: it takes the arguments
#: it was written for and answers anything else with its usage, exit 2, the way the real
#: one does. The 档位 is the argument it does not know.
OLD_RENDER = r'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if len(args) == 5 and args[3] == "--since":
    args = args[:3]
if len(args) != 3:
    sys.stderr.write("usage\n")
    sys.exit(2)
print("独立 review：head " + args[1][:8] + " 结论 无 blocker/major")
'''


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def result_json(sha, *severities, kinds=None):
    """A result with one finding per severity. `kinds` overrides the kind of them, in
    order; the default is the kind most findings have, so a case about severity does not
    have to say it."""
    kind_of = list(kinds or []) + ["runtime"] * len(severities)
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
                "kind": kind_of[i],
                "rung": 4,
                "failure": "it breaks",
                "evidence": "ran it",
                "confirm_with": "",
            }
            for i, sev in enumerate(severities)
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
        # The rules a change loads, as the default branch has them: one scoped to a path
        # this repo's PRs touch, one scoped elsewhere, one scoped to nothing, and the
        # AGENTS.md of a directory one of them changes a file in.
        (work / ".claude/rules").mkdir(parents=True)
        (work / ".claude/rules/backend.md").write_text(
            '---\npaths:\n  - "backend/app/**"\n---\n\n# BACKEND RULE TEXT\n')
        (work / ".claude/rules/elsewhere.md").write_text(
            '---\npaths: ["cli/**"]\n---\n\n# ELSEWHERE RULE TEXT\n')
        (work / ".claude/rules/unscoped.md").write_text("# UNSCOPED RULE TEXT\n")
        (work / "backend").mkdir(parents=True, exist_ok=True)
        (work / "backend/AGENTS.md").write_text("# BACKEND AGENTS TEXT\n")
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
        # A PR whose last commit is mechanical but whose first is not: the tier is the whole
        # change's, so a fallback that only looks at the head commit would report G here.
        git(work, "checkout", "-q", "-b", "mixed", "main")
        (work / "backend/app/auth").mkdir(parents=True, exist_ok=True)
        (work / "backend/app/auth/caller.py").write_text("ok = True\n")
        git(work, "add", "backend/app/auth/caller.py")
        git(work, "commit", "-qm", "auth change")
        (work / "frontend").mkdir(parents=True, exist_ok=True)
        (work / "frontend/pnpm-lock.yaml").write_text("lockfileVersion: 2\n")
        git(work, "add", "frontend/pnpm-lock.yaml")
        git(work, "commit", "-qm", "bump lock")
        cls.mixed = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_MIXED}/head")
        # A PR that rewrites a rule to point at other paths, and changes a file the rule
        # as it is on main covers. Read from the default branch the rule still arrives;
        # read from this PR's own head it would not, and the reviewer would be handed the
        # author's edited copy of the rule judging the author's change.
        git(work, "checkout", "-q", "-b", "rule-edit", "main")
        (work / ".claude/rules/backend.md").write_text(
            '---\npaths: ["cli/**"]\n---\n\n# REWRITTEN RULE TEXT\n')
        (work / "backend/app/auth").mkdir(parents=True, exist_ok=True)
        (work / "backend/app/auth/caller.py").write_text("ok = True\n")
        git(work, "add", ".claude/rules/backend.md", "backend/app/auth/caller.py")
        git(work, "commit", "-qm", "rewrite the rule")
        cls.rule_edit = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_RULE_EDIT}/head")
        # A PR whose reviewed commit is followed by a merge of the base branch: main moves
        # on after the review, the branch merges it, and the head the card sees is the
        # merge. Everything it brings in is main's own version, so §4 exempts the increment.
        git(work, "checkout", "-q", "-b", "base-merge", "main")
        (work / "f2.txt").write_text("branch\n")
        git(work, "add", "f2.txt")
        git(work, "commit", "-qm", "the change under review")
        cls.base_merge_reviewed = git(work, "rev-parse", "HEAD")
        git(work, "checkout", "-q", "main")
        (work / "main-only.txt").write_text("main\n")
        git(work, "add", "main-only.txt")
        git(work, "commit", "-qm", "main moves on")
        git(work, "push", "-q", "origin", "main")
        git(work, "checkout", "-q", "base-merge")
        git(work, "merge", "-q", "--no-autostash", "--no-edit", "main")
        cls.base_merge_head = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_BASE_MERGE}/head")
        git(work, "checkout", "-q", "main")
        git(work, "fetch", "-q", "origin")
        cls.work = work

        cls.bin = cls.tmp / "bin"
        cls.bin.mkdir()
        (cls.bin / "gh").write_text(GH)
        (cls.bin / "gh").chmod(0o755)
        (cls.bin / "git").write_text(
            GIT_WRAPPER.replace("__REAL_GIT__", shutil.which("git")))
        (cls.bin / "git").chmod(0o755)
        cls.log = cls.tmp / "gh.log"
        cls.gitlog = cls.tmp / "git.log"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_script(self, *args, env_extra=None):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_LOG": str(self.log),
            "GIT_LOG": str(self.gitlog),
            "PR_HEAD": self.shas[-1],
            "PR_COMMITS": " ".join([*self.shas, self.side]),
            "PR8_HEAD": self.mech,
            "PR8_COMMITS": self.mech,
            "PR9_HEAD": self.mixed,
            "PR9_COMMITS": self.mixed,
            "PR10_HEAD": self.rule_edit,
            "PR10_COMMITS": self.rule_edit,
            "PR11_HEAD": self.base_merge_head,
            "PR11_COMMITS": self.base_merge_reviewed,
            "PR_REVIEW_DIR": str(self.tmp / "reviews"),
        }
        for key, value in (env_extra or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
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

    def test_prepare_strips_the_callers_git_no_lazy_fetch_and_post_sets_its_own(self):
        # A seat's shell (and some partial-clone setups) export GIT_NO_LAZY_FETCH=1. prepare
        # has to fetch the PR and read origin/main, so it must not inherit that; post's git
        # calls are graph lookups that must not wait on a fetch, so it sets it for itself.
        self.gitlog.unlink(missing_ok=True)
        out = self.run_script("prepare", PR, env_extra={"GIT_NO_LAZY_FETCH": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        seen = self.gitlog.read_text().splitlines()
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"<unset>"})
        self.gitlog.unlink()
        self.post(review_md, "minor")
        seen = self.gitlog.read_text().splitlines()
        # The script resolves its own root before it knows which subcommand runs.
        self.assertEqual(seen[0], "<unset>")
        self.assertTrue(seen[1:])
        self.assertEqual(set(seen[1:]), {"1"})

    def test_the_round_lives_in_the_home_cache_and_survives_post_and_a_lost_tmp_link(self):
        home = self.tmp / "cache-home"
        home.mkdir(exist_ok=True)
        link = Path("/tmp") / f"pr-review-{PR}-{self.shas[-1][:12]}.input"
        self.addCleanup(link.unlink, missing_ok=True)
        env = {"PR_REVIEW_DIR": None, "HOME": str(home)}
        out = self.run_script("prepare", PR, env_extra=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        d = Path(out.stdout.strip()).parent
        self.assertTrue(str(d).startswith(str(home / ".cheese/review-cache")), d)
        # The old /tmp path still names the round.
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), d.resolve())
        sha = self.meta_of(Path(out.stdout.strip()))["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(result_json(sha, "minor")))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 0, out.stderr)
        tree = d.with_name(d.name[: -len(".input")])
        self.assertFalse(tree.exists(), "post removes the review tree")
        self.assertTrue((d / "REVIEW-RESULT.md").exists())
        self.assertTrue((d / "meta").exists())
        # ready reads the cache, not the link: a machine change takes /tmp with it.
        link.unlink()
        out = self.run_script("ready", PR, env_extra={**env, "CI_JSON": self.GREEN})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("可递：head "), out.stdout)

    def test_re_preparing_the_same_commit_keeps_its_conclusion(self):
        # Re-running prepare for a commit that already has a round (after a machine change,
        # or to redo a review that never posted) must not throw away a conclusion that still
        # covers that commit. Its own root, so the round cannot reach other tests.
        env = {"PR_REVIEW_DIR": str(self.tmp / "reprepare-reviews")}
        out = self.run_script("prepare", PR, env_extra=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        d, _ = self.post(Path(out.stdout.strip()), "minor")
        conclusion = (d / "REVIEW-RESULT.md").read_text()
        out = self.run_script("prepare", PR, env_extra=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(Path(out.stdout.strip()).parent, d)
        self.assertEqual((d / "REVIEW-RESULT.md").read_text(), conclusion)
        # What prepare rebuilds is written fresh; the conclusion is what survives.
        self.assertTrue((d / "meta").exists())

    def test_prepare_decides_the_tier_and_prints_its_criteria(self):
        _, text = self.prepare()
        self.assertIn("## Review tier", text)
        self.assertIn("**档位 B 实现代码**", text)
        self.assertIn("两路全新上下文找茬", text)
        self.assertIn("f.txt（实现代码）", text)

    def test_prepare_prints_the_topology_that_tier_spends(self):
        # The topology is what the tier costs in subagents; it is written where the reviewer
        # reads it, next to the tier it belongs to. What it has to say is the numbers and
        # the severities — tier B is two finders and at most two concurrent refuters, spent
        # on blockers and majors — so the assertion is on those, not on the sentence.
        _, text = self.prepare()
        line = next(l for l in text.splitlines() if l.startswith("- 子代理拓扑："))
        self.assertEqual(sorted(int(n) for n in re.findall(r"\d+", line)), [2, 2, 2])
        # The effort is an enum the tier spends, not a phrase: rewording the sentence around
        # it must not go red, and dropping it must.
        self.assertIn("high", line)
        for severity in ("blocker", "major"):
            self.assertIn(severity, line)

    def meta_of(self, review_md):
        return dict(
            line.split("=", 1)
            for line in (review_md.parent / "meta").read_text().splitlines() if "=" in line
        )

    def test_prepare_injects_the_rules_the_changed_paths_load(self):
        # The rules come from the default branch: the rule scoped to the changed path and
        # the AGENTS.md above it arrive with their text, the rule scoped elsewhere does
        # not, and the one scoped to nothing arrives because nothing scoped it.
        out = self.run_script("prepare", PR_RULE_EDIT)
        self.assertEqual(out.returncode, 0, out.stderr)
        d = Path(out.stdout.strip()).parent
        text = (d / "REVIEW.md").read_text()
        self.assertIn("## Rules this change loads", text)
        self.assertIn("BACKEND RULE TEXT", text)
        self.assertIn("BACKEND AGENTS TEXT", text)
        self.assertIn("UNSCOPED RULE TEXT", text)
        self.assertNotIn("ELSEWHERE RULE TEXT", text)
        self.assertIn("- Rules injected:", text)

    def test_a_pull_request_cannot_rewrite_the_rules_its_review_is_handed(self):
        # PR 10 rewrites backend.md to point at cli/** and changes the path main's copy of
        # that rule covers. The reviewer must be handed main's rule, the one that applies
        # to the change, and not the author's edited copy of it.
        out = self.run_script("prepare", PR_RULE_EDIT)
        self.assertEqual(out.returncode, 0, out.stderr)
        text = (Path(out.stdout.strip()).parent / "REVIEW.md").read_text()
        self.assertIn(".claude/rules/backend.md", text)
        self.assertIn("BACKEND RULE TEXT", text)
        self.assertNotIn("REWRITTEN RULE TEXT", text)

    def test_prepare_injects_nothing_scoped_to_an_unrelated_change(self):
        _, text = self.prepare()  # PR 7 changes f.txt, at the repo root
        self.assertIn("UNSCOPED RULE TEXT", text)
        self.assertNotIn("BACKEND RULE TEXT", text)
        self.assertNotIn("BACKEND AGENTS TEXT", text)

    def test_prepare_embeds_the_requirement_contract_it_was_handed(self):
        spec = self.tmp / "spec.md"
        spec.write_text("# Contract\n\n- It must do the thing.\n")
        review_md, text = self.prepare("--spec", str(spec))
        self.assertIn("## Requirement contract", text)
        self.assertIn("It must do the thing.", text)
        self.assertEqual((review_md.parent / "spec.md").read_text(), spec.read_text())
        self.assertIn(f"- Spec: {review_md.parent}/spec.md", text)
        # In meta too: a delta round reuses the contract from the directory it names.
        self.assertEqual(self.meta_of(review_md)["spec"].strip("'"),
                         f"{review_md.parent}/spec.md")

    def test_a_delta_review_reuses_the_last_rounds_contract(self):
        # The contract is what the change was written against; the deliverer hands it over
        # once, at the round that opened the review, not again on every increment.
        spec = self.tmp / "spec.md"
        spec.write_text("CONTRACT TEXT\n")
        self.prepare(self.shas[0], "--spec", str(spec))
        review_md, text = self.prepare("--since", self.shas[0])
        self.assertIn("## Requirement contract", text)
        self.assertIn("CONTRACT TEXT", text)
        self.assertEqual(self.meta_of(review_md)["spec"].strip("'"),
                         f"{review_md.parent}/spec.md")

    def test_without_a_spec_the_review_is_told_to_skip_step_5b(self):
        _, text = self.prepare()
        self.assertNotIn("## Requirement contract", text)
        self.assertIn("- Spec: none", text)

    def test_a_spec_that_is_not_there_is_an_error_not_a_review_without_one(self):
        # Silently reviewing without the contract would report 无 blocker/major for a
        # change half of which was never written, so a --spec that names nothing stops.
        out = self.run_script("prepare", PR, "--spec", str(self.tmp / "nope.md"))
        self.assertEqual(out.returncode, 1)
        self.assertIn("no such file", out.stderr)
        (self.tmp / "empty.md").write_text("")
        out = self.run_script("prepare", PR, "--spec", str(self.tmp / "empty.md"))
        self.assertEqual(out.returncode, 1)
        self.assertIn("empty", out.stderr)

    def test_the_spec_bullet_names_the_step_the_method_it_was_handed_has(self):
        # The bullet used to read `none — skip step 5b` whenever no contract was passed,
        # including in the window where the default branch's method has no 5b at all: it
        # pointed the review at a step that is not above it. Without a contract it says
        # which of the two it is now. The other half is in the old-method test below.
        review_md, text = self.prepare()
        self.assertIn("## 5b", text)
        self.assertIn("- Spec: none — skip step 5b", text)
        self.assertNotIn("this review's method has no step 5b", text)
        self.assertFalse((review_md.parent / "spec.md").exists())

    def test_a_method_that_predates_step_5b_is_handed_no_contract(self):
        # The contract section names step 5b, and the method the reviewer is handed comes
        # from the default branch: until 5b is on main, that section would name a step that
        # is not above it. It is left out in that window the way the 档位 is, and the review
        # is told there is no contract rather than handed a dangling one.
        spec = self.tmp / "spec.md"
        spec.write_text("# Contract\n\n- It must do the thing.\n")
        main = git(self.work, "rev-parse", "origin/main")
        git(self.work, "checkout", "-q", "-b", "old-main", main)
        try:
            (self.work / ".claude/pr-review/prompt.md").write_text(
                "# Independent review\n\n## 5. Now read the PR's own words\n")
            git(self.work, "commit", "-qam", "an older method")
            old = git(self.work, "rev-parse", "HEAD")
            git(self.work, "push", "-q", "-f", "origin", f"{old}:main")
            git(self.work, "checkout", "-q", "pr")
            out = self.run_script("prepare", PR, "--spec", str(spec))
            self.assertEqual(out.returncode, 0, out.stderr)
            d = Path(out.stdout.strip()).parent
            text = (d / "REVIEW.md").read_text()
            self.assertNotIn("## Requirement contract", text)
            self.assertNotIn("It must do the thing.", text)
            self.assertFalse((d / "spec.md").exists())
            self.assertEqual(self.meta_of(Path(out.stdout.strip()))["spec"], "''")
            self.assertIn("还没有 step 5b", out.stderr)
            self.assertIn("- Spec: none — this review's method has no step 5b", text)
            self.assertNotIn("skip step 5b", text)
            # And the same window with no contract offered at all: the bullet still says
            # which method it was handed, rather than pointing at a 5b that is not there.
            out = self.run_script("prepare", PR)
            self.assertEqual(out.returncode, 0, out.stderr)
            text = Path(out.stdout.strip()).read_text()
            self.assertIn("- Spec: none — this review's method has no step 5b", text)
            self.assertNotIn("skip step 5b", text)
        finally:
            git(self.work, "checkout", "-q", "main")
            git(self.work, "branch", "-qD", "old-main")
            git(self.work, "push", "-q", "-f", "origin", f"{main}:main")
            git(self.work, "fetch", "-q", "origin")

    def test_the_schema_takes_a_spec_finding_and_still_refuses_an_unknown_kind(self):
        review_md, _ = self.prepare()
        d = review_md.parent
        meta = self.meta_of(review_md)
        sha = meta["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(
            result_json(sha, "major", kinds=["spec"])))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("档位 B", out.stdout.splitlines()[0])
        (d / "result.json").write_text(json.dumps(
            result_json(sha, "major", kinds=["speculation"])))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 1)
        self.assertIn("speculation", out.stderr)

    def test_prepare_still_works_when_the_tier_cannot_be_decided(self):
        # The tier says how deep to review; it is not a gate on reviewing at all. A machine
        # that cannot decide it — tiers.py not on main yet, a checkout that predates it —
        # must still hand out the method and a usable REVIEW.md, just without a tier, or the
        # review tooling would be down for every PR but the one adding it.
        out = self.run_script("prepare", PR, env_extra={"TIERS": str(self.tmp / "gone.py")})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("算不出档位", out.stderr)
        d = Path(out.stdout.strip()).parent
        text = (d / "REVIEW.md").read_text()
        self.assertIn("## This review", text)
        self.assertNotIn("## Review tier", text)
        meta = dict(
            line.split("=", 1) for line in (d / "meta").read_text().splitlines() if "=" in line
        )
        self.assertEqual(meta["tier"].strip("'"), "")

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

    def test_post_recomputes_from_the_method_not_the_copy_in_the_prepare_dir(self):
        # The same promotion, with the copy of the classifier prepare left in $dir made to
        # lie: post must not run that copy. Deleting it would not prove this, and did not
        # when this test deleted it — a call that fails falls through to the method's own
        # copy and prints the right letter anyway, so only a copy that prints the wrong one
        # tells the two apart.
        review_md, _ = self.prepare()
        d = review_md.parent
        (d / "meta").write_text(
            (d / "meta").read_text().replace("tier=B", "tier=G"))
        (d / "tiers.py").write_text('print("G")\n')
        _, out = self.post(review_md, "minor")
        self.assertIn("档位 B", out.splitlines()[0])

    def test_post_reports_the_whole_changes_tier_not_the_head_commits(self):
        # The tier is decided from the whole change. This PR's head commit is a lock bump,
        # so a tier read off the head commit alone would say G and wave an auth change by.
        out = self.run_script("prepare", PR_MIXED)
        self.assertEqual(out.returncode, 0, out.stderr)
        d = Path(out.stdout.strip()).parent
        self.assertIn("**档位 A 高风险**", (d / "REVIEW.md").read_text())
        meta = dict(
            line.split("=", 1) for line in (d / "meta").read_text().splitlines() if "=" in line
        )
        # prepare's own letter, not just the one printed into REVIEW.md: this is what post
        # renders and what decides whether prepare writes a 免审 result.json itself. Read
        # off the head commit alone it would be G here, and the auth change would come out
        # of prepare already marked as needing no review.
        self.assertEqual(meta["tier"].strip("'"), "A")
        (d / "result.json").write_text(json.dumps(result_json(meta["sha"].strip("'"), "minor")))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.splitlines()[0],
            f"独立 review：head {self.mixed[:8]} 结论 无 blocker/major｜档位 A",
        )

    def test_post_runs_when_the_pinned_render_py_predates_the_tier(self):
        # post hands --tier to the render.py prepare took from main, and that copy carries
        # no 档位 until this branch merges: it answers with its usage and exit 2, so the
        # review of a change would go unwritten because of the change itself — the shape
        # the tiers.py fallback above was written for. A prepare directory made before that
        # fallback is the same case, so the flag is left out for a copy that lacks the
        # field: the line is the older line, without a tier, and post still writes it.
        review_md, _ = self.prepare()
        d = review_md.parent
        meta = dict(
            line.split("=", 1) for line in (d / "meta").read_text().splitlines() if "=" in line
        )
        sha = meta["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(result_json(sha, "minor")))
        (d / "render.py").write_text(OLD_RENDER)
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.splitlines()[0], f"独立 review：head {sha[:8]} 结论 无 blocker/major"
        )
        self.assertIn("predates the 档位", out.stderr)

    def test_prepare_takes_the_checkouts_render_py_when_mains_predates_the_tier(self):
        # The other half: with main's copy unable to print the tier, prepare takes this
        # checkout's copy, so the line still carries one. main itself is the fixture here,
        # and is put back as it was, whatever happens.
        main = git(self.work, "rev-parse", "origin/main")
        git(self.work, "checkout", "-q", "-b", "old-main", main)
        try:
            (self.work / ".claude/pr-review/render.py").write_text(OLD_RENDER)
            git(self.work, "commit", "-qam", "an older render")
            old = git(self.work, "rev-parse", "HEAD")
            git(self.work, "push", "-q", "-f", "origin", f"{old}:main")
            # prepare reads the copy in $ROOT, so the checkout must be one that knows the
            # field — otherwise the fallback would copy the older file back and prove
            # nothing about which of the two it picks.
            git(self.work, "checkout", "-q", "pr")
            review_md, _ = self.prepare()
            d = review_md.parent
            self.assertNotEqual((d / "render.py").read_text(), OLD_RENDER)
            _, out = self.post(review_md, "minor")
            self.assertEqual(
                out.splitlines()[0],
                f"独立 review：head {self.shas[-1][:8]} 结论 无 blocker/major｜档位 B",
            )
        finally:
            git(self.work, "checkout", "-q", "main")
            git(self.work, "branch", "-qD", "old-main")
            git(self.work, "push", "-q", "-f", "origin", f"{main}:main")
            git(self.work, "fetch", "-q", "origin")

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
        # A check run that has not finished is not green either. Asserted while the
        # conclusion is still there, or the missing conclusion alone would explain the exit
        # code and the assertion would prove nothing about the pending run.
        pending = self.ready(d, ci='{"name": "CI required", "status": "in_progress", '
                                   '"conclusion": null, "started_at": "2026-01-01T00:00:00Z"}')
        self.assertEqual(pending.returncode, 1)
        self.assertIn("CI required 在 head 上是 in_progress，等它绿", pending.stdout)
        # No conclusion line in the directory yet: ready must not read CI-green as delivery.
        (d / "REVIEW-RESULT.md").unlink()
        gone = self.ready(d)
        self.assertEqual(gone.returncode, 1)
        self.assertIn("卡里没有审查结论", gone.stdout)

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

    def test_ready_takes_the_original_conclusion_when_the_head_only_merged_main(self):
        # The card's line is the reviewed commit's; the PR head moved because the branch
        # merged origin/main, and everything that came in is main's own version. §4 exempts
        # that increment, so the original conclusion stands and prepare is not asked again.
        out = self.run_script("prepare", PR_BASE_MERGE, self.base_merge_reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        d, _ = self.post(review_md, "minor")
        ready = self.ready(d)
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("只合了主干", ready.stdout)
        self.assertIn("不用重审", ready.stdout)
        self.assertNotIn("缺一次增量审", ready.stdout)
        self.assertIn(f"review {self.base_merge_reviewed[:8]}", ready.stdout)

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
        # Scope is the tree, not the commit graph: a merge that resolved a conflict is the
        # author's code, and a walk that skips merge commits cannot see it.
        self.assertIn("git diff origin/main HEAD -- <path>", text)
        self.assertNotIn("--no-merges", text)
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
            ("prepare", PR, "--spec"),
            ("prepare", PR, "--bogus"),
            ("post",),
            ("show", "d", "--bogus"),
        ):
            self.assertEqual(self.run_script(*args).returncode, 2, args)


class ReviewFindingsTrend(unittest.TestCase):
    """review-findings-trend.py, over result files written by hand.

    Its cases live in this file rather than in one of their own because this is the file
    repo-guards.yml runs for the review tooling: a test in a file no check loads is a test
    nobody runs.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trend-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def finding(self, kind, file, severity, rung=4, title="a thing"):
        return {"severity": severity, "kind": kind, "file": file, "line": 1, "title": title,
                "failure": "f", "evidence": "e", "rung": rung, "confirm_with": ""}

    def round(self, pr, sha, findings):
        d = self.tmp / f"pr-review-{pr}-{sha[:12]}.input"
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps({
            "reviewed_sha": sha, "summary": "", "candidates_found": len(findings),
            "candidates_refuted": 0, "findings": findings, "not_covered": [],
        }))
        return d

    def trend(self, *args, dirs=None):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / ".claude/scripts/review-findings-trend.py"),
             *[a for d in (dirs if dirs is not None else [self.tmp]) for a in ("--dir", str(d))],
             *args],
            capture_output=True, text=True,
        )

    def test_it_clusters_by_kind_and_area_and_ranks_the_busiest_first(self):
        self.round("7", "a" * 40, [
            self.finding("test", "backend/tests/unit/test_a.py", "minor"),
            self.finding("test", "backend/tests/unit/test_b.py", "major"),
            self.finding("runtime", "backend/app/domain/x.py", "blocker"),
        ])
        self.round("8", "b" * 40, [
            self.finding("test", "backend/tests/unit/test_c.py", "minor"),
        ])
        out = self.trend()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("读了 2 轮审查", out.stdout)
        # Two findings in one review are one round and one PR, not two of either, and the
        # busiest cluster is first.
        first = [l for l in out.stdout.splitlines() if l.startswith("| 1 |")][0]
        self.assertIn("`test｜backend/tests`", first)
        self.assertIn("| 3 | 2 | 2 | major |", first)
        # The same area under another kind is a cluster of its own: what kind it is decides
        # which check could hold it, so merging them would lose the answer.
        self.assertIn("`runtime｜backend/app`", out.stdout)

    def test_a_finding_without_a_file_is_placed_rather_than_dropped(self):
        self.round("7", "a" * 40, [self.finding("rule", "", "major")])
        out = self.trend("--json")
        top = json.loads(out.stdout)["top"][0]
        self.assertEqual(top["cluster"], "rule｜(no file)")

    def test_it_counts_the_rounds_that_found_nothing(self):
        self.round("7", "a" * 40, [])
        self.round("8", "b" * 40, [self.finding("ci", ".github/workflows/x.yml", "minor")])
        out = json.loads(self.trend("--json").stdout)
        self.assertEqual(out["rounds_read"], 2)
        self.assertEqual(out["rounds_with_nothing"], 1)
        self.assertEqual(out["findings"], 1)

    def test_it_counts_what_a_refuter_did_not_confirm(self):
        self.round("7", "a" * 40, [
            self.finding("runtime", "backend/app/a.py", "minor", rung=3),
            self.finding("runtime", "backend/app/b.py", "minor", rung=4),
        ])
        out = json.loads(self.trend("--json").stdout)
        self.assertEqual(out["top"][0]["unverified"], 1)

    def test_with_no_dir_it_reads_the_home_cache_and_counts_the_tmp_link_once(self):
        # prepare writes under the home cache now, and leaves a /tmp link to each round for
        # the old path. The board must read the cache, and must not count the same round
        # twice because the link resolves into it.
        home = self.tmp / "home"
        area = f"backend/tests_{uuid.uuid4().hex[:8]}"
        d = home / ".cheese/review-cache" / f"pr-review-7-{'a' * 12}.input"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "reviewed_sha": "a" * 40, "summary": "", "candidates_found": 1,
            "candidates_refuted": 0, "not_covered": [],
            "findings": [self.finding("test", f"{area}/t.py", "minor")],
        }))
        link = Path("/tmp") / f"pr-review-7-{uuid.uuid4().hex[:12]}.input"
        link.symlink_to(d)
        self.addCleanup(link.unlink, missing_ok=True)
        out = subprocess.run(
            [sys.executable, str(REPO_ROOT / ".claude/scripts/review-findings-trend.py"),
             "--json"],
            capture_output=True, text=True,
            env={**os.environ, "HOME": str(home), "PR_REVIEW_DIR": ""})
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = [r for r in json.loads(out.stdout)["top"] if r["cluster"] == f"test｜{area}"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rounds"], 1)

    def test_with_no_dir_it_reads_where_prepare_writes(self):
        # prepare writes under $PR_REVIEW_DIR. Looking in /tmp regardless would tell a
        # machine that moved it that its results are gone while they sit on disk.
        self.round("7", "a" * 40, [self.finding("test", "backend/tests/a.py", "minor")])
        out = subprocess.run(
            [sys.executable, str(REPO_ROOT / ".claude/scripts/review-findings-trend.py")],
            capture_output=True, text=True,
            env={**os.environ, "PR_REVIEW_DIR": str(self.tmp)},
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("backend/tests", out.stdout)

    def test_a_result_it_cannot_read_is_skipped_and_said_so(self):
        self.round("7", "a" * 40, [self.finding("test", "backend/tests/a.py", "minor")])
        (self.tmp / "broken.input").mkdir(exist_ok=True)
        (self.tmp / "broken.input" / "result.json").write_text("{")
        out = self.trend()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("skip", out.stderr)
        self.assertIn("backend/tests", out.stdout)

    def test_a_result_of_the_wrong_shape_is_skipped_not_this_boards_problem(self):
        # A seat writes result.json by hand and a round abandoned before `post` leaves one
        # behind: a file that parses and is not a review result is one unreadable round, not
        # a reason for the round that is readable to go unreported.
        self.round("7", "a" * 40, [self.finding("test", "backend/tests/a.py", "minor")])
        for name, body in (("an-array", "[]"),
                           ("findings-not-a-list", '{"findings": {"a": 1}}'),
                           ("a-finding-not-an-object", '{"findings": ["a"]}')):
            (self.tmp / f"{name}.input").mkdir(exist_ok=True)
            (self.tmp / f"{name}.input" / "result.json").write_text(body)
        out = self.trend()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr.count("skip"), 3)
        self.assertIn("backend/tests", out.stdout)

    def test_top_limits_the_board(self):
        self.round("7", "a" * 40, [
            self.finding("test", f"backend/tests{i}/t.py", "minor") for i in range(5)
        ])
        out = json.loads(self.trend("--top", "2", "--json").stdout)
        self.assertEqual(len(out["top"]), 2)

    def test_with_nothing_to_read_it_says_where_results_come_from(self):
        out = self.trend(dirs=[self.tmp / "not-there"])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("没有任何审查结果可读", out.stdout)


if __name__ == "__main__":
    unittest.main()
