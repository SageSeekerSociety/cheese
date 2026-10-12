#!/usr/bin/env python3
"""One day of orchestrator metrics, to tell whether a patrol change worked. Read-only.

Usage: OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_metrics.py <channel topic id> <YYYY-MM-DD>
For that UTC day:
  等采纳      mergeable (accept_ready) -> entered the merge queue: p50/p90/max, and how many took > 10 min
  未就绪递卡  cards whose first signal was a CI failure, or that took > 20 min to become mergeable
  出队        removals from the queue other than merging, and minutes until re-queued
  失败轮次    failed turns per line
Accepting emits no platform event, so "entered the queue" is GitHub's AddedToMergeQueueEvent;
accept_done is sent after the merge and is never used as the acceptance time.
"""
import json, re, statistics, subprocess, sys

from orchestrator_sweep import REPO, events, login, meta, ts

_cache = {}


def gh_pr(n):
    """PR 的合并队列时间线，GitHub 上查不到时返回 None。

    查不到（删了、编号不属于这个仓库）不是查询失败：GitHub 对这两种都回一个格式
    正确、`pullRequest` 为 null 的响应。曾经把它当成结果缓存下来，调用方接着下标
    就抛 `TypeError`，整趟度量断在这里。所以 null 既不缓存，也不当失败重试。
    """
    if n in _cache:
        return _cache[n]
    q = ('query($n:Int!){repository(owner:"%s",name:"%s"){pullRequest(number:$n){'
         'mergedAt timelineItems(first:50,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT])'
         '{nodes{__typename ... on AddedToMergeQueueEvent{createdAt} ... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}' % tuple(REPO))
    err = ""
    for _ in range(3):
        r = subprocess.run(["gh", "api", "graphql", "-f", "query=" + q, "-F", f"n={n}"], capture_output=True, text=True)
        try:
            body = json.loads(r.stdout)["data"]["repository"]["pullRequest"]
        except (ValueError, KeyError, TypeError):
            err = (r.stderr or r.stdout)[:200]
            continue
        if body is None:
            return None
        _cache[n] = body
        return body
    raise SystemExit(f"GitHub 查 PR #{n} 失败：{err}")


def mins(a, b):
    return round((ts(b) - ts(a)).total_seconds() / 60)


def main():
    room, day = sys.argv[1], sys.argv[2]
    tok = login()
    evs = [m for m in events(tok, room, "accept", pages=10) + events(tok, room, "system", pages=10)
           if m["created_at"].startswith(day)]
    evs.sort(key=lambda m: m["created_at"])
    by = {}
    for m in evs:
        by.setdefault(m["conversation_id"], []).append(m)
    waits, late, filed, unready, fails, drops = [], [], 0, 0, {}, []
    for c, ev in by.items():
        for i, m in enumerate(ev):
            et = meta(m).get("event_type")
            if et == "turn_failed":
                fails[c] = fails.get(c, 0) + 1
            if et == "card_filed":
                filed += 1
                nxt = next((x for x in ev[i + 1:] if meta(x).get("event_type") in ("ci_failed", "accept_ready")), None)
                if nxt and (meta(nxt)["event_type"] == "ci_failed" or mins(m["created_at"], nxt["created_at"]) > 20):
                    unready += 1
            if et == "accept_ready":
                n = re.search(r"#(\d+)", m["content"])
                if not n:
                    continue
                pr = gh_pr(int(n.group(1)))
                if pr is None:
                    continue
                tl = pr["timelineItems"]["nodes"]
                adds = [x["createdAt"] for x in tl if x["__typename"] == "AddedToMergeQueueEvent" and x["createdAt"] >= m["created_at"]]
                if adds:
                    w = mins(m["created_at"], adds[0])
                    waits.append(w)
                    if w > 10:
                        late.append((n.group(1), w))
                for j, x in enumerate(tl):
                    if x["__typename"] == "RemovedFromMergeQueueEvent" and x["reason"] != "merged" and x["createdAt"].startswith(day):
                        back = next((y["createdAt"] for y in tl[j + 1:] if y["__typename"] == "AddedToMergeQueueEvent"), None)
                        drops.append((n.group(1), x["reason"], mins(x["createdAt"], back) if back else None))
    waits.sort()
    if waits:
        p90 = waits[min(len(waits) - 1, int(len(waits) * 0.9))]
        print(f"等采纳：{len(waits)} 次，p50 {statistics.median(waits)} 分，p90 {p90} 分，最大 {waits[-1]} 分，合计 {sum(waits)} 分")
    print(f"  >10 分钟 {len(set(late))} 次：" + "、".join(f"#{n} {w}分" for n, w in sorted(set(late))))
    print(f"未就绪递卡：{unready}/{filed}")
    print("出队：" + ("、".join(f"#{n}({r}) {'未重排' if b is None else str(b) + '分后重排'}" for n, r, b in sorted(set(drops))) or "无"))
    print("失败轮次：总 %d；" % sum(fails.values()) + "、".join(f"{c[:8]} {k}" for c, k in sorted(fails.items(), key=lambda x: -x[1])[:8]))


if __name__ == "__main__":
    main()
