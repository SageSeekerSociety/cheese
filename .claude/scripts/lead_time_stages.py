#!/usr/bin/env python3
"""Where a task line's wall clock goes, stage by stage. Read-only.

Usage: OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/lead_time_stages.py \\
           <channel topic id> <YYYY-MM-DD> <YYYY-MM-DD> [--json out.json]
       python3 .claude/scripts/lead_time_stages.py --self-test   (offline)

Cohort: the tasks *created* in the window, both days included. One task line is one task; a line that
delivers several times (step 2, step 3, ...) is still one line, and its stages end
at its first delivery.

Stages, each measured between two recorded instants:

  1 建线→首次响应     task.started_at -> the line's first message in its conversation
  2 首响→首个提交     -> the earliest commit on any of the line's task/* branches
  3 首提交→首次递卡   -> the line's first accept card (accept-card.created_at)
  3a  递卡→可合并     -> the card's first accept_ready (platform event)
  3b  可合并→采纳     -> the card's decided_at
  4 采纳→进队列       -> GitHub's first AddedToMergeQueueEvent on that PR
  5 进队列→合并       -> the PR's mergedAt

Which of those are direct: 1, 3, 3b, 4, 5. Stage 2 reads GitHub commit times, so
it carries the commit/github clock, not the platform's. Stage 3a is the union of
"CI runs" and "someone is reviewing it", and is reported as such.

The wall-clock split of the delivered lines is printed at the end, as one
partition of 建线→首次合并:

  等 CI    the union of every check run of every commit of the line's PRs, inside
           the line's window -- so a line that pushes three times counts three CI
           waits, not one
  等人     the delivered card's decided_at minus the last check run ending before
           it: the card was green and sat with a human. Zero when the human
           accepted while CI was still running
  队列     采纳→进队列 plus 进队列→合并
  空转     the residual; it holds the line's own working time as well, which this
           script does not separate (no turn-level timestamps are read here)

The platform records no gate timestamps on accept cards (gate_started_at and
gate_passed_at are null for every card in this window), so CI comes from GitHub.

Reuses the accept_ready -> queue-added reading and the login/repo helpers from
orchestrator_sweep, which orchestrator_metrics.py also builds on.
"""
import json, os, statistics, subprocess, sys, collections, time
from datetime import datetime, timedelta

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from orchestrator_sweep import REPO, login, get, events, ts, meta  # noqa: E402

#: GitHub owner/name, from orchestrator_sweep's REPO.
OWNER, NAME = REPO


def mins(a, b):
    return (ts(b) - ts(a)).total_seconds() / 60


def pct(values, p):
    """p50/p90 by nearest rank, the same reading orchestrator_metrics.py uses."""
    v = sorted(values)
    if not v:
        return None
    return v[min(len(v) - 1, int(len(v) * p))]


def day(s):
    return s[:10]


def gql(query, tries=3):
    """One GraphQL call, retried: the proxy in front of GitHub drops a TLS
    handshake now and then, and a single drop is not a failed measurement."""
    err = ""
    for _ in range(tries):
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query],
                           capture_output=True, text=True)
        try:
            body = json.loads(r.stdout)
        except ValueError:
            err = (r.stderr or r.stdout)[:300]
            continue
        if "errors" in body:
            raise SystemExit("GitHub graphql errors: " + json.dumps(body["errors"])[:300])
        return body["data"]
    raise SystemExit("GitHub graphql failed: " + err)


