#!/usr/bin/env python3
"""一条任务线的改动，最后是不是真的落到了它要解决的问题上。只读。

它要回答的不是「这条线做了多久」，而是「做完之后，代码里留下了什么」：一条线
在窗口内第一次合入时，把它的合并提交和改动文件、行数、动到的目录都数出来，
再看这些改动到今天还在不在、有没有被同一片文件上的别的线顶掉、有没有被回退。

Usage:
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/change_outcomes.py \
      <频道 topic id> <YYYY-MM-DD> <YYYY-MM-DD> [--json out.json] [--md out.md]
  python3 .claude/scripts/change_outcomes.py --self-test   (offline: 无 token、无网络、无 git)

口径（写进报告时必须一起给出，换口径数字就不可比）:
  队列   一条「任务线」= 一个 task 的全部验收卡按 pr_merged_at 排序；只有第一次
         合入落在 [start, end) 里的线进队列。之后这条线上的每一次合入都算它的。
  首次合入  该线最早一张已合并卡的时间。
  改动文件  该线全部合并提交（squash 进 main 的那个提交）动到的文件并集；行数取
         numstat 的增/删合计。二进制文件 numstat 记 `-`，不计入行数但计入文件数。
  模块   文件路径去掉档名前缀后的第一段（backend/、frontend/、docs/、.claude/…）；
         根目录的散文件记为 `(root)`。
  类别   测试(`/tests/`、`test_`、`.spec.`、`.test.`)、文档(`docs/`、`*.md`)、
         脚本(`.claude/`)、其余为代码。四类互斥，判定顺序即此处列举顺序。
  目标匹配  这条线的第一个合并提交动到的模块集合，与它后续合并提交动到的模块集合
         比较。后续越出首个的模块 = 范围外扩，记 drift_modules；一条线只有一个
         合并提交时无法计算，记 None。
  留存     该线动过的文件里，今天在 HEAD 上仍然存在的比例。文件还在不等于改动还
         在，这条只回答「文件被整体删掉/改名」这一类消失。
  返工     该线某个文件，在它自己第一次合入之后，又被**不属于这条线**的提交改过。
         说明同一片代码在窗口里被反复动。
  回退     窗口里的提交标题匹配 `Revert` 且提到该线的 PR 号或合并提交短 sha。

窗口短（一周）时，「返工」只看得到窗口内的后续提交；报告里要写明观察截止时间。
"""
import collections
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orchestrator_sweep import login, get  # noqa: E402  (house helpers: token + GET)

REPO_PATH = os.environ.get("CHANGE_OUTCOMES_REPO", ".")  # 任务工作目录，默认当前目录
#: 这个仓库的两个产物代码树。一条线的后续合并动到了首个合并没动过的那一棵，
#: 就是「顺手动到了另一头的代码」，算范围外扩。
CODE_TREES = {"backend/", "frontend/"}
REVERT_RE = re.compile(r"\b[Rr]evert\b")
PR_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def unwrap(x):
    """平台列表接口把数组包在 data 里，这里统一剥一层。"""
    return x["data"] if isinstance(x, dict) and "data" in x else x


def git(*args):
    return subprocess.run(["git", "-c", "core.quotepath=false", "-C", REPO_PATH, *args],
                          capture_output=True, text=True, check=True).stdout


def norm_path(p):
    """git 的改名记法 `a/{旧 => 新}/b` 和 `旧 => 新` 都取改名后的那一侧。"""
    p = p.strip()
    if " => " in p:
        if "{" in p:
            pre, rest = p.split("{", 1)
            mid, post = rest.split("}", 1)
            p = pre + mid.split(" => ")[1] + post
        else:
            p = p.split(" => ")[1]
    return p.strip()


def load_history(since):
    """main 上每条提交动了哪些文件。只读 tree 不读 blob，在 blobless 克隆上不联网。"""
    out = git("log", "--name-only", "--format=%x01%H%x02%cI%x02%s",
              "--since=" + since, "origin/main")
    commits, cur = [], None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            sha, date, subject = line[1:].split("\x02", 2)
            cur = {"sha": sha, "date": T(date), "subject": subject, "files": {}}
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur["files"][norm_path(line)] = None
    return commits


