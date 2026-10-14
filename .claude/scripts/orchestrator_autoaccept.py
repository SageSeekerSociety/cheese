#!/usr/bin/env python3
"""Decide, card by card, whether the orchestrator would accept a channel's pending accept cards.

Usage (keep the password in the command's heredoc, never in a file):
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_autoaccept.py <channel topic id> \
      [--skip 1443,1405] [--json] [--apply]
  python3 .claude/scripts/orchestrator_autoaccept.py --self-test   (offline: no token, no network, no gh)

Shadow mode is the default: it reads the platform and GitHub and prints one line per pending
card, writing nothing. A card is 会采 only when every check below passes; any check that fails
or cannot be read makes it 交总调度, with every reason that applies.
  CI        the "CI required" check on the card's head concluded success
  审查      the newest independent review has no blocker/major. Read first from the card body's
            `独立 review：head <sha8>（增量自 <sha8>）结论 …` lines (the last one is the newest),
            and only when the body has none, from the PR's last `<!-- pr-review sha=` comment.
            The reviewed head must be the card's head or an ancestor of it; a newest review
            with findings means the fix still needs its incremental review.
  版本      the card shows the PR's current head, and GitHub does not call the PR CONFLICTING
  部署      the PR touches none of PROD_DEPLOY (renames count on both paths)
  采纳人    the card's reviewer is OKCHEESE_USER: the platform takes an accept only from them

--apply also accepts the 会采 cards as OKCHEESE_USER: approve, then accept with the card's head.
When the platform refuses because the card has never shown a version (it refreshes the card as it
refuses), the card is read and judged again and accepted once more if it still passes.
"""
import json, os, re, subprocess, sys, urllib.error, urllib.request

from orchestrator_sweep import API, REPO, get, login

#: Files of the production deploy chain (etrip). A PR that touches one is never accepted here.
PROD_DEPLOY = {
    ".github/workflows/deploy-prod.yml",
    ".github/workflows/deploy.yml",
    "deploy/deploy.sh",
    "deploy/deploy-docker.sh",
    "deploy/compose/docker-compose.base.yml",
    "deploy/compose/docker-compose.etrip.yml",
}
CI_CHECK = "CI required"
#: The line pr-review.sh renders first, which the delivering seat pastes into the card body.
REVIEW_LINE = re.compile(r"独立 review：\s*head ([0-9a-f]{7,40})\s*(?:（增量自 ([0-9a-f]{7,40})）)?\s*"
                         r"结论 (无 blocker/major|有 (\d+) 条)")
#: The first line of a review posted as a PR comment, and its blocker/major finding headings.
REVIEW_MARK = re.compile(r"<!--\s*pr-review sha=([0-9a-f]{7,40})")
SERIOUS_HEADING = re.compile(r"^### \d+\. \[(?:blocker|major)\]", re.M)
NEVER_SHOWN = "还没有显示过任何版本"


def latest_review(card_body, pr_comments):
    """The newest review as {head, since, serious, source}, or None when there is none."""
    found = list(REVIEW_LINE.finditer(card_body or ""))
    if found:
        m = found[-1]
        return {"head": m.group(1), "since": m.group(2),
                "serious": int(m.group(4) or 0), "source": "卡"}
    for body in reversed(pr_comments or []):
        m = REVIEW_MARK.search(body or "")
        if m:
            return {"head": m.group(1), "since": None,
                    "serious": len(SERIOUS_HEADING.findall(body)), "source": "PR 评论"}
    return None


