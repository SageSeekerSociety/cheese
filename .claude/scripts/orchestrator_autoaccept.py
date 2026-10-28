#!/usr/bin/env python3
"""Decide, card by card, whether the orchestrator would accept a channel's pending accept cards.

Usage (keep the password in the command's heredoc, never in a file):
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_autoaccept.py <channel topic id> \
      [--skip 1443,1405] [--json] [--apply]
  python3 .claude/scripts/orchestrator_autoaccept.py --self-test   (offline: no token, no network, no gh)

Shadow mode is the default: it reads the platform and GitHub and prints one line per pending
card, writing nothing. A card is 会采 only when every check below passes; any check that fails
or cannot be read makes it 交总调度, with one reason per failure. Each reason carries a code
from REASONS and the sentence to send back to the line.
  CI        the "CI required" check on the card's head concluded success
  审查      the newest independent review has no blocker/major, read from the card body's
            `独立 review：head <sha8>（增量自 <sha8>）结论 …` lines (the last one is the newest).
            The reviewed head must be the card's head or an ancestor of it; a newest review
            with findings means the fix still needs its incremental review.
  版本      the card shows the PR's current head, and GitHub calls the PR MERGEABLE. GitHub
            answers UNKNOWN for every in-flight PR the moment main moves, until a lookup
            triggers its background check, so the PR is read up to MERGEABLE_RETRIES more
            times, MERGEABLE_DELAY apart, and only a still-UNKNOWN answer is reported. A PR
            already in the merge queue answers UNKNOWN for as long as it sits there, which is
            no reason to hold its card: that one is read once and accepts normally. A card the
            platform has not shown a version for yet is judged on the PR's own head: the platform
            records one only once the card has been approved, so reading that as a mismatch would
            hand off every fresh card before approve is ever called.
  部署      the PR touches no production deploy file (prod_deploy) and nothing in HIGH_RISK
            (high_risk); a rename counts on both its old and new path
  采纳人    the card's reviewer is OKCHEESE_USER: the platform takes an accept only from them

Output. Text: one line per card,
  [会采] #<task> PR #<n> head <sha8>｜<notes>
  [交总调度] #<task> PR #<n> head <sha8>｜<code>: <detail>；<code>: <detail>
--json: {"room", "apply", "cards": [{"task", "pr", "head", "verdict": "accept" | "handoff",
  "codes": [...], "reasons": [{"code", "detail", "reply"}], "notes": [...], "applied"?}]}.
A detail, note or applied result never contains ；or ｜ in the text shape, so splitting on them is safe. Both shapes are
read by other scripts: change them only together with their readers.

--apply also accepts the 会采 cards as OKCHEESE_USER: approve, then accept with the card's head.
When the platform refuses because the card has never shown a version (it refreshes the card as it
refuses), the card is read and judged again and accepted once more if it still passes.

orchestrator_sweep.py's --auto-accept-check and --auto-accept run this same judgement over
the cards it lists as 可采纳未采纳, through decide() below: the first prints the answer under
the sweep's own list, the second also accepts the 会采 cards. The five conditions and this
script's shapes are the sweep's only source for them.
"""
import json, os, re, subprocess, sys, time, urllib.error, urllib.request

from orchestrator_sweep import API, REPO, get, login