def load_numstat(shas):
    """只对点名的提交取行数。numstat 要读 blob，blobless 克隆会按需从远端拉，
    所以这里只喂合并提交，不要对整段历史跑（那会按提交个数逐次联网）。"""
    if not shas:
        return {}
    out = git("log", "--no-walk", "--numstat", "--format=%x01%H", *shas)
    res, cur = {}, None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            cur = line[1:].strip()
            res[cur] = {}
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            path = norm_path(path)
            try:
                res[cur][path] = (int(a), int(d))
            except ValueError:
                res[cur][path] = (0, 0)
    return res


def head_files():
    return set(x for x in git("ls-tree", "-r", "--name-only", "HEAD").split("\n") if x)


def module_of(path):
    if "/" not in path:
        return "(root)"
    return path.split("/", 1)[0] + "/"


#: 每次改动都会被带着一起重算的文件：生成物和登记表。它们出现在几乎每条线的
#: 改动里，不区分出来，「哪些文件被反复改」这类结论会被它们盖住。
REGISTRY_PAT = ("/i18n/messages/", "openapi.json", "openapi.d.ts", "baseline",
                "route_index.yaml", "scene-baseline", "ratchet")


def registry_of(path):
    return any(pat in path for pat in REGISTRY_PAT)


def kind_of(path):
    if ("/tests/" in path or path.startswith("tests/") or "/test_" in path
            or path.startswith("test_") or ".spec." in path or ".test." in path):
        return "测试"
    if path.startswith("docs/") or path.endswith(".md"):
        return "文档"
    if path.startswith(".claude/"):
        return "脚本"
    return "代码"


def build_cohort(cards, tasks, start, end):
    """按 task 分组、按首次合入筛窗口。返回 (rows, 有卡但从未合入的线数)。"""
    by = collections.defaultdict(list)
    for c in cards:
        by[c["task_id"]].append(c)
    tmap = {t["id"]: t for t in tasks}
    rows, never = [], 0
    for tid, cs in by.items():
        merged = sorted((c for c in cs if c.get("pr_merged_at")),
                        key=lambda c: T(c["pr_merged_at"]))
        if not merged:
            never += 1
            continue
        first = T(merged[0]["pr_merged_at"])
        if not (start <= first < end):
            continue
        t = tmap.get(tid, {})
        rows.append({
            "task_id": tid,
            "number": t.get("number"),
            "title": t.get("title"),
            "owner": t.get("owner_handle"),
            "agent": t.get("agent_handle"),
            "status": t.get("status"),
            "first_merged": first.isoformat(),
            "prs": [c["pr_number"] for c in merged],
        })
    rows.sort(key=lambda r: r["first_merged"])
    return rows, never


def merge_commit(commits, pr_number):
    """squash 合并在 main 上的提交标题以 `(#N)` 结尾。"""
    tag = "(#%d)" % pr_number
    for c in commits:
        if c["subject"].endswith(tag):
            return c
    return None


def summarize(rows, commits, present_at_head, lines):
    """把每条线的合并提交翻成指标。"""
    sha_to_line = {}
    out = []
    for i, r in enumerate(rows):
        files = collections.defaultdict(lambda: [0, 0])  # path -> [add, del]
        trees, kinds = collections.Counter(), collections.Counter()
        first_tree, later_tree = set(), set()
        first_code, later_code = set(), set()
        reg = 0
        first_sha = None
        for j, pr in enumerate(r["prs"]):
            c = merge_commit(commits, pr)
            if c is None:
                continue
            sha_to_line[c["sha"]] = i
            if j == 0:
                first_sha = c["sha"]
            for p, (a, d) in lines.get(c["sha"], {}).items():
                files[p][0] += a
                files[p][1] += d
                kinds[kind_of(p)] += 1
                (first_tree if j == 0 else later_tree).add(module_of(p))
                (first_code if j == 0 else later_code).add(module_of(p))
        if not files:
            continue
        added = sum(v[0] for v in files.values())
        removed = sum(v[1] for v in files.values())
        gone = sorted(p for p in files if p not in present_at_head)
        reg = sum(1 for p in files if registry_of(p))
        out.append({
            "line": i,
            "task_id": r["task_id"],
            "number": r["number"],
            "title": r["title"],
            "owner": r["owner"],
            "agent": r["agent"],
            "status": r["status"],
            "first_merged": r["first_merged"],
            "prs": r["prs"],
            "merge_shas": [c["sha"] for c in
                           (merge_commit(commits, p) for p in r["prs"]) if c],
            "files": len(files),
            "added": added,
            "removed": removed,
            "net": added - removed,
            "trees": dict(collections.Counter(module_of(p) for p in files)),
            "kinds": dict(kinds),
            "drift_modules": sorted(later_tree - first_tree) if len(r["prs"]) > 1 else None,
            "drift_code": sorted((later_code - first_code) & CODE_TREES)
            if len(r["prs"]) > 1 else None,
            "registry_files": reg,
            "gone_files": gone,
            "survive_ratio": round((len(files) - len(gone)) / len(files), 3),
            "rework_files": [],
            "_paths": set(files),
            "reverted": False,
        })
    return out


