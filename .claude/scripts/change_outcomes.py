#!/usr/bin/env python3
"""一条任务线的改动，最后是不是真的落到了它要解决的问题上。只读。

它要回答的不是「这条线做了多久」，而是「做完之后，代码里留下了什么」：一条线
在窗口内第一次合入时，把它的合并提交和改动文件、行数、动到的目录都数出来，
再看这些改动到今天还在不在、有没有被同一片文件上的别的线顶掉、有没有被回退。

Usage:
  OKCHEESE_USER=... OKCHEESE_PASS=... python3 .claude/scripts/change_outcomes.py \
      <频道 topic id> <YYYY-MM-DD> <YYYY-MM-DD> [--json out.json] [--md out.md]
  python3 .claude/scripts/change_outcomes.py --self-test   (离线：无 token、无网络；
      会在临时目录起一个 git 仓库，把两个历史读取函数真的打一遍)

口径（写进报告时必须一起给出，换口径数字就不可比）:
  队列   一条「任务线」= 一个 task 的全部验收卡按 pr_merged_at 排序；只有第一次
         合入落在 [start, end) 里的线进队列。之后这条线上的每一次合入都算它的——
         按平台的已合并卡计，认不出提交的那一次也算一次合入，只是不计入改动。
  首次合入  该线最早一张已合并卡的时间。
  合并提交  squash 合并在默认分支上的提交标题以 `(#N)` 结尾；非 squash 的 GitHub
         合并提交标题是 `Merge pull request #N from …`。两种都认。真正的合并提交
         git 默认不输出文件，所以历史和 numstat 都带 `-m --first-parent`，取它相对
         第一父的改动。认不出的那次合入不计入改动，报告里按「对不上的合并」单独
         报数——它不等于没有合并。
  改动文件  该线全部认出合并提交动到的文件并集；行数取 numstat 的增/删合计。二进制
         文件 numstat 记 `-`，不计入行数但计入文件数。
  模块   文件路径去掉档名前缀后的第一段（backend/、frontend/、docs/、.claude/…）；
         根目录的散文件记为 `(root)`。
  类别   测试(`/tests/`、`test_`、`.spec.`、`.test.`)、文档(`docs/`、`*.md`)、
         脚本(`.claude/`)、其余为代码。四类互斥，按文件去重，判定顺序即此处列举顺序。
  目标匹配  这条线的第一个合并提交动到的模块集合，与它后续合并提交动到的模块集合
         比较。后续越出首个的模块 = 范围外扩，记 drift_modules；只有一个合并提交、
         或首个合并提交没认出来时无法计算，记 None。
  留存     该线动过的文件里，今天在默认分支的 HEAD 上仍然存在的比例。文件还在不等于
         改动还在，这条只回答「文件被整体删掉/改名」这一类消失。
  返工     该线某个文件，在它自己第一次合入（按时间）之后，又被**不属于这条线**的
         提交改过。说明同一片代码在窗口里被反复动。提交按 git log 的时间倒序读，
         「之后」= 列表中更靠前的提交。不属这条线 = 这条线的合并提交之外的提交，
         别的线的合并提交也算——两条线动了同一片文件，正是这一项要量的东西。
  回退     区间内可达的全部提交里（不限第一父链），标题匹配 `Revert` 且提到该线的
         PR 号的那些（读的历史从窗口下界再往前看 `--lookback-days` 天，到观察
         时刻为止，比窗口本身宽），
         且这条提交本身不是该线自己的合并提交（标题里带 rever 的 PR 不算被回退）。
         另一条线的合并提交若回退了这条线，算这条线被回退。
  分位     最近秩：p=0.5 取排序后第 ⌈0.5n⌉ 个，不做插值。
  历史   除回退外按第一父链读（`-m --first-parent`）：只经某次合并的第二父到达的
         提交不在其中，合并提交取它相对第一父的改动。只有 squash 合并的历史上，
         第一父链就是全部历史。回退的标题扫描另读一遍区间内可达的全部提交。

窗口短（一周）时，「返工」只看得到窗口内的后续提交；报告里要写明观察截止时间。
默认分支用 CHANGE_OUTCOMES_REF 换（默认 origin/main），全部历史读取都用它，不用 HEAD。
"""
import collections
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orchestrator_sweep import login, get  # noqa: E402  (house helpers: token + GET)