def judge(f, me):
    """会采 or 交总调度 for one card, from facts already read. Pure: `f` is what gather() returns.

    Returns (verdict, reasons); reasons lists what failed, or for 会采 what is worth knowing."""
    card, pr = f["card"], f["pr"]
    head = card.get("pr_head_sha")
    bad, notes = [], []
    if f.get("error"):
        bad.append(f["error"])
    # 采纳人
    if card.get("reviewer_handle") != me:
        bad.append(f"验收人是 {card.get('reviewer_handle')}，不是 {me}")
    # 版本
    if not head:
        bad.append("卡上还没有显示过版本")
    elif pr and pr.get("head") != head:
        bad.append(f"卡上 head {head[:8]} 不是 PR 的 head {(pr.get('head') or '?')[:8]}")
    if pr:
        if pr.get("merged"):
            bad.append("PR 已经合并")
        if pr.get("mergeable") == "CONFLICTING":
            bad.append("PR 有冲突")
        elif pr.get("mergeable") != "MERGEABLE":
            bad.append(f"GitHub 还没算出能不能合（{pr.get('mergeable')}）")
    # CI
    ci = f.get("ci")
    if ci != "success":
        bad.append(f"{CI_CHECK} 在卡上 head 是 {ci or '没跑'}")
    # 审查
    rv = f.get("review")
    if rv is None:
        bad.append("读不到独立审查")
    elif rv["serious"]:
        bad.append(f"最新审查（{rv['source']}，head {rv['head'][:8]}）有 {rv['serious']} 条 blocker/major，修完要增量审")
    elif head and not head.startswith(rv["head"]):
        reach = f.get("reach") or {}
        if reach.get("status") == "ahead":
            notes.append(f"审查在 {rv['head'][:8]}，之后 {reach.get('ahead_by')} 个提交没审")
        else:
            bad.append(f"审查的 {rv['head'][:8]} 不在卡上 head 的历史里（{reach.get('status') or '查不到'}）")
    # 部署
    files = f.get("files")
    if files is None:
        bad.append("读不到 PR 改了哪些文件")
    else:
        hit = sorted(PROD_DEPLOY & set(files))
        if hit:
            bad.append("碰了正式部署：" + "、".join(hit))
    if bad:
        return "交总调度", bad
    if rv:
        notes.insert(0, f"审查来自{rv['source']}" + (f"（增量自 {rv['since'][:8]}）" if rv["since"] else ""))
    return "会采", notes


def gh(args, paginate=False):
    """`gh api` output as text, or None after three tries."""
    cmd = ["gh", "api"] + (["--paginate"] if paginate else []) + args
    for _ in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
    return None


def gh_facts(n):
    q = ('query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){'
         'merged headRefOid mergeable comments(last:100){nodes{body}}}}}' % tuple(REPO))
    out = gh(["graphql", "-f", "query=" + q, "-F", f"n={n}"])
    try:
        body = json.loads(out)["data"]["repository"]["pullRequest"]
    except (TypeError, ValueError, KeyError):
        return None
    if body is None:
        return None
    return {"merged": body["merged"], "head": body["headRefOid"], "mergeable": body["mergeable"],
            "comments": [c["body"] for c in body["comments"]["nodes"]]}


def ci_on(sha):
    """The newest "CI required" check run's conclusion on `sha` (its status while unfinished)."""
    out = gh([f"repos/{'/'.join(REPO)}/commits/{sha}/check-runs?check_name={CI_CHECK.replace(' ', '%20')}"
              "&per_page=100"])
    if out is None:
        return "读不到"
    runs = json.loads(out).get("check_runs") or []
    if not runs:
        return None
    run = max(runs, key=lambda r: r.get("started_at") or "")
    return run.get("conclusion") or run.get("status")


def gather(tok, task, me):
    """Every fact judge() needs for one task's pending card, read fresh."""
    cards = get(tok, f"/topics/{task['id']}/accept-card")
    cards = cards.get("data", cards) if isinstance(cards, dict) else cards
    card = next((c for c in cards or [] if c["id"] == task["card"]["id"]), None)
    f = {"task": task["number"], "card": card or {}, "pr": None}
    if card is None:
        f["error"] = "读不到这张卡"
        return f
    n = card.get("pr_number")
    pr = gh_facts(n) if n else None
    if pr is None:
        f["error"] = f"GitHub 上读不到 PR #{n}"
        return f
    f["pr"] = pr
    head = card.get("pr_head_sha")
    f["ci"] = ci_on(head) if head else None
    f["review"] = latest_review(card.get("change_body"), pr["comments"])
    rv = f["review"]
    if rv and head and not rv["serious"] and not head.startswith(rv["head"]):
        out = gh([f"repos/{'/'.join(REPO)}/compare/{rv['head']}...{head}"])
        if out is not None:
            c = json.loads(out)
            f["reach"] = {"status": c.get("status"), "ahead_by": c.get("ahead_by")}
    out = gh([f"repos/{'/'.join(REPO)}/pulls/{n}/files?per_page=100",
              "-q", ".[] | .filename, (.previous_filename // empty)"], paginate=True)
    f["files"] = None if out is None else out.split()
    return f


