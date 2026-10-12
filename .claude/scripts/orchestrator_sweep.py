#!/usr/bin/env python3
"""The orchestrator's patrol sweep: list what needs a decision across a channel's task lines. Read-only.

Usage (keep the password in the command's heredoc, never in a file):
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_sweep.py <channel topic id> \
      [--silent-min 60] [--skip 1443,1405]

For every open, started task in the channel it reads platform facts and the PR's GitHub queue state:
  出队          card pending, PR neither merged nor queued, last queue event is a removal
  可采纳未采纳  card pending, PR mergeable >= 5 min, not queued, not removed since
  红了没人修    CI red / conflict / post-merge checks failed >= 30 min ago, no message since
  连续失败      last two system events are failed turns, no message since
  长时间无动静  last message older than --silent-min minutes
Each row carries waiting_on; --skip leaves out tasks deliberately on hold.
"""
import json, os, subprocess, sys, urllib.request
from datetime import datetime, timezone

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
REPO = os.environ.get("ORCHESTRATOR_REPO", "SageSeekerSociety/cheese").split("/")


def login():
    body = json.dumps({"username": os.environ["OKCHEESE_USER"],
                       "password": os.environ["OKCHEESE_PASS"]}).encode()
    req = urllib.request.Request(API + "/users/auth/login", data=body,
                                 headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))["data"]["accessToken"]


def get(tok, path):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(req, timeout=120))["data"]


def events(tok, room, author, pages=50, since=None):
    """Channel-wide events (channel=true), paged until exhausted or older than `since`."""
    out, before = [], None
    for _ in range(pages):
        d = get(tok, f"/topics/{room}/history?channel=true&author={author}&limit=200"
                     + (f"&before={before}" if before else ""))
        out += d["data"]
        if not d.get("has_more") or (since and d["data"] and min(m["created_at"] for m in d["data"]) < since):
            break
        before = d["oldest_id"]
    return out


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def meta(m):
    return m.get("meta") if isinstance(m.get("meta"), dict) else {}


def said(m):
    try:
        return str(meta(m)["i18n"]["detail"]["params"].get("said", ""))[:100]
    except Exception:
        return ""


def gh_queue(n):
    q = ('query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){merged mergeQueueEntry{state} '
         'timelineItems(last:3,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT]){nodes{__typename '
         '... on AddedToMergeQueueEvent{createdAt} ... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}' % tuple(REPO))
    err = ""
    for _ in range(3):
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + q, "-F", f"n={n}"], capture_output=True, text=True)
        try:
            return json.loads(r.stdout)["data"]["repository"]["pullRequest"]
        except (ValueError, KeyError, TypeError):
            err = (r.stderr or r.stdout)[:150]
    return {"error": err}


def main():
    room = sys.argv[1]
    silent = int(sys.argv[sys.argv.index("--silent-min") + 1]) if "--silent-min" in sys.argv else 60
    skip = set(sys.argv[sys.argv.index("--skip") + 1].split(",")) if "--skip" in sys.argv else set()
    tok = login()
    now = datetime.now(timezone.utc)
    age = lambda s: (now - ts(s)).total_seconds() / 60
    d = get(tok, f"/topics/{room}/tasks?status=open&limit=200")
    tasks = d.get("data", d) if isinstance(d, dict) else d
    rows = []
    for t in tasks:
        if not t.get("started_at") or str(t["number"]) in skip:
            continue
        tid = t["id"]
        name = f"#{t['number']} {t['title'][:28]} @{t.get('agent_handle') or '-'}" + (f" 等:{t['waiting_on']}" if t.get("waiting_on") else "")
        msg = get(tok, f"/topics/{tid}/history?limit=1&kind=message")["data"]
        last_msg = msg[-1]["created_at"] if msg else t["started_at"]
        sysev = get(tok, f"/topics/{tid}/history?limit=30&author=system")["data"]
        accev = get(tok, f"/topics/{tid}/history?limit=30&author=accept")["data"]
        sysev.sort(key=lambda m: m["created_at"])
        accev.sort(key=lambda m: m["created_at"])
        add = lambda kind, text: rows.append((kind, name, text))

        card = t.get("card") or {}
        n = card.get("pr_number")
        if card.get("status") == "pending" and n:
            pr = gh_queue(n)
            if "error" in pr:
                add("GitHub 未查", f"PR #{n}：{pr['error']}")
            elif not pr["merged"] and not pr["mergeQueueEntry"]:
                tl = pr["timelineItems"]["nodes"]
                ready = [m for m in accev if meta(m).get("event_type") == "accept_ready"]
                if tl and tl[-1]["__typename"] == "RemovedFromMergeQueueEvent" and (not ready or tl[-1]["createdAt"] > ready[-1]["created_at"]):
                    add("出队", f"PR #{n} {tl[-1]['createdAt'][11:16]} UTC 出队（{tl[-1]['reason']}），{age(tl[-1]['createdAt']):.0f} 分钟没回队列")
                elif accev and meta(accev[-1]).get("event_type") == "accept_ready" and age(accev[-1]["created_at"]) >= 5:
                    add("可采纳未采纳", f"PR #{n} 可以合并 {age(accev[-1]['created_at']):.0f} 分钟")

        red = [m for m in sysev if meta(m).get("event_type") in ("ci_failed", "pr_conflict", "main_checks_failed")]
        if red and red[-1]["created_at"] > last_msg and age(red[-1]["created_at"]) >= 30:
            add("红了没人修", f"{red[-1]['content'][:50]}，{age(red[-1]['created_at']):.0f} 分钟线上没新消息")

        tail = [m for m in sysev if meta(m).get("event_type")]
        if len(tail) >= 2 and all(meta(m)["event_type"] == "turn_failed" for m in tail[-2:]) and tail[-1]["created_at"] > last_msg:
            add("连续失败", f"最后 {tail[-1]['created_at'][11:16]} UTC：{said(tail[-1])}")

        if age(last_msg) >= silent:
            add("长时间无动静", f"{age(last_msg):.0f} 分钟没消息")

    order = ["出队", "可采纳未采纳", "红了没人修", "连续失败", "长时间无动静", "GitHub 未查"]
    for kind, name, text in sorted(rows, key=lambda r: order.index(r[0])):
        print(f"[{kind}] {name}｜{text}")
    if not rows:
        print("各线无待处理信号")


if __name__ == "__main__":
    main()