REPO_PATH = os.environ.get("CHANGE_OUTCOMES_REPO", ".")  # 任务工作目录，默认当前目录
REF = os.environ.get("CHANGE_OUTCOMES_REF", "origin/main")
#: 这个仓库的两个产物代码树。一条线的后续合并动到了首个合并没动过的那一棵，
#: 就是「顺手动到了另一头的代码」，算范围外扩。
CODE_TREES = {"backend/", "frontend/"}
REVERT_RE = re.compile(r"[Rr]evert")


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def parse_day(s):
    """接受带或不带时区的日期时间；不带就按 UTC 算，带就照它自己的偏移。"""
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def unwrap(x):
    """平台列表接口把数组包在 data 里，这里统一剥一层。"""
    return x["data"] if isinstance(x, dict) and "data" in x else x


def git(*args):
    p = subprocess.run(["git", "-c", "core.quotepath=false", "-C", REPO_PATH, *args],
                       capture_output=True, text=True)
    if p.returncode:
        raise SystemExit("git %s 失败（退出码 %d）：%s"
                         % (" ".join(args[:2]), p.returncode, p.stderr.strip()[:300]))
    return p.stdout


def norm_path(p):
    """git 的改名记法 `a/{旧 => 新}/b` 和 `旧 => 新` 都取改名后的那一侧，
    并把整条被 C 引号包起来的路径还原。"""
    p = p.strip()
    if len(p) > 1 and p.startswith('"') and p.endswith('"'):
        p = p[1:-1].replace('\\\\', '\\').replace('\\"', '"')
    if " => " in p:
        if "{" in p:
            pre, rest = p.split("{", 1)
            mid, post = rest.split("}", 1)
            p = pre + mid.split(" => ")[1] + post
        else:
            p = p.split(" => ")[1]
    return p.strip()


def load_history(since):
    """默认分支上每条提交动了哪些文件，git log 顺序（**新的在前**）。只读 tree
    不读 blob，在 blobless 克隆上不联网。调用方要记住这个顺序。

    `-m --first-parent`：真正的合并提交默认不输出任何文件，这样才取到它相对第一
    父的改动。`--first-parent` 也把遍历限制在第一父链上，所以只经某次合并的第二
    父到达的提交不在这个列表里（它们的改动已经含在那次合并相对第一父的改动里，
    所以文件和行数不受影响；受影响的只有按标题扫描的回退，那种提交由
    `load_subjects` 单独读）。只有 squash 合并的历史上，这个列表就是全部提交。"""
    out = git("log", "--name-only", "-m", "--first-parent",
              "--format=%x01%H%x02%cI%x02%s", "--since=" + since, REF)
    commits, cur = [], None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            sha, date, subject = line[1:].split("\x02", 2)
            cur = {"sha": sha, "date": T(date), "subject": subject, "files": {}}
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur["files"][norm_path(line)] = None
    return commits


def load_subjects(since):
    """区间内可达的每条提交的 sha 和标题，**不限第一父链**。回退只看标题，所以
    要能看见只经某次合并的第二父到达的提交（`load_history` 看不到它们）。不读
    tree 也不读 blob。"""
    out = git("log", "--format=%x01%H%x02%s", "--since=" + since, REF)
    res = []
    for line in out.split("\n"):
        if line.startswith("\x01"):
            sha, subject = line[1:].split("\x02", 1)
            res.append({"sha": sha, "subject": subject})
    return res


def load_numstat(shas):
    """只对点名的提交取行数。numstat 要读 blob，blobless 克隆会按需从远端拉，
    所以这里只喂合并提交，不要对整段历史跑（那会按提交个数逐次联网）。

    `-m --first-parent` 同 `load_history`：合并提交要它才有行数。"""
    if not shas:
        return {}
    out = git("log", "--no-walk", "--numstat", "-m", "--first-parent",
              "--format=%x01%H", *shas)
    res, cur = {}, None
    for line in out.split("\n"):
        if line.startswith("\x01"):
            cur = line[1:].strip()
            res.setdefault(cur, {})
        elif line.strip() and cur is not None:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            path = norm_path(path)
            try:
                res[cur][path] = (int(a), int(d))
            except ValueError:  # 二进制
                res[cur][path] = (0, 0)
    return res


def head_files():
    return set(x for x in git("ls-tree", "-r", "--name-only", REF).split("\n") if x)


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
    """合进默认分支的那个提交。标题以 `(#N)` 结尾的是 squash，`Merge pull request #N`
    开头的是非 squash 合并；两种都认，别的提交（例如正文里提到 #N 的）不算。"""
    suffix = "(#%d)" % pr_number
    merge = "Merge pull request #%d " % pr_number
    for c in commits:
        if c["subject"].endswith(suffix) or c["subject"].startswith(merge):
            return c
    return None