def post(tok, path, body):
    """(ok, detail) for one platform write; a refusal is a result, not a crash."""
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json",
                                          "Authorization": "Bearer " + tok})
    try:
        urllib.request.urlopen(req, timeout=120).read()
        return True, ""
    except urllib.error.HTTPError as e:
        return False, f"{e.code} " + e.read(400).decode("utf-8", "replace")


def accept(tok, task, me):
    """Approve and accept one card, re-reading it once when the platform says it never showed a version."""
    for attempt in range(2):
        f = gather(tok, task, me)
        verdict, why = judge(f, me)
        if verdict != "会采":
            return f"重读后不采：{'；'.join(why)}"
        card = f["card"]
        ok, detail = post(tok, f"/accept-cards/{card['id']}/approve", {"approver_handle": me})
        if not ok:
            return f"approve 被拒：{detail}"
        ok, detail = post(tok, f"/accept-cards/{card['id']}/accept",
                          {"decided_by": me, "head_sha": card["pr_head_sha"]})
        if ok:
            return f"已采纳 head {card['pr_head_sha'][:8]}"
        if NEVER_SHOWN not in detail or attempt:
            return f"accept 被拒：{detail}"
    return "accept 被拒"


def line(f, verdict, why):
    pr = (f["card"] or {}).get("pr_number")
    head = ((f["card"] or {}).get("pr_head_sha") or "")[:8] or "-"
    return f"[{verdict}] #{f['task']} PR #{pr} head {head}" + (f"｜{'；'.join(why)}" if why else "")


def value_of(argv, flag):
    if flag not in argv:
        return None
    i = argv.index(flag) + 1
    if i >= len(argv) or argv[i].startswith("--"):
        raise SystemExit(f"{flag} 后面要给一个值")
    return argv[i]


def main():
    argv = sys.argv
    if argv[1:2] == ["--self-test"]:
        return self_test()
    if len(argv) < 2 or argv[1].startswith("--"):
        raise SystemExit(__doc__)
    room = argv[1]
    skip = set((value_of(argv, "--skip") or "").split(",")) - {""}
    apply, as_json = "--apply" in argv, "--json" in argv
    me = os.environ["OKCHEESE_USER"]
    tok = login()
    d = get(tok, f"/topics/{room}/tasks?status=open&limit=200")
    tasks = d.get("data", d) if isinstance(d, dict) else d
    pending = [t for t in tasks if (t.get("card") or {}).get("status") == "pending"
               and str(t["number"]) not in skip]
    rows = []
    for t in pending:
        f = gather(tok, t, me)
        verdict, why = judge(f, me)
        row = {"task": t["number"], "pr": f["card"].get("pr_number"),
               "head": f["card"].get("pr_head_sha"), "verdict": verdict, "reasons": why}
        if apply and verdict == "会采":
            row["applied"] = accept(tok, t, me)
        rows.append(row)
        if not as_json:
            print(line(f, verdict, why) + (f"｜{row['applied']}" if "applied" in row else ""))
    if as_json:
        print(json.dumps({"room": room, "apply": apply, "cards": rows}, ensure_ascii=False, indent=2))
    elif not rows:
        print("没有待采纳的卡")
    return 0


