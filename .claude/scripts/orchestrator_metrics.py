#!/usr/bin/env python3
"""One day of orchestrator metrics, to tell whether a patrol change worked. Read-only.

Usage: OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/orchestrator_metrics.py <channel topic id> <YYYY-MM-DD>
       python3 .claude/scripts/orchestrator_metrics.py --week [<YYYY-MM-DD>|ISO instant>]
       python3 .claude/scripts/orchestrator_metrics.py --self-test   (offline: no token, no network, no gh)
For that UTC day:
  等采纳      mergeable (accept_ready) -> entered the merge queue: p50/p90/max, and how many took > 10 min
  未就绪递卡  cards whose first signal was a CI failure, or that took > 20 min to become mergeable
  出队        removals from the queue other than merging, and minutes until re-queued
  失败轮次    failed turns per line
Accepting emits no platform event, so "entered the queue" is GitHub's AddedToMergeQueueEvent;
accept_done is sent after the merge and is never used as the acceptance time.

For the week ending at `--week` (UTC, default now):
  同文件先合率  本周合并的 PR 里，开着期间有别的 PR 改到同一文件、并且先合进了 main
                的比例；另报只算代码文件的版本（登记类文件之外的都算代码）和贡献
                最多的前 5 个文件
That mode reads GitHub through `gh` and needs no OKCHEESE_* login, which is why it is
`--week` and not another day: the day mode's numbers come from the platform's event
log, and this one has nothing to do with it. `--week 2026-10-14T02:30:00Z` is the
week the study measured; a bare date means that day at 00:00 UTC.
"""
import importlib.util
import json, re, statistics, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def sorted_drops(drops):
    """按 #PR、原因、分钟数排出队记录，没重排的（分钟数 None）排在最前。

    出队的每条是 (PR 号, 原因, 分钟数或 None)。`sorted(set(drops))` 直接比
    元组，同一 PR 同一原因可以掉两次——一次重排、一次没重排——第 3 项 int 和
    None 一相遇就 TypeError，报表断在出队这一行，它和后面的失败轮次都打不出
    来。键把 None 落到 -1，比数字都小，有数字时的次序不变。
    """
    return sorted(set(drops), key=lambda d: (d[0], d[1], -1 if d[2] is None else d[2]))


def drops_line(drops):
    return "出队：" + ("、".join(
        f"#{n}({r}) {'未重排' if b is None else str(b) + '分后重排'}" for n, r, b in sorted_drops(drops)) or "无")


# ------------------------------------------------------ 同文件先合率（按周）

_ARCH = None


