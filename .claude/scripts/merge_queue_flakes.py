#!/usr/bin/env python3
"""合并队列里哪些用例在偶发判红 —— 一条命令出两张榜。

`merge_group` 一次把几条 PR 合到一起跑，同一个红灯其实有两个来源：一条用例自
己偶发（时序、共享资源），和几条 PR 的改动合起来把某个接口弄坏。两者的处置完全
相反 —— 前者记下来重跑，后者是这次合入的真故障，得把涉及的 PR 挡住 —— 但它们在
CI 上都只是「一个红的作业」。混在一张榜里，真故障会被当成又一次偶发忽略掉，所以
这个脚本存在的唯一理由是**把这两类红分开**。

分法是「同一个错误签名命中多少条不同用例」。一条用例零星报某个错，是偶发；一批
互不相干的用例在**同一批运行里**同时报同一个错（改了个接口、缺了个关键字参数、
导入坏掉），几乎不可能是巧合，那是组合出来的真故障。跨用例数达到阈值的签名在榜上
标 `[疑似合并冲突]`，提醒看榜的人先去看它，而不是去重跑那些用例。

那两张榜只做分类，不替你下结论。下一段路要靠 `KNOWN_FLAKES` —— 一份已经查清的偶
发档案，每一条对应一次查到底的红灯（收尾期间排队的消息被丢、跑者死亡后的 orphaned
输入断言、planner 钉查询计划、合并队列的合成运行被无关偶发拖红）。命中档案的红灯
不必再当新故障查，但**命中不等于偶发**：判据只有一句话，同一条 workflow、同一次队列
入场既红过又绿过，才算坐实；只见过红的，按真故障读。少了这一步，一条偶发和一条真回
归在数据里长得一模一样 —— 都是「一个红的作业」，把后者当偶发放下就是漏一次真故障。

判据里的「同一个 SHA」指**这次入场的身份**，不是运行自己报的 `head_sha`。运行报的 head_sha
是它当时合成的那个合并提交，每条队列条目一个，实测 300 次 `merge_group` 运行是 300 个互不
相同的值 —— 拿它当判据永远判不出坐实。跨入场重复的是分支名
`gh-readonly-queue/main/pr-<n>-<sha>` 的整个后半段：同一个 PR 从同一条基线再入场一次，名字
逐字相同（同一份实测：300 次入场只有 292 个不同的名字，其中 1 个红绿都有 —— #3905 那次；
290 个 head_sha 出现在别人的分支名上）。只按那个 sha 分组要宽得多 —— 同一条基线上常挂着几
个不同 PR 的条目，各自带着自己的改动，那一档有 6 组红绿都有，多出来的 5 组是不同 PR，不是
同一份代码，不能拿来坐实。`Run.subject` 就是这个名字。

这条判据有一处够不着：同一个 PR 在同一条基线上推了新提交再入场，名字也一样，那么红转绿可能
是修好了而不是偶发。所以它只用来给 `KNOWN_FLAKES` 里已经认出来的偶发坐实，单独不下任何结论。

    python3 .claude/scripts/merge_queue_flakes.py [--repo R] [--runs N] [--top K]
        [--conflict-min-tests M]
    python3 .claude/scripts/merge_queue_flakes.py --json-dir DIR ...   # 离线样本
    python3 .claude/scripts/merge_queue_flakes.py --self-test     # 自测：内嵌样本 + 读盘

取数走 `gh api`（凭据由平台注入，本脚本不读也不打印 token）：先按 `event=merge_group`
分页拿运行列表，只要 `conclusion == failure` 的运行，再逐个拿作业，再逐个拿失败作业
的日志。`--json-dir` 换成从磁盘读同样三步的原始返回，用来在没网时对着抓下来的样本
跑；目录布局：

    DIR/runs*.json             gh api repos/{repo}/actions/runs?event=merge_group
                               的分页原文，每页 {"total_count":..., "workflow_runs":[...]}
    DIR/jobs/<run_id>.json     gh api repos/{repo}/actions/runs/<run_id>/jobs 的原文
    DIR/logs/job-<job_id>.log  失败作业的原始日志

分母是**最近 N 次 merge_group 运行的总数**，不只是失败的：偶发的比例要拿全部运行
当分母，否则「4 次红」到底是 4/26 还是 4/300 就分不清了。

阈值的道理：一条偶发只脏一条用例，于是把阈值定在「好几条」之上 —— 默认 5 条不同
用例共用一个签名才算组合出来的真故障，低于它的仍是零星红。调这个数要用
`--conflict-min-tests`，别改这里的默认值：自测按默认值断言那条跨用例签名被标出来。

只读：不写任何文件，也不跑 git。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import contextlib
from dataclasses import dataclass, field
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# 运行的红：只有 conclusion == failure 才算这条 merge_group 运行失败。
FAILED_RUN = frozenset({"failure"})
# 作业的红：比运行宽松一档，超时和 action_required 同样是「这个作业没绿」。
FAILED_JOB = frozenset({"failure", "timed_out", "action_required"})
# 运行的绿：只有整轮 success 才算。cancelled 是「不知道」，判据拿它当不了证据。
GREEN_RUN = frozenset({"success"})

#: 合并队列的分支名就是这次入场的身份：`gh-readonly-queue/main/pr-<n>-<sha>`。同一个 PR
#: 从同一条基线再入场一次，名字逐字相同。
_QUEUE_BRANCH = re.compile(r"^gh-readonly-queue/[^/]+/(pr-\d+-[0-9a-f]{7,40})$")

#: 跨用例数达到几条才算组合出来的真故障。一条偶发只脏一条用例；真故障（缺关键字
#: 参数、导入坏掉、接口改了）会一次点亮一批互不相干的用例。5 是「明显不止一条」
#: 的下限，又不至于把两条用例撞同一个常见断言算进来。
DEFAULT_CONFLICT_MIN_TESTS = 5
DEFAULT_TOP = 20
DEFAULT_RUNS = 300

#: pytest 短摘要里的 `FAILED <节点> - <错误>`。前面的时间戳前缀、xdist 的 `[gw2]`
#: 用什么搜都行，因为锚点是 `FAILED` 这个词本身；节点里必须含 `::`，否则是别的
#: 输出里碰巧出现的 `FAILED`，不是一条用例。`##[error]` 那行用的是破折号 `—`
#: 而不是 ` - `，天然不匹配，不会重复计一次。
_FAILED_LINE = re.compile(r"FAILED\s+(\S+)\s+-\s+(.*)$")
_SIG_WIDTH = 80


@dataclass
class Job:
    """一个作业：判没判红，以及（判红时）它的原始日志。"""

    id: int
    conclusion: str | None
    log: str


@dataclass
class Run:
    """一条 merge_group 运行：结论、它的作业（只有失败的才带日志），和它测的提交。"""

    id: int
    conclusion: str | None
    created_at: str
    jobs: list[Job] = field(default_factory=list)
    head_sha: str = ""
    head_branch: str = ""
    workflow: str = ""
    event: str = ""

    @property
    def subject(self) -> str:
        """这次入场的身份 —— 判据里说的「同一个 SHA」就是它。

        取队列分支名里的 `pr-<n>-<sha>`：同一个 PR 从同一条基线再入场一次（红过之
        后被移出队列、重新采纳是最常见的成因），名字逐字相同，那才是同一份代码跑
        了两遍。不能只取 sha：同一条基线上常常挂着几个不同 PR 的条目，它们各自带
        着自己的改动，不是同一份代码 —— 实测同一窗口里，只按 sha 分组有 6 组红绿
        都有，按 PR 号加 sha 只剩 1 组。也不能拿运行自己报的 head_sha：那是它当时
        合成的合并提交，一条条目一个（实测 300 次运行 300 个不同值），永远判不出
        坐实。非队列运行（`pull_request` 那类）没有这层身份，才回落 head_sha。
        """
        match = _QUEUE_BRANCH.match(self.head_branch or "")
        return match.group(1) if match else (self.head_sha or "")


def extract_failures(log: str) -> list[tuple[str, str]]:
    """从一份日志里抽出 (用例 id, 错误签名)，按日志内去重。

    同一份日志里同一条用例可能不止打一次 `FAILED`（`-x` 之前、重试、短摘要），
    去重是为了让「次数」是「几条运行红过」的量级，而不是日志行数。
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in log.splitlines():
        match = _FAILED_LINE.search(line)
        if not match:
            continue
        node, signature = match.group(1).strip(), match.group(2).strip()
        if "::" not in node:
            continue
        key = (node, signature)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