#: Every code a 交总调度 reason can carry, with the sentence the orchestrator sends back to the line.
#: The first seven are the ones 《总调度工作流 v2》 §9 S1 names; the rest cover what it does not.
REASONS = {
    "ci_red": "CI required 在卡上的版本红了，先修绿再推。",
    "review_missing": "卡上的版本没有独立审查覆盖，跑 pr-review.sh 把结论写进卡。",
    "review_major": "审查还有 blocker/major，修完跑增量审（--since 上一轮），结论写进卡。",
    "head_mismatch": "卡上的版本不是 PR 现在的 head，push-fix 刷新卡。",
    "conflicting": "PR 和主干冲突，合 main（不要 rebase）解冲突后 push-fix。",
    "high_risk": "碰了高风险文件（迁移、鉴权、部署链、用户可见文案），等总调度采纳前核对。",
    "prod_deploy": "碰了正式部署，自动采纳不收，等总调度和人决定。",
    "ci_pending": "CI required 在卡上的版本还没跑完，跑完再看。",
    "mergeable_unknown": "GitHub 还没算出能不能合，下一轮再看。",
    "not_reviewer": "这张卡的验收人不是开发号，自动采纳采不了，交给验收人。",
    "pr_merged": "PR 已经合并而卡还挂着，总调度核一下卡的状态。",
    "unreadable": "有一项没读到（卡、PR、CI 或文件列表），下一轮重读；连着读不到交总调度查。",
}
#: The production deploy chain (etrip and the shared docker deploy). Prefixes end in "/".
PROD_DEPLOY = (
    ".github/workflows/deploy-prod.yml",
    ".github/workflows/deploy.yml",
    "deploy/deploy.sh",
    "deploy/deploy-docker.sh",
    "deploy/registry-retry.sh",
    "deploy/docker-compose.prod.yml",
    "deploy/.env.prod.example",
    "deploy/compose/",
)
#: Areas a person checks before accepting, as (what, prefix). Checked after PROD_DEPLOY.
HIGH_RISK = (
    ("迁移", "backend/alembic/"),
    ("鉴权", "backend/app/auth/"),
    ("鉴权", "backend/app/api/auth/"),
    ("鉴权", "backend/app/domain/identity/"),
    ("鉴权", "backend/app/domain/authz/"),
    ("鉴权", "cli/internal/auth/"),
    ("部署链", "deploy/"),
    ("部署链", ".github/workflows/deploy"),
    ("部署链", "scripts/redeploy.sh"),
    # The gates deploy-prod.yml runs (uses: test.yml) and check-auto-deploy.py reads by name.
    ("部署链", ".github/workflows/test.yml"),
    ("部署链", ".github/workflows/build.yml"),
    ("部署链", ".github/workflows/required-ci.yml"),
    ("文案", "frontend/src/i18n/messages/"),
    ("文案", "backend/app/core/sentences.py"),
)
CI_CHECK = "CI required"
#: What the platform writes in a card's note once its PR is in the merge queue. It rewrites the
#: note when the PR leaves the queue, so the note reads the card's state now — decided_at does not:
#: the platform sets that on every decision and never clears it, so a card whose PR was refused or
#: pulled back out of the queue keeps it while its card sits pending again.
QUEUE_NOTE = "已进入合并队列"
#: One PR read: each GraphQL field, and the key pr_facts() files it under. The query is built from
#: this table so its two ends cannot drift, and the offline stub answers only the fields a query
#: names, as GitHub does — a field dropped from the query is missing from the body and the next
#: read raises, rather than every card quietly coming back unreadable on the next live run.
PR_FIELDS = (("merged", "merged"), ("headRefOid", "head"), ("mergeable", "mergeable"),
             ("isInMergeQueue", "in_queue"))
#: How many times more to read a PR GitHub has not yet decided mergeability for, and the gap between reads.
MERGEABLE_RETRIES, MERGEABLE_DELAY = 3, 2.0
#: The line pr-review.sh renders first, which the delivering seat pastes into the card body.
REVIEW_LINE = re.compile(r"独立 review：\s*head ([0-9a-f]{7,40})\s*(?:（增量自 ([0-9a-f]{7,40})）)?\s*"
                         r"结论 (无 blocker/major|有 (\d+) 条)")
NEVER_SHOWN = "还没有显示过任何版本"


def latest_review(card_body):
    """The newest review as {head, since, serious, source}, or None when there is none."""
    found = list(REVIEW_LINE.finditer(card_body or ""))
    if not found:
        return None
    m = found[-1]
    return {"head": m.group(1), "since": m.group(2),
            "serious": int(m.group(4) or 0), "source": "卡"}


def changed_paths(entries):
    """Every path a PR's file entries (GitHub's pulls/{n}/files objects) name: a rename names two."""
    out = []
    for e in entries:
        out.append(e["filename"])
        if e.get("previous_filename"):
            out.append(e["previous_filename"])
    return out


def _under(path, prefix):
    return path.startswith(prefix) if prefix.endswith("/") else path == prefix or (
        path.startswith(prefix) and "/" not in path[len(prefix):])


def deploy_reasons(files):
    """(code, detail) for the prod_deploy and high_risk paths in `files`; a path counts once."""
    prod = sorted(p for p in files if any(_under(p, x) for x in PROD_DEPLOY))
    risky = {}
    for p in sorted(set(files) - set(prod)):
        what = next((w for w, x in HIGH_RISK if _under(p, x)), None)
        if what:
            risky.setdefault(what, []).append(p)
    out = []
    if prod:
        out.append(("prod_deploy", "碰了正式部署：" + "、".join(prod)))
    if risky:
        out.append(("high_risk", "，".join(f"{w}：{'、'.join(ps)}" for w, ps in risky.items())))
    return out


def shown_head(card, pr):
    """The commit to judge a card on: the one the platform has shown it, or the PR's own head.

    The platform records pr_head_sha only once a card has been approved or accepted, so a card
    that has just been handed off carries none. Reading that as "卡上还没有显示过版本" hands off
    every new card before accept() ever gets to approve one, and the platform's own refusal —
    NEVER_SHOWN, which accept() retries with a re-read — can never be reached."""
    return card.get("pr_head_sha") or (pr or {}).get("head")