def gh_prs(nums):
    """One GraphQL call per batch: merge times, commits, queue timeline.

    The queue timeline is the only place "added to the merge queue" exists —
    accepting emits no platform event, and accept_done is sent after the merge.
    """
    out = {}
    nums = sorted(nums)
    for i in range(0, len(nums), 10):
        chunk = nums[i:i + 10]
        q = ("query{repository(owner:\"%s\",name:\"%s\"){%s}}" % (
            OWNER, NAME,
            " ".join(
                "p%d: pullRequest(number:%d){number createdAt mergedAt headRefOid headRefName "
                "commits(first:30){nodes{commit{committedDate oid "
                "statusCheckRollup{contexts(first:40){nodes{"
                "... on CheckRun{name startedAt completedAt conclusion}}}}}}} "
                "timelineItems(first:100,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT])"
                "{nodes{__typename ... on AddedToMergeQueueEvent{createdAt}}}}" % (n, n)
                for n in chunk)))
        body = gql(q)["repository"]
        for n in chunk:
            out[str(n)] = body.get("p%d" % n)
    return out


def subtract(a, b, ivs):
    """[a, b] minus the union of ivs, as the intervals that are left."""
    out, cur = [], a
    for x, y in merge_iv([i for i in ivs if i[0] < b and i[1] > a]):
        if x > cur:
            out.append([cur, min(x, b)])
        cur = max(cur, y)
        if cur >= b:
            break
    if cur < b:
        out.append([cur, b])
    return out


def merge_iv(ivs):
    """Union of [start, end] epoch-second intervals: CI waits of different
    commits never overlap in wall clock, but they can if two PRs ran at once."""
    out = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def ci_runs(pr, lo=None, hi=None):
    """CI wall clock and machine time over every commit of one PR.

    Each commit's check runs are the CI that commit was gated on. A commit whose
    head never ran anything is skipped, not counted as zero. The returned last
    completion is the moment the PR's CI went quiet, which is when a human's turn
    starts. Intervals are clipped to [lo, hi] when given, so CI that ran before
    the line was created or after it merged is not charged to it.
    """
    ivs, machine, last = [], 0.0, None
    for node in ((pr or {}).get("commits") or {}).get("nodes") or []:
        roll = ((node or {}).get("commit") or {}).get("statusCheckRollup") or {}
        runs = [(n.get("startedAt"), n.get("completedAt"))
                for n in (roll.get("contexts") or {}).get("nodes") or []
                if n.get("conclusion") not in ("SKIPPED", "NEUTRAL", None)
                and n.get("startedAt") and n.get("completedAt")]
        if not runs:
            continue
        a = min(ts(x) for x, _ in runs)
        b = max(ts(y) for _, y in runs)
        m = sum((ts(y) - ts(x)).total_seconds() for x, y in runs) / 60
        if lo is not None:
            a, b = max(a, lo), min(b, hi)
            if a >= b:
                continue
        ivs.append([a, b])
        machine += m
        if last is None or b > last:
            last = b
    return merge_iv(ivs), machine, last


def collect(tok, channel, start, end):
    """Everything the stages need, straight from the platform and GitHub."""
    tasks = get(tok, f"/topics/{channel}/tasks?limit=200")["data"]
    cards = get(tok, f"/topics/{channel}/accept-card")["data"]
    by_line_cards = collections.defaultdict(list)
    for c in cards:
        by_line_cards[c["task_id"]].append(c)
    # accept_ready: the platform's "this card is mergeable now" signal.
    ready = {}
    for m in events(tok, channel, "accept", pages=10):
        if meta(m).get("event_type") != "accept_ready":
            continue
        import re
        n = re.search(r"PR #(\d+)", m["content"] or "")
        if n:
            ready.setdefault((m["conversation_id"], int(n.group(1))), []).append(m["created_at"])
    # first message per conversation: the line's first response.
    first_msg = {}
    before = None
    for _ in range(60):
        d = get(tok, f"/topics/{channel}/history?channel=true&limit=200&kind=message"
                     + (f"&before={before}" if before else ""))
        for m in d["data"]:
            c = m["conversation_id"]
            if m["created_at"] and (c not in first_msg or m["created_at"] < first_msg[c]):
                first_msg[c] = m["created_at"]
        if not d.get("has_more") or not d["data"]:
            break
        if min(m["created_at"] for m in d["data"]) < start:
            break
        before = d["oldest_id"]
    pr_nums = {c["pr_number"] for c in cards if c.get("pr_number")}
    # --gh-cache: the GraphQL pull is the slow half, and iterating on the split
    # should not refetch it. The cache is a plain dump of gh_prs's result.
    cache = None
    if "--gh-cache" in sys.argv:
        cache = sys.argv[sys.argv.index("--gh-cache") + 1]
    if cache and os.path.exists(cache) and os.path.getmtime(cache) > time.time() - 3600:
        with open(cache) as f:
            gh = json.load(f)
    else:
        gh = gh_prs(pr_nums)
        if cache:
            with open(cache, "w") as f:
                json.dump(gh, f)
    return tasks, by_line_cards, ready, first_msg, gh