@dataclass(frozen=True)
class KnownFlake:
    """一条已经查清的偶发：命中它的红灯不必再当新故障查。

    命中的判据**两边都要**：用例节点 id 含 `tests` 里的一个片段，错误签名含
    `signatures` 里的一个片段（`signatures` 留空就只看用例）。只看用例不核签名，
    一条被改写过断言的用例会借着旧档案溜走；只看签名不核用例，别的用例撞上同一句
    话也会被认领。两边都收窄，错了才是「多查一次」，而不是「漏一条真故障」。

    `tests` 留空 = 运行级档案：按 `events` 圈出可能说起的那几轮运行，给的是这一轮
    该怎么读，而不是「哪条用例是偶发」—— 那个问题一条运行答不了。所以它只圈范围，
    判定在 `judge_known_flakes` 里另做：一轮运行要被列出来，还得真红在一条**坐实**的
    用例上。光按事件认领会把合出来的真故障也挂进「红在无关偶发上」。
    """

    id: str
    title: str
    action: str
    seen: str
    tests: tuple[str, ...] = ()
    signatures: tuple[str, ...] = ()
    events: tuple[str, ...] = ()

    def matches_case(self, node: str, signature: str) -> bool:
        if not self.tests:
            return False
        if not any(fragment in node for fragment in self.tests):
            return False
        return not self.signatures or any(
            fragment in signature for fragment in self.signatures
        )

    def matches_run(self, run: Run) -> bool:
        """这一轮运行属不属于这条运行级档案**可能说起**的范围（事件对得上）。

        对得上不等于认领：`judge_known_flakes` 还要这轮真红在一条坐实的用例上才
        列出来。这里只圈范围，别把判定的活挪进来。
        """
        return not self.tests and run.event in self.events


#: 已经查到底的偶发。每一条的 `seen` 是当时核实过的事实，不是猜测；写进来的是
#: 「再遇到就少走一趟」的东西，所以宁可窄 —— 匹配不上只是多查一次。
KNOWN_FLAKES: tuple[KnownFlake, ...] = (
    KnownFlake(
        id="integration-writeup-message-not-dropped",
        title="收尾期间排队的消息没保住（assert 'closed' == 'open'）",
        tests=(
            "test_a_last_step_lands_while_work_goes_on.py::"
            "test_a_message_sent_while_the_task_is_written_up_is_not_dropped",
        ),
        signatures=("'closed' == 'open'",),
        action=(
            "先核自己的 diff 碰不碰得到任务收尾和消息投递这条链路；碰不到就重跑失败"
            "作业，同一个 head 上再红就别再重跑。"
        ),
        seen=(
            "2026-10-16 有三条互不相关的线在同一分片撞到它（两条只改测试文件），重跑"
            "和下一次 Required CI 都全绿，同一批 1174 个用例。"
        ),
    ),
    KnownFlake(
        id="integration-orphaned-input-assertion",
        title="跑者死亡后被中断的输入没标成 orphaned",
        tests=(
            "test_claude_session_records.py::"
            "test_a_message_after_a_dead_runner_gets_a_turn",
        ),
        # 断言写在推导式里（`assert all(row.termination == "orphaned" ...)`），
        # pytest 改不了推导式内的表达式，短摘要只打 `assert False` —— 真日志里就是
        # 这五个字，写「orphaned」一个字也匹配不上。
        signatures=("assert False",),
        action=(
            "先核自己的 diff 碰不碰得到跑者死亡到输入收尾那条链路；碰不到就重新采纳"
            "进队 —— --reruns 1 对它无效。"
        ),
        seen=(
            "2026-09 由 #3401 加入；2026-10-16 在同一台机器上改前/改后各跑 30 次，红 "
            "2 次和 3 次，同一行同一句断言，与任何改动无关。合并提交 1e22b6cd 就是被"
            "它拖红三项而移出队列的。"
        ),
    ),
    KnownFlake(
        id="indexed-rows-planner-assertion",
        title="查询计划里出现了 GroupAggregate（钉的是 planner 的形状）",
        tests=(
            "test_indexed_rows.py::"
            "test_the_speaking_again_reads_do_not_reach_past_the_question",
        ),
        signatures=("GroupAggregate",),
        action=(
            "别去调 SQL —— 那只是把这个 flake 挪到别处。要动就动测试本身：改成钉索引"
            "是否存在，或者钉更粗的判据。"
        ),
        seen=(
            "2026-10-13 在三个不同的 merge commit 上各红一次，报出的成本值每次不同"
            "（832.09 对 993.09）—— 那是 Postgres planner 按统计信息做的选择，不是代"
            "码变了。同一天另有 52 次 merge group 是绿的。"
        ),
    ),
    KnownFlake(
        id="merge-group-synthetic-run",
        title="合并队列的合成运行被无关偶发拖红时，PR 被移出队列",
        events=("merge_group",),
        action=(
            "别 push-fix 加提交（「修好提交后再推」那句话不是这个意思，照做只会清掉已"
            "有批准、扩大 scope）。先读 PR 的 state/mergedAt，已合并就停手；没合并就"
            "重新采纳让它回队 —— 入队发生在采纳那一刻。"
        ),
        seen=(
            "2026-10-16 #3905 自己的 28 项检查全绿、MERGEABLE，红的是 merge_group 那"
            "遍。同一天 #1628 自己的运行也红在同一条用例上，但 #1666 的队列分支建在它"
            "的合并提交上跑绿了，整段前缀一起 fast-forward 落地 —— 自己的运行红了不"
            "等于没落地。"
        ),
    ),
)

#: 档案按 id 取回 —— 判定那一层拿着 id 要回原档案读处置和依据。
_KNOWN_BY_ID: dict[str, KnownFlake] = {flake.id: flake for flake in KNOWN_FLAKES}


def known_flake_for_case(node: str, signature: str) -> KnownFlake | None:
    """这条用例红在这句话上，是不是一份已经查清的档案。"""
    for flake in KNOWN_FLAKES:
        if flake.matches_case(node, signature):
            return flake
    return None