def judge(f, me):
    """会采 or 交总调度 for one card, from facts already read. Pure: `f` is what gather() returns.

    Returns (verdict, reasons, notes): reasons is a list of (code, detail) for 交总调度, empty
    for 会采; notes says what is worth knowing about a 会采."""
    card, pr = f["card"], f["pr"]
    head = shown_head(card, pr)
    bad, notes = [], []
    if f.get("error"):
        bad.append(("unreadable", f["error"]))
    # 采纳人
    if card and card.get("reviewer_handle") != me:
        bad.append(("not_reviewer", f"验收人是 {card.get('reviewer_handle')}，不是 {me}"))
    # 版本
    if not head:
        bad.append(("head_mismatch", "卡上还没有显示过版本"))
    elif pr and pr.get("head") != head:
        bad.append(("head_mismatch", f"卡上 head {head[:8]} 不是 PR 的 head {(pr.get('head') or '?')[:8]}"))
    if pr:
        if pr.get("merged"):
            bad.append(("pr_merged", "PR 已经合并"))
        if pr.get("mergeable") == "CONFLICTING":
            bad.append(("conflicting", "PR 有冲突"))
        elif pr.get("mergeable") != "MERGEABLE":
            # A PR in the merge queue answers UNKNOWN until the queue gets to it, and the platform
            # keeps its card pending while the queue works; either way the card is behind the queue,
            # not blocked on a number GitHub has not computed yet.
            if pr.get("in_queue") or QUEUE_NOTE in (card.get("note") or ""):
                notes.append("已在合并队列，合并交给队列")
            else:
                bad.append(("mergeable_unknown", f"GitHub 还没算出能不能合（{pr.get('mergeable')}）"))
    # CI
    ci = f.get("ci")
    if ci in ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"):
        bad.append(("ci_red", f"{CI_CHECK} 在卡上 head 是 {ci}"))
    elif ci == "读不到":
        bad.append(("unreadable", f"{CI_CHECK} 读不到"))
    elif ci != "success":
        bad.append(("ci_pending", f"{CI_CHECK} 在卡上 head 是 {ci or '没跑'}"))
    # 审查
    rv = f.get("review")
    if rv is None:
        bad.append(("review_missing", "读不到独立审查"))
    elif rv["serious"]:
        bad.append(("review_major", f"最新审查（{rv['source']}，head {rv['head'][:8]}）有 {rv['serious']} 条 blocker/major"))
    elif head and not head.startswith(rv["head"]):
        reach = f.get("reach") or {}
        if reach.get("status") == "ahead":
            notes.append(f"审查在 {rv['head'][:8]}，之后 {reach.get('ahead_by')} 个提交没审")
        else:
            bad.append(("review_missing", f"审查的 {rv['head'][:8]} 不在卡上 head 的历史里（{reach.get('status') or '查不到'}）"))
    # 部署
    files = f.get("files")
    if files is None:
        bad.append(("unreadable", "读不到 PR 改了哪些文件"))
    else:
        bad += deploy_reasons(files)
    if bad:
        return "交总调度", bad, []
    if rv:
        notes.insert(0, f"审查来自{rv['source']}" + (f"（增量自 {rv['since'][:8]}）" if rv["since"] else ""))
    return "会采", [], notes


def gh(args, paginate=False):
    """`gh api` output as text, or None after three tries."""
    cmd = ["gh", "api"] + (["--paginate"] if paginate else []) + args
    for _ in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout
    return None


def pr_facts(body):
    """What judge() reads about a PR, out of one GraphQL pullRequest body."""
    return {key: body[field] for field, key in PR_FIELDS}


def settle_mergeable(read, retries=MERGEABLE_RETRIES, delay=MERGEABLE_DELAY, sleep=time.sleep):
    """Read a PR until GitHub has decided whether it can merge, waiting `delay` between reads.

    GitHub resets every in-flight PR to UNKNOWN the moment main moves and only recomputes when
    something looks; a single read right after a merge would call a perfectly good card unknown.
    `read` returns one pullRequest body or None. The retries stop at the first decided answer
    (MERGEABLE or CONFLICTING), at an already merged PR, at a PR in the merge queue — GitHub
    answers UNKNOWN for the whole time it sits there, so waiting would never end — and at a retry
    that failed to read, which keeps the body in hand rather than calling a PR that was just read
    unreadable."""
    body = read()
    for _ in range(retries):
        if body is None or body["merged"] or body["isInMergeQueue"] or body["mergeable"] != "UNKNOWN":
            break
        sleep(delay)
        again = read()
        if again is None:
            break
        body = again
    return body