def summarize(rows, commits, present_at_head, lines):
    """把每条线的合并提交翻成指标。一个文件也没有的线不进结果：报告把它按
    「合入认不出」和「认出来了但没有可计的文件」分开报数，不当作记 0 个文件。"""
    out = []
    for i, r in enumerate(rows):
        files = collections.defaultdict(lambda: [0, 0])  # path -> [add, del]
        first_tree, later_tree = set(), set()
        first_code, later_code = set(), set()
        found = [merge_commit(commits, pr) for pr in r["prs"]]
        for j, c in enumerate(found):
            if c is None:
                continue
            for p, (a, d) in lines.get(c["sha"], {}).items():
                files[p][0] += a
                files[p][1] += d
                (first_tree if j == 0 else later_tree).add(module_of(p))
                (first_code if j == 0 else later_code).add(module_of(p))
        if not files:
            continue
        # 首个合并提交没认出来时，first_tree 是空的，任何模块都会读成「外扩」，
        # 所以这一条只能不判。
        multi = len(found) > 1 and found[0] is not None
        gone = sorted(p for p in files if p not in present_at_head)
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
            "merge_shas": [c["sha"] for c in found if c],
            "merges_declared": len(found),
            "merges_matched": sum(1 for c in found if c),
            "files": len(files),
            "added": sum(v[0] for v in files.values()),
            "removed": sum(v[1] for v in files.values()),
            "net": sum(v[0] for v in files.values()) - sum(v[1] for v in files.values()),
            "trees": dict(collections.Counter(module_of(p) for p in files)),
            "kinds": dict(collections.Counter(kind_of(p) for p in files)),
            "drift_modules": sorted(later_tree - first_tree) if multi else None,
            "drift_code": sorted((later_code - first_code) & CODE_TREES) if multi else None,
            "registry_files": sum(1 for p in files if registry_of(p)),
            "gone_files": gone,
            "survive_ratio": round((len(files) - len(gone)) / len(files), 3),
            "rework_files": [],
            "_paths": set(files),
            "reverted": False,
        })
    return out


def rework(rows, commits, subjects):
    """线的文件在它自己第一次合入之后被别的提交改过。

    `commits` 是 git log 顺序，索引越小越新，所以「之后」是索引更小的一侧。
    认不出首个合并提交的线不判返工。「别的提交」= 不是**这条线自己**的合并提交；
    另一条线的合并提交动了同一条线的文件，算这条线被返工。

    回退同理，但查的是 `subjects`（区间内可达的全部提交，不限第一父链）：一条标题
    带 `Revert` 的提交提到该线的 PR 号就算，除非它就是该线自己的合并提交（那种是
    这条线自己在做回退，不是被回退）。别的线的合并提交回退了它，照样算。
    """
    pos = {c["sha"]: k for k, c in enumerate(commits)}
    for r in rows:
        r["files_set"] = set()
        r["own"] = set(r["merge_shas"])
        for s in r["merge_shas"]:
            if s in pos:
                r["files_set"] |= set(commits[pos[s]]["files"])
        if r["merge_shas"]:
            first = r["merge_shas"][0]
            if first in pos:
                r["_base"] = pos[first]
    for k, c in enumerate(commits):
        for p in c["files"]:
            for r in rows:
                if (c["sha"] not in r["own"] and "_base" in r
                        and k < r["_base"] and p in r["files_set"]):
                    r["rework_files"].append(p)
    for r in rows:
        r["rework_files"] = sorted(set(r["rework_files"]))
        r["rework_registry"] = sum(1 for p in r["rework_files"] if registry_of(p))
        r["rework"] = len(r["rework_files"]) - r["rework_registry"]
        r.pop("files_set", None)
        r.pop("_base", None)
    for c in subjects:
        if not REVERT_RE.search(c["subject"]):
            continue
        for r in rows:
            if c["sha"] in r["own"]:
                continue
            if any("(#%d)" % p in c["subject"] for p in r["prs"]):
                r["reverted"] = True
    for r in rows:
        r.pop("own", None)
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
    name = {r["task_id"]: (r["number"] if r["number"] is not None else r["task_id"][:8])
            for r in rows}
    for ts in shared.values():
        for a in range(len(ts)):
            for b in range(a + 1, len(ts)):
                pairs[(name[ts[a]], name[ts[b]])] += 1
    return shared, pairs


def pct(values, p):
    """最近秩分位：p=0.5 取第 ⌈0.5n⌉ 个，不做插值。"""
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, max(0, math.ceil(p * len(v)) - 1))]


