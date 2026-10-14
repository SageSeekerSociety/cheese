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
#: §4's exempt increment: 改文案. The copy paths are the ones the A tier calls 文案.
COPY = tuple(prefix for what, prefix in tiers.HIGH_RISK if what == "文案")


def gh(*args):
    """`gh api …` as text, or None when the call failed (the caller says 读不到)."""
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


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
    run = max(runs, key=lambda r: r.get("started_at") or "")
    return run.get("conclusion") or run.get("status")


def exempt(path):
    """True when §4 lets this file change without a new review. Not the tier: a copy change
    is tier A for a whole review, and still exempt as an increment."""
    return tiers.is_test(path) or tiers.is_doc(path) or any(tiers.under(path, p) for p in COPY)


def author_files(repo, base, since, head):
    """The files the author's own commits touched between `since` and `head`. Commits that
    came in by merging the base branch are not the author's change and do not count: a
    merge of main must not look like an edit of everything main changed."""
    revs = [f"{since}..{head}"]
    if base:
        revs += ["--not", f"origin/{base}"]
    out = tiers._git(repo, "log", "--no-merges", "--format=%H", *revs)
    if out is None:
        return None
    files = set()
    for sha in out.split():
        listed = tiers._git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        if listed is None:
            return None
        files |= {p for p in listed.splitlines() if p.strip()}
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
    root = args.root or tiers._git(str(directory), "rev-parse", "--show-toplevel")
    root = root or str(directory)
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
    for name, over, want in (
        ("CI 红", {"ci": "failure"}, "CI required 在 head 上是 failure"),
        ("CI 还在跑", {"ci": "in_progress"}, "等它绿"),
        ("CI 没跑过", {"ci": None}, "没跑"),
        ("CI 读不到", {"ci": "读不到"}, "CI required 读不到"),
        ("没有审查结论", {"review": None}, "卡里没有审查结论"),
        ("审查有两条 blocker/major", {"review": review(head, 2)}, "审查结论有 2 条 blocker/major"),
    ):
        ready, lines = verdict({**base_facts, **over})
        check(f"{name}：不能递", (ready, any(want in l for l in lines)), (False, True))
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

    with tempfile.TemporaryDirectory(prefix="ready-test-") as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                       capture_output=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)

        def write(rel, text):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
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
        write("backend/tests/test_x.py", "def test_x():\n    assert 1\n")
        write("AGENTS.md", "rules, again\n")
        write("frontend/src/i18n/messages/en/ask.json", '{"a": "b"}\n')
        docs = commit("tests, docs and copy")
        check("只改测试、文档、文案：不用重审",
              increment(repo, "main", logic, docs),
              {"needs": False, "detail": "只改了测试、文档或文案："
                                         "AGENTS.md、backend/tests/test_x.py、"
                                         "frontend/src/i18n/messages/en/ask.json"})

    check("文案算豁免（整份审查时它还是 A 档）",
          (exempt("frontend/src/i18n/messages/en/ask.json"), tiers.tier_of_path(
              "frontend/src/i18n/messages/en/ask.json")), (True, "A"))

    for line in bad:
        print("FAIL " + line)
    if bad:
        print(f"self-test: {len(bad)} of {total} failed")
        return 1
    print(f"self-test: ok ({total} cases, offline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