def same_sha_green(runs: list[Run], reds: list[Run]) -> dict[str, int]:
    """红过的那些入场里，哪些**在同一条 workflow 上**还有一次绿；返回 入场 → 绿运行 id。

    两半都不能省、也不能换：换了入场就是换了代码，那条红绿差可能只是别人修好了；
    换了 workflow 就不是同一批检查，它绿不证明这条用例当时绿。返回空 = 没坐实，
    调用方见了空必须按真故障读 —— 只见过红的偶发，和一条真回归在这份数据里没有
    区别。

    workflow 空的运行一律不算：`--jq` 挑窄了的离线样本会缺这个字段，而空串当成
    同一条 workflow 会把不相干的运行归到一处，坐实就成了假的。判不了就不判。
    """
    green_by_key: dict[tuple[str, str], int] = {}
    for run in runs:
        if run.conclusion in GREEN_RUN and run.subject and run.workflow:
            green_by_key.setdefault((run.workflow, run.subject), run.id)
    confirmed: dict[str, int] = {}
    for run in reds:
        if not run.subject or not run.workflow:
            continue
        green_id = green_by_key.get((run.workflow, run.subject))
        if green_id is not None:
            confirmed.setdefault(run.subject, green_id)
    return confirmed


def judge_known_flakes(runs: list[Run]) -> dict:
    """把命中档案的红灯按同入场判据判一遍，返回两段。

    `cases` 是用例级档案的判定，一条一行：红过的入场、坐实的入场（同名还有一次
    绿的那些）、以及坐实与否。`runs` 是运行级档案要说的那几轮运行，而且**有证据
    才列**：这一轮得真红在一条坐实的用例上。只见过红、没同入场绿的轮次不列 —— 它
    可能就是这次合出来的真故障，挂到「红在无关偶发上」下面正是这个脚本要拦的误读。
    """
    matches: dict[tuple[str, str], list[Run]] = defaultdict(list)
    for run in runs:
        if run.conclusion not in FAILED_RUN:
            continue
        for job in run.jobs:
            if job.conclusion not in FAILED_JOB:
                continue
            for node, signature in extract_failures(job.log):
                flake = known_flake_for_case(node, signature)
                if flake is None:
                    continue
                bucket = matches[(flake.id, node)]
                if all(seen.id != run.id for seen in bucket):
                    bucket.append(run)

    #: 判据的 key 是 (workflow, 入场名)。运行级那道闸要用同一对 key 逐轮核，
    #: 不能按用例桶汇：同一条用例在入场 A 上绿过，不说明入场 B 的那一轮也绿过；
    #: 同一入场在另一条 workflow 上绿过，也不说明这条 workflow 绿过。按桶汇会把
    #: 从未绿过的轮次一起放进来，给它「别 push-fix、重新采纳」的处置 —— 那正是
    #: 这条档案要拦的误读。
    green_keys = {
        (run.workflow, run.subject)
        for run in runs
        if run.conclusion in GREEN_RUN and run.subject and run.workflow
    }

    case_rows = []
    evidenced: set[int] = set()
    for (flake_id, node), reds in matches.items():
        flake = _KNOWN_BY_ID[flake_id]
        greens = same_sha_green(runs, reds)
        evidenced.update(
            run.id for run in reds if (run.workflow, run.subject) in green_keys
        )
        #: 这批红所在的入场里有没有 workflow 取不到（`--jq` 挑窄）的运行。判据对空
        #: workflow 一律不下结论，渲染那边也就不能说「没有绿」—— 数据里可能就有，
        #: 只是认不出来。两件事分开说，处置才不会指错方向。
        subjects = {run.subject for run in reds if run.subject}
        workflow_unknown = any(
            not run.workflow and run.subject in subjects for run in runs
        )
        case_rows.append(
            {
                "flake": flake_id,
                "title": flake.title,
                "case": node,
                "reds": sorted(subjects),
                "greens": greens,
                "confirmed": sorted(greens),
                "verdict": "confirmed" if greens else "unproven",
                "workflow_unknown": workflow_unknown,
                "action": flake.action,
                "seen": flake.seen,
            }
        )
    # 坐实的先看：它才是「这条红可以放下」的那一类。
    case_rows.sort(key=lambda row: (row["verdict"] != "confirmed", row["case"]))

    run_rows: dict[str, dict] = {}
    for run in runs:
        if run.conclusion not in FAILED_RUN or run.id not in evidenced:
            continue
        for flake in KNOWN_FLAKES:
            if not flake.matches_run(run):
                continue
            row = run_rows.setdefault(
                flake.id,
                {
                    "flake": flake.id,
                    "title": flake.title,
                    "action": flake.action,
                    "seen": flake.seen,
                    "runs": [],
                },
            )
            if run.id not in row["runs"]:
                row["runs"].append(run.id)
    return {"cases": case_rows, "runs": [run_rows[key] for key in sorted(run_rows)]}


def analyze(runs: list[Run], top: int, min_tests: int) -> dict:
    """把运行聚合成两张榜。纯函数：不碰网络也不碰磁盘，好让自测直接喂假样本。"""
    failed_runs = [run for run in runs if run.conclusion in FAILED_RUN]

    test_runs: dict[str, set[int]] = defaultdict(set)
    test_sig: dict[str, Counter[str]] = defaultdict(Counter)
    sig_tests: dict[str, set[str]] = defaultdict(set)
    sig_runs: dict[str, set[int]] = defaultdict(set)
    sig_hits: Counter[str] = Counter()
    failed_jobs = 0

    for run in failed_runs:
        for job in run.jobs:
            if job.conclusion not in FAILED_JOB:
                continue
            failed_jobs += 1
            for node, signature in extract_failures(job.log):
                test_runs[node].add(run.id)
                test_sig[node][signature] += 1
                sig_tests[signature].add(node)
                sig_runs[signature].add(run.id)
                sig_hits[signature] += 1

    # 跨用例数是这个脚本的分界线：达到阈值的签名，它的红灯更像合出来的真故障。
    flagged = {sig for sig in sig_hits if len(sig_tests[sig]) >= min_tests}

    signature_rows = [
        {
            "signature": sig,
            "hits": sig_hits[sig],
            "tests": len(sig_tests[sig]),
            "runs": len(sig_runs[sig]),
            "flagged": sig in flagged,
        }
        for sig in sorted(sig_hits, key=lambda s: (-sig_hits[s], s))
    ]

    failed_count = len(failed_runs)
    total_count = len(runs)
    test_rows = []
    for node in sorted(test_runs, key=lambda t: (-len(test_runs[t]), t)):
        top_sig = test_sig[node].most_common(1)[0][0]
        test_rows.append(
            {
                "test": node,
                "runs": len(test_runs[node]),
                # 分母是全部运行，不只是失败的：这个占比就是这个用例对整体判红率
                # 的贡献，治理的目标（把判红率压下去）才读得出来。
                "share": (len(test_runs[node]) / total_count) if total_count else 0.0,
                "signature": top_sig,
                "flagged": top_sig in flagged,
            }
        )

    return {
        "runs_total": len(runs),
        "runs_failed": failed_count,
        "jobs_failed": failed_jobs,
        "flagged_signatures": sorted(flagged),
        "signatures": signature_rows[:top],
        "tests": test_rows[:top],
        "flakes": judge_known_flakes(runs),
    }


def _clip(text: str, width: int = _SIG_WIDTH) -> str:
    """榜单要能并排看，长错误签名截到宽度。聚合按完整签名做，只有显示才截。"""
    return text if len(text) <= width else text[: width - 1] + "…"