def drop_report(cohort, data, commits):
    """上表之外的线：合入认不出的次数（unmatched）、一次都没认出的线数
    （never_matched）、认出来了但没有可计文件的线数（no_files）。"""
    matched = {r["task_id"]: sum(1 for p in r["prs"] if merge_commit(commits, p))
               for r in cohort}
    unmatched = sum(len(r["prs"]) - matched[r["task_id"]] for r in cohort)
    never_matched = sum(1 for r in cohort if not matched[r["task_id"]])
    no_files = len(cohort) - never_matched - len(data)
    return unmatched, never_matched, no_files


def render(rows, never, dropped, start, end, as_of, pairs):
    """报告文本。每一句都从 rows 里算出来，不写死任何数字。

    `dropped` 是 `drop_report` 的三元组，只用来写表外那几种线。"""
    n = len(rows)
    files = [r["files"] for r in rows]
    net = [r["net"] for r in rows]
    code_files = [r["kinds"].get("代码", 0) for r in rows]
    test_files = [r["kinds"].get("测试", 0) for r in rows]
    multi = [r for r in rows if r["drift_modules"] is not None]
    drift_code = [r for r in multi if r["drift_code"]]
    drift_any = [r for r in multi if r["drift_modules"]]
    reg_heavy = [r for r in rows if r["registry_files"] * 2 >= r["files"] and r["files"]]
    dead_code = [r for r in rows if any(not registry_of(p) for p in r["gone_files"])]
    rew = [r for r in rows if r.get("rework")]
    rew_reg = [r for r in rows if r.get("rework_registry")]
    rev = [r for r in rows if r["reverted"]]
    tot_reg = sum(r["registry_files"] for r in rows)
    tot_files = sum(files) or 1
    merged_twice = [r for r in rows if r["merges_declared"] >= 2]
    unmatched, never_matched, no_files = dropped
    top_pairs = pairs.most_common(5) if isinstance(pairs, collections.Counter) else []

    def p(v):
        return "—" if v is None else str(v)

    L = []
    L.append("# 任务线的改动落到了哪里（%s ~ %s，观察截止 %s）" % (start, end, as_of))
    L.append("")
    L.append("队列：窗口内第一次合入的 **%d 条线**，共 %d 次合入（其中 %d 次在默认"
             "分支上认出了对应提交）；同期有卡但从未合入的 %d 条。"
             % (n, sum(r["merges_declared"] for r in rows),
                sum(r["merges_matched"] for r in rows), never))
    if unmatched or never_matched or no_files:
        bits = []
        if unmatched:
            bits.append("%d 次合入在默认分支上认不出对应提交（那次合入不计入改动，"
                        "但它所属的线仍在上面这些数里）" % unmatched)
        if never_matched:
            bits.append("%d 条线一次合并都没认出（这些线不计入上表，也不计入任何一项）"
                        % never_matched)
        if no_files:
            bits.append("%d 条线的合入认出来了但没有可计的文件（这些线不计入上表）"
                        % no_files)
        L.append("（%s。）" % "；".join(bits))
    L.append("口径见脚本头。窗口短，返工只看得到窗口内的后续提交。")
    L.append("")
    L.append("## 一次改动多大")
    L.append("")
    L.append("| | 中位 | 四分位 | 最大 |")
    L.append("|---|---|---|---|")
    for label, values in (("改动文件数", files), ("净增行数", net),
                          ("其中代码文件", code_files), ("其中测试文件", test_files)):
        L.append("| %s | %s | %s–%s | %s |"
                 % (label, p(pct(values, .5)), p(pct(values, .25)), p(pct(values, .75)),
                    max(values) if values else 0))
    L.append("")
    L.append("- 生成物与登记表（i18n、openapi、baseline、ratchet 这类每次都会被带着重算"
             "的文件）占全部改动文件的 **%d%%**（%d/%d）；改动文件一半以上是生成物的线 "
             "%d 条。" % (round(100 * tot_reg / tot_files), tot_reg, tot_files, len(reg_heavy)))
    L.append("- 只有 1 次合入的线 %d 条，≥2 次的 %d 条。"
             % (n - len(merged_twice), len(merged_twice)))
    L.append("")
    L.append("## 打在该解决的问题上了吗")
    L.append("")
    if multi:
        L.append("- 首个合入认得出的多条合入线里（%d 条），后续合并动到了**另一个代码树**"
                 "（backend/ 与 frontend/ 之间互相跨界）的 **%d** 条。"
                 % (len(multi), len(drift_code)))
        L.append("- 放宽到任意模块（含 docs/、.github/、.claude/ 等收尾件）：**%d** 条。"
                 "这一项把同一条线的收尾也算成外扩，只作参考。" % len(drift_any))
    else:
        L.append("- 窗口内没有多条合并的线，这一项算不出来。")
    L.append("- 只动代码不碰测试的线 %d 条；碰了文档的线 %d 条。"
             % (sum(1 for r in rows if r["kinds"].get("测试", 0) == 0),
                sum(1 for r in rows if r["kinds"].get("文档", 0))))
    L.append("")
    L.append("## 留下来了吗")
    L.append("")
    L.append("- 改动文件今天仍在默认分支上：%d/%d 条线是 100%%。"
             % (sum(1 for r in rows if not r["gone_files"]), n))
    L.append("- 去掉生成物后仍有文件消失的线：%d 条（消失的多是改名或被重算掉的登记表）。"
             % len(dead_code))
    L.append("- 第一次合入之后，改动过的文件又被别的提交改过（返工，去掉生成物）："
             "%d 条线、共 %d 个文件。" % (len(rew), sum(r["rework"] for r in rew)))
    if rew_reg:
        L.append("- 只看生成物被再改：%d 条线（几乎每条线都在重算同一批登记表）。" % len(rew_reg))
    L.append("- 读到的历史里有回退提交指向的线：%d 条。" % len(rev))
    L.append("")
    L.append("## 同一片文件上的线")
    L.append("")
    if top_pairs:
        L.append("- 共 %d 对线在同一批文件上相遇。" % len(pairs))
        for (a, b), c in top_pairs:
            L.append("- #%s 与 #%s：共用 %d 个文件。" % (a, b, c))
    else:
        L.append("- 没有被两条线共用的文件。")
    return "\n".join(L)