def rework(rows, commits):
    """线的文件在它自己第一次合入之后被别的提交动过。"""
    first_sha = {}
    for r in rows:
        if r["merge_shas"]:
            first_sha[r["task_id"]] = r["merge_shas"][0]
    pos = {c["sha"]: k for k, c in enumerate(commits)}
    own = {}   # sha -> task_id（本线自己的提交不算返工）
    sha_to_task = {}
    for r in rows:
        for s in r["merge_shas"]:
            sha_to_task[s] = r["task_id"]
    for r in rows:
        fs = set()
        for s in r["merge_shas"]:
            fs |= set(commits[pos[s]]["files"]) if s in pos else set()
        r["files_set"] = fs
    for k, c in enumerate(commits):
        if c["sha"] in sha_to_task:
            continue
        for p in c["files"]:
            for r in rows:
                if p in r.get("files_set", ()) and r["merge_shas"]:
                    base = pos[r["merge_shas"][0]]
                    if k > base:
                        r["rework_files"].append(p)
    for r in rows:
        r["rework_files"] = sorted(set(r["rework_files"]))
        r["rework"] = len(r["rework_files"])
        r["rework_registry"] = sum(1 for p in r["rework_files"] if registry_of(p))
        r["rework"] -= r["rework_registry"]
        r.pop("files_set", None)
    for c in commits:
        if not REVERT_RE.search(c["subject"]):
            continue
        for r in rows:
            for p in r["prs"]:
                if "(#%d)" % p in c["subject"]:
                    r["reverted"] = True
    return rows


def overlap(rows):
    """同一批文件上被两条以上的线动过。"""
    owner = collections.defaultdict(set)
    for r in rows:
        for p in r.get("_paths", ()):
            owner[p].add(r["task_id"])
    for r in rows:
        r.pop("_paths", None)
    shared = {p: sorted(v) for p, v in owner.items() if len(v) > 1}
    pairs = collections.Counter()
    title = {r["task_id"]: r["number"] for r in rows}
    for p, ts in shared.items():
        for a in range(len(ts)):
            for b in range(a + 1, len(ts)):
                pairs[(title[ts[a]], title[ts[b]])] += 1
    return shared, pairs


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = min(len(values) - 1, int(round((len(values) - 1) * p)))
    return values[k]