def tool_calls(tok, channel, start, cache=None):
    """[conversation_id, at] for every recorded tool call in the window.

    One page of the channel's event stream carries many lines at once, so this is
    a single backward walk of the whole channel rather than a walk per task. It
    is the slowest thing here (about seven minutes for a week); --calls-cache
    keeps it out of a rerun.
    """
    if cache and os.path.exists(cache) and os.path.getmtime(cache) > time.time() - 6 * 3600:
        with open(cache) as f:
            return json.load(f)
    out, before, pages = [], None, 0
    while pages < 4000:
        d = get(tok, f"/topics/{channel}/history?channel=true&limit=200&kind=event"
                     + (f"&before={before}" if before else ""))
        items = d["data"]
        pages += 1
        out += [[m["conversation_id"], m["created_at"]] for m in items]
        if not d.get("has_more") or not items:
            break
        if min(m["created_at"] for m in items) < start:
            break
        before = d["oldest_id"]
    if cache:
        with open(cache, "w") as f:
            json.dump(out, f)
    return out


#: A silence longer than this many minutes counts as the line not running.
GAP = 10.0


def running_spans(rows, calls, gap=GAP):
    """Per line, the wall clock it was doing something observable: (busy, quiet).

    A line's tool calls cluster into bursts, and two calls less than `gap`
    minutes apart are read as the same burst -- the line is between two steps of
    one turn, or the model is thinking, and in neither case is it free to be
    doing something else. A longer silence is read as the line doing nothing.

    The reading is only as good as the events it is built on: a single long Bash
    command leaves no call for its duration and shows up as a silence, and `gap`
    is a judgement, so the caller prints the totals at a few gaps. Lines whose
    window runs past the end of the dump are skipped: their tail would look
    silent whether or not it was.
    """
    by_conv = collections.defaultdict(list)
    for conv, at in calls:
        by_conv[conv].append(ts(at))
    if not calls:
        return {}
    covered = max(ts(c[1]) for c in calls)
    out = {}
    for r in rows:
        if not r.get("merged_at") or not r.get("conv"):
            continue
        lo, hi = ts(r["created"]), ts(r["merged_at"])
        if hi > covered:
            continue
        stamps = sorted(x for x in by_conv.get(r["conv"], []) if lo <= x <= hi)
        busy, bursts = 0.0, []
        if stamps:
            start = prev = stamps[0]
            for x in stamps[1:]:
                if (x - prev).total_seconds() > gap * 60:
                    busy += (prev - start).total_seconds() / 60
                    bursts.append([start, prev])
                    start = x
                prev = x
            busy += (prev - start).total_seconds() / 60
            bursts.append([start, prev])
        r["_busy_ivs"] = bursts
        out[r["task"]] = (busy, r["total"] - busy)
    return out


def in_window(created, start, end):
    """Both days are included: created on <end> itself counts. Comparing the
    date part, not the timestamp, so `end` does not have to be midnight."""
    return start <= (created or "")[:10] <= end