def registry_file(path):
    """文件是不是登记类：arch-metrics.py 里那一份判断，加载过来用。

    「只算代码文件」和指标二「只冲突在登记类文件」用的是同一条界定。在这里抄
    一份就会有第二个定义，以后各说各的；所以照 arch-metrics.py 自己加载
    check-file-sizes.py 的办法把它加载过来，不重写。
    """
    global _ARCH
    if _ARCH is None:
        source = Path(__file__).resolve().parent / "arch-metrics.py"
        spec = importlib.util.spec_from_file_location("_arch_metrics", source)
        if spec is None or spec.loader is None:
            raise SystemExit(f"读不到 {source}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["_arch_metrics"] = module
        spec.loader.exec_module(module)
        _ARCH = module
    return _ARCH.registry_file(path)


def gh_json(args, what, tries=3, pause=2):
    """调一次 gh，返回解析好的 JSON；网络抖一下重试，试完都不成才报错。

    TLS 握手超时在这种代理后面真的发生过，一周要打几十次 REST，一次超时就让整
    周没有数——这是最坏的一种失败，看上去像「查不了」，其实只是没重试。重试之间
    等一下，因为超时通常意味着对面正忙。stdout 前面可能顶着一行 gh 自己的说明，
    所以从第一个 `{` 起解析。
    """
    err = ""
    for attempt in range(tries):
        if attempt:
            time.sleep(pause * attempt)
        r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
        body = r.stdout[r.stdout.find("{"):]
        try:
            return json.loads(body)
        except ValueError:
            err = (r.stderr or r.stdout).strip()[:200]
    raise SystemExit(f"GitHub {what} 失败：{err}")


def gh_graphql(variables, what="GraphQL 调用"):
    return gh_json(["graphql", *variables], what)


def gh_search(query, after=None):
    """搜索结果的一页，最多 100 条。`after` 是上一页的 endCursor。"""
    fields = ("number author{login} createdAt mergedAt "
              "files(first:100){nodes{path} pageInfo{hasNextPage}}")
    if after:
        doc = ('query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after)'
               '{pageInfo{hasNextPage endCursor}nodes{... on PullRequest{%s}}}}' % fields)
        variables = ["-f", "query=" + doc, "-f", "q=" + query, "-f", "after=" + after]
    else:
        doc = ('query($q:String!){search(query:$q,type:ISSUE,first:100)'
               '{pageInfo{hasNextPage endCursor}nodes{... on PullRequest{%s}}}}' % fields)
        variables = ["-f", "query=" + doc, "-f", "q=" + query]
    body = gh_graphql(variables, "搜索")
    try:
        return body["data"]["search"]
    except (KeyError, TypeError):
        raise SystemExit("GitHub 搜索失败：" + json.dumps(body)[:200])


def gh_rest(path):
    """REST 那一步试几次就够：它一次一页，一周要打几十次。

    代理后面的 REST 会整段握手超时，试多少次都一样；补文件列表那份有 GraphQL
    兜底（见 complete_files），在这里多耗时间没有意义。
    """
    return gh_json([path], f"REST 调用（{path}）", tries=3, pause=2)


def merged_prs(start, end):
    """合并时间在 [start, end) 的 PR，带 createdAt、mergedAt 和文件列表。

    `merged:` 只到天，所以先按天框一个范围、再按 mergedAt 精确筛。搜索一次最多
    回 1000 条：一周的量在它以下，撞上上限就报错，不拿半份名单当真。
    """
    query = (f"repo:{REPO[0]}/{REPO[1]} is:pr is:merged "
             f"merged:{start:%Y-%m-%d}..{end:%Y-%m-%d} -author:app/dependabot")
    nodes, after, pages = [], None, 0
    while True:
        page = gh_search(query, after)
        nodes += page["nodes"]
        pages += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
        if pages >= 10:
            raise SystemExit(f"搜索超过 1000 条，窗口太长：{query}")
    out = []
    for node in nodes:
        if not node or not node.get("mergedAt"):
            continue
        if (node.get("author") or {}).get("login") == "app/dependabot":
            continue
        when = ts(node["mergedAt"])
        if not start <= when < end:
            continue
        out.append({
            "number": node["number"],
            "createdAt": ts(node["createdAt"]),
            "mergedAt": when,
            "files": [f["path"] for f in node["files"]["nodes"]],
            "more_files": node["files"]["pageInfo"]["hasNextPage"],
        })
    return out


def complete_files(pr):
    """文件列表超过 100 个的 PR，用 REST 补全。

    GraphQL 的 files 连接一页最多 100 条，搜索结果里的嵌套连接又翻不了页，所以
    超过 100 个文件的 PR 在搜索里只拿得到前 100 个。补不到就报错：拿半份文件列表
    算出来的比例偏小，而且看不出来。

    补这一下先走 REST；代理后面的 REST 会整段握手超时，那就退回单独查一次这个
    PR——`repository.pullRequest.files(after:)` 能翻页，同一个连接、同一份数据。
    """
    if not pr["more_files"]:
        return pr["files"]
    paths = []
    try:
        page = 1
        while True:
            arr = gh_rest(f"repos/{REPO[0]}/{REPO[1]}/pulls/{pr['number']}/files"
                          f"?per_page=100&page={page}")
            if not isinstance(arr, list) or not arr:
                break
            paths += [f["filename"] for f in arr]
            if len(arr) < 100:
                break
            page += 1
    except SystemExit:
        paths = []
    if paths:
        return paths

    head = ('query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,'
            'name:$name){pullRequest(number:$n){files(first:100){nodes{path}'
            'pageInfo{hasNextPage endCursor}}}}}')
    tail = ('query($owner:String!,$name:String!,$n:Int!,$after:String!){repository('
            'owner:$owner,name:$name){pullRequest(number:$n){files(first:100,'
            'after:$after){nodes{path} pageInfo{hasNextPage endCursor}}}}}')
    after = None
    while True:
        variables = ["-f", "query=" + (head if after is None else tail),
                     "-f", "owner=" + REPO[0], "-f", "name=" + REPO[1],
                     "-F", f"n={pr['number']}"]
        if after is not None:
            variables += ["-f", "after=" + after]
        try:
            files = gh_graphql(variables, "查单个 PR 的文件列表")["data"]["repository"]["pullRequest"]["files"]
        except (KeyError, TypeError, SystemExit):
            break
        paths += [f["path"] for f in files["nodes"]]
        if not files["pageInfo"]["hasNextPage"]:
            break
        after = files["pageInfo"]["endCursor"]
    if not paths:
        raise SystemExit(f"PR #{pr['number']} 的文件列表补不全")
    return paths


def landed_first(week, pool, files_of):
    """(命中数, {文件: 因为和它重叠而命中的 PR 数})。

    命中：本周合并的 PR p，在它开着的那段时间里（createdAt <= Q.mergedAt <
    mergedAt）有另一个已合并的 PR Q 改到同一个文件。只比合并时间，不看 Q 是从
    哪个分支合的；Q 没合并就不可能「先合进 main」，所以 pool 里只放合并了的
    PR。p 在自己这轮里不算 Q，不然每个 PR 都会和自己重叠。
    """
    landed = [(p["number"], p["mergedAt"]) for p in pool]
    hit, contrib = 0, {}
    for p in week:
        mine = files_of.get(p["number"], frozenset())
        if not mine:
            continue
        got = set()
        for number, when in landed:
            if number == p["number"] or not (p["createdAt"] <= when < p["mergedAt"]):
                continue
            got |= mine & files_of.get(number, frozenset())
        if got:
            hit += 1
            for path in got:
                contrib[path] = contrib.get(path, 0) + 1
    return hit, contrib


def top_line(contrib, count=5):
    return "、".join(f"{path} {n}" for path, n in sorted(
        contrib.items(), key=lambda kv: (-kv[1], kv[0]))[:count]) or "无"


def week_report(end):
    """一周的同文件先合率。窗口是 [end - 7 天, end)。

    「只算代码」是「登记类文件之外的都算」，和指标二「只冲突在登记类文件」用同
    一条界定：文档、配置、测试都在代码这一边。按扩展名挑过一版，研究那两个基线
    对不上，这个口径能一分数不差地对上，所以用的是它。

    重叠对象往前多取一周：能当 Q 的只有已合并的 PR，而 Q 可能早在 start 前一周
    就合进了 main，却仍在某个 P 开着的时候。PR 生命很短（研究中位数 0.9 小时），
    再往前取不会改变结果。
    """
    start = end - timedelta(days=7)
    pool = merged_prs(start - timedelta(days=7), end)
    for pr in pool:
        pr["files"] = complete_files(pr)
    week = [pr for pr in pool if pr["mergedAt"] >= start]
    files_all = {pr["number"]: frozenset(pr["files"]) for pr in pool}
    files_code = {n: frozenset(f for f in fs if not registry_file(f)) for n, fs in files_all.items()}

    hit_all, contrib_all = landed_first(week, pool, files_all)
    hit_code, contrib_code = landed_first(week, pool, files_code)
    total = len(week)
    print(f"同文件先合率：{start:%Y-%m-%dT%H:%M:%SZ} → {end:%Y-%m-%dT%H:%M:%SZ}，本周合并 PR {total} 个")
    if not total:
        print("  这一周没有合并的 PR，比例无从谈起")
        return 0
    print(f"  全部文件：{hit_all / total * 100:.1f}%（{hit_all} 个）")
    print(f"  只算代码：{hit_code / total * 100:.1f}%（{hit_code} 个）")
    print(f"  贡献最多的文件：{top_line(contrib_all)}")
    print(f"  只算代码时：{top_line(contrib_code)}")
    return 0


def parse_instant(args):
    """`--week` 的结束时刻：给日期是当天 00:00 UTC，不给是现在。"""
    if not args:
        return datetime.now(timezone.utc)
    raw = args[0]
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        return ts(raw)
    except ValueError:
        raise SystemExit(f"--week {raw} 要写成 2026-10-14 或 2026-10-14T02:30:00Z")


def main():
    if sys.argv[1:2] == ["--self-test"]:
        return self_test()
    if sys.argv[1:2] == ["--week"]:
        return week_report(parse_instant(sys.argv[2:3]))
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
    print(drops_line(drops))
    print("失败轮次：总 %d；" % sum(fails.values()) + "、".join(f"{c[:8]} {k}" for c, k in sorted(fails.items(), key=lambda x: -x[1])[:8]))


def self_test():
    """Offline checks of the pure parts: no token, no network, no gh.

    The first case is the bug this file was fixed for: one PR dropped twice
    with the same reason, once re-queued (minutes) and once not (None). Before
    the fix, `sorted(set(drops))` compared the third element — int against
    None — raised TypeError, and the report died at the 出队 line, so that line
    and the failed-turn line after it never printed. The check pins both that
    mix sorting and the wording of the 出队 line.
    """
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check(
        "未重排和 N 分后重排混着排不崩，未重排在前",
        drops_line([("1500", "ci_failed", 7), ("1500", "ci_failed", None)]),
        "出队：#1500(ci_failed) 未重排、#1500(ci_failed) 7分后重排",
    )
    check("没有出队时写「无」", drops_line([]), "出队：无")
    check(
        "同一条重复出现只算一次",
        drops_line([("9", "manual", 3), ("9", "manual", 3)]),
        "出队：#9(manual) 3分后重排",
    )

    # -- 同文件先合率，纯函数部分 ------------------------------------------
    # 手写的 PR 表，不碰网络也不碰 gh。Q 全部早于 P 合并，唯一变的是「合进了
    # main 没有」和「改到同一文件没有」，这样每一条断言只考一件事。
    def pr(number, created, merged, files):
        return {"number": number, "createdAt": ts(created), "mergedAt": ts(merged),
                "files": files}

    pool = [
        pr(1, "2026-10-07T09:00:00Z", "2026-10-07T10:00:00Z", ["backend/app/a.py", "frontend/src/b.ts"]),
        pr(2, "2026-10-07T08:00:00Z", "2026-10-07T09:30:00Z", ["backend/app/a.py", "frontend/src/b.ts"]),
        pr(3, "2026-10-07T09:10:00Z", "2026-10-07T11:00:00Z", ["backend/app/b.py"]),
        # 比 3 早合并，但早于 3 创建：3 开着的时候它已经在 main 上了，不算
        pr(4, "2026-10-07T07:00:00Z", "2026-10-07T08:00:00Z", ["backend/app/b.py"]),
        # 只碰登记类文件：全文件口径里算命中，只算代码的版本里连分母都不参与比较
        pr(5, "2026-10-07T09:00:00Z", "2026-10-07T12:00:00Z",
           ["i18n/messages/en/work.json"]),
        pr(6, "2026-10-07T08:00:00Z", "2026-10-07T09:30:00Z",
           ["i18n/messages/en/work.json"]),
        # 和 7 同时刻合并：不算「先」，不算命中
        pr(7, "2026-10-07T09:00:00Z", "2026-10-07T13:00:00Z", ["backend/app/c.py"]),
        pr(8, "2026-10-07T08:00:00Z", "2026-10-07T13:00:00Z", ["backend/app/c.py"]),
    ]
    week = [p for p in pool if p["number"] in (1, 3, 5, 7)]

    files_all = {p["number"]: frozenset(p["files"]) for p in pool}
    check(
        "同一时刻合并不算先合进 main",
        landed_first([p for p in week if p["number"] == 7], pool, files_all)[0],
        0,
    )
    hit, contrib = landed_first(week, pool, files_all)
    check("全文件口径命中 1 和 5", hit, 2)
    # 3 只和 4 重叠，而 4 在 3 创建之前就已经在 main 上了，所以 3 不算命中；
    # 3 是唯一碰 b.py 的本周 PR，b.py 进不了贡献表就是这条成立。
    check("3 没被别人先合过同名文件", "backend/app/b.py" in contrib, False)
    check("命中算在两个文件上", contrib["backend/app/a.py"], 1)
    check("登记类文件也被算进来了", contrib["i18n/messages/en/work.json"], 1)
    check("命中的文件按贡献数排，并列按名字", top_line(contrib),
          "backend/app/a.py 1、frontend/src/b.ts 1、i18n/messages/en/work.json 1")

    files_code = {n: frozenset(f for f in fs if not registry_file(f))
                  for n, fs in files_all.items()}
    check("登记类文件不是代码文件", registry_file("i18n/messages/en/work.json"), True)
    check("普通模块是", registry_file("backend/app/a.py"), False)
    hit_code, contrib_code = landed_first(week, pool, files_code)
    check("只算代码时 5 不算命中", hit_code, 1)
    check("只算代码时的贡献不含登记类文件",
          sorted(contrib_code), ["backend/app/a.py", "frontend/src/b.ts"])
    check("只有登记类文件的 PR 不进贡献表", "i18n/messages/en/work.json" in contrib_code, False)
    # 「只算代码」是「登记类之外的都算」，不是按扩展名挑：文档和配置也算在
    # 里面。研究那两个基线（31.9% → 26.6%）只有这个口径能一分数不差地对上。
    check("登记类之外的文件都算代码", registry_file("docs/manual/dev/architecture.md"), False)
    check("后端测试也算", registry_file("backend/tests/integration/test_x.py"), False)

    check("没有命中时写「无」", top_line({}), "无")
    check("贡献前 5 名之外的不列", top_line({f"f{i}.py": 1 for i in range(7)}),
          "f0.py 1、f1.py 1、f2.py 1、f3.py 1、f4.py 1")

    check("--week 给日期就是当天 00:00 UTC",
          parse_instant(["2026-10-14"]), datetime(2026, 10, 14, tzinfo=timezone.utc))
    check("--week 收 ISO 时刻", parse_instant(["2026-10-14T02:30:00Z"]),
          datetime(2026, 10, 14, 2, 30, tzinfo=timezone.utc))
    check("--week 不给就是现在（是个带时区的时刻）",
          parse_instant([]).utcoffset(), timedelta(0))

    for f in failures:
        print("FAIL", f)
    print("self-test:", "ok" if not failures else f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