def render(rows, never, since, start, end, as_of, pairs):
    """报告文本。每一句都从 rows 里算出来，不写死任何数字。"""
    n = len(rows)
    files = [r["files"] for r in rows]
    net = [r["net"] for r in rows]
    code_files = [r["kinds"].get("代码", 0) for r in rows]
    test_files = [r["kinds"].get("测试", 0) for r in rows]
    multi = [r for r in rows if r["drift_modules"] is not None]
    drift_code = [r for r in multi if r["drift_code"]]
    drift_any = [r for r in multi if r["drift_modules"]]
    reg_share = [r["registry_files"] / r["files"] for r in rows if r["files"]]
    reg_heavy = [r for r in rows if r["files"] and r["registry_files"] * 2 >= r["files"]]
    dead = [r for r in rows if r["gone_files"]]
    dead_code = [r for r in rows if any(not registry_of(p) for p in r["gone_files"])]
    rew = [r for r in rows if r.get("rework")]
    rew_reg = [r for r in rows if r.get("rework_registry")]
    rev = [r for r in rows if r["reverted"]]
    tot_reg = sum(r["registry_files"] for r in rows)
    tot_files = sum(files) or 1
    top_pairs = pairs.most_common(5) if isinstance(pairs, collections.Counter) else []

    def p(v):
        return "—" if v is None else str(v)

    L = []
    L.append("# 任务线的改动落到了哪里（%s ~ %s，观察截止 %s）" % (start, end, as_of))
    L.append("")
    L.append("队列：窗口内第一次合入的 **%d 条线**，共 %d 次合并；同期有卡但从未合入的 %d 条。"
             % (n, sum(len(r["prs"]) for r in rows), never))
    L.append("口径见脚本头。窗口短，返工只看得到窗口内的后续提交。")
    L.append("")
    L.append("## 一次改动多大")
    L.append("")
    L.append("| | 中位 | 四分位 | 最大 |")
    L.append("|---|---|---|---|")
    L.append("| 改动文件数 | %s | %s–%s | %s |"
             % (p(pct(files, .5)), p(pct(files, .25)), p(pct(files, .75)),
                max(files) if files else 0))
    L.append("| 净增行数 | %s | %s–%s | %s |"
             % (p(pct(net, .5)), p(pct(net, .25)), p(pct(net, .75)),
                max(net) if net else 0))
    L.append("| 其中代码文件 | %s | %s–%s | %s |"
             % (p(pct(code_files, .5)), p(pct(code_files, .25)), p(pct(code_files, .75)),
                max(code_files) if code_files else 0))
    L.append("| 其中测试文件 | %s | %s–%s | %s |"
             % (p(pct(test_files, .5)), p(pct(test_files, .25)), p(pct(test_files, .75)),
                max(test_files) if test_files else 0))
    L.append("")
    L.append("- 生成物与登记表（i18n、openapi、baseline、ratchet 这类每次都会被带着重算"
             "的文件）占全部改动文件的 **%d%%**（%d/%d）；改动文件一半以上是生成物的线 "
             "%d 条。" % (round(100 * tot_reg / tot_files), tot_reg, tot_files, len(reg_heavy)))
    L.append("- 只有 1 次合并的线 %d 条，≥2 次的 %d 条。" % (n - len(multi), len(multi)))
    L.append("")
    L.append("## 打在该解决的问题上了吗")
    L.append("")
    if multi:
        L.append("- 多条合并的线里，后续合并动到了**另一个代码树**（backend/ 与 frontend/ 之间"
                 "互相跨界）的 **%d/%d** 条。" % (len(drift_code), len(multi)))
        L.append("- 放宽到任意模块（含 docs/、.github/、.claude/ 等收尾件）：**%d/%d** 条。"
                 "这一项把同一条线的收尾也算成外扩，只作参考。" % (len(drift_any), len(multi)))
    else:
        L.append("- 窗口内没有多条合并的线，这一项算不出来。")
    L.append("- 只动代码不碰测试的线 %d 条；碰了文档的线 %d 条。"
             % (sum(1 for r in rows if r["kinds"].get("测试", 0) == 0),
                sum(1 for r in rows if r["kinds"].get("文档", 0))))
    L.append("")
    L.append("## 留下来了吗")
    L.append("")
    L.append("- 改动文件今天仍在 HEAD 上：%d/%d 条线是 100%%。"
             % (sum(1 for r in rows if not r["gone_files"]), n))
    L.append("- 去掉生成物后仍有文件消失的线：%d 条（消失的多是改名或被重算掉的登记表）。"
             % len(dead_code))
    L.append("- 改动过的文件又被别的提交改过（返工，去掉生成物）：%d 条线、共 %d 个文件。"
             % (len(rew), sum(r["rework"] for r in rew)))
    if rew_reg:
        L.append("- 只看生成物被再改：%d 条线（几乎每条线都在重算同一批登记表）。" % len(rew_reg))
    L.append("- 窗口内有回退提交指向的线：%d 条。" % len(rev))
    L.append("")
    L.append("## 同一片文件上的线")
    L.append("")
    L.append("- 共 %d 对线在同一批文件上相遇。" % len(pairs) if top_pairs else "- 没有被两条线共用的文件。")
    for (a, b), c in top_pairs:
        L.append("- #%s 与 #%s：共用 %d 个文件。" % (a, b, c))
    return "\n".join(L)