def _fixture():
    """自测的输入。提交列表按 git log 顺序摆：**新的在前**，和 load_history 一致。"""
    cards = [
        {"task_id": "A", "pr_number": 1, "pr_merged_at": "2026-10-08T00:00:00Z"},
        {"task_id": "A", "pr_number": 2, "pr_merged_at": "2026-10-09T00:00:00Z"},
        {"task_id": "B", "pr_number": 3, "pr_merged_at": "2026-10-08T12:00:00Z"},
        {"task_id": "C", "pr_number": 4, "pr_merged_at": "2026-10-01T00:00:00Z"},
        {"task_id": "D", "pr_number": 5, "pr_merged_at": None},          # 从未合入
        {"task_id": "E", "pr_number": 6, "pr_merged_at": "2026-10-20T00:00:00Z"},  # 窗口之后
        {"task_id": "F", "pr_number": 8, "pr_merged_at": "2026-10-08T06:00:00Z"},
        {"task_id": "J", "pr_number": 21, "pr_merged_at": "2026-10-15T00:00:00Z"},  # 恰好在上界
    ]
    tasks = [{"id": "A", "number": 10, "title": "a", "status": "closed"},
             {"id": "B", "number": 11, "title": "b", "status": "closed"},
             {"id": "E", "number": 12, "title": "e", "status": "closed"},
             {"id": "F", "number": 13, "title": "f", "status": "closed"},
             {"id": "J", "number": 14, "title": "j", "status": "closed"}]
    commits = [   # 索引越小越新
        {"sha": "s10", "date": None, "subject": 'Revert "feat: x (#1)"',
         "files": {"frontend/a.ts": (0, 10)}},
        {"sha": "s9", "date": None, "subject": "chore: later",
         "files": {"frontend/a.ts": (1, 1)}},
        {"sha": "s2", "date": None, "subject": "fix: y (#2)",
         "files": {"backend/b.py": (3, 0)}},
        {"sha": "s3", "date": None, "subject": "feat: z (#3)",
         "files": {"frontend/a.ts": (2, 0)}},
        {"sha": "s8", "date": None, "subject": "feat: revert the old default (#8)",
         "files": {"other/c.py": (4, 0)}},
        {"sha": "s1", "date": None, "subject": "feat: x (#1)",
         "files": {"frontend/a.ts": (10, 1)}},
        {"sha": "s0", "date": None, "subject": "chore: before the line merged",
         "files": {"backend/b.py": (5, 0)}},
        {"sha": "s7", "date": None, "subject": "Merge pull request #7 from x/y",
         "files": {"other/d.py": (1, 0)}},
    ]
    return cards, tasks, commits


def _subjects(commits):
    """`load_subjects` 的形状：sha + 标题，没有文件。"""
    return [{"sha": c["sha"], "subject": c["subject"]} for c in commits]


