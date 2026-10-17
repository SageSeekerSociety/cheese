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
    python3 .claude/scripts/pr-state.py dev --sha <合并 SHA> --json-input <file>
    python3 .claude/scripts/pr-state.py deploy-failure --json-input <file>

部署子命令不联网。dev 的 JSON 是 {"logs": "原始 job 日志", "conclusion": "success"}，
只认 DEPLOY OK 中完整 SHA，不拿 run 绿当上线。deploy-failure 的 JSON 是
{"run": {"conclusion": "failure", "jobs": [...]}, "changed_files": ["frontend/src/App.vue"]}。
run.jobs 使用 Actions jobs API 的 name/conclusion/steps；run.build 可提供候选 SHA
最新构建的 status/conclusion；run.failure_files 可提供已经核实、覆盖全部失败的源输入
路径。未知归因不猜，两种已知部署链失败或完整失败输入与改动不相交时提醒别重跑。

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
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
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
DEV_CONFIRMED = "DEV_CONFIRMED"
DEV_UNCONFIRMED = "DEV_UNCONFIRMED"
DEPLOY_UNRELATED = "DEPLOY_UNRELATED"
DEPLOY_RELATED = "DEPLOY_RELATED"

# actions/jobs/<id>/logs 的时间戳和部署脚本的 log 前缀；不匹配日志里回显的 shell 命令。
DEPLOY_CONFIRMATION = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+)?"
    r"(?:\[deploy-docker \d{2}:\d{2}:\d{2}\]\s+)?"
    r"DEPLOY OK: sha=([0-9a-f]{40})(?: healthy)?\s*$",
    re.MULTILINE,
)

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
            "CONFLICTING — 确有合并冲突，先核对合并树，再 git fetch origin main && git merge --no-autostash origin/main",
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


def classify_dev(merged_sha: str, deployment: dict) -> Verdict:
    """只认目标完整 SHA 的部署确认行；run/check 绿、跳过和别的 SHA 都不是证据。

    logs 来自 actions/jobs/<id>/logs。结论只表示日志确认过该 SHA，当前版本另查 /api/version。
    """
    logs = deployment.get("logs", "")
    if (isinstance(merged_sha, str) and re.fullmatch(r"[0-9a-f]{40}", merged_sha)
            and isinstance(logs, str)
            and merged_sha in DEPLOY_CONFIRMATION.findall(logs)):
        return Verdict(DEV_CONFIRMED, f"DEV_CONFIRMED — dev 已确认部署 SHA={merged_sha}（日志证据，非当前版本）")
    return Verdict(DEV_UNCONFIRMED, "DEV_UNCONFIRMED — 还没到 dev（未找到目标完整 SHA 的 DEPLOY OK 确认），别重跑旧部署")


def _paths(value: object) -> set[str] | None:
    """证据路径必须是仓库相对路径；缺失或坏数据不能当成空集来证明不相交。"""
    if not isinstance(value, list):
        return None
    paths = set()
    for item in value:
        if not isinstance(item, str) or not item or "\\" in item:
            return None
        path = PurePosixPath(item)
        if path.is_absolute() or ".." in path.parts or str(path) == ".":
            return None
        paths.add(str(path))
    return paths


def classify_deploy_failure(run: dict, changed_files: list[str] | None) -> Verdict:
    """归因只给线索，不把相交冒充因果，也不建议 rerun。

    run 的 conclusion 和 jobs（含 steps）取自 Actions；build 是候选 SHA 的最新构建
    status/conclusion，不是 workflow_run 的 head_sha（后者可能是 main 的另一个 tip）。
    failure_files 可选：调用者已核实的、覆盖本次全部失败的源输入路径，不是堆栈里随便
    出现的路径或 workflow 文件名。没有完整证据时只识别两种已知部署链失败，其余 UNKNOWN。
    """
    unknown = Verdict(UNKNOWN, "UNKNOWN — 部署归因证据不足，核对失败步骤与合并树，别盲目重跑")
    changed = _paths(changed_files)
    if run.get("conclusion") != "failure" or changed is None:
        return unknown

    # 完整诊断路径优先；相交只是待核实的嫌疑，不能声称本分支一定导致失败。
    inputs = _paths(run.get("failure_files"))
    if inputs:
        if changed & inputs:
            return Verdict(DEPLOY_RELATED, "DEPLOY_RELATED — 失败输入与本分支改动相交，请核对合并树，尚不能断言因果")
        return Verdict(DEPLOY_UNRELATED, "DEPLOY_UNRELATED — 失败输入与本分支改动不相交，不是你的线，别重跑")
    if "failure_files" in run and inputs is None:
        return unknown

    jobs = run.get("jobs", [])
    failed = [job for job in jobs if job.get("conclusion") == "failure"]
    names = {job.get("name") for job in failed}
    login_failure = (
        len(failed) == 1 and names == {"deploy"}
        and any(job.get("name") == "eligibility" and job.get("conclusion") == "success" for job in jobs)
        and [step.get("name") for step in failed[0].get("steps", [])
             if step.get("conclusion") == "failure"] == ["Log in to ghcr"]
    )
    build = run.get("build") or {}
    cancelled_build = (
        bool(failed) and names <= {"eligibility", "build-did-not-produce-images"}
        and all(job.get("name") == "build-did-not-produce-images" or
                [step.get("name") for step in job.get("steps", [])
                 if step.get("conclusion") == "failure"] == ["Fail when this commit will not be deployed"]
                for job in failed)
        and (build.get("conclusion") == "cancelled" or build.get("status") == "in_progress")
    )
    if not (login_failure or cancelled_build):
        return unknown
    # 改过部署链本身时，上面两种形状也不能替本分支免责。
    if any(path.startswith((".github/", "deploy/")) for path in changed):
        return Verdict(DEPLOY_RELATED, "DEPLOY_RELATED — 本分支改过部署链，请核对失败步骤与合并树，尚不能断言因果")
    reason = "镜像仓库登录失败" if login_failure else "候选 SHA 的构建被取消或仍在运行"
    return Verdict(DEPLOY_UNRELATED, f"DEPLOY_UNRELATED — {reason}，属部署链问题，不是你的线，别重跑")


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


def deployment_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"pr-state.py {argv[0]}", description=(
        "离线部署判定：dev 读取 {logs, status?, conclusion?}；deploy-failure 读取 {run, changed_files}"
    ))
    parser.add_argument("--json-input", required=True, metavar="FILE", help="部署证据 JSON 文件")
    if argv[0] == "dev":
        parser.add_argument("--sha", required=True, help="完整合并 SHA（40 位）")
    args = parser.parse_args(argv[1:])
    payload = json.loads(Path(args.json_input).read_text(encoding="utf-8"))
    if argv[0] == "dev":
        verdict = classify_dev(args.sha, payload)
    else:
        verdict = classify_deploy_failure(payload.get("run", {}), payload.get("changed_files"))
    print(verdict.line)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("dev", "deploy-failure"):
        return deployment_command(argv)
    parser = argparse.ArgumentParser(
        description="一次问清一个 PR 的真实状态，给出唯一结论和下一步",
        epilog="部署子命令：dev --sha SHA --json-input FILE；deploy-failure --json-input FILE",
    )
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