def self_test():
    cards = [
        {"task_id": "A", "pr_number": 1, "pr_merged_at": "2026-10-08T00:00:00Z"},
        {"task_id": "A", "pr_number": 2, "pr_merged_at": "2026-10-09T00:00:00Z"},
        {"task_id": "B", "pr_number": 3, "pr_merged_at": "2026-10-08T12:00:00Z"},
        {"task_id": "C", "pr_number": 4, "pr_merged_at": "2026-10-01T00:00:00Z"},
    ]
    tasks = [{"id": "A", "number": 10, "title": "a", "status": "closed"},
             {"id": "B", "number": 11, "title": "b", "status": "closed"}]
    rows, never = build_cohort(cards, tasks,
                               datetime(2026, 10, 7, tzinfo=timezone.utc),
                               datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert [r["number"] for r in rows] == [10, 11], rows
    assert never == 0
    commits = [
        {"sha": "s1", "date": None, "subject": "feat: x (#1)", "files": {"frontend/a.ts": (10, 1)}},
        {"sha": "s2", "date": None, "subject": "fix: y (#2)", "files": {"backend/b.py": (3, 0)}},
        {"sha": "s3", "date": None, "subject": "feat: z (#3)", "files": {"frontend/a.ts": (2, 0)}},
        {"sha": "s9", "date": None, "subject": "chore: later", "files": {"frontend/a.ts": (1, 1)}},
        {"sha": "s10", "date": None, "subject": "Revert \"feat: x (#1)\"", "files": {"frontend/a.ts": (0, 10)}},
    ]
    assert merge_commit(commits, 1)["sha"] == "s1"
    assert merge_commit(commits, 99) is None
    assert kind_of("frontend/a.spec.ts") == "测试"
    assert kind_of("backend/tests/unit/test_x.py") == "测试"
    assert kind_of("docs/manual/a.md") == "文档"
    assert kind_of(".claude/scripts/x.py") == "脚本"
    assert kind_of("backend/app/main.py") == "代码"
    assert module_of("backend/app/main.py") == "backend/"
    assert module_of("README.md") == "(root)"
    lines = {c["sha"]: c["files"] for c in commits}
    out = summarize(rows, commits, {"frontend/a.ts", "backend/b.py"}, lines)
    a = [r for r in out if r["task_id"] == "A"][0]
    assert a["files"] == 2 and a["net"] == 12, a
    assert a["drift_modules"] == ["backend/"], a["drift_modules"]
    b = [r for r in out if r["task_id"] == "B"][0]
    assert b["drift_modules"] is None, b["drift_modules"]
    out = rework(out, commits)
    a = [r for r in out if r["task_id"] == "A"][0]
    assert a["rework"] >= 1 and a["reverted"], a
    # gone file
    out = summarize(rows, commits, {"frontend/a.ts"}, lines)
    a = [r for r in out if r["task_id"] == "A"][0]
    assert a["gone_files"] == ["backend/b.py"], a
    assert pct([1, 2, 3, 4], .5) == 3
    print("self-test ok")


def main(argv):
    if argv and argv[0] == "--self-test":
        return self_test()
    if len(argv) < 3:
        print(__doc__)
        return 2
    room, start, end = argv[0], argv[1], argv[2]
    opts = {"--json": None, "--md": None, "--lookback-days": "7"}
    for i in range(3, len(argv) - 1, 2):
        key = argv[i]
        if key in opts:
            opts[key] = argv[i + 1]
    start_d = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_d = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    since = (start_d - timedelta(days=int(opts["--lookback-days"]))).date().isoformat()
    tok = login()
    cards = unwrap(get(tok, "/topics/%s/accept-card" % room))
    tasks = unwrap(get(tok, "/topics/%s/tasks?limit=200" % room))
    rows, never = build_cohort(cards, tasks, start_d, end_d)
    commits = load_history(since)
    present = head_files()
    shas = [c["sha"] for r in rows for c in
            (merge_commit(commits, p) for p in r["prs"]) if c]
    data = summarize(rows, commits, present, load_numstat(shas))
    data = rework(data, commits)
    shared, pairs = overlap(data)
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = render(data, never, since, start, end, as_of, pairs)
    if opts["--json"]:
        json.dump({"cohort": data, "never_merged": never,
                   "overlap_pairs": {"%d-%d" % k: v for k, v in pairs.items()},
                   "window": [start, end], "as_of": as_of},
                  open(opts["--json"], "w"), ensure_ascii=False, indent=1)
    if opts["--md"]:
        open(opts["--md"], "w").write(md)
    if not opts["--json"] and not opts["--md"]:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