def self_test():
    cards, tasks, commits = _fixture()
    subs = _subjects(commits)
    rows, never = build_cohort(cards, tasks,
                               datetime(2026, 10, 7, tzinfo=timezone.utc),
                               datetime(2026, 10, 15, tzinfo=timezone.utc))
    # 窗口两端都要咬：10-01 的 C、10-20 的 E、以及**恰好落在上界**的 J 都不进队列；
    # D 没合入过。
    assert [r["number"] for r in rows] == [10, 13, 11], rows
    assert never == 1, never

    assert merge_commit(commits, 1)["sha"] == "s1"     # 不是正文里带 (#1) 的 s10
    assert merge_commit(commits, 7)["sha"] == "s7"     # 非 squash 的 `Merge pull request #7 …`
    assert merge_commit(commits, 99) is None

    assert kind_of("frontend/a.spec.ts") == "测试"
    assert kind_of("backend/tests/unit/test_x.py") == "测试"
    assert kind_of("docs/manual/a.md") == "文档"
    assert kind_of(".claude/scripts/x.py") == "脚本"
    assert kind_of("backend/app/main.py") == "代码"
    assert module_of("backend/app/main.py") == "backend/"
    assert module_of("README.md") == "(root)"
    assert norm_path("dir/{old name => new name}.py") == "dir/new name.py"
    assert norm_path('"we\\"ird.txt"') == 'we"ird.txt'
    assert registry_of("frontend/src/i18n/messages/zh-CN/a.json")
    assert not registry_of("backend/app/main.py")
    assert pct([1, 2, 3, 4], .5) == 2 and pct([1, 2, 3, 4], .75) == 3
    assert pct([], .5) is None

    lines = {c["sha"]: c["files"] for c in commits}
    present = {"frontend/a.ts", "backend/b.py", "other/c.py"}
    out = summarize(rows, commits, present, lines)
    a = [r for r in out if r["task_id"] == "A"][0]
    assert a["files"] == 2 and a["net"] == 12, a
    assert a["kinds"] == {"代码": 2}, a["kinds"]          # 按文件去重，不是按提交次数
    assert a["drift_modules"] == ["backend/"], a["drift_modules"]
    assert a["merges_declared"] == 2 and a["merges_matched"] == 2, a
    b = [r for r in out if r["task_id"] == "B"][0]
    assert b["drift_modules"] is None, b["drift_modules"]  # 只合并过一次，不判外扩
    f = [r for r in out if r["task_id"] == "F"][0]
    assert f["drift_modules"] is None, f["drift_modules"]  # 首个合并提交在窗口外，不判

    out = rework(out, commits, subs)
    a = [r for r in out if r["task_id"] == "A"][0]
    # 合入之后被动过的只有 frontend/a.ts（s3、s9、s10）；更早的 s0 动 backend/b.py 不算。
    assert a["rework_files"] == ["frontend/a.ts"], a["rework_files"]
    assert a["rework"] == 1, a
    assert a["reverted"] is True, a
    f = [r for r in out if r["task_id"] == "F"][0]
    assert f["reverted"] is False, f     # 自己的标题里有 revert，不是被回退

    # 返工和回退都只排除**这条线自己**的合并提交，不排除别的线的（口径第 31–35 行）。
    # n1 提到 (#9) 但不是 revert；r1 是线 M 的合并提交，回退了线 L 的 #1；m1 是线 L
    # 自己的合入。行 L 应当被 r1 返工、被 r1 回退；行 M 两样都不算。
    cross = [
        {"sha": "n1", "date": None, "subject": "chore: mention (#9) again",
         "files": {"frontend/y.ts": (1, 0)}},
        {"sha": "r1", "date": None, "subject": 'Revert "feat: y (#1)" (#9)',
         "files": {"frontend/x.ts": (0, 5)}},
        {"sha": "m1", "date": None, "subject": "feat: mine (#1)",
         "files": {"frontend/x.ts": (5, 0)}},
    ]

    def _row(tid, number, prs, shas):
        return {"task_id": tid, "number": number, "prs": prs, "merge_shas": shas,
                "rework_files": [], "reverted": False}

    l_row, m_row = _row("L", 20, [1], ["m1"]), _row("M", 21, [9], ["r1"])
    test = rework([l_row, m_row], cross, _subjects(cross))
    assert test[0]["rework_files"] == ["frontend/x.ts"], test[0]
    assert test[0]["reverted"] is True, test[0]
    assert test[1]["rework_files"] == [], test[1]
    assert test[1]["reverted"] is False, test[1]

    # 回退查的是 load_subjects 那份（不限第一父链），不是 commits 那份：只经合并第二
    # 父到达的回退提交不在 commits 里，仍要被看见。
    side = [{"sha": "m1", "date": None, "subject": "feat: mine (#1)",
             "files": {"frontend/x.ts": (5, 0)}}]
    side_subs = _subjects(side) + [{"sha": "rv", "subject": 'Revert "feat: x (#1)" (#9)'}]
    t2 = rework([_row("L", 20, [1], ["m1"]), _row("M", 21, [9], ["rv"])], side, side_subs)
    assert t2[0]["reverted"] is True, t2[0]
    assert t2[0]["rework_files"] == [], t2[0]     # rv 不在 commits 里，不参与返工
    assert t2[1]["reverted"] is False, t2[1]

    # kinds 按文件去重：线 I 的两次合并都动 frontend/a.ts，只算一个代码文件。
    i_line = summarize([{"task_id": "I", "number": 92, "title": "i", "owner": None,
                         "agent": None, "status": "closed",
                         "first_merged": "2026-10-08T00:00:00+00:00",
                         "prs": [1, 3]}], commits, present, lines)[0]
    assert i_line["files"] == 1 and i_line["kinds"] == {"代码": 1}, i_line

    shared, pairs = overlap(out)
    assert shared == {"frontend/a.ts": ["A", "B"]}, shared   # A 与 B 撞同一个文件
    assert pairs == {(10, 11): 1}, pairs

    # 首个合并提交认不出来时不判外扩；一次都认不出来的线不进结果，由 drop_report
    # 单独报数。
    def _line(prs, tid="G"):
        return [{"task_id": tid, "number": 90, "title": "g", "owner": None, "agent": None,
                 "status": "closed", "first_merged": "2026-10-08T00:00:00+00:00", "prs": prs}]

    g = summarize(_line([99, 3]), commits, present, lines)[0]
    assert g["drift_modules"] is None and g["drift_code"] is None, g
    assert g["merges_declared"] == 2 and g["merges_matched"] == 1, g
    assert summarize(_line([99]), commits, present, lines) == []

    out = summarize(rows, commits, {"frontend/a.ts"}, lines)   # a.ts 还在，b.py 没了
    a = [r for r in out if r["task_id"] == "A"][0]
    assert a["gone_files"] == ["backend/b.py"], a

    # drop_report：G 的 #99 和 H 的 #98 认不出（2 次），H 一次都没认出来（1 条线），
    # 没有「认出来了但没有可计文件」的线。
    two = rows + _line([99, 3], "G") + _line([98], "H")
    two_out = summarize(two, commits, present, lines)
    dropped = drop_report(two, two_out, commits)
    assert dropped == (2, 1, 0), dropped

    md = render(two_out, never, dropped, "2026-10-07", "2026-10-15",
                "2026-10-15 00:00 UTC", pairs)
    assert "队列：窗口内第一次合入的 **%d 条线**" % len(two_out) in md, md
    assert "生成物与登记表" in md and "返工" in md
    # 表外那句不能自相矛盾：说得出「N 条线一次合并都没认出」，就得说得出是几次。
    assert "2 次合入在默认分支上认不出对应提交" in md, md
    assert "1 条线一次合并都没认出" in md, md
    # 「几次合入」按平台的已合并卡算，不按认出来的提交数：G 有两张已合并卡、只认出
    # 一个提交，仍算 ≥2 次那一边。队列那句也照这个口径，另外写明认出了几次。
    assert "共 6 次合入（其中 5 次在默认分支上认出了对应提交）" in md, md
    assert "只有 1 次合入的线 2 条，≥2 次的 2 条。" in md, md

    # 「认出来了但没有可计文件」那一条要有自己的说法（合入为空 diff 时走到）。
    empty_commit = [{"sha": "e1", "date": None, "subject": "feat: empty (#50)",
                     "files": {}}]
    empty = summarize(_line([50], "K"), empty_commit, present, {})
    assert empty == [], empty
    d3 = drop_report(_line([50], "K"), empty, empty_commit)
    assert d3 == (0, 0, 1), d3
    md3 = render([], 0, d3, "2026-10-07", "2026-10-15", "2026-10-15 00:00 UTC", pairs)
    assert "1 条线的合入认出来了但没有可计的文件（这些线不计入上表）" in md3, md3

    # JSON 的键名是下游的接口，改名要连自检一起改。
    got = payload(two_out, never, dropped, pairs, "2026-10-07", "2026-10-15",
                  "2026-10-15 00:00 UTC")
    assert sorted(got) == ["as_of", "cohort", "never_matched_lines", "never_merged",
                           "no_files_lines", "overlap_pairs", "ref",
                           "unmatched_merges", "window"], sorted(got)
    assert got["unmatched_merges"] == 2 and got["never_matched_lines"] == 1, got

    _loader_self_test()
    print("self-test ok")