def render(report: dict) -> str:
    lines: list[str] = []
    lines.append(
        f"merge_group runs: {report['runs_total']} total, "
        f"{report['runs_failed']} failed, "
        f"{report['jobs_failed']} failed jobs read"
    )
    if not report["runs_failed"]:
        lines.append("no failed merge_group runs in the window: nothing to rank")
        return "\n".join(lines)

    lines.append("")
    lines.append(
        f"[错误签名榜]  {len(report['signatures'])} shown, ranked by occurrences "
        f"over {report['runs_failed']} failed runs"
    )
    lines.append(f"  {'次数':>6} {'用例':>6} {'运行':>5}  错误签名")
    for row in report["signatures"]:
        mark = "[疑似合并冲突] " if row["flagged"] else ""
        lines.append(
            f"  {row['hits']:>6} {row['tests']:>6} {row['runs']:>5}  "
            f"{mark}{_clip(row['signature'])}"
        )

    lines.append("")
    lines.append(
        f"[用例复发榜]  {len(report['tests'])} shown, share of all "
        f"{report['runs_total']} runs"
    )
    lines.append(f"  {'次数':>6} {'占比':>7}  用例 / 最常同现的错误签名")
    for row in report["tests"]:
        mark = "  [疑似合并冲突]" if row["flagged"] else ""
        lines.append(
            f"  {row['runs']:>6} {row['share'] * 100:>6.1f}%  {row['test']}{mark}"
        )
        lines.append(f"          ↳ {_clip(row['signature'], 70)}")

    flakes = report.get("flakes") or {"cases": [], "runs": []}
    lines.append("")
    lines.append(
        "[已知偶发判据]  同一条 workflow、同一次入场（队列分支名 pr-<n>-<sha>）"
        "既红过又绿过才算坐实；只见过红的按真故障读"
    )
    if not flakes["cases"] and not flakes["runs"]:
        lines.append("  这一批红灯没有命中已知偶发档案")
    for row in flakes["cases"]:
        if row["verdict"] == "confirmed":
            lines.append(f"  [坐实]   {row['case']}")
            lines.append(
                "          同 SHA 红绿："
                + "、".join(
                    f"{subject}（绿运行 {row['greens'][subject]}）"
                    for subject in row["confirmed"]
                )
            )
            lines.append("          同一个名字再入场才落这里；中间推过提交的话，这次红是真故障")
        else:
            lines.append(f"  [未坐实] {row['case']}")
            if row["workflow_unknown"]:
                lines.append(
                    "          取数里 workflow 是空的，判不了同 SHA 有没有绿：先修取数，别当偶发放行"
                )
            else:
                lines.append("          只有红、没有同 SHA 的绿：按真故障读，别当偶发放行")
        lines.append(f"          档案 {row['flake']} — {row['title']}")
        lines.append(f"          处置 {row['action']}")
        lines.append(f"          依据 {_clip(row['seen'], 96)}")
    for row in flakes["runs"]:
        lines.append(f"  [运行级档案] {row['flake']} — {row['title']}")
        lines.append(
            f"          这些轮里有坐实的偶发 {', '.join(str(run_id) for run_id in row['runs'])}"
        )
        lines.append(
            "          一轮里可能还有别的红；上面那些坐实的用例才判得了，别把它整轮放过"
        )
        lines.append(f"          处置 {row['action']}")
    return "\n".join(lines)


def _gh(args: list[str]) -> str:
    """跑一次 `gh`，把 stdout 原样返回。凭据在环境里，脚本不碰 token。"""
    # 按字节抓再替换着解：一份作业日志里有一个坏字节，不该让整张榜读不出来，
    # 而文件那条路本来就是这么读的，两条路得一样能扛。
    proc = subprocess.run(["gh", *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()[:300]}"
        )
    return proc.stdout.decode("utf-8", errors="replace")


def _gh_json(endpoint: str) -> object:
    return json.loads(_gh(["api", endpoint]))


def _unique(payloads: list[dict]) -> list[dict]:
    """按 id 去重，保留第一次出现的那条（gh 最新的运行在前）。

    分页之间新运行会挤动位置，同一条运行可能在两页里各出现一次。不去重就会把
    一次运行算两遍：真运行少拿一条，签名的命中数翻倍而它的运行数不动。
    """
    seen: set[int] = set()
    unique: list[dict] = []
    for raw in payloads:
        if raw["id"] in seen:
            continue
        seen.add(raw["id"])
        unique.append(raw)
    return unique


def _run_fields(raw: dict) -> dict:
    """gh 的运行对象 → Run 那几个来源字段。

    样本是 `--jq` 挑过字段抓下来的话，这几个可能缺；缺就留空串，不编。空值一律
    「证不出来」，走未坐实那一侧，这是安全的方向 —— 判据那边逐字段守住了：空提交号
    和空入场名不参与比对，`same_sha_green` 也不认 workflow 为空的运行，所以留空不会
    把不相干的运行归到一处、凑出一次假的坐实。这里不报错，是因为窄样本仍能出两张
    排行榜，没必要为判据那一半的输入把整张榜毙掉。
    """
    return {
        "head_sha": raw.get("head_sha") or "",
        "head_branch": raw.get("head_branch") or "",
        "workflow": raw.get("path") or raw.get("name") or "",
        "event": raw.get("event") or "",
    }


def fetch_jobs(repo: str, run_id: int) -> list[dict]:
    jobs: list[dict] = []
    for page in range(1, 101):
        data = _gh_json(
            f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}"
        )
        items = data["jobs"]
        jobs.extend(items)
        if len(items) < 100:
            break
    return jobs


