#!/usr/bin/env python3
"""PR 的真实状态一次问清：一条命令给出唯一结论和下一步。

作者在这个仓库里最容易犯的两个错，都是拿旧印象当现状：往一个**其实已经合并**的分支
继续推提交（合并会删掉 head ref，PR 冻在合并那一刻的 SHA，之后推什么都没反应），或者
把一个只是**缺检查**（`mergeable=MERGEABLE`、`mergeStateStatus=BLOCKED`）的 PR 当成
有冲突，去重做一遍 `git merge`。第二种尤其贵：分支 tip 一变，已经绿的检查全部作废，
前端四分片加 e2e 五分片重跑二十分钟起，既有批准还会被清掉。

两个错的根子一样 —— 没人去看 PR 现在到底什么样。而这个状态在一次查询里就全了：
`state` / `mergedAt` 说它是不是已经结束，`mergeable` / `mergeStateStatus` 说它能不能
合，`isInMergeQueue` 说它是不是正在队列里。所以这里没有第二处需要人去查：

    python3 .claude/scripts/pr-state.py <PR 编号>
    python3 .claude/scripts/pr-state.py --json-input <file>   # 离线：读一条同样的返回

取数走一次 GraphQL 查询（`gh api graphql`），不是 `gh pr view --json`：后者的字段表里
没有 `isInMergeQueue`（gh 2.62.0 实测报 `Unknown JSON field`），而队列状态正是这里
要分开的一档。查 `isInMergeQueue` 需要仓库名，所以先用 `gh repo view` 取一次仓库标识
（它是常量，不是会变的 PR 状态），PR 状态本身仍然只问一次。

只查一次，只打一行：结论 + 下一步。判定按下面的优先级短路，先命中的那个就是答案：

    state == MERGED                        已结束，停手
    state == CLOSED                        已关闭
    mergeable == CONFLICTING 或 DIRTY      真冲突，才轮到 git merge
    isInMergeQueue                         在队列里，等队列
    MERGEABLE + BLOCKED                    没冲突，缺门禁/批准，别重做 merge
    MERGEABLE + CLEAN/HAS_HOOKS/UNSTABLE   没冲突，可推进
    其余（含 mergeable == UNKNOWN）        认不出来，重查，绝不当成没问题

`MERGED` 排在 `isInMergeQueue` 前面是有意的：队列退出那一刻发出的「离开队列未确认合并」
通知，常常是 PR 已经被合掉之后的旧消息，收到它时 `isInMergeQueue` 可能仍是 true。

退出码恒为 0：这是给人看的一句话，不是给命令链的开关。查不到状态（gh 失败、PR 不存在）
才非零退出，并在 stderr 写清是哪一步失败。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# 一次查询取齐判状态要用的全部字段：多一次往返就多一次「拿到旧答案」的机会。
# 查询的字段表由它拼出来，所以少问一个字段，这条命令自己就查不到那个状态。
STATE_FIELDS = (
    "number",
    "state",
    "mergedAt",
    "mergeable",
    "mergeStateStatus",
    "headRefOid",
    "isInMergeQueue",
)

PR_QUERY = (
    "query($owner:String!,$repo:String!,$num:Int!){"
    "repository(owner:$owner,name:$repo){pullRequest(number:$num){"
    + " ".join(STATE_FIELDS)
    + "}}}"
)

MERGED = "MERGED"
CLOSED = "CLOSED"
CONFLICTING = "CONFLICTING"
QUEUED = "IN_MERGE_QUEUE"
BLOCKED = "MERGEABLE_BLOCKED"
CLEAN = "MERGEABLE_CLEAN"
UNKNOWN = "UNKNOWN"

# GitHub 说「能合、且没有挡路的东西」。BLOCKED 不在其中：那是等必需检查或批准。
READY_STATUSES = ("CLEAN", "HAS_HOOKS", "UNSTABLE")


class Verdict(NamedTuple):
    """结论码，加一行给人看的「结论 + 下一步」。"""

    code: str
    line: str


def classify(pr: dict) -> Verdict:
    """按优先级把一条 PR 状态返回判成唯一结论。"""
    state = pr.get("state")
    mergeable = pr.get("mergeable")
    status = pr.get("mergeStateStatus")

    if state == MERGED:
        return Verdict(MERGED, f"MERGED (mergedAt={pr.get('mergedAt')}) — 已合并，立即停手，严禁再推提交或重跑")
    if state == CLOSED:
        return Verdict(CLOSED, "CLOSED — PR 已关闭")
    if mergeable == "CONFLICTING" or status == "DIRTY":
        return Verdict(
            CONFLICTING,
            "CONFLICTING — 确有合并冲突，请 git fetch origin main && git merge --no-autostash origin/main",
        )
    if pr.get("isInMergeQueue"):
        return Verdict(QUEUED, "IN_MERGE_QUEUE — 已在合并队列中，等待队列检查完成")
    if mergeable == "MERGEABLE" and status == "BLOCKED":
        return Verdict(
            BLOCKED,
            "MERGEABLE_BLOCKED — 无代码冲突（仅门禁检查未齐或待审批），严禁重做 git merge",
        )
    if mergeable == "MERGEABLE" and status in READY_STATUSES:
        return Verdict(CLEAN, "MERGEABLE_CLEAN — 无冲突，可推进审查或采纳")
    if mergeable == "UNKNOWN" or status == "UNKNOWN":
        return Verdict(UNKNOWN, "UNKNOWN — GitHub 正在计算合并状态，请稍后重查")
    return Verdict(
        UNKNOWN,
        f"UNKNOWN — 合并状态没认出来（mergeable={mergeable}, mergeStateStatus={status}），请重查并人工判断",
    )


def fetch(pr_number: int) -> dict:
    """查一次 PR 状态。凭据由平台注入，本脚本不读也不打印 token。"""
    repo = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, check=False,
    )
    if repo.returncode != 0 or "/" not in repo.stdout:
        sys.stderr.write(repo.stderr)
        raise SystemExit("gh repo view 失败，取不到当前仓库，确定不了要查哪个 PR")
    owner, _, name = repo.stdout.strip().partition("/")

    query = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={PR_QUERY}",
         "-F", f"owner={owner}", "-F", f"repo={name}", "-F", f"num={pr_number}"],
        capture_output=True, text=True, check=False,
    )
    if query.returncode != 0:
        sys.stderr.write(query.stderr)
        raise SystemExit(f"gh api graphql 失败，查不到 PR #{pr_number} 的状态")
    payload = json.loads(query.stdout)
    if payload.get("errors"):
        raise SystemExit(f"查询 PR #{pr_number} 出错：{payload['errors'][0].get('message')}")
    pr = payload["data"]["repository"]["pullRequest"]
    if pr is None:
        raise SystemExit(f"仓库里没有 PR #{pr_number}")
    return pr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一次问清一个 PR 的真实状态，给出唯一结论和下一步")
    parser.add_argument("pr", nargs="?", type=int, help="PR 编号")
    parser.add_argument("--json-input", metavar="FILE", help="离线：读一条同样形状的 JSON 返回")
    args = parser.parse_args(argv)

    if args.json_input:
        payload = json.loads(Path(args.json_input).read_text(encoding="utf-8"))
    elif args.pr is not None:
        payload = fetch(args.pr)
    else:
        parser.error("给一个 PR 编号，或用 --json-input 读一条同样形状的 JSON 返回")

    print(classify(payload).line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