def _loader_self_test():
    """`load_history` / `load_numstat` 要真的打到 git 上：自检喂的是现成的文件清单，
    看不出这两个函数的参数对不对（合并提交默认不输出文件，非 squash 的合并只有带
    `-m --first-parent` 才取得到改动）。这里临时起一个仓库，做一次真的非 squash
    合并，再读回来。"""
    global REPO_PATH, REF
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="co-selftest-")
    old_path, old_ref = REPO_PATH, REF
    try:
        REPO_PATH, REF = tmp, "HEAD"

        def sh(*a):
            p = subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True)
            if p.returncode:
                raise SystemExit("自检的 git %s 失败：%s" % (a[0], p.stderr[:300]))
            return p.stdout

        sh("init", "-q", "-b", "main")
        sh("config", "user.email", "t@t"); sh("config", "user.name", "t")
        open(os.path.join(tmp, "a.txt"), "w").write("1\n")
        sh("add", "a.txt"); sh("commit", "-qm", "chore: base")
        sh("checkout", "-q", "-b", "feature")
        open(os.path.join(tmp, "b.txt"), "w").write("2\n")
        sh("add", "b.txt"); sh("commit", "-qm", "feat: side branch work")
        sh("checkout", "-q", "main")
        sh("merge", "-q", "--no-ff", "-m", "Merge pull request #7 from x/y", "feature")

        hist = load_history("2000-01-01")
        merge = merge_commit(hist, 7)
        assert merge is not None, [c["subject"] for c in hist]
        assert sorted(merge["files"]) == ["b.txt"], merge["files"]
        # 只经第二父到达的提交不在第一父链里，但回退扫描要看得见它的标题。
        assert not any(c["subject"] == "feat: side branch work" for c in hist), hist
        assert any(s["subject"] == "feat: side branch work"
                   for s in load_subjects("2000-01-01"))
        num = load_numstat([merge["sha"]])
        assert sorted(num[merge["sha"]]) == ["b.txt"], num
    finally:
        REPO_PATH, REF = old_path, old_ref
        shutil.rmtree(tmp, ignore_errors=True)