def self_test():
    """Offline checks of judge() and latest_review(): each check passing once and stopping once."""
    failures = []
    H, OLD = "a" * 40, "b" * 40

    def facts(**over):
        f = {"task": 1, "card": {"id": "c", "pr_number": 9, "pr_head_sha": H, "reviewer_handle": "me",
                                 "change_body": f"x\n独立 review：head {H[:8]} 结论 无 blocker/major\n"},
             "pr": {"head": H, "mergeable": "MERGEABLE", "merged": False, "comments": []},
             "ci": "success", "files": ["backend/app/x.py"]}
        f.update(over)
        f["review"] = latest_review(f["card"].get("change_body"), f["pr"]["comments"])
        return f

    def card(**over):
        c = dict(facts()["card"])
        c.update(over)
        return c

    def check(name, f, want, has=""):
        verdict, why = judge(f, "me")
        if verdict != want or has not in "；".join(why):
            failures.append(f"{name}: got {verdict} {why!r}, want {want} containing {has!r}")

    check("四条全过", facts(), "会采", "审查来自卡")
    # CI
    check("CI 红", facts(ci="failure"), "交总调度", "CI required 在卡上 head 是 failure")
    check("CI 没跑", facts(ci=None), "交总调度", "没跑")
    # 审查
    check("审查有 major", facts(card=card(change_body=f"独立 review：head {H[:8]} 结论 有 2 条")),
          "交总调度", "有 2 条 blocker/major")
    check("读不到审查", facts(card=card(change_body="没有审查")), "交总调度", "读不到独立审查")
    two = (f"独立 review：head {OLD[:8]} 结论 有 1 条\n"
           f"独立 review：head {H[:8]}（增量自 {OLD[:8]}）结论 无 blocker/major")
    check("major 修完有增量审", facts(card=card(change_body=two)), "会采", "增量自 bbbbbbbb")
    check("major 之后没有增量审",
          facts(card=card(change_body=f"独立 review：head {OLD[:8]} 结论 有 1 条")), "交总调度", "有 1 条")
    clean_old = f"独立 review：head {OLD[:8]} 结论 无 blocker/major"
    check("审查后又有提交、仍在历史里", dict(facts(card=card(change_body=clean_old)),
                                     reach={"status": "ahead", "ahead_by": 2}), "会采", "之后 2 个提交没审")
    check("审查的版本不在历史里", dict(facts(card=card(change_body=clean_old)),
                                  reach={"status": "diverged", "ahead_by": 1}), "交总调度", "diverged")
    comment = f"<!-- pr-review sha={H} -->\n**Independent review**\n\n### 1. [minor] typo\n"
    pr_c = {"head": H, "mergeable": "MERGEABLE", "merged": False, "comments": [comment]}
    check("卡里没有就退回 PR 评论", facts(card=card(change_body=""), pr=pr_c), "会采", "审查来自PR 评论")
    pr_major = dict(pr_c, comments=[comment + "\n### 2. [major] broken\n"])
    check("PR 评论里有 major", facts(card=card(change_body=""), pr=pr_major), "交总调度", "有 1 条")
    # 版本
    check("head 不一致", facts(pr={"head": OLD, "mergeable": "MERGEABLE", "merged": False, "comments": []}),
          "交总调度", "不是 PR 的 head bbbbbbbb")
    check("卡上没有版本", facts(card=card(pr_head_sha=None)), "交总调度", "还没有显示过版本")
    check("有冲突", facts(pr={"head": H, "mergeable": "CONFLICTING", "merged": False, "comments": []}),
          "交总调度", "PR 有冲突")
    check("GitHub 还在算", facts(pr={"head": H, "mergeable": "UNKNOWN", "merged": False, "comments": []}),
          "交总调度", "UNKNOWN")
    # 部署
    check("碰正式部署", facts(files=["backend/a.py", "deploy/deploy-docker.sh"]), "交总调度",
          "碰了正式部署：deploy/deploy-docker.sh")
    check("改名前是部署文件也算", facts(files=["deploy/new.sh", ".github/workflows/deploy-prod.yml"]),
          "交总调度", "deploy-prod.yml")
    check("读不到文件列表", facts(files=None), "交总调度", "读不到 PR 改了哪些文件")
    check("dev 部署不算", facts(files=[".github/workflows/deploy-dev.yml"]), "会采")
    # 采纳人
    check("验收人不是自己", facts(card=card(reviewer_handle="someone")), "交总调度", "验收人是 someone")
    check("多条原因一起报", facts(ci="failure", files=["deploy/deploy.sh"]), "交总调度", "碰了正式部署")

    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