def stages(tasks, by_line_cards, ready, first_msg, gh, start, end):
    lines = [t for t in tasks if in_window(t["created_at"], start, end)]
    out = []
    for t in lines:
        pref = "task/" + t["id"][:8]
        prs = sorted({c["pr_number"] for c in by_line_cards[t["id"]] if c.get("pr_number")})
        commits = [min(c["commit"]["committedDate"] for c in gh[str(n)]["commits"]["nodes"])
                   for n in prs if gh.get(str(n)) and gh[str(n)]["commits"]["nodes"]]
        cs = sorted(by_line_cards[t["id"]], key=lambda c: c["created_at"])
        first_card = cs[0] if cs else None
        decided = [c for c in cs if c.get("decided_at") and c.get("status") == "accepted"]
        s = {"task": t["number"], "title": t["title"][:40], "conv": t["id"],
             "created": t["created_at"], "status": t["status"],
             "prs": prs, "steps": len(cs)}
        fm = first_msg.get(t["id"])
        # A line can answer before the platform stamps started_at: a small negative
        # reading here is clock order, not a line that answered early.
        s["s1"] = max(0.0, mins(t.get("started_at") or t["created_at"], fm)) if fm else None
        s["s2"] = mins(fm or t["created_at"], min(commits)) if (fm and commits) else None
        s["s3"] = mins(min(commits), first_card["created_at"]) if (commits and first_card) else None
        if first_card:
            prn = first_card.get("pr_number")
            rdy = ready.get((t["id"], prn), [])
            s["s3a"] = mins(first_card["created_at"], min(rdy)) if rdy else None
            s["s3b"] = mins(min(rdy), first_card["decided_at"]) if (rdy and first_card.get("decided_at")) else None
            s["decided_at"] = first_card.get("decided_at")
            s["pr"] = prn
            if first_card.get("decided_at") and prn and gh.get(str(prn)):
                adds = [x["createdAt"] for x in gh[str(prn)]["timelineItems"]["nodes"]
                        if x["__typename"] == "AddedToMergeQueueEvent"]
                s["s4"] = mins(first_card["decided_at"], adds[0]) if adds else None
                mg = gh[str(prn)]["mergedAt"]
                s["s5"] = mins(adds[0], mg) if (adds and mg) else None
                s["merged_at"] = mg
                if mg:
                    s["total"] = mins(t["created_at"], mg)
        out.append(s)
    return out


def report(rows):
    def line(name, key, n_label=""):
        v = [r[key] for r in rows if r.get(key) is not None]
        if not v:
            print(f"{name}: 无数据")
            return
        print(f"{name}: n={len(v)}  p50 {pct(v,.5):.0f} 分  p90 {pct(v,.9):.0f} 分  "
              f"max {max(v):.0f} 分  {n_label}")
    for key, name in [("s1", "1 建线→首次响应"), ("s2", "2 首响→首个提交"),
                      ("s3", "3 首提交→首次递卡"), ("s3a", "3a 递卡→可合并"),
                      ("s3b", "3b 可合并→采纳"), ("s4", "4 采纳→进队列"),
                      ("s5", "5 进队列→合并"), ("total", "总 建线→首次合并")]:
        line(name, key)
    print()
    for r in sorted(rows, key=lambda r: -(r.get("total") or -1))[:10]:
        print(f"  #{r['task']} {r['title'][:28]:30s} 步数 {r['steps']} "
              f"总 {r.get('total') or 0:6.0f} 分  1 {fmt(r.get('s1'))} 2 {fmt(r.get('s2'))} "
              f"3 {fmt(r.get('s3'))} 3a {fmt(r.get('s3a'))} 3b {fmt(r.get('s3b'))} "
              f"4 {fmt(r.get('s4'))} 5 {fmt(r.get('s5'))}")


def fmt(v):
    return "—" if v is None else f"{v:.0f}"