def payload(data, never, dropped, pairs, start, end, as_of):
    """`--json` 写出去的那份。和报告同一批数，键名变了就是下游的接口变了。"""
    return {
        "cohort": data,
        "never_merged": never,
        "unmatched_merges": dropped[0],
        "never_matched_lines": dropped[1],
        "no_files_lines": dropped[2],
        "overlap_pairs": {"%s-%s" % k: v for k, v in pairs.items()},
        "window": [start, end],
        "ref": REF,
        "as_of": as_of,
    }


def main(argv):
    if argv and argv[0] == "--self-test":
        return self_test()
    args, opts = [], {"--json": None, "--md": None, "--lookback-days": "7"}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in opts:
            if i + 1 >= len(argv):
                print("选项 %s 后面缺值" % a, file=sys.stderr)
                return 2
            opts[a] = argv[i + 1]
            i += 2
        elif a.startswith("--"):
            print("未知选项 %s" % a, file=sys.stderr)
            return 2
        else:
            args.append(a)
            i += 1
    if len(args) != 3:
        print(__doc__)
        return 2
    room, start, end = args
    start_d, end_d = parse_day(start), parse_day(end)
    if end_d <= start_d:
        print("窗口的上界必须晚于下界", file=sys.stderr)
        return 2
    since = (start_d - timedelta(days=int(opts["--lookback-days"]))).date().isoformat()
    tok = login()
    cards = unwrap(get(tok, "/topics/%s/accept-card" % room))
    tasks = unwrap(get(tok, "/topics/%s/tasks?limit=200" % room))
    cohort, never = build_cohort(cards, tasks, start_d, end_d)
    commits = load_history(since)
    present = head_files()
    shas = [c["sha"] for r in cohort for c in
            (merge_commit(commits, p) for p in r["prs"]) if c]
    data = summarize(cohort, commits, present, load_numstat(shas))
    data = rework(data, commits, load_subjects(since))
    shared, pairs = overlap(data)
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dropped = drop_report(cohort, data, commits)
    md = render(data, never, dropped, start, end, as_of, pairs)
    if opts["--json"]:
        json.dump(payload(data, never, dropped, pairs, start, end, as_of),
                  open(opts["--json"], "w"), ensure_ascii=False, indent=1)
    if opts["--md"]:
        open(opts["--md"], "w").write(md)
    if not opts["--json"] and not opts["--md"]:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