def gh_facts(n, retries=MERGEABLE_RETRIES, delay=MERGEABLE_DELAY, sleep=time.sleep):
    """One PR's facts. The retry arguments are settle_mergeable's, so the offline self-test can
    drive this function itself without waiting."""
    q = ('query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){%s}}}'
         % (REPO[0], REPO[1], " ".join(f for f, _ in PR_FIELDS)))

    def read():
        out = gh(["graphql", "-f", "query=" + q, "-F", f"n={n}"])
        try:
            return json.loads(out)["data"]["repository"]["pullRequest"]
        except (TypeError, ValueError, KeyError):
            return None

    body = settle_mergeable(read, retries, delay, sleep)
    return None if body is None else pr_facts(body)


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
    head = shown_head(card, pr)
    f["ci"] = ci_on(head) if head else None
    f["review"] = latest_review(card.get("change_body"))
    rv = f["review"]
    if rv and head and not rv["serious"] and not head.startswith(rv["head"]):
        out = gh([f"repos/{'/'.join(REPO)}/compare/{rv['head']}...{head}"])
        if out is not None:
            c = json.loads(out)
            f["reach"] = {"status": c.get("status"), "ahead_by": c.get("ahead_by")}
    # One compact object per line: --paginate concatenates pages, so the raw body is not one JSON.
    out = gh([f"repos/{'/'.join(REPO)}/pulls/{n}/files?per_page=100", "-q", ".[]"], paginate=True)
    f["files"] = None if out is None else changed_paths(json.loads(x) for x in out.splitlines() if x.strip())
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
        verdict, why, _ = judge(f, me)
        if verdict != "会采":
            return "重读后不采：" + "；".join(f"{c}: {d}" for c, d in why)
        card = f["card"]
        ok, detail = post(tok, f"/accept-cards/{card['id']}/approve", {"approver_handle": me})
        if not ok:
            return f"approve 被拒：{detail}"
        # judge() only says 会采 with a head: the card's own once the platform has shown one, else
        # the PR's. The platform records a card's head from its own read of the PR, so a card it has
        # not shown one for yet may refuse this accept — the re-read and second attempt below are
        # what pick up the head it has written by then.
        head = shown_head(card, f["pr"])
        # A card that hands over a file or an address also says which version of it is on the
        # card; the accept carries it back, the same way it carries the head.
        revision = (card.get("deliverable") or {}).get("revision")
        ok, detail = post(tok, f"/accept-cards/{card['id']}/accept",
                          {"decided_by": me, "head_sha": head, "deliverable_revision": revision})
        if ok:
            return f"已采纳 head {head[:8]}"
        if NEVER_SHOWN not in detail or attempt:
            return f"accept 被拒：{detail}"
    return "accept 被拒"


def row_of(f, verdict, why, notes):
    card = f["card"] or {}
    # The same head judge() ruled on, so a 会采 row always names a commit: a card the platform has
    # not shown one for would otherwise print `head -` on a line the documented shape calls <sha8>.
    return {"task": f["task"], "pr": card.get("pr_number"), "head": shown_head(card, f["pr"]),
            "verdict": "accept" if verdict == "会采" else "handoff",
            "codes": [c for c, _ in why],
            "reasons": [{"code": c, "detail": d, "reply": REASONS[c]} for c, d in why],
            "notes": notes}


def _flat(text):
    """A detail as one text-line field: the separators line() uses never appear inside it."""
    return " ".join(str(text).split()).replace("；", "，").replace("｜", "/")


def line(row):
    verdict = "会采" if row["verdict"] == "accept" else "交总调度"
    tail = ([_flat(n) for n in row["notes"]] if row["verdict"] == "accept"
            else [f"{r['code']}: {_flat(r['detail'])}" for r in row["reasons"]])
    head = (row["head"] or "")[:8] or "-"
    out = f"[{verdict}] #{row['task']} PR #{row['pr']} head {head}" + (f"｜{'；'.join(tail)}" if tail else "")
    return out + (f"｜{_flat(row['applied'])}" if "applied" in row else "")


def value_of(argv, flag):
    if flag not in argv:
        return None
    i = argv.index(flag) + 1
    if i >= len(argv) or argv[i].startswith("--"):
        raise SystemExit(f"{flag} 后面要给一个值")
    return argv[i]


def decide(tok, task, me, apply=False):
    """One card, read fresh: judge it and, when asked and it passes, accept it.

    Returns the row shape `line()` prints and --json carries. The CLI's own loop builds
    its rows through this, and orchestrator_sweep.py asks for a card's verdict through
    it too, so the two entry points cannot judge the same card two ways."""
    f = gather(tok, task, me)
    row = row_of(f, *judge(f, me))
    if apply and row["verdict"] == "accept":
        row["applied"] = accept(tok, task, me)
    return row


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
        row = decide(tok, t, me, apply)
        rows.append(row)
        if not as_json:
            print(line(row))
    if as_json:
        print(json.dumps({"room": room, "apply": apply, "cards": rows}, ensure_ascii=False, indent=2))
    elif not rows:
        print("没有待采纳的卡")
    return 0