def split(rows, gh):
    """等 CI / 等人 / 队列 / 空转 over the delivered lines, as one partition.

    A partition of 建线→首次合并, and one that counts once: the CI intervals are
    the union over every commit of the line's PRs, clipped to the line's window.
    No CI interval is ever charged to two lines, but the check runs of a commit
    can outlive its PR's merge, and that tail is clipped away and left in 空转.

    等人 is the card's decided_at minus the last check run ending before it: the
    card was green and sat with a human. It is zero for a card accepted while CI
    was still running -- the human wait is inside 等 CI's window there, and the
    partition says so instead of double counting.
    """
    ci_ivs = []
    ci_machine = 0.0
    people = queue = idle = 0.0
    n = 0
    for r in rows:
        if not r.get("merged_at") or not r.get("decided_at") or not r.get("total"):
            continue
        pr = gh.get(str(r["pr"]))
        if not pr:
            continue
        n += 1
        lo, hi = ts(r["created"]), ts(r["merged_at"])
        dec = ts(r["decided_at"])
        ivs, machine, last = ci_runs(pr, lo, hi)
        ci_ivs += [list(x) for x in ivs]
        ci_machine += machine
        # The human's turn: from the moment CI went quiet (or the card was
        # written, if it never ran) to the acceptance. Zero when the card was
        # accepted while a check run was still going.
        start = last if last is not None else max(lo, ts(pr.get("createdAt") or r["created"]))
        r["_wait"] = [start, dec]
        r["_queue_iv"] = [[dec, dec + timedelta(minutes=(r.get("s4") or 0)
                                                + (r.get("s5") or 0))]]
        r["_ci_ivs"] = [list(x) for x in ivs]
        people += max(0.0, (dec - start).total_seconds()) / 60
        queue += (r.get("s4") or 0) + (r.get("s5") or 0)
        idle += r["total"]
    ci = sum((b - a).total_seconds() for a, b in merge_iv(ci_ivs)) / 60
    return {"ci": ci, "ci_machine": ci_machine, "people": people, "queue": queue,
            "total": sum(r["total"] for r in rows
                         if r.get("merged_at") and r.get("decided_at")), "n": n}


def partition_split(rows, calls, parts):
    """One label per minute of the delivered lines' window, at a few silences.

    Each minute goes to the first of these that covers it: 等人 (the card sat
    green waiting for a human), 队列, 等 CI, 在跑 (the line called a tool), and
    静默 for what is left.

    The order matters and is the whole point of printing it. CI runs while the
    line is working, and a line waiting on CI often keeps poking at it, so
    without an order the buckets would overlap and the shares would add up past
    100%. Blocked time is charged to the block: a minute the line spent with the
    card in a human's hands is 等人 even if the line was also busy, and 在跑 is
    what is left over -- the minutes it was working and not blocked.
    """
    if not calls:
        print("\n墙钟去向：未读到工具调用记录")
        return
    live = [r for r in rows if r.get("merged_at") and r.get("decided_at") and r.get("total")]
    names = ("等人", "队列", "等 CI", "在跑", "静默")
    print(f"\n墙钟去向（{parts['n']} 条线，合计 {parts['total']:.0f} 分；"
          f"每分钟只算一个桶：等人 > 队列 > 等 CI > 在跑 > 静默）")
    for gap in (5.0, 10.0, 30.0):
        spans = running_spans(live, calls, gap)
        c = collections.Counter()
        for r in [r for r in live if r["task"] in spans]:
            used, row = [], 0.0
            for name, ivs in (("等人", [r["_wait"]] if r.get("_wait") else []),
                              ("队列", r.get("_queue_iv") or []),
                              ("等 CI", r.get("_ci_ivs") or []),
                              ("在跑", r.get("_busy_ivs") or [])):
                fresh = [x for iv in ivs for x in subtract(iv[0], iv[1], used)]
                m = sum((b - a).total_seconds() for a, b in fresh) / 60
                c[name] += m
                row += m
                used = merge_iv(used + [list(x) for x in ivs])
            c["静默"] += (r["total"] or 0) - row
        # 在跑 leads anyway; the rest follow in the order above, so a reader can
        # see what the threshold moved and what it did not.
        print(f"  间隔 {gap:4.0f} 分算同一次：" + "  ".join(
            f"{k} {c[k]:7.0f}（{100 * c[k] / parts['total']:.0f}%）" for k in names))
    print("  （在跑 = 工具调用间隔小于这个阈值，含模型思考与长命令；静默 ="
          "没在跑、也没在等 CI 或人）")
    # Is that silence the line's own problem or the whole room's? A line that
    # stalls while other lines are working is a stuck line; a line silent while
    # nothing at all is running is the room being quiet, and no fix aimed at one
    # line moves it.
    running_spans(live, calls, GAP)
    room = merge_iv(merge_iv([]) + [iv for r in live for iv in r.get("_busy_ivs") or []])
    own, roomy = 0.0, 0.0
    for r in live:
        for iv in subtract(ts(r["created"]), ts(r["merged_at"]), idle_used(r)):
            for a, b in subtract(iv[0], iv[1], room):
                own += (b - a).total_seconds() / 60
            roomy += (iv[1] - iv[0]).total_seconds() / 60
    print(f"  静默里：这 90 条线自己停着、频道里别的线在跑 {roomy - own:7.0f} 分"
          f"（{100 * (roomy - own) / roomy if roomy else 0:.0f}%）；"
          f"整个频道都没在跑 {own:7.0f} 分")


