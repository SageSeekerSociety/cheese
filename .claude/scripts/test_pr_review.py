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
#: A B PR bigger than the fast path: seven files have to buy the two-finder topology back.
PR_BIG = "13"

GH = r"""#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$*" in
  *.head.sha*) case "$*" in *pulls/8*) h=$PR8_HEAD ;; *pulls/9*) h=$PR9_HEAD ;;
    *pulls/10*) h=$PR10_HEAD ;; *pulls/11*) h=$PR11_HEAD ;;
    *pulls/12*) h=$PR12_HEAD ;; *pulls/13*) h=$PR13_HEAD ;;
    *) h=$PR_HEAD ;; esac
    # ready.py reads `[.head.sha, .draft] | @tsv`: these PRs are all ready, not drafts.
    case "$*" in *.draft*) printf '%s\tfalse\n' "$h" ;; *) echo "$h" ;; esac ;;
  *.base.ref*) echo main ;;
  *--paginate*/commits*)
    case "$*" in *pulls/8*) printf '%s\n' $PR8_COMMITS ;;
      *pulls/9*) printf '%s\n' $PR9_COMMITS ;;
      *pulls/10*) printf '%s\n' $PR10_COMMITS ;;
      *pulls/11*) printf '%s\n' $PR11_COMMITS ;;
      *pulls/12*) printf '%s\n' $PR12_COMMITS ;;
      *pulls/13*) printf '%s\n' $PR13_COMMITS ;; *) printf '%s\n' $PR_COMMITS ;; esac ;;
  *check-runs*) printf '%s\n' "${CI_JSON:-}" ;;
  *pulls/*) python3 - <<'PY'
import json, os
h = os.environ['PR_HEAD']
print(json.dumps({'head': {'sha': h}, 'base': {'ref': 'main'}, 'draft': False,
                  'body': os.environ.get('PR_BODY', '独立 review：head ' + h[:8] + ' 结论 无 blocker/major｜档位 B')}))
PY
    ;;
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
        (work / "f.txt").write_text(
            "\n".join(f"value_{n} = 0" for n in range(20)) + "\n")
        # Every commit in this fixture gets a per-run id in its base tree. Two runs started
        # in the same second otherwise produce identical commit hashes, and a test that
        # names a round by PR and SHA would race the other run for the same path.
        (work / ".test-run-id").write_text(uuid.uuid4().hex + "\n")
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
        # A PR that is reviewed before its head merges the base branch. Only the reviewed
        # commit exists here: the test moves main and merges it afterwards, the order in
        # which it really happens, so prepare cannot fetch the merge the review predates.
        git(work, "checkout", "-q", "-b", "base-merge", "main")
        (work / "f2.py").write_text("value = 'branch'\n")
        git(work, "add", "f2.py")
        git(work, "commit", "-qm", "the change under review")
        cls.base_merge_reviewed = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_BASE_MERGE}/head")
        # A B PR whose size leaves the fast path: seven ordinary implementation files. The
        # fast path is a claim about how much one reader can cover, and this is the case
        # that has to buy the second finder back.
        git(work, "checkout", "-q", "-b", "big", "main")
        for n in range(7):
            (work / f"big{n}.py").write_text(f"value = {n}\n")
        git(work, "add", *[f"big{n}.py" for n in range(7)])
        git(work, "commit", "-qm", "seven files")
        cls.big = git(work, "rev-parse", "HEAD")
        git(work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_BIG}/head")
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
            "PR11_HEAD": self.base_merge_reviewed,
            "PR11_COMMITS": self.base_merge_reviewed,
            "PR12_HEAD": self.shas[-1],
            "PR13_HEAD": self.big,
            "PR13_COMMITS": self.big,
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

    def prepare(self, *args, env_extra=None):
        out = self.run_script("prepare", PR, *args, env_extra=env_extra)
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
        return review_md, review_md.read_text()

    def own_root(self, name):
        """A PR_REVIEW_DIR of this test's own, for a test whose round carries a contract:
        prepare now keeps spec.md across a re-prepare, and a shared fixture directory would
        hand that contract to the next test that prepares the same commit without one."""
        return {"PR_REVIEW_DIR": str(self.tmp / name)}

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
        d, out = self.post(review_md)
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
        self.post(review_md)
        seen = self.gitlog.read_text().splitlines()
        # post's own git calls run guarded. At most the script's pre-dispatch root lookup
        # may run before the guard; moving the export below merge-base or the tier
        # classifier leaves more than that, and the last call (the worktree removal) must
        # be guarded again.
        self.assertIn("1", seen)
        self.assertLessEqual(seen.count("<unset>"), 1, seen)
        self.assertEqual(seen[-1], "1")

    def test_the_round_lives_in_the_home_cache_and_survives_post(self):
        home = self.tmp / "cache-home"
        home.mkdir(exist_ok=True)
        env = {"PR_REVIEW_DIR": None, "HOME": str(home)}
        out = self.run_script("prepare", PR, env_extra=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        d = Path(out.stdout.strip()).parent
        self.assertTrue(str(d).startswith(str(home / ".cheese/review-cache")), d)
        sha = self.meta_of(Path(out.stdout.strip()))["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(result_json(sha)))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 0, out.stderr)
        tree = d.with_name(d.name[: -len(".input")])
        self.assertFalse(tree.exists(), "post removes the review tree")
        self.assertTrue((d / "REVIEW-RESULT.md").exists())
        self.assertTrue((d / "meta").exists())
        # A newer round under /tmp must not answer for this PR: /tmp is shared with every
        # other seat on the machine, and the cache is this seat's.
        other = Path("/tmp") / f"pr-review-{PR}-{uuid.uuid4().hex[:12]}.input"
        other.mkdir()
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        # A real competing round, meta included: a lookup that only skips directories
        # without meta must still not let this one answer.
        shutil.copy(d / "meta", other / "meta")
        (other / "REVIEW-RESULT.md").write_text(
            "独立 review：head ffffffff 结论 无 blocker/major｜档位 B\n")
        out = self.run_script("ready", PR, env_extra={**env, "CI_JSON": self.GREEN})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(out.stdout.startswith("可递：head "), out.stdout)
        self.assertIn(f"review {self.shas[-1][:8]}", out.stdout)

    def test_re_preparing_the_same_commit_keeps_its_conclusion(self):
        # Re-running prepare for a commit that already has a round (after a machine change,
        # or to redo a review that never posted) must not throw away a conclusion, a result
        # or a contract that still belongs to that commit. Its own root, so the round cannot
        # reach other tests.
        env = {"PR_REVIEW_DIR": str(self.tmp / "reprepare-reviews")}
        spec = self.tmp / "spec-keep.md"
        spec.write_text("CONTRACT STAYS\n")
        out = self.run_script("prepare", PR, "--spec", str(spec), env_extra=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        d, _ = self.post(Path(out.stdout.strip()))
        conclusion = (d / "REVIEW-RESULT.md").read_text()
        result = (d / "result.json").read_text()
        out = self.run_script("prepare", PR, env_extra=env)  # no --spec this time
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(Path(out.stdout.strip()).parent, d)
        self.assertEqual((d / "REVIEW-RESULT.md").read_text(), conclusion)
        self.assertEqual((d / "result.json").read_text(), result)
        # The contract survives and is handed to the review again.
        self.assertIn("CONTRACT STAYS", Path(out.stdout.strip()).read_text())
        # The path meta recorded stays usable for a re-prepare, after the directory was
        # rebuilt: prepare keeps the file rather than deleting it with the round.
        out = self.run_script("prepare", PR, "--spec", str(d / "spec.md"), env_extra=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("CONTRACT STAYS", Path(out.stdout.strip()).read_text())
        # What prepare rebuilds is written fresh; the round's own files are what survive.
        self.assertTrue((d / "meta").exists())

    def test_prepare_decides_the_tier_and_prints_its_criteria(self):
        _, text = self.prepare()
        self.assertIn("## Review tier", text)
        self.assertIn("**档位 B 实现代码**", text)
        # PR 7 is one file and one line: a B change small enough for the fast path, whose
        # letter stays B while the depth bullet says one finder instead of two.
        self.assertIn("一路全新上下文找茬", text)
        self.assertIn("f.txt（实现代码）", text)

    def test_the_method_says_what_the_review_must_not_spend_time_on(self):
        # The method is what the seat reads, so the rules that keep it from filing noise
        # have to be in REVIEW.md itself: do not re-run what CI already checks, file only
        # rung-4 blocker/major findings, and treat what OCR never selected as uncovered
        # rather than as fine. Asserted by phrase, not by line: rewording that keeps the
        # rule stays green, and removing it goes red.
        _, text = self.prepare()
        for needle in ("`ruff`", "`eslint`", "the result takes only",
                       "`blocker` and `major`", "refuses a result that names a `minor`",
                       "not_covered", "Not checked is not the same as fine"):
            self.assertIn(needle, text)

    def test_the_method_runs_open_code_review(self):
        # The finding is one open-code-review run; the method has to say how to start it,
        # what it cannot run against, and that a partial or failed run is not a verdict.
        # Asserted by phrase, not by section number, so rewording that keeps the rule
        # stays green.
        _, text = self.prepare()
        for needle in (
            "open-code-review's (OCR)",
            ".claude/scripts/ocr-review.sh",
            "--from <merge base> --to HEAD",
            "needs a full object store",
            "partial",
            "A failed run is not a finding",
            "OCR does not read tests or docs by default",
            "Bring the survivors to evidence rung 4",
            "not let a program, or OCR, decide the verdict",
            "how many files OCR selected",
        ):
            self.assertIn(needle, text)

    def test_prepare_prints_the_topology_that_tier_spends(self):
        # The topology is what the tier costs; it is written where the reviewer reads it,
        # next to the tier it belongs to. PR 7 is small enough for the B fast path: one
        # group, one OCR run, one lane.
        _, text = self.prepare()
        line = next(l for l in text.splitlines() if l.startswith("- 子代理拓扑："))
        self.assertEqual(sorted(int(n) for n in re.findall(r"\d+", line)), [1, 1])
        # The effort is an enum the tier spends, not a phrase: rewording the sentence around
        # it must not go red, and dropping it must.
        self.assertIn("high", line)
        self.assertIn("跑一次 OCR", line)

    def test_a_b_change_over_the_size_buys_the_second_finder_back(self):
        # The fast path is a claim about how much one reader can cover. Seven files is
        # past it, and the tier must then spend the two-finder topology — this is the
        # deliberate violation the change's own review is prompted to try.
        out = self.run_script("prepare", PR_BIG)
        self.assertEqual(out.returncode, 0, out.stderr)
        text = Path(out.stdout.strip()).read_text()
        self.assertIn("**档位 B 实现代码**", text)
        line = next(l for l in text.splitlines() if l.startswith("- 子代理拓扑："))
        self.assertEqual(sorted(int(n) for n in re.findall(r"\d+", line)), [2, 2])
        self.assertIn("按包分组、每组一路全新上下文找茬", text)

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
        review_md, text = self.prepare("--spec", str(spec),
                                       env_extra=self.own_root("spec-embed"))
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
        spec = self.tmp / "spec-delta.md"
        spec.write_text("CONTRACT TEXT\n")
        env = self.own_root("delta-contract")
        self.prepare(self.shas[0], "--spec", str(spec), env_extra=env)
        review_md, text = self.prepare("--since", self.shas[0], env_extra=env)
        self.assertIn("## Requirement contract", text)
        self.assertIn("CONTRACT TEXT", text)
        self.assertEqual(self.meta_of(review_md)["spec"].strip("'"),
                         f"{review_md.parent}/spec.md")

    def test_without_a_spec_the_review_is_told_to_skip_step_5b(self):
        _, text = self.prepare(env_extra=self.own_root("no-spec"))
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
        review_md, text = self.prepare(env_extra=self.own_root("spec-bullet"))
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

    def test_post_refuses_a_minor_finding(self):
        # The result is only what must be fixed: 88% of the minors this method used to
        # file were never acted on (#1936), so a minor must not render at all. The refusal
        # is the schema's, which is what makes it hold whatever a seat's prompt says.
        review_md, _ = self.prepare()
        d = review_md.parent
        sha = self.meta_of(review_md)["sha"].strip("'")
        (d / "result.json").write_text(json.dumps(result_json(sha, "minor")))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 1)
        self.assertIn("minor", out.stderr)
        self.assertIn("blocker", out.stderr)

    def test_post_refuses_a_finding_nobody_could_prove(self):
        # Evidence rung 4 is the bar: a command was run that would fail loudly if the
        # claim were wrong. A rung-3 candidate is counted in candidates_refuted, not put
        # on the card for the author to work out.
        review_md, _ = self.prepare()
        d = review_md.parent
        result = result_json(self.meta_of(review_md)["sha"].strip("'"), "major")
        result["findings"][0]["rung"] = 3
        (d / "result.json").write_text(json.dumps(result))
        out = self.run_script("post", str(d))
        self.assertEqual(out.returncode, 1)
        self.assertIn("rung", out.stderr)

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
        _, out = self.post(review_md)
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
        _, out = self.post(review_md)
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
        (d / "result.json").write_text(json.dumps(result_json(meta["sha"].strip("'"))))
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
        (d / "result.json").write_text(json.dumps(result_json(sha)))
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
            _, out = self.post(review_md)
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
        d, _ = self.post(review_md)
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
        d, _ = self.post(review_md)
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
        # In the order it happens: review the commit, post, then main moves and the branch
        # merges it. Everything the merge brings in is main's own version, so §4 exempts
        # the increment, the original conclusion stands, and prepare is not run again.
        try:
            out = self.run_script("prepare", PR_BASE_MERGE, self.base_merge_reviewed)
            self.assertEqual(out.returncode, 0, out.stderr)
            review_md = Path(out.stdout.strip())
            d, _ = self.post(review_md)
            git(self.work, "checkout", "-q", "main")
            (self.work / "main-only.py").write_text("value = 1\n")
            git(self.work, "add", "main-only.py")
            git(self.work, "commit", "-qm", "main moves on")
            git(self.work, "push", "-q", "origin", "main")
            git(self.work, "checkout", "-q", "base-merge")
            git(self.work, "merge", "-q", "--no-autostash", "--no-edit", "main")
            merged = git(self.work, "rev-parse", "HEAD")
            git(self.work, "push", "-q", "origin", f"HEAD:refs/pull/{PR_BASE_MERGE}/head")
            git(self.work, "checkout", "-q", "main")
            (self.work / "main-only.py").write_text("value = 2\n")
            git(self.work, "commit", "-qam", "main advances after the merge")
            git(self.work, "push", "-q", "origin", "main")
            git(self.work, "fetch", "-q", "origin")
        finally:
            git(self.work, "checkout", "-q", "main")
        ready = self.run_script("ready", str(d), env_extra={
            "PR11_HEAD": merged, "CI_JSON": self.GREEN})
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        self.assertIn("只合了主干", ready.stdout)
        self.assertIn("不用重审", ready.stdout)
        self.assertNotIn("缺一次增量审", ready.stdout)
        self.assertIn(f"review {self.base_merge_reviewed[:8]}", ready.stdout)

    def test_ready_requires_review_of_code_written_while_resolving_a_main_merge(self):
        out = self.run_script("prepare", PR_BASE_MERGE, self.base_merge_reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        d, _ = self.post(Path(out.stdout.strip()))
        original_main = git(self.work, "rev-parse", "origin/main")
        try:
            git(self.work, "checkout", "-q", "-b", "resolution-main", f"{self.base_merge_reviewed}^")
            (self.work / "f2.py").write_text("value = 'main'\n")
            git(self.work, "add", "f2.py")
            git(self.work, "commit", "-qm", "main adds the same file")
            git(self.work, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(self.work, "checkout", "-q", "-b", "resolution-pr", self.base_merge_reviewed)
            conflict = subprocess.run(
                ["git", "-C", str(self.work), "merge", "--no-autostash", "--no-edit", "resolution-main"],
                capture_output=True, text=True)
            self.assertEqual(conflict.returncode, 1, conflict.stdout + conflict.stderr)
            (self.work / "f2.py").write_text("value = 'author-written resolution'\n")
            git(self.work, "add", "f2.py")
            git(self.work, "commit", "-qm", "resolve with new code")
            resolved = git(self.work, "rev-parse", "HEAD")
            ready = self.run_script("ready", str(d), env_extra={
                "PR11_HEAD": resolved, "CI_JSON": self.GREEN})
            self.assertEqual(ready.returncode, 1, ready.stdout + ready.stderr)
            self.assertIn("缺一次增量审", ready.stdout)
            self.assertIn("f2.py", ready.stdout)
        finally:
            git(self.work, "update-ref", "refs/remotes/origin/main", original_main)
            git(self.work, "checkout", "-q", "main")

    def test_ready_requires_review_when_main_renames_an_edited_file(self):
        out = self.run_script("prepare", PR_BASE_MERGE, self.base_merge_reviewed)
        self.assertEqual(out.returncode, 0, out.stderr)
        d, _ = self.post(Path(out.stdout.strip()))
        original_main = git(self.work, "rev-parse", "origin/main")
        try:
            git(self.work, "checkout", "-q", "-b", "rename-main", "main")
            git(self.work, "mv", "f.txt", "g.txt")
            git(self.work, "commit", "-qm", "main renames the file")
            git(self.work, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(self.work, "checkout", "-q", "-b", "rename-pr", self.base_merge_reviewed)
            (self.work / "f.txt").write_text(
                (self.work / "f.txt").read_text().replace("value_0 = 0", "value_0 = 999"))
            git(self.work, "commit", "-qam", "edit after review")
            git(self.work, "merge", "-q", "--no-autostash", "--no-edit", "rename-main")
            merged = git(self.work, "rev-parse", "HEAD")
            self.assertIn("value_0 = 999", git(self.work, "show", "HEAD:g.txt"))
            self.assertIn("R", git(self.work, "diff", "--name-status",
                                   self.base_merge_reviewed, merged))
            ready = self.run_script("ready", str(d), env_extra={
                "PR11_HEAD": merged, "CI_JSON": self.GREEN})
            self.assertEqual(ready.returncode, 1, ready.stdout + ready.stderr)
            self.assertIn("缺一次增量审", ready.stdout)
        finally:
            git(self.work, "update-ref", "refs/remotes/origin/main", original_main)
            git(self.work, "checkout", "-q", "main")

    def test_ready_asks_for_an_increment_review_when_the_head_moved(self):
        # The head moved, and what moved it is a change of this PR's own: the review of the
        # older commit no longer covers the head, so ready points at the delta review.
        review_md, _ = self.prepare()
        d, _ = self.post(review_md)
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
        # Its own root: prepare keeps a previous round's REVIEW-RESULT.md, and a shared
        # fixture directory would make this assertion depend on test order.
        out = self.run_script("prepare", PR, env_extra={
            "PR_REVIEW_DIR": str(self.tmp / "dry-run-reviews")})
        self.assertEqual(out.returncode, 0, out.stderr)
        review_md = Path(out.stdout.strip())
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
        d, out = self.post(review_md, "blocker", "major")
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


if __name__ == "__main__":
    unittest.main()