def self_test():
    """Offline checks of the pure parts: every check passing once and stopping once, with its code."""
    failures = []
    H, OLD = "a" * 40, "b" * 40

    def facts(**over):
        f = {"task": 1, "card": {"id": "c", "pr_number": 9, "pr_head_sha": H, "reviewer_handle": "me",
                                 "change_body": f"x\n独立 review：head {H[:8]} 结论 无 blocker/major\n"},
             "pr": {"head": H, "mergeable": "MERGEABLE", "merged": False, "in_queue": False},
             "ci": "success", "files": ["backend/app/x.py"]}
        f.update(over)
        f["review"] = latest_review(f["card"].get("change_body"))
        return f

    def card(**over):
        c = dict(facts()["card"])
        c.update(over)
        return c

    def pr(**over):
        p = dict(facts()["pr"])
        p.update(over)
        return p

    def check(name, f, want, code=None, has=""):
        verdict, why, notes = judge(f, "me")
        codes = [c for c, _ in why]
        text = "；".join([d for _, d in why] + notes)
        if verdict != want or (code and code not in codes) or has not in text:
            failures.append(f"{name}: got {verdict} {why!r} {notes!r}, want {want} {code} containing {has!r}")

    check("四条全过", facts(), "会采", has="审查来自卡")
    # CI
    check("CI 红", facts(ci="failure"), "交总调度", "ci_red", "CI required 在卡上 head 是 failure")
    check("CI 没跑", facts(ci=None), "交总调度", "ci_pending", "没跑")
    check("CI 读不到", facts(ci="读不到"), "交总调度", "unreadable")
    for red in ("failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"):
        check(f"CI {red} 算红", facts(ci=red), "交总调度", "ci_red")
    check("CI 还在跑", facts(ci="in_progress"), "交总调度", "ci_pending")
    # 审查
    check("审查有 major", facts(card=card(change_body=f"独立 review：head {H[:8]} 结论 有 2 条")),
          "交总调度", "review_major", "有 2 条 blocker/major")
    check("读不到审查", facts(card=card(change_body="没有审查")), "交总调度", "review_missing", "读不到独立审查")
    two = (f"独立 review：head {OLD[:8]} 结论 有 1 条\n"
           f"独立 review：head {H[:8]}（增量自 {OLD[:8]}）结论 无 blocker/major")
    check("major 修完有增量审", facts(card=card(change_body=two)), "会采", has="增量自 bbbbbbbb")
    check("major 之后没有增量审",
          facts(card=card(change_body=f"独立 review：head {OLD[:8]} 结论 有 1 条")), "交总调度", "review_major")
    check("冒号后带空格也认", facts(card=card(change_body=f"独立 review： head {H[:8]} 结论 无 blocker/major")),
          "会采", has="审查来自卡")
    clean_old = f"独立 review：head {OLD[:8]} 结论 无 blocker/major"
    check("审查后又有提交、仍在历史里", dict(facts(card=card(change_body=clean_old)),
                                     reach={"status": "ahead", "ahead_by": 2}), "会采", has="之后 2 个提交没审")
    check("审查的版本不在历史里", dict(facts(card=card(change_body=clean_old)),
                                  reach={"status": "diverged", "ahead_by": 1}), "交总调度", "review_missing", "diverged")
    # 版本
    check("head 不一致", facts(pr=pr(head=OLD)), "交总调度", "head_mismatch", "不是 PR 的 head bbbbbbbb")
    check("卡上还没有版本就按 PR 自己的 head 判", facts(card=card(pr_head_sha=None)),
          "会采", has="审查来自卡")
    check("有冲突", facts(pr=pr(mergeable="CONFLICTING")), "交总调度", "conflicting")
    check("GitHub 还在算", facts(pr=pr(mergeable="UNKNOWN")), "交总调度", "mergeable_unknown", "UNKNOWN")
    check("在合并队列里就不按 UNKNOWN 拦", facts(pr=pr(mergeable="UNKNOWN", in_queue=True)),
          "会采", has="已在合并队列")
    check("队列里也照常拦冲突", facts(pr=pr(mergeable="CONFLICTING", in_queue=True)),
          "交总调度", "conflicting")
    check("平台说已进合并队列的卡，也不按 UNKNOWN 拦",
          facts(card=card(note="PR #9 已进入合并队列，等待队列检查和实际合并。"),
                pr=pr(mergeable="UNKNOWN", in_queue=False)),
          "会采", has="已在合并队列")
    # decided_at is set on every decision and never cleared, so a card whose PR already left the
    # queue — refused or pulled back out — still carries it and still has to hand off.
    check("记过采纳但已经离开队列的，仍按未算出来交总调度",
          facts(card=card(decided_at="2026-01-01T00:00:00Z", note="PR #9 没有通过合并队列检查。"),
                pr=pr(mergeable="UNKNOWN", in_queue=False)),
          "交总调度", "mergeable_unknown")

    # GitHub resets mergeable to UNKNOWN whenever main moves, so gh_facts itself has to outlast
    # that; these go through gh_facts rather than settle_mergeable, or reverting the call inside it
    # to a single read — the very behaviour the retry exists to replace — would leave them green.
    def raw(mergeable, merged=False, queued=False):
        return {"merged": merged, "headRefOid": H, "mergeable": mergeable, "isInMergeQueue": queued}

    def github(*answers):
        """A stubbed `gh` answering `answers` in order, repeating the last, counting its calls;
        a None entry is a read that failed."""
        asked = []

        def fake(args):
            asked.append(1)
            body = answers[min(len(asked) - 1, len(answers) - 1)]
            if body is None:
                return None
            # GitHub answers only the fields the query names, and this stub answers the same way:
            # a field dropped from the query is then missing from the body and breaks the read
            # here, instead of this stub quietly supplying it and leaving the query unguarded.
            q = next((a for a in args if a.startswith("query=")), "")
            return json.dumps({"data": {"repository": {"pullRequest":
                              {k: v for k, v in body.items() if k in q}}}})

        return fake, asked

    real_gh = gh
    try:
        globals()["gh"], asked = github(raw("UNKNOWN"), raw("MERGEABLE"))
        waits = []
        check("UNKNOWN 后算出能合就按会采判", facts(pr=gh_facts(9, sleep=waits.append)),
              "会采", has="审查来自卡")
        # The waited value, not just that something was waited: a wrong delay forwarded from
        # gh_facts to settle_mergeable is what this catches, and it is the module's contract.
        if asked != [1, 1] or waits != [MERGEABLE_DELAY]:
            failures.append(f"算出能合就停，且中间按 MERGEABLE_DELAY 等: got {len(asked)} reads, waits {waits!r}")
        globals()["gh"], asked = github(raw("UNKNOWN"), raw("CONFLICTING"))
        check("UNKNOWN 后算出有冲突就按冲突判", facts(pr=gh_facts(9, sleep=lambda _: None)),
              "交总调度", "conflicting")
        if asked != [1, 1]:
            failures.append(f"算出有冲突就停，不再重查: got {len(asked)} reads")
        globals()["gh"], asked = github(raw("UNKNOWN"))
        check("一直 UNKNOWN 仍按原契约报", facts(pr=gh_facts(9, sleep=lambda _: None)),
              "交总调度", "mergeable_unknown", "UNKNOWN")
        if len(asked) != MERGEABLE_RETRIES + 1:
            failures.append(f"一直 UNKNOWN 时按 MERGEABLE_RETRIES 重查后放弃: {len(asked)} reads")
        globals()["gh"], asked = github(raw("MERGEABLE"))
        if gh_facts(9, sleep=lambda _: None)["mergeable"] != "MERGEABLE" or asked != [1]:
            failures.append(f"已经有明确答案时不重查: {len(asked)} reads")
        globals()["gh"], asked = github(raw("UNKNOWN", merged=True))
        if gh_facts(9, sleep=lambda _: None)["merged"] is not True or asked != [1]:
            failures.append(f"已合并的 PR 不重查: {len(asked)} reads")
        globals()["gh"], asked = github(raw("UNKNOWN", queued=True))
        check("在合并队列里 UNKNOWN 也按会采判", facts(pr=gh_facts(9, sleep=lambda _: None)),
              "会采", has="已在合并队列")
        if asked != [1]:
            failures.append(f"在合并队列里不重查，UNKNOWN 是那里的常态: {len(asked)} reads")
        globals()["gh"], _ = github(raw("UNKNOWN"), None)
        card_facts = gh_facts(9, sleep=lambda _: None)
        if not card_facts or card_facts["mergeable"] != "UNKNOWN":
            failures.append(f"重查那次读失败时保留已经读到的结果: got {card_facts!r}")
    finally:
        globals()["gh"] = real_gh

    # A card handed off a moment ago: the platform records pr_head_sha only once the card has been
    # approved, so accept() has to get as far as approve for the card ever to be taken. Driven
    # through accept() itself, or reverting judge()'s fallback leaves every check here green while
    # every fresh card is handed off unseen.
    real = {name: globals()[name] for name in ("get", "gh", "post")}
    try:
        calls = []
        globals()["get"] = lambda tok, path: [{"id": "c", "pr_number": 9, "pr_head_sha": None,
                                               "reviewer_handle": "me",
                                               "change_body": f"x\n独立 review：head {H[:8]} 结论 无 blocker/major\n"}]

        def fresh_gh(args, paginate=False):
            if args and args[0] == "graphql":
                return json.dumps({"data": {"repository": {"pullRequest": raw("MERGEABLE")}}})
            if "check-runs" in args[0]:
                return json.dumps({"check_runs": [{"started_at": "2026-01-01", "conclusion": "success"}]})
            return '{"filename": "backend/app/x.py"}'

        def keep_post(tok, path, body):
            calls.append((path, body))
            return True, ""

        globals()["gh"], globals()["post"] = fresh_gh, keep_post
        said = accept("tok", {"id": "t", "number": 1, "card": {"id": "c"}}, "me")
        want = [("/accept-cards/c/approve", {"approver_handle": "me"}),
                ("/accept-cards/c/accept", {"decided_by": "me", "head_sha": H,
                                             "deliverable_revision": None})]
        if calls != want or not said.startswith("已采纳 head "):
            failures.append(f"卡上还没有版本时也要走到 approve 和 accept: {said!r} {calls!r}")
    finally:
        for name, fn in real.items():
            globals()[name] = fn

    check("PR 已合并", facts(pr=pr(merged=True)), "交总调度", "pr_merged")
    # 部署
    check("碰正式部署脚本", facts(files=["backend/a.py", "deploy/deploy-docker.sh"]), "交总调度",
          "prod_deploy", "deploy/deploy-docker.sh")
    check("碰正式 compose", facts(files=["deploy/docker-compose.prod.yml"]), "交总调度", "prod_deploy")
    check("碰共用 compose 目录", facts(files=["deploy/compose/docker-compose.forgejo.yml"]), "交总调度",
          "prod_deploy")
    renamed = changed_paths([{"filename": "deploy/old.sh", "previous_filename": "deploy/deploy.sh"}])
    check("改名走的部署文件也算", facts(files=renamed), "交总调度", "prod_deploy", "deploy/deploy.sh")
    check("读不到文件列表", facts(files=None), "交总调度", "unreadable", "读不到 PR 改了哪些文件")
    check("dev 部署是高风险不是正式部署", facts(files=[".github/workflows/deploy-dev.yml"]), "交总调度",
          "high_risk", "部署链")
    check("迁移", facts(files=["backend/alembic/versions/x.py"]), "交总调度", "high_risk", "迁移")
    check("鉴权", facts(files=["backend/app/api/auth/resolver.py"]), "交总调度", "high_risk", "鉴权")
    check("用户可见文案", facts(files=["frontend/src/i18n/messages/en/x.ts"]), "交总调度", "high_risk", "文案")
    check("i18n 的代码不算文案", facts(files=["frontend/src/i18n/index.ts"]), "会采")
    # Each area the deploy check covers, named by a real path rather than read back from the tables.
    for path, code in [
        ("deploy/deploy.sh", "prod_deploy"), ("deploy/deploy-docker.sh", "prod_deploy"),
        ("deploy/docker-compose.prod.yml", "prod_deploy"), ("deploy/.env.prod.example", "prod_deploy"),
        ("deploy/compose/docker-compose.gateway.yml", "prod_deploy"),
        (".github/workflows/deploy.yml", "prod_deploy"), (".github/workflows/deploy-prod.yml", "prod_deploy"),
        ("deploy/gateway/config.yaml", "high_risk"), ("deploy/check-auto-deploy.py", "high_risk"),
        ("scripts/redeploy.sh", "high_risk"), (".github/workflows/deploy-drift.yml", "high_risk"),
        (".github/workflows/test.yml", "high_risk"), (".github/workflows/build.yml", "high_risk"),
        (".github/workflows/required-ci.yml", "high_risk"),
        ("backend/alembic/env.py", "high_risk"), ("backend/app/auth/core.py", "high_risk"),
        ("backend/app/domain/identity/actor.py", "high_risk"), ("backend/app/domain/authz/x.py", "high_risk"),
        ("cli/internal/auth/token.go", "high_risk"), ("backend/app/core/sentences.py", "high_risk"),
        ("frontend/src/i18n/messages/zh-CN/room.json", "high_risk"),
    ]:
        got = [c for c, _ in judge(facts(files=[path]), "me")[1]]
        if got != [code]:
            failures.append(f"{path}: got {got}, want [{code!r}]")
    check("部署文件只报正式部署一次", facts(files=["deploy/deploy.sh"]), "交总调度", "prod_deploy")
    if [c for c, _ in judge(facts(files=["deploy/deploy.sh"]), "me")[1]] != ["prod_deploy"]:
        failures.append("正式部署的文件不再算一遍高风险")
    check("前缀不吃掉相邻路径", facts(files=["backend/app/api/auth.py/x", "scripts/redeploy.sh.d/x"]), "会采")
    check("名字相近但不是鉴权文件", facts(files=["backend/app/api/auth_extra/x.py", "deployment.md"]), "会采")
    # decide() is the one entry point both the CLI's --apply and orchestrator_sweep.py's
    # --auto-accept-check / --auto-accept go through. The judgement itself is checked above;
    # this pins the two things decision-by-callers depends on: apply=False makes no write at
    # all (that is what makes the sweep's check-only flag read-only), and apply=True writes
    # to the 会采 card and to nothing else.
    real = {name: globals()[name] for name in ("gather", "post")}
    try:
        writes = []
        globals()["gather"] = lambda tok, t, me: facts()
        globals()["post"] = lambda tok, path, body: (writes.append(path), (True, ""))[1]
        if decide("tok", {"id": "t", "number": 1, "card": {"id": "c"}}, "me")["verdict"] != "accept" or writes:
            failures.append(f"decide 默认只判不写: got {writes!r}")
        got = decide("tok", {"id": "t", "number": 1, "card": {"id": "c"}}, "me", True)
        if writes != ["/accept-cards/c/approve", "/accept-cards/c/accept"] or got.get("applied") != "已采纳 head " + H[:8]:
            failures.append(f"decide --apply 对会采的卡 approve 再 accept: got {got!r} {writes!r}")
        writes = []
        globals()["gather"] = lambda tok, t, me: facts(files=["backend/alembic/versions/x.py"])
        got = decide("tok", {"id": "t", "number": 1, "card": {"id": "c"}}, "me", True)
        if writes or got["verdict"] != "handoff" or got["codes"] != ["high_risk"]:
            failures.append(f"decide --apply 对交总调度的卡一个写都不发: got {got!r} {writes!r}")
    finally:
        for name, fn in real.items():
            globals()[name] = fn

    # 采纳人与读失败
    check("验收人不是自己", facts(card=card(reviewer_handle="someone")), "交总调度", "not_reviewer", "someone")
    check("读不到卡", {"task": 1, "card": {}, "pr": None, "error": "读不到这张卡"}, "交总调度", "unreadable",
          "读不到这张卡")
    if judge(dict(facts(), error="读不到 X"), "me")[1] != [("unreadable", "读不到 X")]:
        failures.append("其余都过时，读失败本身也交总调度、码是 unreadable")
    # 输出格式
    f = facts(ci="failure", files=["deploy/deploy.sh", "backend/alembic/versions/x.py"])
    row = row_of(f, *judge(f, "me"))
    if row["verdict"] != "handoff" or row["codes"] != ["ci_red", "prod_deploy", "high_risk"] \
            or [r["reply"] for r in row["reasons"]] != [REASONS["ci_red"], REASONS["prod_deploy"], REASONS["high_risk"]]:
        failures.append(f"多条原因按判据顺序给码并附话术: got {row!r}")
    codes = {"ci_red", "review_missing", "review_major", "head_mismatch", "conflicting", "high_risk",
             "prod_deploy", "ci_pending", "mergeable_unknown", "not_reviewer", "pr_merged", "unreadable"}
    # What each sentence must tell the line to do, not its wording.
    musts = {"ci_red": "CI", "review_missing": "pr-review.sh", "review_major": "增量审", "head_mismatch": "push-fix",
             "conflicting": "合 main", "prod_deploy": "正式部署", "high_risk": "高风险"}
    if set(REASONS) != codes or not all(REASONS.values()) or any(w not in REASONS[c] for c, w in musts.items()):
        failures.append(f"原因码是固定的十二个、各有话术: got {sorted(REASONS)}")
    mixed = facts(files=["backend/alembic/versions/x.py", "backend/app/api/auth/resolver.py"], ci="failure")
    tail = line(row_of(mixed, *judge(mixed, "me"))).split("｜", 1)[1].split("；")
    if [t.split(":")[0] for t in tail] != ["ci_red", "high_risk"]:
        failures.append(f"文本行按 ；切开每段都以码开头: got {tail!r}")
    odd = dict(facts(), error="平台回了 422；原因｜看不懂\n第二行")
    parts = line(row_of(odd, *judge(odd, "me"))).split("｜")
    if len(parts) != 2 or parts[1] != "unreadable: 平台回了 422，原因/看不懂 第二行":
        failures.append(f"细节里的 ；｜和换行不拆行: got {parts!r}")
    applied = dict(row_of(facts(), *judge(facts(), "me")), applied="accept 被拒：409 已过时；重读｜再试")
    if line(applied).count("｜") != 2 or "；" in line(applied):
        failures.append(f"--apply 的结果也不拆行: got {line(applied)!r}")
    want = "[交总调度] #1 PR #9 head aaaaaaaa｜ci_red: CI required 在卡上 head 是 failure；prod_deploy: "
    if not line(row).startswith(want):
        failures.append(f"文本行格式: got {line(row)!r}")
    ok_row = row_of(facts(), *judge(facts(), "me"))
    if line(ok_row) != "[会采] #1 PR #9 head aaaaaaaa｜审查来自卡" or ok_row["codes"] != []:
        failures.append(f"会采行格式: got {line(ok_row)!r}")
    # 会采的行要指名一个提交：卡上还没有版本时就是 PR 自己的 head，不能是 "-"。
    fresh = facts(card=card(pr_head_sha=None))
    if line(row_of(fresh, *judge(fresh, "me"))) != "[会采] #1 PR #9 head aaaaaaaa｜审查来自卡":
        failures.append(f"卡上还没有版本时会采行也要有 head: got {line(row_of(fresh, *judge(fresh, 'me')))!r}")

    for x in failures:
        print("FAIL", x)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