def fetch_online(repo: str, limit: int) -> list[Run]:
    """gh api 走三步取数：运行列表 → 失败运行的作业 → 失败作业的日志。"""
    payloads: list[dict] = []
    for page in range(1, (limit + 99) // 100 + 1):
        data = _gh_json(
            f"repos/{repo}/actions/runs?event=merge_group&per_page=100&page={page}"
        )
        payloads.extend(data["workflow_runs"])
        if len(data["workflow_runs"]) < 100:
            break

    runs: list[Run] = []
    for raw in _unique(payloads):
        if len(runs) >= limit:
            break
        run_id = raw["id"]
        if raw.get("conclusion") not in FAILED_RUN:
            runs.append(
                Run(
                    run_id,
                    raw.get("conclusion"),
                    raw.get("created_at", ""),
                    **_run_fields(raw),
                )
            )
            continue
        jobs = [
            Job(
                job["id"],
                job.get("conclusion"),
                # 只失败作业才下日志：成功作业的日志对这张榜没有信息。
                _gh(["api", f"repos/{repo}/actions/jobs/{job['id']}/logs"])
                if job.get("conclusion") in FAILED_JOB
                else "",
            )
            for job in fetch_jobs(repo, run_id)
        ]
        runs.append(
            Run(
                run_id,
                raw.get("conclusion"),
                raw.get("created_at", ""),
                jobs,
                **_run_fields(raw),
            )
        )
    return runs


def load_offline(root: Path, limit: int) -> list[Run]:
    """从 `--json-dir` 读样本；布局见模块 docstring。取最近 limit 条运行。"""
    paths = sorted(root.glob("runs*.json"))
    if not paths:
        # 目录写错、或样本还没抓下来，读出来同样是「一条都没失败」；那和队列
        # 真干净分不出来，所以在这里就报错，不往下渲染一张空榜。
        raise ValueError(f"no runs*.json under {root}")
    payloads: list[dict] = []
    for path in paths:
        payloads.extend(json.loads(path.read_text())["workflow_runs"])

    ordered = sorted(
        _unique(payloads),
        key=lambda r: (r.get("created_at") or "", r["id"]),
        reverse=True,
    )

    runs: list[Run] = []
    for raw in ordered[:limit]:
        run_id = raw["id"]
        jobs: list[Job] = []
        jobs_file = root / "jobs" / f"{run_id}.json"
        if raw.get("conclusion") in FAILED_RUN and jobs_file.exists():
            for job in json.loads(jobs_file.read_text())["jobs"]:
                log_file = root / "logs" / f"job-{job['id']}.log"
                log = log_file.read_text(errors="replace") if log_file.exists() else ""
                jobs.append(Job(job["id"], job.get("conclusion"), log))
        runs.append(
            Run(
                run_id,
                raw.get("conclusion"),
                raw.get("created_at", ""),
                jobs,
                **_run_fields(raw),
            )
        )
    return runs


def _self_test_offline() -> None:
    """`--json-dir` 那条路也真跑一遍：把样本写进临时目录，再读回来。

    造四样东西：翻页边界上重复出现的一条运行（不能算两遍）、一份带非 UTF-8
    字节的日志（一个坏字节不该让整张榜读不出来）、一个空目录、一份文件在但
    没有运行的样本（后两者都要报错，不能渲染成「队列很干净」）。
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)

        def page(name: str, workflow_runs: list[dict]) -> None:
            (root / name).write_text(json.dumps({"workflow_runs": workflow_runs}))

        def raw_run(run_id: int, conclusion: str, created_at: str, pr: int) -> dict:
            """照 gh 返回的样子造，字段名一个不少 —— 判据全靠它们。"""
            return {
                "id": run_id,
                "conclusion": conclusion,
                "created_at": created_at,
                "head_sha": f"{run_id:040x}",
                "head_branch": f"gh-readonly-queue/main/pr-{pr}-{'a' * 40}",
                "path": ".github/workflows/required-ci.yml",
                "event": "merge_group",
            }

        # 第二页把第一页的最后一条又带了一次：gh 分页之间位置会被新运行挤动。
        repeated = raw_run(3, "success", "2026-10-16T09:00:00Z", 4009)
        page(
            "runs-page1.json",
            [
                raw_run(1, "failure", "2026-10-15T05:00:00Z", 4010),
                raw_run(2, "failure", "2026-10-16T08:00:00Z", 4011),
                repeated,
            ],
        )
        page("runs-page2.json", [repeated])

        (root / "jobs").mkdir()
        for run_id, job_id in ((1, 11), (2, 21)):
            (root / "jobs" / f"{run_id}.json").write_text(
                json.dumps({"jobs": [{"id": job_id, "conclusion": "failure"}]})
            )
        (root / "logs").mkdir()
        # 一个坏字节混在同一行日志里：整行还有效，榜不该整张读不出来。
        (root / "logs" / "job-11.log").write_bytes(
            b"2026-10-15T05:48:30.3537274Z FAILED tests/b.py::test_flaky - "
            b"AssertionError: x \xff\xfe\n"
        )
        (root / "logs" / "job-21.log").write_text(
            "2026-10-16T08:12:41.1140393Z FAILED tests/b.py::test_flaky - "
            "AssertionError: x\n"
        )

        runs = load_offline(root, limit=300)
        # 3 条而不是 4 条：重复的那条被去掉了；最近的在最前。
        assert [run.id for run in runs] == [3, 2, 1], [run.id for run in runs]
        # gh 的字段名读错一位，整条判据会静默降级成「什么也坐实不了」——subject 回落
        # 到那个永不重复的 head_sha。所以这条路要逐字段核一遍，不只看条数。
        loaded = runs[1]
        assert loaded.head_sha == f"{2:040x}", loaded
        assert loaded.head_branch == f"gh-readonly-queue/main/pr-4011-{'a' * 40}", loaded
        assert loaded.subject == f"pr-4011-{'a' * 40}", loaded
        assert loaded.workflow == ".github/workflows/required-ci.yml", loaded
        assert loaded.event == "merge_group", loaded
        report = analyze(runs, top=DEFAULT_TOP, min_tests=DEFAULT_CONFLICT_MIN_TESTS)
        assert report["runs_total"] == 3 and report["runs_failed"] == 2, report
        top_test = report["tests"][0]
        assert top_test["test"] == "tests/b.py::test_flaky" and top_test["runs"] == 2, (
            top_test
        )
        # 分母是 3（含那条绿运行），不是 2 —— 和内存里那条路同一套算法。
        assert abs(top_test["share"] - 2 / 3) < 1e-9, top_test

        # `--jq` 挑窄的样本缺 path/name：workflow 留空、两张榜照出，不整张毙掉。
        # 这条守着 `_run_fields` 不再 raise —— 改回去这里就红。
        narrow = root / "narrow"
        narrow.mkdir()
        page(
            "narrow/runs-page1.json",
            [
                {
                    "id": 4,
                    "conclusion": "failure",
                    "created_at": "2026-10-16T10:00:00Z",
                    "head_sha": "c" * 40,
                    "head_branch": f"gh-readonly-queue/main/pr-4012-{'a' * 40}",
                    "event": "merge_group",
                }
            ],
        )
        thin = load_offline(narrow, limit=300)
        assert [(run.id, run.workflow) for run in thin] == [(4, "")], thin
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(["--json-dir", str(narrow)]) == 0

        # 目录写错和目录里没有数据：都要退 2，并把原因说出来。
        (root / "blank").mkdir()
        page("blank/runs-page1.json", [])
        for missing in (root / "empty", root / "nope", root / "blank"):
            missing.mkdir(exist_ok=True)
            said = io.StringIO()
            with contextlib.redirect_stderr(said):
                code = main(["--json-dir", str(missing)])
            assert code == 2, (missing, code)
            assert "merge_group runs" in said.getvalue(), (missing, said.getvalue())


def _self_test_gh_bytes() -> None:
    """`gh` 那条路也真跑一遍：换掉 subprocess，看 `_gh` 怎么读回作业日志。

    gh 是按字节吐日志的，一个坏字节就落在这一行上。替身照真实的样子演：管道是字节
    还是文本，由 `text`、`universal_newlines`、`encoding`、`errors` 里给了哪个决定
    （给了任何一个就是文本，和真实 subprocess 同一条规则）；字节模式把带坏字节的
    输出原样交回来；文本模式却**没给** `errors=`，就在那里当场抛 UnicodeDecodeError
    —— 那正是「整张榜读不出来」这个毛病的来路。给了 `errors=` 的文本模式是同样合法
    的另一种写法，替身照它给的那套解出字符串（没给 `encoding=` 时替身按 utf-8 解，
    真实那边用的是 locale）。断言落在效果上：坏字节要变成替换字符出现，不能悄悄不见，
    也不能连累整份输出。非零退出那条路也走一遍，报错里要带着 gh 说的话。
    """
    real_run = subprocess.run
    seen: list[list[str]] = []
    bad = b"assert 'closed' == 'open' \xff\xfe\n"
    #: 坏字节该以替换字符出现，不是悄悄消失，也不是连累整份输出。
    replaced = "\N{REPLACEMENT CHARACTER}"

    class Proc:
        def __init__(self, out, err, code: int) -> None:
            self.stdout, self.stderr, self.returncode = out, err, code

    def text_like(kwargs: dict):
        """真实 subprocess 的解码参数：`text`、`universal_newlines`、`encoding`、
        `errors` 里给了任何一个真值，管道就是文本模式（CPython 的 text_mode 就是这
        四个的或），于是坏字节在没有 `errors=` 时在那里抛，给了 `errors=` 就按它给
        的那套走。文本模式用哪个编码解，就是给的 `encoding=` 本身——空串在真实那边
        也是 `LookupError`，不能悄悄回落到 utf-8 把一个会崩的写法放过去。"""
        if not any(
            kwargs.get(name)
            for name in ("text", "universal_newlines", "encoding", "errors")
        ):
            return None
        given = kwargs.get("encoding")
        return ("utf-8" if given is None else given), kwargs.get("errors")

    def fake_run(args, **kwargs):
        seen.append(args)
        how = text_like(kwargs)
        if how and how[1] is None:
            raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
        if how:
            return Proc(bad.decode(*how), "", 0)
        return Proc(bad, b"", 0)

    subprocess.run = fake_run
    try:
        out = _gh(["api", "x"])
    finally:
        subprocess.run = real_run

    assert seen == [["gh", "api", "x"]], seen
    assert out == f"assert 'closed' == 'open' {replaced}{replaced}\n", repr(out)

    def failing(args, **kwargs):
        how = text_like(kwargs)
        if how and how[1] is None:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        err = b"HTTP 404 \xff\n"
        return Proc("", err.decode(*how), 1) if how else Proc(b"", err, 1)

    subprocess.run = failing
    try:
        try:
            _gh(["api", "y"])
        except RuntimeError as exc:
            said = str(exc)
        else:
            raise AssertionError("a gh that exits non-zero must not look like success")
    finally:
        subprocess.run = real_run
    assert "failed (1)" in said and replaced in said, said


def _self_test_flake_criterion() -> None:
    """已知偶发档案和同 SHA 判据各真跑一遍，而且要能证明判据拦得住。

    样本用的是档案里那几条真用例 id 和真断言 —— 档案写错一个字，这里就对不上。四
    份判定样本，一份管一件事：同提交、同 workflow 红绿都有 → 坐实；只红过，另一个
    workflow 在同一个提交上绿了不算数 → 未坐实；同一个 PR 从同一条基线入场两次、合
    成提交不同 → 坐实；两个不同 PR 的条目挂在同一条基线上（红的那次入场自己没绿过）
    → 未坐实。

    收尾再跑四次真实变异：去掉「同 SHA 要有绿」这一步、判据里不认 workflow、把「入
    场身份」换回运行自己报的 head_sha、只按基线 sha 分组（不认 PR 号）。每一次都得
    让某条判定变样 —— 样本要是怎么都绿，它证明不了判据在拦东西。
    """
    required_ci = ".github/workflows/required-ci.yml"
    frontend = ".github/workflows/frontend.yml"
    #: 同一个 PR 从同一条基线入场两次，队列分支名逐字相同，合成提交各不相同。
    head = "f" * 40
    writeup = (
        "tests/integration/test_a_last_step_lands_while_work_goes_on.py::"
        "test_a_message_sent_while_the_task_is_written_up_is_not_dropped[turn-waiting]"
    )
    orphan = (
        "tests/integration/test_claude_session_records.py::"
        "test_a_message_after_a_dead_runner_gets_a_turn"
    )
    planner = (
        "tests/integration/test_indexed_rows.py::"
        "test_the_speaking_again_reads_do_not_reach_past_the_question[asyncio]"
    )

    def failed(
        run_id: int, job_id: int, sha: str, node: str, signature: str, **extra
    ) -> Run:
        log = f"2026-10-16T08:12:41.1140393Z FAILED {node} - {signature}\n"
        extra.setdefault("event", "pull_request")
        extra.setdefault("workflow", required_ci)
        return Run(
            run_id,
            "failure",
            "2026-10-16T08:00:00Z",
            [Job(job_id, "failure", log)],
            head_sha=sha,
            **extra,
        )

    runs = [
        # 坐实：同一个提交、同一条 workflow 上先红后绿。
        failed(1, 11, "a" * 40, writeup, "AssertionError: assert 'closed' == 'open'"),
        Run(
            2,
            "success",
            "2026-10-16T08:30:00Z",
            head_sha="a" * 40,
            workflow=required_ci,
            event="pull_request",
        ),
        # 未坐实：只红过这一次；同一个提交上另有 frontend 的绿，不是同一批检查。
        failed(3, 31, "b" * 40, orphan, "assert False"),
        Run(
            4,
            "success",
            "2026-10-16T08:30:00Z",
            head_sha="b" * 40,
            workflow=frontend,
            event="pull_request",
        ),
        # 队列入场两次：合成提交 ccc… 与 ddd… 不同，入场名字相同，于是坐实。
        failed(
            5,
            41,
            "c" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            head_branch=f"gh-readonly-queue/main/pr-4010-{head}",
            event="merge_group",
        ),
        Run(
            6,
            "success",
            "2026-10-16T08:40:00Z",
            head_sha="d" * 40,
            head_branch=f"gh-readonly-queue/main/pr-4010-{head}",
            workflow=required_ci,
            event="merge_group",
        ),
    ]

    # 判据认的那个身份来自队列分支名；非队列分支回落运行自己报的 head_sha。
    assert runs[4].subject == f"pr-4010-{head}", runs[4].subject
    assert runs[0].subject == "a" * 40, runs[0].subject

    judged = judge_known_flakes(runs)
    cases = {row["flake"]: row for row in judged["cases"]}
    assert len(judged["cases"]) == 3, judged["cases"]

    confirmed = cases["integration-writeup-message-not-dropped"]
    assert confirmed["verdict"] == "confirmed", confirmed
    assert confirmed["greens"] == {"a" * 40: 2}, confirmed["greens"]

    unproven = cases["integration-orphaned-input-assertion"]
    assert unproven["verdict"] == "unproven", unproven
    assert unproven["reds"] == ["b" * 40] and not unproven["confirmed"], unproven

    queued = cases["indexed-rows-planner-assertion"]
    assert queued["verdict"] == "confirmed", queued
    # 红绿落在两个合成提交上，坐实的是它们共同的入场名字。
    assert queued["reds"] == [f"pr-4010-{head}"], queued
    assert queued["greens"] == {f"pr-4010-{head}": 6}, queued

    # 同一条基线上两条不同 PR 的条目：一红一绿，但各自带着自己的改动，不是同一份
    # 代码，不许坐实。真数据里这种分组比同名入场多（同一窗口 6 组对 1 组）。
    siblings = [
        failed(
            7,
            51,
            "e" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            head_branch=f"gh-readonly-queue/main/pr-4011-{head}",
            event="merge_group",
        ),
        Run(
            8,
            "success",
            "2026-10-16T09:00:00Z",
            head_sha="9" * 40,
            head_branch=f"gh-readonly-queue/main/pr-4012-{head}",
            workflow=required_ci,
            event="merge_group",
        ),
    ]
    sibling_case = judge_known_flakes(siblings)["cases"][0]
    assert sibling_case["verdict"] == "unproven", sibling_case
    assert sibling_case["reds"] == [f"pr-4011-{head}"], sibling_case

    # 运行级档案只列有证据的轮次：这一轮得真红在一条坐实的用例上。运行 5 就是那
    # 一轮（planner 用例在 pr-4010 上红绿都有），运行 3 也红在已知用例上，但
    # orphan 那条只红过、没同入场绿，不许把它挂进「被无关偶发拖红」下面。
    assert [row["flake"] for row in judged["runs"]] == ["merge-group-synthetic-run"], (
        judged
    )
    assert judged["runs"][0]["runs"] == [5], judged["runs"]

    # 证据要逐轮核，不能按用例桶汇：同一条用例在入场 A 上红绿都有，不等于入场 B
    # 的那一轮也坐实过。这里 B 只红过一次、从没绿过，运行级只许列 A 那一轮。
    # 按桶汇（把桶里所有红运行都记成有证据）会把 22 一起放进来。
    same_node_other_entry = [
        failed(
            20,
            62,
            "5" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            head_branch=f"gh-readonly-queue/main/pr-4020-{head}",
            event="merge_group",
        ),
        Run(
            21,
            "success",
            "2026-10-16T09:30:00Z",
            head_sha="6" * 40,
            head_branch=f"gh-readonly-queue/main/pr-4020-{head}",
            workflow=required_ci,
            event="merge_group",
        ),
        failed(
            22,
            63,
            "7" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            head_branch=f"gh-readonly-queue/main/pr-4021-{head}",
            event="merge_group",
        ),
    ]
    entry_rows = judge_known_flakes(same_node_other_entry)["runs"]
    assert [row["runs"] for row in entry_rows] == [[20]], entry_rows

    # 同一入场但另一条 workflow 上的绿也不算：判据的 key 里那一半不能丢。
    other_workflow = [
        failed(
            23,
            64,
            "8" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            head_branch=f"gh-readonly-queue/main/pr-4022-{head}",
            event="merge_group",
        ),
        Run(
            24,
            "success",
            "2026-10-16T09:40:00Z",
            head_sha="9" * 40,
            head_branch=f"gh-readonly-queue/main/pr-4022-{head}",
            workflow=frontend,
            event="merge_group",
        ),
        # 同一条入场在 frontend 上也红了一轮：它才是那条 workflow 上坐实过的运行。
        # 少了这一条，样本里没有任何一轮「绿在同一入场」，按 subject 汇的写法也全绿。
        failed(
            27,
            66,
            "c" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            head_branch=f"gh-readonly-queue/main/pr-4022-{head}",
            workflow=frontend,
            event="merge_group",
        ),
    ]
    assert [row["runs"] for row in judge_known_flakes(other_workflow)["runs"]] == [
        [27]
    ]
    # workflow 取不到的运行同样不许坐实 —— 空串当成同一条 workflow，会把不相干的
    # 轮次归到一处。这条守的是 `_run_fields` 留空之后判据那一侧的分寸。
    no_workflow = [
        failed(
            25,
            65,
            "a" * 40,
            planner,
            "AssertionError: assert 'GroupAggregate' not in plan",
            workflow="",
            head_branch=f"gh-readonly-queue/main/pr-4023-{head}",
            event="merge_group",
        ),
        Run(
            26,
            "success",
            "2026-10-16T09:50:00Z",
            head_sha="b" * 40,
            head_branch=f"gh-readonly-queue/main/pr-4023-{head}",
            workflow="",
            event="merge_group",
        ),
    ]
    no_wf = judge_known_flakes(no_workflow)
    assert no_wf["cases"][0]["verdict"] == "unproven", no_wf["cases"]
    assert no_wf["runs"] == [], no_wf["runs"]
    # 判不了和判过是两句话：workflow 取不到的样本里，渲染不能说「数据里没有绿」，
    # 那是在下一句事实里不存在的话。处置也要指向取数，不是让人去查真故障。
    thin_text = render(
        analyze(no_workflow, top=DEFAULT_TOP, min_tests=DEFAULT_CONFLICT_MIN_TESTS)
    )
    assert "workflow 是空的，判不了同 SHA 有没有绿" in thin_text, thin_text
    assert "只有红、没有同 SHA 的绿" not in thin_text, thin_text

    # 不在档案里的用例不许被认领。
    assert known_flake_for_case("tests/b.py::test_other", "AssertionError: x") is None
    # 用例对上、签名对不上，是同一个用例的另一种红：借旧档案溜走就是漏一条真故障。
    # 少了这一半（签名那一句被删成 return True），这里会当场认领。
    assert known_flake_for_case(writeup, "assert False") is None
    assert known_flake_for_case(orphan, "AssertionError: a different failure") is None
    assert known_flake_for_case(planner, "ValueError: boom") is None

    # 渲染也真跑一遍：坐实、未坐实、运行级三种行都得出来。
    text = render(analyze(runs, top=DEFAULT_TOP, min_tests=DEFAULT_CONFLICT_MIN_TESTS))
    for fragment in ("[已知偶发判据]", "[坐实]", "[未坐实]", "[运行级档案]"):
        assert fragment in text, text

    def judged_with(criterion, sample: list[Run] | None = None) -> dict:
        """换掉判据再判一遍 —— 用来证明下面每份样本都真的区分得开。

        只交用例级：运行级那道闸已经按自己的样本直接断过了，不再经过这里。
        """
        real = same_sha_green
        globals()["same_sha_green"] = criterion
        try:
            judged_again = judge_known_flakes(sample if sample is not None else runs)
            return {row["flake"]: row for row in judged_again["cases"]}
        finally:
            globals()["same_sha_green"] = real

    def without_green(candidates: list[Run], reds: list[Run]) -> dict[str, int]:
        """变异一：红过的提交一律当坐实，缺了「同 SHA 要有绿」这一步。"""
        return {run.subject: run.id for run in reds if run.subject}

    def any_workflow(candidates: list[Run], reds: list[Run]) -> dict[str, int]:
        """变异二：判据里不认 workflow，别的 workflow 绿也算同一批检查。"""
        greens: dict[str, int] = {}
        for run in candidates:
            if run.conclusion in GREEN_RUN and run.subject:
                greens.setdefault(run.subject, run.id)
        return {run.subject: greens[run.subject] for run in reds if run.subject in greens}

    def merge_commit(candidates: list[Run], reds: list[Run]) -> dict[str, int]:
        """变异三：拿运行自己报的 head_sha 当身份，不认队列分支名。"""
        greens: dict[tuple[str, str], int] = {}
        for run in candidates:
            if run.conclusion in GREEN_RUN and run.head_sha:
                greens.setdefault((run.workflow, run.head_sha), run.id)
        found: dict[str, int] = {}
        for run in reds:
            green_id = greens.get((run.workflow, run.head_sha))
            if green_id is not None:
                found.setdefault(run.head_sha, green_id)
        return found

    def base_sha_only(candidates: list[Run], reds: list[Run]) -> dict[str, int]:
        """变异四：身份只取基线 sha，把同一条基线上不同 PR 的条目当成同一份代码。"""

        def base(run: Run) -> str:
            return run.subject.rsplit("-", 1)[-1] if run.subject else ""

        greens: dict[tuple[str, str], int] = {}
        for run in candidates:
            if run.conclusion in GREEN_RUN and run.subject:
                greens.setdefault((run.workflow, base(run)), run.id)
        found: dict[str, int] = {}
        for run in reds:
            if not run.subject:
                continue
            green_id = greens.get((run.workflow, base(run)))
            if green_id is not None:
                found.setdefault(run.subject, green_id)
        return found

    for criterion, sample, flake_id in (
        (without_green, runs, "integration-orphaned-input-assertion"),
        (any_workflow, runs, "integration-orphaned-input-assertion"),
        (merge_commit, runs, "indexed-rows-planner-assertion"),
        (base_sha_only, siblings, "indexed-rows-planner-assertion"),
    ):
        mutated = judged_with(criterion, sample)[flake_id]["verdict"]
        plain = {
            row["flake"]: row for row in judge_known_flakes(sample)["cases"]
        }[flake_id]["verdict"]
        assert mutated != plain, (
            f"变异「{criterion.__name__}」没让 {flake_id} 的判定变样：这份样本区分"
            "不开它，也就证明不了判据在拦它"
        )

    # 运行级那一行不是按事件认领整轮：一轮 merge_group 只红在**未坐实**的用例上，
    # 就不许列出来 —— 它可能正是这次合出来的真故障。按事件认领（少了这道闸）
    # 这里会返回 [12]。
    lone = [
        failed(
            12,
            52,
            "7" * 40,
            orphan,
            "assert False",
            head_branch=f"gh-readonly-queue/main/pr-4013-{head}",
            event="merge_group",
        )
    ]
    lone_judged = judge_known_flakes(lone)
    assert lone_judged["runs"] == [], lone_judged["runs"]
    assert lone_judged["cases"][0]["verdict"] == "unproven", lone_judged["cases"]


def self_test() -> int:
    """离线核对整条链路：抽取 → 聚合 → 渲染，外加取数和两个 loader 各走一遍。

    假样本刻意造出两类红：一个签名跨 5 条用例（组合出来的真故障，要标出来），
    一条用例在两个运行里各红一次（偶发，不标）。抽取那步再混进 `[gw2]` 前缀，
    确认锚在 `FAILED` 上、不靠时间戳位置。只读自己写进临时目录的样本，不碰网络。

    已知偶发档案和同 SHA 判据另有一套自己的样本，见 `_self_test_flake_criterion`。
    """
    common = (
        "2026-10-15T05:48:30.3537274Z [gw2] FAILED tests/a.py::test_one - "
        "TypeError: boom\n"
        "2026-10-15T05:48:30.4000000Z FAILED tests/a.py::test_two - TypeError: boom\n"
        "2026-10-15T05:48:30.4100000Z FAILED tests/a.py::test_three - TypeError: boom\n"
        "2026-10-15T05:48:30.4200000Z FAILED tests/a.py::test_four - TypeError: boom\n"
        "2026-10-15T05:48:30.4300000Z FAILED tests/a.py::test_five - TypeError: boom\n"
        "2026-10-15T05:48:30.4400000Z FAILED tests/b.py::test_flaky - AssertionError: x\n"
        "2026-10-15T05:48:31.0000000Z ##[error]tests/a.py::test_one — TypeError: boom "
        "| rerun: cd backend && uv run pytest 'tests/a.py::test_one'\n"
    )
    flaky_only = "2026-10-16T08:12:41.1140393Z FAILED tests/b.py::test_flaky - AssertionError: x\n"
    runs = [
        Run(1, "failure", "2026-10-15T05:00:00Z", [Job(11, "failure", common)]),
        Run(2, "failure", "2026-10-16T08:00:00Z", [Job(21, "failure", flaky_only)]),
        Run(3, "success", "2026-10-16T09:00:00Z"),
        Run(4, "cancelled", "2026-10-16T10:00:00Z"),
    ]

    report = analyze(runs, top=DEFAULT_TOP, min_tests=DEFAULT_CONFLICT_MIN_TESTS)

    assert report["runs_total"] == 4, report
    assert report["runs_failed"] == 2, report
    # `##[error]` 那行（破折号）不能算成第二条记录：5 条用例，每条命中一次。
    assert report["jobs_failed"] == 2, report

    top_sig = report["signatures"][0]
    assert top_sig["signature"].startswith("TypeError: boom"), top_sig
    assert top_sig["hits"] == 5 and top_sig["tests"] == 5 and top_sig["runs"] == 1, (
        top_sig
    )
    assert top_sig["flagged"], "a signature across 5 tests must be flagged"

    flaky_sig = next(
        r for r in report["signatures"] if "AssertionError" in r["signature"]
    )
    assert flaky_sig["tests"] == 1 and not flaky_sig["flagged"], flaky_sig

    top_test = report["tests"][0]
    assert top_test["test"] == "tests/b.py::test_flaky", top_test
    assert top_test["runs"] == 2, top_test
    # 分母是 4（含两次没失败的运行），不是 2：占比要能读成对判红率的贡献。
    assert abs(top_test["share"] - 0.5) < 1e-9, top_test
    assert not top_test["flagged"], "a lone flake must not be flagged"

    cluster_test = next(
        r for r in report["tests"] if r["test"] == "tests/a.py::test_one"
    )
    assert cluster_test["runs"] == 1 and cluster_test["flagged"], cluster_test

    # 渲染也要真跑一遍：断言标出来的行确实带着标记，而不是只信聚合的结果。
    text = render(report)
    assert "[疑似合并冲突]" in text, text
    assert "tests/b.py::test_flaky" in text, text

    _self_test_gh_bytes()
    _self_test_offline()
    _self_test_flake_criterion()

    print(
        "self-test ok: "
        f"{report['runs_total']} runs, {report['runs_failed']} failed, "
        f"flagged={report['flagged_signatures']}; gh decode ok; offline loader ok; "
        f"known-flake criterion ok (same-SHA green required, mutation caught)"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rank which merge_group cases flake. Two boards: cases by occurrences, "
            "and error signatures by occurrences/tests/runs. A signature spanning "
            ">= --conflict-min-tests distinct cases is marked [疑似合并冲突] because "
            "a merge-combination break is not a flake. --json-dir reads a saved "
            "sample instead of the network; layout: DIR/runs*.json (gh pages of "
            "actions/runs?event=merge_group), DIR/jobs/<run_id>.json "
            "(actions/runs/<id>/jobs), DIR/logs/job-<job_id>.log (job logs)."
        )
    )
    parser.add_argument("--repo", default="SageSeekerSociety/cheese")
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help="how many recent merge_group runs (default 300)",
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP, help="rows per board (default 20)"
    )
    parser.add_argument(
        "--json-dir", type=Path, help="offline sample directory; see module docstring"
    )
    parser.add_argument(
        "--conflict-min-tests",
        type=int,
        default=DEFAULT_CONFLICT_MIN_TESTS,
        help=(
            "distinct cases sharing one signature before it is marked "
            "[疑似合并冲突] (default 5: one flake dirties one case)"
        ),
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run the offline self-test"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    try:
        if args.json_dir is not None:
            runs = load_offline(args.json_dir, args.runs)
        else:
            runs = fetch_online(args.repo, args.runs)
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"could not gather merge_group runs: {exc}", file=sys.stderr)
        return 2

    if not runs:
        # 一条运行也没读到（目录写错、样本是空的）。这和「队列真干净」读起来一样，
        # 所以报错退出，不渲染一张「没什么可排的」的榜。
        where = args.json_dir if args.json_dir is not None else args.repo
        print(f"no merge_group runs to rank in {where}", file=sys.stderr)
        return 2

    print(render(analyze(runs, args.top, args.conflict_min_tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
