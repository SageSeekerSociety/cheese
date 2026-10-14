#!/usr/bin/env python3
"""Say whether one PR is ready to be delivered, or what is missing (§8 of the workflow).

    ready.py --dir /tmp/pr-review-7-abcdef123456.input [--repo OWNER/NAME] [--self-test]

`pr-review.sh ready DIR` runs this. DIR is a `prepare` directory: it holds meta (the PR and
the commit that was reviewed) and REVIEW-RESULT.md (what the seat pastes into the card).
Two things gate a delivery, and this checks both:

  1. CI required is green on the PR's current head.
  2. There is a review conclusion with no blocker/major on that head. A review of an older
     commit still counts when everything after it is an increment §4 does not re-review:
     commits that came in by merging the base branch, and changes that are only tests,
     copy or docs. Anything else needs an increment review first.

It reads GitHub for the head and the check run; everything else is the local review. It
does not deliver anything and does not write to the card. Exit 0 for 可递, 1 for 缺, 2 for a
usage error.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render  # noqa: E402  — the review line's grammar lives with the code that writes it
import tiers  # noqa: E402

DEFAULT_REPO = "SageSeekerSociety/cheese"
#: The check the acceptance gate reads, same name as orchestrator_autoaccept.CI_CHECK.
CI_CHECK = "CI required"
RED = ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale")
UNREADABLE = "读不到"
#: §4's exempt increment: 改文案. The message files only. The A tier also calls
#: `backend/app/core/sentences.py` 文案, but that module loads the templates and formats the
#: notices, so editing it is editing code: it needs its increment read like any other code.
COPY = ("frontend/src/i18n/messages/",)


def gh(*args):
    """`gh api …` as text, or None when the call failed (the caller says 读不到)."""
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def newest_run(runs):
    """The check run that speaks for the head: the one that started last. A re-run after a
    push is that one, and an older green must not answer for a newer red."""
    return max(runs, key=lambda r: r.get("started_at") or "")


def ci_on(repo, sha):
    """The newest "CI required" check run on `sha`: its conclusion, its status while it is
    still running, None when it never ran, or UNREADABLE. Mirrors orchestrator_autoaccept
    so that 可递 here means what the acceptance gate will do with the same commit."""
    out = gh(f"repos/{repo}/commits/{sha}/check-runs", "--paginate",
             "--jq", f'[.check_runs[] | select(.name == "{CI_CHECK}")] | .[]')
    if out is None:
        return UNREADABLE
    try:
        runs = [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return UNREADABLE
    if not runs:
        return None
    run = newest_run(runs)
    return run.get("conclusion") or run.get("status")


def exempt(path):
    """True when §4 lets this file change without a new review. Not the tier: a copy change
    is tier A for a whole review, and still exempt as an increment."""
    return tiers.is_test(path) or tiers.is_doc(path) or any(tiers.under(path, p) for p in COPY)


def _blob(repo, rev, path):
    """The blob of `path` in `rev`, or None when the path is not in that tree."""
    out = tiers._git(repo, "rev-parse", "--verify", "--quiet", f"{rev}:{path}")
    return out.strip() if out and out.strip() else None


def author_files(repo, base, since, head):
    """The files whose content at `head` differs from the reviewed commit's version AND from
    the base branch's: what the author changed after the review, plus anything a merge
    brought in that is neither of those two versions — a conflict resolution the author
    wrote. A merge of the base branch therefore does not count: every file it brought in
    comes out at the base branch's own version, or unchanged from `since`.

    The comparison is between trees (`git diff since head`), not the commit graph. A merge
    commit's own `diff-tree` prints nothing, so a log walk that skips merges never sees the
    resolution a merge carries — it lives only in the tree that merge produced."""
    out = tiers.paths_of(repo, since, head)
    if out is None:
        return None
    files = []
    for path in out:
        here = _blob(repo, head, path)
        # Without a base branch there is nothing to compare against: keep the file. 判不了
        # 按 B — a file nobody can clear is re-reviewed, not waved through.
        if base and here == _blob(repo, f"origin/{base}", path):
            continue
        files.append(path)
    return sorted(files)


def increment(repo, base, since, head):
    """What stands between the reviewed commit and the head: {"needs": bool, "detail": str}.
    `needs` False means the review still covers the head under §4."""
    files = author_files(repo, base, since, head)
    if files is None:
        return {"needs": True, "detail": "读不到这两个提交之间改了什么"}
    if not files:
        return {"needs": False, "detail": "只合了主干，没有别的改动"}
    beyond = [p for p in files if not exempt(p)]
    if not beyond:
        return {"needs": False, "detail": "只改了测试、文档或文案：" + "、".join(files[:4])}
    return {"needs": True, "detail": "改了 " + "、".join(beyond[:4])}


def verdict(f):
    """(ready, lines): what to print. Pure — `f` is what gather() returns, so the rules can
    be tested without a network or a repository."""
    missing, notes = [], []
    if f.get("error"):
        return False, [f"读不到 PR 的状态：{f['error']}", "这一条要人看：上面那句就是原因"]
    head = f["head"]
    if head is None:
        return False, ["读不到 PR 的 head"]
    rv = f.get("review")
    if rv is None:
        missing.append("卡里没有审查结论：先 prepare，审完 post")
    elif rv["serious"]:
        missing.append(f"审查结论有 {rv['serious']} 条 blocker/major")
    elif not (head.startswith(rv["head"]) or rv["head"].startswith(head)):
        inc = f.get("increment") or {"needs": True, "detail": "读不到增量"}
        if inc["needs"]:
            missing.append(
                f"审查是 {rv['head'][:8]} 的，之后又推了提交（{inc['detail']}），"
                f"缺一次增量审：prepare <PR> --since {rv['head'][:8]}")
        else:
            notes.append(f"审查是 {rv['head'][:8]} 的，之后{inc['detail']}，不用重审")
    ci = f.get("ci")
    if ci == UNREADABLE:
        missing.append(f"{CI_CHECK} 读不到")
    elif ci in RED:
        missing.append(f"{CI_CHECK} 在 head 上是 {ci}")
    elif ci != "success":
        missing.append(f"{CI_CHECK} 在 head 上是 {ci or '没跑'}，等它绿")
    if missing:
        return False, ["还不能递："] + [f"- {m}" for m in missing] + [f"- 说明：{n}" for n in notes]
    tier = f"｜档位 {rv['tier']}" if rv and rv.get("tier") else ""
    line = (f"可递：head {head[:8]}｜{CI_CHECK} {ci}"
            f"｜review {rv['head'][:8]}{'（增量）' if rv.get('since') else ''} "
            f"结论 {rv['verdict']}{tier}")
    return True, [line] + [f"- {n}" for n in notes]


def review_from(directory):
    """The review line at the top of DIR/REVIEW-RESULT.md, as the card will carry it, or
    None when there is no result yet."""
    file = Path(directory) / "REVIEW-RESULT.md"
    if not file.exists():
        return None
    m = render.CARD_LINE.search(file.read_text())
    if not m:
        return None
    return {"head": m.group("head"), "since": m.group("since"),
            "verdict": m.group("verdict"),
            "serious": int(m.group("serious") or 0), "tier": m.group("tier") or ""}


def meta_of(directory):
    out = {}
    for line in (Path(directory) / "meta").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip("'")
    return out


def gather(directory, repo, root):
    meta = meta_of(directory)
    f = {"head": None, "ci": None, "review": review_from(directory), "error": None}
    got = gh(f"repos/{repo}/pulls/{meta['pr']}", "--jq", ".head.sha")
    if got is None:
        f["error"] = "GitHub 读不到这个 PR"
        return f
    f["head"] = got.strip()
    f["ci"] = ci_on(repo, f["head"])
    if f["review"] and f["head"] != f["review"]["head"]:
        f["increment"] = increment(root, meta.get("base"), f["review"]["head"], f["head"])
    return f


def resolve_root(arg_root, directory):
    """The repository the commits live in. `--root` wins; otherwise the toplevel of DIR,
    with the newline `git rev-parse` prints stripped off. A path carrying that newline is
    not a directory, so every git call against it fails and the increment reads 读不到,
    which makes 可递 refuse a PR whose only sin is that nobody passed --root."""
    found = arg_root or tiers._git(str(directory), "rev-parse", "--show-toplevel")
    return (found or "").strip() or str(directory)


def main(argv=None):
    p = argparse.ArgumentParser(add_help=True, description=__doc__)
    p.add_argument("--dir", dest="directory")
    p.add_argument("--root", default=None, help="the repository the commits live in")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.directory:
        p.print_help(sys.stderr)
        return 2
    directory = Path(args.directory)
    if not (directory / "meta").exists():
        print(f"ready: {directory} 没有 meta；先跑 prepare", file=sys.stderr)
        return 2
    root = resolve_root(args.root, directory)
    ready, lines = verdict(gather(directory, args.repo, root))
    print("\n".join(lines))
    return 0 if ready else 1


def self_test():
    """Offline: the rules and the increment classifier, on facts and on a throwaway
    repository. No network, no gh."""
    import tempfile

    bad, total = [], 0

    def check(name, got, want):
        nonlocal total
        total += 1
        if got != want:
            bad.append(f"{name}\n  想要 {want!r}\n  读成 {got!r}")

    head, older = "a" * 40, "b" * 40
    review = lambda sha, serious=0, tier="B", since=None: {
        "head": sha, "since": since, "serious": serious,
        "verdict": f"有 {serious} 条" if serious else "无 blocker/major", "tier": tier}
    base_facts = {"head": head, "ci": "success", "review": review(head), "error": None}

    check("全绿、审查在这版上、无 blocker/major：可递",
          verdict(base_facts), (True, ["可递：head aaaaaaaa｜CI required success｜review aaaaaaaa 结论 无 blocker/major｜档位 B"]))
    # Whole lines, not substrings: the message for a red CI is a prefix of the one for a CI
    # still running（「是 failure」与「是 failure，等它绿」）, so a substring test cannot tell
    # the two branches apart — and RED losing a member then goes on printing 等它绿.
    for name, over, want in (
        ("CI 红", {"ci": "failure"}, "- CI required 在 head 上是 failure"),
        ("CI 还在跑", {"ci": "in_progress"}, "- CI required 在 head 上是 in_progress，等它绿"),
        ("CI 没跑过", {"ci": None}, "- CI required 在 head 上是 没跑，等它绿"),
        ("CI 读不到", {"ci": "读不到"}, "- CI required 读不到"),
        ("没有审查结论", {"review": None}, "- 卡里没有审查结论：先 prepare，审完 post"),
        ("审查有两条 blocker/major", {"review": review(head, 2)},
         "- 审查结论有 2 条 blocker/major"),
    ):
        ready, lines = verdict({**base_facts, **over})
        check(f"{name}：不能递", (ready, want in lines), (False, True))
    # RED is a reading of someone else's vocabulary: which of GitHub's conclusions mean this
    # check will not go green by itself. Pinned here, because a member dropped by accident
    # reports a failed check as one still running, and nothing else in the tree notices.
    check("红线结论就是这几条", RED,
          ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"))
    ready, lines = verdict({**base_facts, "ci": "queued"})
    check("不在红线集合里的结论，按还没出结果处理",
          (ready, f"- {CI_CHECK} 在 head 上是 queued，等它绿" in lines), (False, True))
    ready, lines = verdict({**base_facts, "review": review(older),
                            "increment": {"needs": True, "detail": "改了 backend/app/x.py"}})
    check("head 变了、增量要审：不能递，并给出命令",
          (ready, any("--since bbbbbbbb" in l for l in lines)), (False, True))
    ready, lines = verdict({**base_facts, "review": review(older),
                            "increment": {"needs": False, "detail": "只改了测试"}})
    check("head 变了、增量只改测试：可递，并说一句",
          (ready, any("不用重审" in l for l in lines)), (True, True))
    check("读不到 GitHub：不能递，且不假装看过",
          verdict({"error": "GitHub 读不到这个 PR"})[0], False)
    # A re-run after a push leaves two runs on the head; the newer one decides.
    green = {"started_at": "2026-01-01T00:00:00Z", "conclusion": "success"}
    red = {"started_at": "2026-01-01T01:00:00Z", "conclusion": "failure"}
    check("同一个 head 上跑过两次：较新的那次说了算（旧的绿不能替新的红）",
          newest_run([green, red])["conclusion"], "failure")
    check("较新的一次还没出结论：报的是它在跑",
          newest_run([green, {"started_at": "2026-01-01T01:00:00Z", "status": "in_progress"}])
          .get("status"), "in_progress")

    with tempfile.TemporaryDirectory(prefix="ready-test-") as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                       capture_output=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)

        def write(rel, text):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            if text is None:
                (repo / rel).unlink()
            else:
                (repo / rel).write_text(text)

        def commit(msg):
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", msg], check=True,
                           capture_output=True)
            return tiers._git(repo, "rev-parse", "HEAD").strip()

        write("backend/app/x.py", "x = 1\n")
        write("backend/app/y.py", "y = 1\n")
        write("backend/tests/test_x.py", "def test_x():\n    pass\n")
        write("AGENTS.md", "rules\n")
        root_commit = commit("base")
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main",
                        root_commit], check=True)
        write("backend/app/x.py", "x = 2\n")
        reviewed = commit("the reviewed change")
        # The base branch moves on, and this branch merges it: the merge is not a change of
        # this branch's own, so it must not read as one.
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "upstream",
                        root_commit], check=True)
        write("backend/app/z.py", "z = 1\n")
        write("backend/app/y.py", None)  # the base branch deletes a file of its own
        commit("on main")
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main",
                        "HEAD"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "merge", "-q", "--no-autostash", "--no-edit",
                        "upstream"], check=True, capture_output=True)
        merged = tiers._git(repo, "rev-parse", "HEAD").strip()

        check("只合主干：不用重审",
              increment(repo, "main", reviewed, merged),
              {"needs": False, "detail": "只合了主干，没有别的改动"})
        write("backend/app/x.py", "x = 3\n")
        logic = commit("more logic")
        check("又推了一处逻辑：要补增量审",
              increment(repo, "main", reviewed, logic)["needs"], True)
        # Code and its test in one commit: the test file is exempt, the code file is not,
        # so this commit needs its increment read.
        write("backend/app/domain/review/pr_text.py", "x = 1\n")
        write("backend/tests/test_x.py", "def test_x():\n    assert 1\n")
        mixed = commit("code and its test")
        check("代码和它的测试一起改：要补增量审（豁免的是文件，不是整个提交）",
              increment(repo, "main", logic, mixed),
              {"needs": True, "detail": "改了 backend/app/domain/review/pr_text.py"})
        write("backend/tests/test_x.py", "def test_x():\n    assert 2\n")
        write("AGENTS.md", "rules, again\n")
        write("frontend/src/i18n/messages/en/ask.json", '{"a": "b"}\n')
        docs = commit("tests, docs and copy")
        check("只改测试、文档、文案：不用重审",
              increment(repo, "main", mixed, docs),
              {"needs": False, "detail": "只改了测试、文档或文案："
                                         "AGENTS.md、backend/tests/test_x.py、"
                                         "frontend/src/i18n/messages/en/ask.json"})
        # A path holding a non-ASCII byte comes back quoted from `--name-only`, and a
        # quoted path resolves to no blob on either side: `None == None` reads the file as
        # untouched, the increment reads as "只合了主干" and ready waves the head through.
        write("backend/app/中文模块.py", "x = 1\n")
        nonascii = commit("a path git would quote")
        check("改动落在非 ASCII 路径上：仍要补增量审",
              increment(repo, "main", docs, nonascii),
              {"needs": True, "detail": "改了 backend/app/中文模块.py"})
        # The base branch now changes the same file this branch changed, so the merge has to
        # be resolved by hand. That resolution is the author's code: a merge commit's own
        # diff-tree is empty, so only the tree it produced says what came in.
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "conflict2",
                        reviewed], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "upstream"], check=True)
        write("backend/app/x.py", "x = 9\n")
        commit("main changes x too")
        subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main",
                        "HEAD"], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "conflict2"], check=True)
        subprocess.run(["git", "-C", str(repo), "merge", "-q", "--no-autostash", "--no-edit",
                        "upstream"], capture_output=True)
        write("backend/app/x.py", "x = 2\nauthor = 1\n")
        subprocess.run(["git", "-C", str(repo), "add", "backend/app/x.py"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "resolution"], check=True,
                       capture_output=True)
        resolved = tiers._git(repo, "rev-parse", "HEAD").strip()

        check("解冲突写进去的代码：要补增量审",
              increment(repo, "main", reviewed, resolved),
              {"needs": True, "detail": "改了 backend/app/x.py"})
        check("默认 root 来自 git 输出：末尾不带它多打的换行",
              (lambda r: (r == r.strip(), Path(r).is_dir()))(resolve_root(None, str(repo))),
              (True, True))

    check("文案算豁免（整份审查时它还是 A 档）",
          (exempt("frontend/src/i18n/messages/en/ask.json"), tiers.tier_of_path(
              "frontend/src/i18n/messages/en/ask.json")), (True, "A"))
    check("sentences.py 是逻辑，不是文案：不豁免",
          exempt("backend/app/core/sentences.py"), False)

    for line in bad:
        print("FAIL " + line)
    if bad:
        print(f"self-test: {len(bad)} of {total} failed")
        return 1
    print(f"self-test: ok ({total} cases, offline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