def idle_used(r):
    """Everything a line's window was spent on other than being idle."""
    ivs = [list(x) for x in (r.get("_busy_ivs") or [])]
    if r.get("_wait"):
        ivs.append(list(r["_wait"]))
    ivs += [list(x) for x in (r.get("_queue_iv") or []) + (r.get("_ci_ivs") or [])]
    return merge_iv(ivs)


def main():
    if sys.argv[1:2] == ["--self-test"]:
        return self_test()
    channel, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    tok = login()
    tasks, cards, ready, first_msg, gh = collect(tok, channel, start, end)
    rows = stages(tasks, cards, ready, first_msg, gh, start, end)
    report(rows)
    parts = split(rows, gh)
    calls = tool_calls(tok, channel, start,
                       sys.argv[sys.argv.index("--calls-cache") + 1]
                       if "--calls-cache" in sys.argv else None)
    partition_split(rows, calls, parts)
    if "--json" in sys.argv:
        # default=str: the underscore keys hold datetimes, not numbers.
        json.dump(rows, open(sys.argv[sys.argv.index("--json") + 1], "w"),
                  ensure_ascii=False, default=str)
    return 0


def self_test():
    """Offline checks of the pure parts: no token, no network, no gh."""
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"{name}: got {got!r} want {want!r}")

    check("pct uses nearest rank like orchestrator_metrics", pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], .9), 10)
    check("pct on one sample", pct([7], .5), 7)
    check("pct on none", pct([], .5), None)
    check("mins over a minute boundary",
          round(mins("2026-10-08T00:59:30Z", "2026-10-08T01:00:30Z"), 3), 1.0)
    check("day", day("2026-10-08T00:00:00Z"), "2026-10-08")
    check("in_window takes the first day",
          in_window("2026-10-08T00:00:00Z", "2026-10-08", "2026-10-14"), True)
    check("in_window takes the last day whole",
          in_window("2026-10-14T23:59:59Z", "2026-10-08", "2026-10-14"), True)
    check("in_window drops the day after",
          in_window("2026-10-15T00:00:00Z", "2026-10-08", "2026-10-14"), False)
    check("in_window drops the day before",
          in_window("2026-10-07T23:00:00Z", "2026-10-08", "2026-10-14"), False)
    check("merge_iv joins overlapping runs", merge_iv([[1, 5], [4, 9], [20, 21]]),
          [[1, 9], [20, 21]])
    check("merge_iv keeps touching-but-disjoint runs apart",
          merge_iv([[1, 5], [5, 9]]), [[1, 9]])
    check("merge_iv of nothing", merge_iv([]), [])
    t = ts("2026-10-08T10:00:00Z")
    hr = lambda h: t + timedelta(hours=h)
    check("subtract cuts out the middle",
          subtract(hr(0), hr(4), [[hr(1), hr(2)]]), [[hr(0), hr(1)], [hr(2), hr(4)]])
    check("subtract of a covered span leaves nothing",
          subtract(hr(1), hr(2), [[hr(0), hr(3)]]), [])
    check("subtract trims at both ends",
          subtract(hr(1), hr(4), [[hr(0), hr(2)], [hr(3), hr(5)]]),
          [[hr(2), hr(3)]])
    check("subtract keeps points untouched by any interval",
          subtract(hr(0), hr(1), []), [[hr(0), hr(1)]])

    def pr(*commits):
        """One PR whose commits each carry a list of (start, end) check runs."""
        return {"commits": {"nodes": [
            {"commit": {"statusCheckRollup": {"contexts": {"nodes": [
                {"conclusion": "SUCCESS", "startedAt": a, "completedAt": b}
                for a, b in runs]}}}} for runs in commits]}}

    # Two commits, one run each: 10:00-10:05 and 10:30-10:40. Clipped to the
    # line's window, which starts at 10:01, so the first run loses a minute.
    p = pr([("2026-10-08T10:00:00Z", "2026-10-08T10:05:00Z")],
           [("2026-10-08T10:30:00Z", "2026-10-08T10:40:00Z")])
    ivs, machine, last = ci_runs(p, ts("2026-10-08T10:01:00Z"),
                                 ts("2026-10-08T11:00:00Z"))
    check("ci_runs spans both commits", ivs, [[ts("2026-10-08T10:01:00Z"), ts("2026-10-08T10:05:00Z")],
                                              [ts("2026-10-08T10:30:00Z"), ts("2026-10-08T10:40:00Z")]])
    check("ci_runs machine time ignores the clip", round(machine, 3), 15.0)
    check("ci_runs clips an interval ending after the window",
          ci_runs(pr([("2026-10-08T10:30:00Z", "2026-10-08T11:30:00Z")]),
                  ts("2026-10-08T10:00:00Z"), ts("2026-10-08T11:00:00Z"))[0],
          [[ts("2026-10-08T10:30:00Z"), ts("2026-10-08T11:00:00Z")]])
    check("ci_runs last completion", last, ts("2026-10-08T10:40:00Z"))
    # A commit that never ran anything is skipped, not counted as a zero-length
    # wait; a commit whose whole run falls outside the window is dropped.
    p2 = pr([], [("2026-10-08T09:00:00Z", "2026-10-08T09:01:00Z")],
            [("2026-10-08T10:30:00Z", "2026-10-08T10:40:00Z")])
    ivs2, _, _ = ci_runs(p2, ts("2026-10-08T10:00:00Z"), ts("2026-10-08T11:00:00Z"))
    check("ci_runs skips empty and out-of-window commits", len(ivs2), 1)
    check("ci_runs on a PR with no runs", ci_runs(pr([]))[0], [])

    # One line, tool calls at 10:00 10:04 10:50 10:55 -- two bursts, 4 and 5
    # minutes long, with a 46 minute silence between them.
    r = [{"task": 1, "conv": "c", "created": "2026-10-08T10:00:00Z",
          "merged_at": "2026-10-08T10:55:00Z", "total": 55.0}]
    calls = [["c", "2026-10-08T10:00:00Z"], ["c", "2026-10-08T10:04:00Z"],
             ["c", "2026-10-08T10:50:00Z"], ["c", "2026-10-08T10:55:00Z"]]
    spans = running_spans(r, calls, 10.0)
    check("running_spans sums the bursts", (round(spans[1][0], 3), round(spans[1][1], 3)),
          (9.0, 46.0))
    check("running_spans drops bursts the gap splits up",
          running_spans(r, calls, 3.0)[1], (0.0, 55.0))
    check("running_spans ignores a line that runs past the dump",
          running_spans([{**r[0], "merged_at": "2026-10-08T12:00:00Z"}], calls, 10.0), {})
    check("running_spans with no calls at all", running_spans(r, [], 10.0), {})
    for f in fails:
        print("FAIL", f)
    print("self-test:", "ok" if not fails else f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
