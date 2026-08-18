"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.platform_notices import (
    EVENT_CI_FAILED,
    EVENT_MERGE_REFUSED,
    SEVERITY_ERROR,
    WHO_CHEESE,
    notice,
)
from app.domain.alert.models import AlertKind, AlertLevel
from app.domain.alert.services import AlertService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import looks_like_agent_handle
from app.domain.machine.services import MachineService
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import AiMode, Project, ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.review import (
    archive,
    commit_message,
    delivery,
    notes,
    pr_publish,
    pr_text,
)
from app.domain.review import forge as forge_mod
from app.domain.review.models import AcceptCard, AcceptStatus, GateOutcome
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.schemas import AcceptCardOut
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.webhook import service as webhook_service
from app.domain.workspace import identity

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.review.github_pr import PullRequestStatus

logger = logging.getLogger("cheesex.review")

# note 前缀家族 (docs/topics/诊断信息搬上验收卡.md). 多个写入方共用一条 `note`,
# 靠前缀互相识别 —— 所以每个前缀都必须是**具名常量**, 不能靠 "⚠️" 这个共同的
# 表情去粗判 (2026-08-10 修的就是这个: 用 "⚠️" 粗判会让"轮询暂停"冒充"重推
# 失败", 把真正的 CI 失败通知整个吞掉, 见 _nudge_pr_fix).
_REPUSH_FAILED_PREFIX = notes.REPUSH_FAILED_PREFIX
#: 本地话题分支与 PR 分支分叉 (采纳即合并 #296, 2026-08-12). `push_topic_branch_
#: for_github_pr` 是**非强制**推送，一旦本地分支被 jj rewind / rebase 挪到了 PR
#: 分支的祖先或旁支上（bookmark set --allow-backwards 允许回退），plain push 就会
#: 被 GitHub 以 non-fast-forward 拒绝——而轮询每 60 秒无脑重试这条注定失败的推送，
#: 就是 card 946bf5de 每 ~70 秒失败一次的死循环。检测到不能快进就**不推**，留一条
#: 具名 note 交给芝士在工作区把 PR 分支合并进来，而不是替它强推覆盖 PR 上的提交。
_REPUSH_DIVERGED_PREFIX = notes.REPUSH_DIVERGED_PREFIX
_POLL_PAUSED_PREFIX = notes.POLL_PAUSED_PREFIX
# One occurrence per automatic base-update (#468) — counted to cap rebase loops.
_REBASE_NOTE_MARK = notes.REBASE_NOTE_MARK
#: 采纳现场补开 App PR 失败（存量无 PR 卡，#296 stage 1 的回归修复）。开不出 PR
#: 时采纳停下、原因亮在卡上——绑定 GitHub 的项目绝不静默本地合并直推 main
#: （all commits go through PR）。卡保持 pending，人处理后可直接重试采纳。
_ACCEPT_PR_OPEN_FAILED_PREFIX = notes.ACCEPT_PR_OPEN_FAILED_PREFIX
#: 卡上有 PR 但此刻推进不了（GitHub 不可达 / PR 被关闭未合并 / …）。绑定 GitHub
#: 的项目采纳只通过合并 PR 完成 (#363)——这类失败停下亮出来，永不落 local merge。
_ACCEPT_PR_STALLED_PREFIX = notes.ACCEPT_PR_STALLED_PREFIX
#: 未接 GitHub 的项目 (#363)：平台自己就是 forge，local merge 是它唯一、正当的
#: 采纳语义——不是降级。这句写在卡上，让它和「该走 PR 却没走」的卡一眼可分。
#: 合并很久了，部署既没成功也没失败——最常见的成因是这个提交根本没有部署 run
#: (2026-08-11 实测)。比"还在等"强、比"❌ 部署失败"弱，所以是自己的前缀。
_DEPLOY_STALLED_PREFIX = notes.DEPLOY_STALLED_PREFIX

#: 一次轮询里最多问 GitHub 多少次「这次成功部署包含我的提交吗」
#: (`_later_successful_deploy`)。顶替我们的那次部署必然是合并之后最近的几次之一，
#: 而这段代码每 60 秒跑一次 —— 无上限地遍历会把一次误报变成持续的 API 消耗。
_MAX_SUPERSEDE_COMPARES = 5

#: pending_gate 孤儿卡 (2026-08-11). 判死的卡和检查真红了的卡都落在 `gate_failed`
#: 上，但对芝士意味着完全相反的下一步——「没跑完」= 原样重递，「没通过」= 去修
#: 代码。状态列分不开，所以**这条前缀就是那个区分**：它在 note 和 gate_output 里
#: 都出现，任何读卡的人/代码靠它判断，不要靠猜 gate_output 是不是空的。
GATE_ABANDONED_PREFIX = notes.GATE_ABANDONED_PREFIX
#: 人工作废 (2026-08-11)。作废复用 `revoked` 终态（archive.py 收敛非终态卡时也
#: 用它），所以「谁作废的、为什么」只能靠这条前缀留在 note 里。
VOIDED_PREFIX = notes.VOIDED_PREFIX
#: 等 CI (App 采纳等 CI 再合)。`pr_open` 期间「什么都没发生」和「还在等」在卡面上
#: 长得一模一样——一张不动的卡读起来像死了。这条前缀让等待自己说话：在等哪几项、
#: 已经等了多久。它是 note 家族里**优先级最低**的一条：只在 note 为空、或上一条
#: 也是它自己的时候才写，绝不盖掉 ⚠️/🚫/✋/❌/🌿/🚪 这些描述真实故障的 note。
WAITING_CHECKS_PREFIX = notes.WAITING_CHECKS_PREFIX
#: 人工放行 (App 采纳等 CI 再合)。红着合有时是对的（CI 基础设施抽风、与本次改动
#: 无关的既有失败），不能接受的是**没有人做过这个决定**。这条前缀就是那个署名：
#: 谁、什么时候、当时检查是什么状态、理由。默认拒绝、显式放行。
FORCE_MERGED_PREFIX = notes.FORCE_MERGED_PREFIX

#: 等待提示里「已等多久」的粒度。轮询每 60 秒一次，按分钟写会让这条 note 每一轮
#: 都变一次（等于每分钟一次无意义的写 + UI 抖动）；按 5 分钟分档，一次等待里它
#: 最多每 5 分钟变一次，而人想知道的「等很久了没有」照样看得出来。
_WAIT_BUCKET_MINUTES = 5


@dataclass(frozen=True)
class _GitHubCredentials:
    """驱动一张 `pr_open` 卡所需的 GitHub 凭据 —— **两把钥匙，不是一把**。

    个人 token 那条路上它们是同一个字符串（一个 OAuth token 什么都能干）。App
    这条路上它们不是，而且分不开就会坏：`GitHubAppTokens.write_token()` 请求的是
    `contents:write` + `pull_requests:write` + `metadata:read`，**没有 `checks`**
    （`_WRITE_PERMISSIONS`，故意的：写权限不该顺带把「读检查」也捆进去；
    `GitHubPRClient.check_runs` 早就为此改用只读 mint 了）。拿写 token 去读
    `/commits/{ref}/check-runs` 会 403 —— 而轮询器把它当成一次 GitHub 抖动，下一
    轮再来，于是卡永远停在 `pr_open`，卡面上什么都不会写。

    所以：GET 用 `read`，推分支和合并用 `write`。
    """

    write: str
    read: str


def _nudge_note_prefix(stage: str) -> str:
    return f"⚠️ {stage} 检查未通过："


_MERGE_FAILED_MESSAGE = (
    "Acceptance could not complete because the topic could not be merged. "
    "The card remains pending and the topic stays active; repair the workspace "
    "and retry."
)


#: How much of the failure detail rides in the nudge message. The detail is
#: already bounded per job upstream (`github_pr._failure_detail`); this is the
#: backstop that keeps a pathological payload from flooding the topic.
_NUDGE_TAIL_LIMIT = 4000


def _ci_log_howto(repo_full_name: str) -> str:
    """The "where do I read the rest" paragraph of a CI-failure nudge.

    The excerpt above it is deliberately short, so the message has to say how
    to get the whole thing — and that path was undocumented everywhere 芝士
    can read (not in `.claude/`, not in `CLAUDE.md`): the read-only token is
    a `cheese gh-token` away, but nothing told it so, and nothing told it the
    repo's name either, which `gh api repos/:owner/:repo/...` needs. Both are
    in hand right here, at the one moment they're wanted.
    """
    repo = repo_full_name or "<owner>/<repo>"
    return (
        "上面是失败 job 的名字、Actions 页面链接，以及日志里错误行附近的片段。"
        "要看完整日志，在本话题的工作区里跑：\n"
        "```bash\n"
        "export GH_TOKEN=$(cheese gh-token)   # 只读 token，约 1 小时过期\n"
        f"gh api repos/{repo}/actions/jobs/<job_id>/logs\n"
        "```\n"
        "（`<job_id>` 就是上面 Actions 链接里 `/job/` 后面那串数字；"
        f"要重新列出这次提交的所有检查：`gh api "
        f"repos/{repo}/commits/<head_sha>/check-runs`。）\n"
    )


# 两阶段采纳 (PR迭代式) 降级原因可见性: TOKEN_UNAVAILABLE_* → 人能看懂的中文说明,
# 绝不包含 token/密文本身 —— 这些常量只是"哪个前提没满足"的分类标签.
_TOKEN_UNAVAILABLE_MESSAGES = {
    "not_connected": "批准人未连接 GitHub 账号",
    "undecryptable": (
        "批准人的 GitHub 账号已连接，但存储的 token 无法解密"
        "（密钥已轮换，或数据损坏），需要重新连接账号"
    ),
    "expired_no_refresh": (
        "批准人的 GitHub token 已过期，且没有可用于续期的 refresh token，"
        "需要重新连接账号"
    ),
    "refresh_failed": (
        "批准人的 GitHub token 已过期，续期失败"
        "（GitHub 拒绝、refresh token 本身不可用，或网络错误）"
    ),
    "provider_not_configured": "服务器未启用 GitHub 账号连接（oauth provider 未配置）",
}


def _describe_token_unavailable(reason: str | None) -> str:
    if reason is None:
        return "批准人未连接 GitHub 账号"
    return _TOKEN_UNAVAILABLE_MESSAGES.get(
        reason, f"批准人的 GitHub token 不可用（{reason}）"
    )


# ---- CI 镜像与 405 如实转译 (#362, 对齐 GitHub) ------------------------------
#
# 平台对可合并性的全部立场：问 forge、显示 forge 说的、转译 forge 拒绝的原因—
# 自己永远不发明拦截。GitHub 在没有 branch protection 时（本仓是 free plan 私有
# 仓库，required status checks 配不了——admin 实测 403 "Upgrade to Pro"，
# 2026-08-13）红着的 checks 照样能 merge：它做的是把检查状态醒目摆在 merge 按钮
# 上方，把决定留给人。平台持同一姿态：采纳界面在点击前展示 /pr-checks 的实时
# 状态（前端 WorkspaceView 的 PR chip + 每条 check 行），合并时再读一次并把
# 当时的状态原样写进卡片 note 和房间通知——人看着红点采纳是合法决定，但那个
# 决定必须留痕。


#: check-run conclusions that read as green. `neutral` and `skipped` are
#: non-blocking by GitHub's own semantics; everything else that is not
#: `success` (failure / timed_out / cancelled / action_required / stale) reads
#: as red.
_GREEN_CHECK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})


def _checks_summary(checks: list[dict] | None) -> str:
    """One line mirroring the forge's check state at merge time — GitHub
    merge-box style, for the card note and the room notification. Never used
    to block anything."""
    if checks is None:
        return "合并前未能读取 CI 检查状态"
    if not checks:
        return "该 PR 没有任何 CI 检查"
    not_green = [
        c
        for c in checks
        if c.get("status") != "completed"
        or (c.get("conclusion") or "") not in _GREEN_CHECK_CONCLUSIONS
    ]
    if not not_green:
        return f"CI 检查全绿（{len(checks)} 项）"
    red = [c for c in not_green if c.get("status") == "completed"]
    running = [c for c in not_green if c.get("status") != "completed"]
    parts = []
    if red:
        parts.append("未通过：" + "、".join(str(c.get("name")) for c in red[:5]))
    if running:
        parts.append("还在跑：" + "、".join(str(c.get("name")) for c in running[:5]))
    return f"CI 检查未全绿（{'；'.join(parts)}）"


def _github_merge_refusal_message(exc: BaseException) -> str:
    """GitHub's own sentence out of a 405 body, for faithful surfacing — the
    body rides in the exception text as JSON (`{"message": "...", ...}`).
    Empty string when there is no parseable message (fakes, truncation)."""
    match = re.search(r'"message"\s*:\s*"([^"]+)"', str(exc))
    return match.group(1)[:300] if match else ""


def _with_pr_degrade_note(base: str, pr_degrade_reason: str) -> str:
    """Prefix a local-merge accept note with WHY the two-phase PR path was
    skipped, so a card that fell back reads as "两阶段采纳没走成，原因是 X；
    然后走了老路径，结果是 Y" instead of looking identical to a topic that
    was never eligible for the PR path at all. No-op when the PR path never
    even attempted a degrade for this accept (`pr_degrade_reason` empty).

    One shape: ⚠️ + the raw reason. Every degrade that ends in a local merge
    deserves a human's glance now — the calm ℹ️ variant for workflow-scope
    rejections is gone with its "known permanent limitation" premise: the
    platform's App credential has held `workflows:write` since 2026-08-12
    (installation 152342238), so a workflow-file rejection is a failure to
    look at, not a fact of life to absorb."""
    if not pr_degrade_reason:
        return base
    prefix = f"⚠️ 未走 PR 采纳（{pr_degrade_reason}）"
    return (f"{prefix}；{base}" if base else prefix)[:2000]


# 递卡互斥 (2026-08-10): a topic may have at most one card that is still
# "live" — awaiting a decision, mid-delivery, or blocked mid-accept. Each gets
# its own message because the way OUT differs (改验收人 / 等交付 / 解冲突).
#
# `accepted` joins them for a different reason (2026-08-17): it is not live, it
# is DONE, and it is what freezes the delivery surface now that a merge no
# longer archives the topic. Archive used to do double duty — "someone put this
# away" AND "this branch already landed, stop offering it" — and only the first
# half is a human's call (#442 decision 1). The second half has to survive on
# its own, because a card filed on a branch that is already in main opens a PR
# with no commits: GitHub refuses it (422 No commits between), the platform
# reads that as a failure and degrades to a local merge, and the card reaches
# `accepted` having delivered nothing at all.
_BLOCKED_BY_CARD_MESSAGES = {
    AcceptStatus.pending: "已有待处理的验收卡，请改验收人而不是再递一张",
    AcceptStatus.pending_gate: "已有待处理的验收卡，请改验收人而不是再递一张",
    AcceptStatus.pr_open: (
        "这个话题的验收卡已经在交付中（PR 正在跑 CI / 等部署），"
        "不能再递一张；要改动就提交到工作区，平台会自动同步到那个 PR"
    ),
    AcceptStatus.conflict: (
        "上一张验收卡卡在合并冲突上，解决冲突后由人重试采纳，不要再递一张"
    ),
    AcceptStatus.accepted: (
        "这个话题已经交付过一次：上一张验收卡合并了，这条分支已经在 main 上。"
        "再递一张开出来的 PR 没有新提交，GitHub 会拒绝，平台会降级成本地合并——"
        "卡看起来采纳了，实际什么都没交付。\n"
        "话题没有归档，接着讨论、接着写文档都可以（归档是人的决定，不是合并的"
        "副作用）；要再交付一份改动，请在房间里开一件新的事——新话题＝从 main "
        "新切的分支。"
    ),
}
_CARD_BLOCKS_NEW_CARD = tuple(_BLOCKED_BY_CARD_MESSAGES)

#: Refusal for a card filed with no commit subject at all. It is long on
#: purpose: the reader is an agent one turn away from re-filing, and an error
#: that only says "缺少 change_subject" costs a whole turn to act on. The
#: example is a real, valid subject — copy-pasteable, not a placeholder.
_MISSING_SUBJECT = (
    "递卡必须带提交标题（--subject）。它不是给人看的说明，是这次改动留在 "
    "git 历史里的那一行：递卡开 PR 用它当标题，采纳时整个分支被压成一个"
    "提交，标题还是它。\n"
    "写法：`type(scope): description`，type 取值 "
    f"{', '.join(commit_message.TYPES)}；英文祈使句，"
    f"≤{commit_message.MAX_SUBJECT} 字符，结尾不加句号。\n"
    "例：\n"
    '  cheese accept-request lisi "最懂这块" \\\n'
    "    --subject 'fix(accept): open the PR as the requester, not the bot' \\\n"
    "    --body 'PRs opened with the App token belong to the bot on GitHub, "
    "so the person whose work it is gets no attribution.'"
)

#: Where an alembic revision lives. Two live cards each ADDING a file under
#: here is the one overlap a machine can judge on its own (#314).
_ALEMBIC_VERSIONS_DIR = "alembic/versions/"


# ---- 人类授权动作前移 (2026-08-10) -----------------------------------------
#
# 人点的那一下从「合并前」挪到了「开 PR 前」：它买到的是"以我的名义把这条分支
# 推上去、让真 CI 开始跑，之后的迭代不用再问我"。摩擦因此是 O(1) 而不是
# O(迭代次数)。代价是那一刻 CI 还没有任何结果，所以机器后来自动合并之前必须自
# 己守住三道闸——这三条就是整个方案的安全阀，任何一条命中都不自动合并，回来找人。

#: 例外 3: prod 永远两次都要人。本项目当前采纳目标是 main/dev，这里先把判断位
#: 留出来（不是猜测式匹配：只认这几个确切分支名，`main-prod` 之类要显式加）。
_PROD_BASE_BRANCHES = frozenset({"prod", "production", "release"})

#: 例外 1 的两类"敏感路径"。第三类（新增文件）看的是 diff 状态而不是路径。
_CI_CONFIG_PREFIX = ".github/"


def _is_prod_base(base: str) -> bool:
    return base.strip().lower() in _PROD_BASE_BRANCHES


def _is_migration_path(path: str) -> bool:
    """迁移文件——改数据库结构的东西，人授权时没看见就不该跟着自动合进去。"""
    return (
        "alembic/versions/" in path
        or path.startswith("migrations/")
        or "/migrations/" in path
    )


def _drift_reasons(
    authorized: list[tuple[str, str]], current: list[tuple[str, str]]
) -> list[str]:
    """例外 1: 授权之后 head 又动了，新 diff 里有没有超出授权范围的东西。

    只看"授权时那份 diff 里没有的路径"——人已经看过的文件被继续改，正是这次设计
    要放行的迭代（改 CI 报错、补一行断言），拦下来就把 O(1) 变回 O(迭代次数)。
    在这些新出现的路径里，只有三类算越界：新增文件 / 碰 `.github/` / 碰迁移。"""
    known = {path for _, path in authorized}
    reasons: list[str] = []
    for status, path in current:
        if path in known:
            continue
        if status == "added":
            reasons.append(f"新增了文件 {path}")
        elif path.startswith(_CI_CONFIG_PREFIX):
            reasons.append(f"动了 CI 配置 {path}")
        elif _is_migration_path(path):
            reasons.append(f"动了数据库迁移 {path}")
    return reasons


#: Tier-2 required 名单 (#468) 的一条：检查名 + 「什么样的改动才要求它出现」。
#:
#: `paths` 为空 = 无条件要求。非空 = 只有当 PR 的改动命中其中某条 glob 时才要求
#: ——因为 workflow 自己就带路径过滤：本仓库的 `test` 只在改动碰 `backend/**` 时
#: 触发，一个纯前端 PR 上它**永远不会出现**，把缺席一律读作「还在等」就是让这类
#: 卡片无限等下去（2026-08-16 实测：#483/#485/#486 全绿却等到人工去 GitHub 合）。
@dataclass(frozen=True)
class _RequiredCheck:
    name: str
    paths: tuple[str, ...] = ()


def _parse_required_checks(spec: str) -> list[_RequiredCheck]:
    """`"test:backend/**;.github/workflows/test.yml,lint"` → 名单。

    逗号分条目，条目里 `名字:glob;glob` —— 冒号后面是「这条检查对哪些改动有效」，
    不写冒号就是无条件要求。glob 用 GitHub Actions 路径过滤那一套的子集：`**`
    跨目录、`*` 不跨目录、`?` 单字符。"""
    out: list[_RequiredCheck] = []
    for entry in spec.split(","):
        name, sep, globs = entry.strip().partition(":")
        name = name.strip()
        if not name:
            continue
        paths = tuple(g.strip() for g in globs.split(";") if g.strip()) if sep else ()
        out.append(_RequiredCheck(name=name, paths=paths))
    return out


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """GitHub Actions 路径过滤那套 glob 编译成正则。

    只实现 workflow `paths:` 里真正会写的三个通配：`**`（跨目录）、`*`（不跨
    目录）、`?`（单字符）。`fnmatch` 不能用 —— 它的 `*` 会跨 `/`，那样
    `frontend/*.ts` 会把 `backend/a/b.ts` 也算命中，等于把这道阀关掉。"""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _diff_touches(paths: tuple[str, ...], changed: list[tuple[str, str]]) -> bool:
    """这次改动里有没有文件命中 `paths` 里的任何一条 glob。"""
    return any(_glob_regex(p).match(path) for _, path in changed for p in paths)


#: `_required_and_absent` 的结论：名单里缺席的检查中，哪几项对**这次改动**仍然
#: 必需 —— 外加**这个结论是怎么得出来的**。
#:
#: `fallback is None` = 平台真的算过改动范围，这几项该出现而还没出现。
#: `fallback` 非空 = 范围根本没算出来（认不出基线 / GitHub 没给文件清单），于是
#: 保守地把带路径条件的项也照旧算必需，`fallback` 就是那句「为什么没算出来」。
#:
#: 这两件事以前在卡面上一个字都不差（都写「required 检查还没出现：test」），
#: 人只能去翻后端日志才分得清 —— 而那份日志的保留期只有「距上次部署多久」。
@dataclass(frozen=True)
class _AbsentRequired:
    names: list[str]
    fallback: str | None = None


def _absent_required_tail(shown: str, fallback: str | None) -> str:
    """等待提示里 `⏳ 等 CI（…）：` 后面那半句。

    回退的那半句必须自陈是回退：人看到「还没出现」会去 Actions 页面找那个
    workflow，而回退状态下真正该看的是「这次改动到底碰了什么、这项检查是不是
    本来就不会触发」——两条完全不同的排查路。"""
    if fallback is None:
        return "required 检查还没出现：" + shown
    return f"平台没能判断这次改动碰了哪些文件（{fallback}），保守起见仍然要求：{shown}"


def _absent_required_timeout(
    shown: str, fallback: str | None, minutes: int
) -> tuple[str, str]:
    """等过头交给人时的 (reason, explain) —— 同样要分清等待和回退。

    回退状态下照搬「多半是 workflow 没被触发、被改名或被禁用」是在给人一个平台
    根本没验证过的判断：它连这次改动碰没碰后端都不知道。"""
    if fallback is None:
        return (
            f"required 检查 {shown} 迟迟没有出现（已等约 {minutes} 分钟）——"
            "多半是 workflow 没被触发、被改名或被禁用，"
            "平台不会替人判定它可以不跑",
            "这不是检查红了，是它根本没报到：平台只能确认「没人跑过这项检查」，"
            "不能替人认定它不需要跑。",
        )
    return (
        f"required 检查 {shown} 迟迟没有出现（已等约 {minutes} 分钟），而平台"
        f"没能判断这次改动碰了哪些文件（{fallback}），只能保守地仍然要求它",
        "平台没算出这次改动的范围，所以不知道这几项检查是「该跑而没跑」还是"
        "「本来就不会对这次改动触发」——这一条需要人来判断，机器不猜。",
    )


#: 人工放行时「当时检查是什么状态」对应的那半句话。以前它是写死的「明知检查未
#: 全绿仍合并」，而放行的常见场景之一恰恰是检查**已经全绿**、平台却还没合（比如
#: 在等一项对这次改动根本不会触发的 required 检查）：2026-08-17 的 PR #520 因此
#: 在卡上留下了一条自相矛盾的历史 ——「明知检查未全绿仍合并（合并时检查状态：
#: success（全部 5 项检查通过））」。写错的留痕比没有留痕更糟：事后追责会照着它
#: 去问一个从没发生过的决定。
_FORCE_MERGE_VERDICTS = {
    "failure": "明知检查未全绿仍合并",
    "success": "当时检查其实已经全绿",
    "pending": "没等检查跑完",
    "no_checks": "当时没有任何 CI 跑过这次改动",
}


def _force_merge_verdict(state: str | None) -> str:
    """`None` = 那一刻根本没读到检查状态（凭据坏了不该把人锁在门外，所以照样
    放行）——它和「读到了，是红的」是两回事，卡面不能把前者写成后者。"""
    if state is None:
        return "当时读不到检查状态"
    return _FORCE_MERGE_VERDICTS.get(state, f"当时检查状态是 {state}")


def approvals_required_of(project: Project | None) -> int:
    """主分支保护 (spec §4.4): distinct approvals an accept needs. Default 1 —
    the accepter's own accept counts, so unconfigured projects are unchanged."""
    if project is None:
        return 1
    try:
        return max(1, int((project.settings or {}).get("approvals_required") or 1))
    except (TypeError, ValueError):
        return 1


def check_command_of(project: Project | None) -> str | None:
    """The project's stored `check_command`, or None.

    采纳即合并退役闸门 (docs/accept-is-merge.md #296, stage 1): this setting no
    longer gates anything. The platform used to run it in the topic workspace
    before a card reached the reviewer; that whole mechanism is retired — a
    repository declares its checks in `.github/workflows`, the forge runs them,
    and the card mirrors the forge's result. The value is still stored and read
    back (the settings endpoint round-trips it, and a later stage clears it)
    but nothing consumes it to produce a green card any more.
    """
    if project is None:
        return None
    cmd = str((project.settings or {}).get("check_command") or "").strip()
    return cmd or None


class AcceptService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = AcceptCardRepository(session)
        self._topics = TopicRepository(session)
        self._projects = ProjectRepository(session)
        self._machines = MachineService(session)

    async def _topic_or_404(self, topic_id: uuid.UUID) -> Topic:
        topic = await self._topics.get(topic_id)
        if topic is None:
            raise NotFoundError("Topic not found")
        return topic

    async def _card_or_404(self, card_id: uuid.UUID) -> AcceptCard:
        card = await self._repo.get(card_id)
        if card is None:
            raise NotFoundError("Accept card not found")
        return card

    async def _release_billed_compute(self, topic: Topic) -> None:
        """Release the delivered topic's BILLED compute — its Cloud VM — and
        nothing else.

        This used to tear down the working surface too (the sandbox container,
        and a device topic's screen plus its remote work dir). It doesn't any
        more, because 交付完成 no longer means 话题结束 (#442 decision 1: 一个
        话题往往是连续的): the room keeps working after the merge, and killing
        its box mid-life is not free — a rebuilt container loses everything
        installed inside it (jj, procps, the git identity the test suite needs),
        so the next turn pays for a teardown nobody asked for. Both surfaces
        have their own idle reaper (``scheduler.reap_idle_containers`` /
        ``reap_idle_device_screens``), which is where reclaiming them belongs:
        the question "is anyone still using this" is about activity, not about
        whether a branch landed.

        The Cloud VM is the one exception and it stays here, deliberately: it is
        the only one that costs money per hour and the only one with NO reaper —
        release is manual-archive-or-nothing (`TopicService._release_cloud_machine`,
        #442 decision 3). Dropping it here would turn every merged topic into a
        billed leak that nothing ever collects. So it is not best-effort either:
        accept must not report success if MicroCloud did not accept deletion.

        Consequence worth knowing: a Cloud-backed topic that just delivered has
        no VM until someone provisions one again, and `ensure_topic_machine`
        requires an authorized human caller — so its next turn needs a human to
        speak. That is the pre-existing trade-off of "archive is the VM's only
        lifecycle", not a new one; the reclamation policy itself is still open."""
        await self._machines.release_topic_machine(topic.id)

    async def create_card(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handle: str,
        routing_reason: str = "",
        change_subject: str | None = None,
        change_body: str | None = None,
    ) -> AcceptCard:
        topic = await self._topic_or_404(topic_id)
        # Before anything else touches the DB: a missing or malformed subject is
        # the filer's to fix in the same breath, and it is the one thing here
        # that ends up in permanent history.
        #
        # 2026-08-17: omitting it used to be allowed, and `pr_text` quietly
        # filled in `chore: <话题标题>`. PR #500 is what that looks like from the
        # outside — a chat-room name as the title of a merged change. The
        # fallback stays (rows filed before `change_subject` existed still have
        # NULL), but nothing new is allowed to reach it.
        change_subject = (change_subject or "").strip()
        if not change_subject:
            raise ValidationError(_MISSING_SUBJECT)
        try:
            change_subject = commit_message.check_subject(change_subject)
        except commit_message.InvalidSubject as exc:
            raise ValidationError(str(exc)) from exc
        # 归档是人主动收起来的话题，工作面跟着冻结。它不再是"交付完成"的同义词
        # (#442 decision 1) —— 那一半由下面的 `accepted` 卡挡着。
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能再递验收卡")
        # One card at a time, not a broadcast (spec §4.4): re-route / wait
        # instead of stacking a new one.
        #
        # 2026-08-10: the guard used to cover only pending/pending_gate, so a
        # card mid-DELIVERY (`pr_open`, a real PR running CI) or stuck on a
        # merge `conflict` did not block a second card. The frontend only ever
        # renders the NEWEST card, so the older one — and the PR it was
        # driving — vanished from the UI while the poller kept advancing it.
        # Every non-terminal status blocks now; `gate_failed` and `gate_blocked`
        # deliberately do not (a red gate voids the card, and re-递卡 after fixing
        # IS the flow — same for a gate that never ran: 芝士 fixes the check
        # environment and re-files. Adding either here locks 芝士 out for good).
        existing = await self._repo.list_for_topic(topic_id)
        blocking = next(
            (c for c in existing if c.status in _CARD_BLOCKS_NEW_CARD), None
        )
        if blocking is not None:
            raise ValidationError(_BLOCKED_BY_CARD_MESSAGES[blocking.status])
        # 采纳即合并 (docs/accept-is-merge.md #296, stage 1): the card is always
        # born `pending`. The old machine gate (`check_command` → born
        # `pending_gate`, platform runs the check, only green promotes to
        # pending) is retired: a card is the platform's view of a PR, and real
        # CI on that PR — not a private platform check — is what decides whether
        # a change is good. `pending_gate`/`gate_failed`/`gate_blocked` are no
        # longer entered; existing rows keep their historical values and their
        # exits (review/gate_sweep.py, AcceptService.void) stay in place.
        card = await self._repo.add(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=AcceptStatus.pending,
            change_subject=change_subject,
            change_body=(change_body or None),
        )
        await self._warn_about_a_second_pending_migration(topic)
        return card

    async def _warn_about_a_second_pending_migration(self, topic: Topic) -> None:
        """两张未决卡各带一个新迁移 → 在房间里说一声 (#314).

        The narrow, clean half of "two rooms doing the same work". Two branches
        editing the same existing file is ordinary — parent and child legitimately
        touch one file each. Two branches each CREATING an alembic revision is
        not: at best it forks the chain the moment both land (#312), at worst the
        two are the same feature implemented twice, which is what happened on
        2026-08-11 — `topics.progress` (a column) and `topic_progress` (a table),
        two incompatible data models, each with a green card.

        Deliberately a notice, not a block. The judgement "these two are the same
        work" needs a human; what a machine can contribute is making sure the
        human is looking at the moment there is something to look at. Both cards
        being individually green is exactly the state that hides this.

        Best-effort throughout: a git read that fails, or a room that won't take
        the message, must never stop someone filing a card.
        """
        from app.domain.workspace import service as ws

        def migrations(topic_id: uuid.UUID) -> list[str]:
            try:
                added = ws.topic_added_files(topic.project_id, topic_id)
            except Exception:  # noqa: BLE001 — a diagnostic must not break 递卡
                return []
            return [p for p in added if _ALEMBIC_VERSIONS_DIR in p]

        mine = migrations(topic.id)
        if not mine:
            return
        others = await self._repo.list_live_in_project(
            topic.project_id, statuses=_CARD_BLOCKS_NEW_CARD
        )
        collisions = [
            other
            for other in others
            if other.topic_id != topic.id and migrations(other.topic_id)
        ]
        if not collisions:
            return
        rooms = []
        for other in collisions:
            sibling = await self._topics.get(other.topic_id)
            rooms.append(f"「{sibling.title}」" if sibling else str(other.topic_id))
        self._notify_merge_result(
            topic,
            "⚠️ **另一张未决的验收卡也新建了迁移**："
            + "、".join(rooms)
            + "。两张卡各带一个 alembic revision，合到一起会把迁移链分叉"
            + "（#312），而且往往说明同一件事被做了两遍（#314 那次是 "
            + "`topics.progress` 列和 `topic_progress` 表）。"
            + "\n\n这里不拦，只是提醒验收的人**先比一下两张卡的改动**："
            + "如果确实是两件事，照常采纳，先合的那张合完后另一张要 rebase。",
        )

    async def project_id_for_topic(self, topic_id: uuid.UUID) -> uuid.UUID:
        """The topic's project id — what the PR-publish dispatch needs to resolve
        the App installation and upstream (采纳即合并 #296)."""
        topic = await self._topic_or_404(topic_id)
        return topic.project_id

    async def mark_gate_started(self, *, card_id: uuid.UUID) -> AcceptCard:
        """闸门开跑打点 (孤儿卡, 2026-08-11). Idempotent-ish and deliberately
        forgiving: if the card already left `pending_gate` (swept as abandoned,
        or voided by a human) this is a no-op rather than an error — a
        diagnostic timestamp must never resurrect a closed card, and it must
        never be the thing that fails a check that is about to run anyway."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending_gate:
            return card
        card.gate_started_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def finish_gate(
        self, *, card_id: uuid.UUID, outcome: GateOutcome, output_tail: str
    ) -> AcceptCard:
        """Settle a pending_gate card: 绿 → pending (卡片这才递到验收人手上),
        红 → gate_failed (卡片作废，芝士被 nudge 去修), 没跑成 → gate_blocked
        (同样不递出去，但检查对代码没有结论，别说成"未通过")."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending_gate:
            raise ValidationError("只有等待检查的验收卡能记录检查结果")
        card.gate_output = output_tail
        if outcome == GateOutcome.passed:
            card.status = AcceptStatus.pending
            card.gate_passed_at = datetime.now(UTC)
        elif outcome == GateOutcome.blocked:
            card.status = AcceptStatus.gate_blocked
        else:
            card.status = AcceptStatus.gate_failed
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def list_for_topic(self, topic_id: uuid.UUID) -> tuple[list[AcceptCard], int]:
        cards = await self._repo.list_for_topic(topic_id)
        return cards, len(cards)

    async def open_pr_card_ids(self) -> list[uuid.UUID]:
        """还开着 PR、等着被推进状态机的验收卡 id —— 调度器每轮的输入。

        只回 id 不回对象：调度器一张卡一个事务，跨事务复用 ORM 对象拿到的是过期状态。
        「哪个状态算开着」是本领域的知识，所以判断留在这里，而不是让调度器自己去查
        ``AcceptCardRepository``。

        孤儿卡修复 (2026-08-10) 的那条判据也在这里面：已归档话题上的卡不算开着——
        推进它们等于拿批准人的 GitHub token 去动没人跟的活儿。
        """
        cards = await self._repo.list_pr_open_on_active_topics()
        return [c.id for c in cards]

    async def anybody_still_waiting(self, topic_ids: list[uuid.UUID]) -> bool:
        """这些话题里，还有没有一张卡等着人决议 —— 归档前必须问的那一句。

        归档会把非终态的卡当场收敛掉（`review/archive.py`），所以任何**平台自己
        发起**的归档（结论卡默认采信就是）都得先问这一句，否则会把一张验收人还
        没看见的卡作废掉。判据（哪些状态算"还等着"）留在本领域，调用方不该自己
        去数状态——这正是 `close_cards_for_archived_topic` 收敛的那一张表。

        话题是一组而不是一个：归档是级联的，孙子话题的卡会跟着一起被收掉。
        """
        return bool(
            await self._repo.list_live_for_topics(
                topic_ids, statuses=archive.OPEN_CARD_STATUSES
            )
        )

    async def latest_decision_at(self, topic_ids: list[uuid.UUID]) -> datetime | None:
        """这些话题上最后一张卡是什么时候有结果的 —— None = 从来没有过卡。

        给"卡决议之后留一个重新递卡的窗口"用：驳回的意思是回去改了再来，而归档
        话题递不出新卡，所以窗口从这一刻起算。
        """
        return await self._repo.latest_decision_at(topic_ids)

    async def reviewer_topic_ids(
        self, topic_ids: list[uuid.UUID], reviewer_handle: str
    ) -> dict[uuid.UUID, bool]:
        """{话题: 这上面还有没有一张卡在等这个人} —— 只有点过名给他的话题会出现。

        给话题列表的「与我的相关性」用，一次查完：**在不在 key 里**是「这话题
        点过我的名」（采纳完也还算我的事），**value** 是「现在就等我动手」。
        """
        return await self._repo.reviewer_topic_ids(topic_ids, reviewer_handle)

    async def describe(self, card: AcceptCard) -> dict:
        """AcceptCardOut payload enriched with the vote state (approvals live in
        their own table; the requirement is a project setting)."""
        data = AcceptCardOut.model_validate(card).model_dump(mode="json")
        # 「这条 note 有多严重」由拥有这些前缀的那一侧算（domain/review/notes.py），
        # 随卡下发一个码。浏览器过去自己按 emoji 开头猜，而那份硬编码列表漏掉了
        # 后来加的 `🌿`。
        level = notes.note_level(card.note)
        data["note_level"] = level.value if level else None
        data["approvals"] = await self._repo.list_approver_handles(card.id)
        topic = await self._topics.get(card.topic_id)
        project = (
            await self._projects.get(topic.project_id) if topic is not None else None
        )
        data["approvals_required"] = approvals_required_of(project)
        # 交付进度: the steps THIS project has, decided here rather than derived
        # in the browser from `pr_merged_at` — whether a project has external
        # checks is a property of its forge, which only the backend can see.
        if topic is not None:
            forge = await self._resolve_forge(topic.project_id)
            data["stages"] = delivery.steps_for(card, forge)
        else:
            data["stages"] = []
        return data

    async def _enforce_protocol(self, topic: Topic, decided_by: str) -> None:
        """机构协议 (spec §4.2/§4.4): a 赛题 may require a mentor to accept a
        particular topic, and a project created from that 赛题 accepted the terms.

        Reads them from the 赛题's 项目集 (with the 赛题's own override — #370
        option (c)), reached through `project.external_task_id`. That is the link
        the 赛题 page's 「从这道赛题创建项目」 button writes; the cheesex
        `project_task_links` chain it replaced pointed at a 题目 hierarchy that
        had no way to be created.
        """
        from app.domain.space.models import SpaceCategory
        from app.domain.task.models import Task
        from app.domain.task.protocol import resolve

        project = await self._projects.get(topic.project_id)
        task_id = getattr(project, "external_task_id", None) if project else None
        if not task_id:
            return
        task = await self._session.get(Task, task_id)
        if task is None:
            return
        category = (
            await self._session.get(SpaceCategory, task.category_id)
            if getattr(task, "category_id", None)
            else None
        )
        if not resolve(category=category, task=task).mentor_required_for(topic.title):
            return
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        mentors = {m.user_handle for m in members if m.role == ProjectRole.mentor}
        if decided_by not in mentors:
            raise ValidationError("按机构协议，这个话题须由导师验收")

    async def reassign(
        self, *, card_id: uuid.UUID, reviewer_handle: str, reason: str = ""
    ) -> AcceptCard:
        """改验收人 (spec §4.4): anyone can re-route a pending accept card to a
        different reviewer."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError("只有待处理的验收卡能改验收人")
        card.reviewer_handle = reviewer_handle
        if reason:
            card.routing_reason = reason
        await self._session.flush()
        await self._session.refresh(card)
        return card

    def _forbid_ai(self, project: Project | None, handle: str, action: str) -> None:
        """Hard rule (spec §4.4): in collaborative mode AI cannot accept (or
        vote for) its own work — a human must. Autonomous mode allows it.

        Matches the whole 芝士 handle namespace, not the bare ``cheese`` string:
        每个话题的分身 acts under its own ``cheese-<topic hex>`` handle, and an
        exact-string rule would have let any 分身 walk straight through this."""
        if (
            project is not None
            and project.ai_mode == AiMode.collaborative
            and looks_like_agent_handle(handle)
        ):
            raise ValidationError(f"AI 不能{action}自己做的东西，必须有人来")

    async def approve(self, *, card_id: uuid.UUID, approver_handle: str) -> AcceptCard:
        """主分支保护 (spec §4.4): record one vote toward this card's accept.
        Idempotent per (card, approver); AI cannot vote in collaborative mode."""
        card = await self._card_or_404(card_id)
        # Votable while the card is still live (incl. behind the gate / in a
        # merge-conflict retry); decided or gate-failed cards are closed.
        if card.status not in (
            AcceptStatus.pending,
            AcceptStatus.pending_gate,
            AcceptStatus.conflict,
        ):
            raise ValidationError("验收卡已关闭，不能再批准")
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, approver_handle, "批准")
        await self._repo.add_approval(card_id, approver_handle)
        return card

    def _notify_merge_result(self, topic: Topic, content: str) -> None:
        """merge 后结果回房间: post the accept's merge outcome into the topic
        timeline via the webhook primitive's internal function (卡1) — no HTTP
        hop, no token check, this call is trusted by construction. Uses its
        own session (async_session_factory), independent of self._session, so
        the notice lands even when the accept itself is about to be rolled
        back by a raised ValidationError.

        Fire-and-forget, but through `spawn`, which holds a strong reference:
        asyncio keeps only a weak one, and this coroutine sleeps up to 35s across
        its retries — a wide window in which an unreferenced task can be
        collected mid-await. Losing it means the room never learns the accept's
        outcome at all. The accepter's HTTP response still doesn't wait on the
        notification succeeding — only on the merge itself."""
        spawn(
            webhook_service.post_with_retries(
                async_session_factory,
                project_id=topic.project_id,
                topic_id=topic.id,
                content=content,
                source="accept",
            ),
            name=f"accept notice topic={topic.id}",
        )

    async def accept(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # 机器闸门 (eval C2): the card isn't in the reviewer's hands yet / died.
        if card.status == AcceptStatus.pending_gate:
            raise ValidationError("平台检查还在进行中，检查通过后才能采纳")
        if card.status == AcceptStatus.gate_failed:
            raise ValidationError("平台检查未通过，等芝士修复后重新递卡")
        if card.status == AcceptStatus.gate_blocked:
            raise ValidationError("平台检查没能跑起来（对代码没有结论），等重新递卡")
        # pending → first attempt; conflict → retry after 芝士 resolved.
        if card.status not in (AcceptStatus.pending, AcceptStatus.conflict):
            raise ValidationError("验收卡已处理，不能重复验收")
        # 递给某个具体的人 (spec §4.4): only the routed reviewer may accept —
        # decided_by is the caller's verified actor handle, never body-trusted.
        if decided_by != card.reviewer_handle:
            raise ForbiddenError("你不是这张验收卡指定的验收人，无权采纳")

        topic = await self._topic_or_404(card.topic_id)
        # 归档会连带终结这个话题上还没决议的卡 (review/archive.py)，所以这里通常
        # 走不到；留着是为了兜住"归档与采纳同时发生"的竞态。重复采纳本身由上面的
        # 卡状态闸门挡（一张卡只能 accepted 一次），不再依赖话题被归档。
        if topic.status == TopicStatus.archived:
            raise ValidationError("话题已归档，不能重复采纳")
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "验收")

        # Institution protocol from linked Task Templates (spec §4.2).
        await self._enforce_protocol(topic, decided_by)

        # 主分支保护 (spec §4.4): the accept itself counts as the accepter's
        # vote (default requirement of 1 ⇒ 现行为不变); short of votes the whole
        # transaction rolls back and nothing merges.
        approvers = await self._repo.list_approver_handles(card_id)
        # Count this decision as a vote without persisting it yet. A merge can
        # fail outside SQLAlchemy; delaying the write keeps even callers that
        # catch ValidationError from accidentally committing a failed accept.
        votes = len(set(approvers) | {decided_by})
        required = approvals_required_of(project)
        if votes < required:
            raise ValidationError(
                f"批准人数不足，还差 {required - votes} 票（{votes}/{required}）"
            )

        # 采纳 = merging the topic branch into the project's authoritative main,
        # wherever that main lives. WHERE is the forge, and the forge is resolved
        # once (app.domain.review.forge) instead of being crossed out of
        # `pr_publish.enabled()` × binding × `card.pr_number` here — the crossing
        # that put a GitHub-bound project's accept onto a direct push to main
        # twice in one day (#362).
        #
        # What each lane means for the code below:
        #   - requires_pr (the App forge): a PR is the only way in. A card
        #     without one — filed before accept_via_pr shipped, or whose
        #     fire-and-forget publish failed or is still in flight — gets its PR
        #     opened right here, and ANY failure on the PR path stops the accept
        #     visibly rather than falling through to the local merge. The one
        #     PR-less case that legitimately proceeds is a discussion-only topic
        #     with no branch, where the local merge no-ops and bypasses nothing.
        #     Accepting there does NOT merge: it AUTHORIZES (see
        #     `_authorize_pr_for_accept`) and the poller merges once CI is
        #     actually green.
        #   - the platform forge: the local merge IS this project's accept
        #     (#363), and `forge.note` says so on the card so it can never read
        #     as a bound project that skipped its PR.
        #   - the personal-token forge: the pre-#296 two-phase path, further
        #     down, with its documented degrade to the local merge.
        pr_degrade_reason = ""
        forge = await self._resolve_forge(topic.project_id)
        if forge.requires_pr:
            if card.pr_number is None:
                await self._publish_pr_for_accept(card, topic)
            if card.pr_number is not None:
                # App 采纳等 CI 再合: 采纳 = 授权，合并归轮询器。Falls through to
                # the local merge ONLY when the topic has no branch at all
                # (`_publish_pr_for_accept` returned without a PR) — there the
                # merge is a no-op and bypasses nothing.
                return await self._authorize_pr_for_accept(card, topic, decided_by)
        if card.pr_number is not None:
            settled, existing_pr_degrade_reason = await self._accept_via_pr(
                card, topic, decided_by
            )
            if settled is not None:
                return settled
            if forge.requires_pr:
                # 绑定 GitHub 的项目采纳永不落 local merge (#363).
                await self._stop_accept_pr_unavailable(
                    card, topic, existing_pr_degrade_reason
                )
            pr_degrade_reason = existing_pr_degrade_reason
        unbound_note = forge.note

        # 两阶段采纳 (PR迭代式, 2026-08-09): no PR yet — try opening a NEW one via
        # the approver's own connected GitHub token. Any missing prerequisite
        # (no connected token / no connected repo) or any GitHub-side failure
        # (push/API) degrades to the old direct-merge path below — a normal
        # degrade, never an accept failure (拍板 decision 2). Resolving the
        # prerequisites themselves must degrade the same way: a DB hiccup here
        # is exactly as "mechanism unavailable" as a missing token.
        #
        # 采纳即合并 (#296) coexistence guard: when the App owns PR creation
        # (`pr_publish.enabled()`), this personal-token path is SKIPPED —
        # opening a competing personal-token PR is exactly the "run both
        # mechanisms at once" the design warns against. A card still PR-less
        # at this point in that world is either on an unbound project (#363:
        # the platform is its forge, the local merge below is its one accept)
        # or a discussion-only topic with no branch (the merge no-ops); a
        # GitHub-side failure never reaches here — it raises above. The
        # two-phase path stays live only where the App mechanism is off (a
        # project without the App, or the .env override).
        #
        # `pr_degrade_reason` makes WHY visible (this card's whole reason for
        # existing): every path below that falls through to the local-merge
        # branch sets it to a human-readable, secret-free explanation, and it
        # gets prefixed onto card.note further down so "looks like account
        # not connected" and "账号连了但密文坏了" are no longer
        # indistinguishable in the UI.
        two_phase_degrade_reason = ""
        if forge.kind is forge_mod.ForgeKind.github_user:
            try:
                (
                    pr_prereqs,
                    two_phase_degrade_reason,
                ) = await self._resolve_pr_prerequisites(topic, decided_by)
            except Exception as exc:  # noqa: BLE001 — degrade, don't fail the accept
                logger.warning(
                    "could not resolve PR prerequisites for topic=%s, degrading to "
                    "direct merge: %s",
                    topic.id,
                    exc,
                )
                pr_prereqs = None
                two_phase_degrade_reason = (
                    f"检查 PR 前提条件时出错（{type(exc).__name__}）"
                )
            if pr_prereqs is not None:
                token, pr_owner, pr_repo = pr_prereqs
                try:
                    return await self._open_pr_for_accept(
                        card=card,
                        topic=topic,
                        decided_by=decided_by,
                        token=token,
                        owner=pr_owner,
                        repo=pr_repo,
                    )
                except Exception as exc:  # noqa: BLE001 — degrade, don't fail accept
                    logger.warning(
                        "PR-based accept unavailable for topic=%s, degrading to "
                        "direct merge: %s",
                        topic.id,
                        exc,
                    )
                    # exc is either GitHubPrError (GitHub's own response body,
                    # capped at 300 chars) or a ValidationError from a git
                    # push failure (the token travels via an env-var
                    # credential helper, never argv/URL — see _token_push_env
                    # — so git's stderr can't contain it either); safe to
                    # surface verbatim, same as the push_back() note below.
                    # A workflow-permission rejection lands here too now: its
                    # ℹ️ "known permanent limitation" sentinel is deleted. On
                    # the App path these cards just work (the App has held
                    # `workflows:write` since 2026-08-12); on THIS
                    # personal-token path a rejection that survives the
                    # sync-and-retry in push_topic_branch_for_github_pr means
                    # the approver's own token lacks the workflow scope —
                    # worth a human's ⚠️ look, never a calm auto-direct-merge.
                    two_phase_degrade_reason = f"GitHub 侧调用失败：{exc}"[:300]
        # Combine rather than overwrite: an existing-PR degrade (closed
        # unmerged / merge-call failure, see `_accept_via_pr`) must not be
        # silently dropped just because the two-phase attempt that follows it
        # also had something to say.
        if two_phase_degrade_reason:
            pr_degrade_reason = (
                f"{pr_degrade_reason}；{two_phase_degrade_reason}"
                if pr_degrade_reason
                else two_phase_degrade_reason
            )
        # 采纳 = merge (spec §6.3) — and the merge DECIDES the outcome. A
        # conflict must never silently archive the topic while the work is
        # stranded on its branch (that shipped a lie once): the card moves to
        # `conflict`, 芝士 gets dispatched to resolve, a human retries.
        from app.domain.workspace import service as ws

        try:
            merged = await asyncio.to_thread(ws.merge_topic, topic.project_id, topic.id)
        except Exception as exc:  # noqa: BLE001 — surface, don't invent success
            logger.exception(
                "accept merge raised for project=%s topic=%s",
                topic.project_id,
                topic.id,
            )
            self._notify_merge_result(
                topic, f"❌ 采纳未完成：合并出错，请检查工作区状态。（{exc}）"
            )
            raise ValidationError(_MERGE_FAILED_MESSAGE) from exc

        if not merged.get("merged"):
            # The conflicts key means an attempted merge failed. An empty list
            # is still a failure: Git can error before it identifies paths.
            if "conflicts" in merged and merged.get("conflicts"):
                await self._repo.add_approval(card_id, decided_by)
                card.status = AcceptStatus.conflict
                card.decided_by = decided_by
                card.decided_at = datetime.now(UTC)
                card.note = _with_pr_degrade_note(
                    merged.get("reason", ""), pr_degrade_reason
                )
                await self._session.flush()
                await self._session.refresh(card)
                conflict_msg = "❌ 采纳未完成：合并冲突，需要芝士处理后重试。"
                if card.note:
                    conflict_msg += f"\n{card.note}"
                self._notify_merge_result(topic, conflict_msg)
                return card

            # Discussion-only topics and a topic already on the base branch
            # intentionally have nothing to merge and remain acceptable.
            if merged.get("noop") is not True:
                logger.error(
                    "accept merge failed for project=%s topic=%s result=%r",
                    topic.project_id,
                    topic.id,
                    merged,
                )
                self._notify_merge_result(
                    topic, "❌ 采纳未完成：合并失败，请检查工作区状态。"
                )
                raise ValidationError(_MERGE_FAILED_MESSAGE)

        # Merged (or nothing to merge — e.g. a discussion topic with no branch
        # work): the accept completes as before.
        await self._repo.add_approval(card_id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now
        # 采纳即上线: propagate the merge to the upstream repo. The outcome is
        # RECORDED on the card — a push that only landed a side branch (or failed
        # outright) used to be swallowed here, so the accept looked complete while
        # nothing reached the upstream and no one could tell why.
        if merged.get("merged"):
            note = ""
            try:
                pushed = await asyncio.to_thread(
                    ws.push_back, topic.project_id, topic.id
                )
            except Exception as exc:  # noqa: BLE001 — never fail the accept itself
                note = f"上游回推失败：{exc}"[:2000]
            else:
                mode = pushed.get("mode")
                if mode == "upstream":
                    note = f"已合并并推送到上游 {pushed.get('target')}"
                elif mode == "branch":
                    why = (pushed.get("reason") or "").strip()
                    note = (
                        f"上游 {pushed.get('target')} 未能直接推送，"
                        f"已推分支 {pushed.get('branch')} 待合并"
                        + (f"（{why[-200:]}）" if why else "")
                    )[:2000]
                elif mode == "blocked":
                    note = str(pushed.get("reason") or "")[:2000]
                elif mode == "none":
                    note = str(pushed.get("reason") or "")[:2000]
            card.note = _with_pr_degrade_note(note, pr_degrade_reason)
        else:
            # noop (nothing to merge, e.g. a discussion-only topic) still
            # deserves the degrade reason — the two-phase attempt happened
            # and fell back, even though there's no push outcome to report.
            card.note = _with_pr_degrade_note("", pr_degrade_reason)
        if unbound_note:
            # 平台即 forge (#363): 如实标注，而不是让这张卡看起来像绕过了 PR。
            card.note = (f"{unbound_note}；{card.note}" if card.note else unbound_note)[
                :2000
            ]

        # 这次改动交付完了 → 释放计费算力，工作面留着（见 _release_billed_compute）。
        await self._release_billed_compute(topic)

        # 交付完成 ≠ 话题结束 (#442 decision 1). accepted_by/accepted_at 是这一刻
        # 自动打上的交付标记；status 不动，归档只由人来做（POST /topics/{id}/archive）。
        topic.accepted_by = decided_by
        topic.accepted_at = now

        await self._session.flush()
        await self._session.refresh(card)
        success_msg = (
            f"✅ 话题已被 {decided_by} 采纳并合并"
            "（这一次交付完成了，话题继续活跃——归档由人决定）。"
        )
        if card.note:
            success_msg += f"\n{card.note}"
        self._notify_merge_result(topic, success_msg)
        return card

    # ---- 两阶段采纳 (PR迭代式, 2026-08-09) ----------------------------------

    def _local_topic_branch_head(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> str | None:
        """Local topic branch head after folding any pending 芝士 edits into a
        jj commit — local-only (no network), used to decide whether a re-push
        to the PR branch is needed before touching GitHub at all. Deliberately
        built from `workspace.service`'s existing public helpers
        (ensure_repo/branch_for_topic/snapshot_worktree) rather than adding a
        new one there — this feature's touch scope is review/ + oauth/ only.
        None if the repo/branch genuinely doesn't exist yet (nothing to push)."""
        import subprocess

        from app.domain.workspace import service as ws

        try:
            ws.snapshot_worktree(project_id, topic_id, ws.SNAPSHOT_BEFORE_CI_POLL)
        except ValidationError:
            pass  # no workspace/jj state yet — nothing pending to fold
        repo_path = ws.ensure_repo(project_id)
        branch = ws.branch_for_topic(topic_id)
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--verify", "-q", branch],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _remote_head_ff_from_local(
        self, project_id: uuid.UUID, remote_head: str, local_head: str
    ) -> bool:
        """Would a plain (non-force) push of `local_head` fast-forward the PR
        branch that is currently at `remote_head`? True only when `remote_head`
        is an ancestor of `local_head` in the platform's own repo.

        采纳即合并 (#296): `push_topic_branch_for_github_pr` pushes WITHOUT
        --force, so a local head that is behind or diverged from the remote PR
        branch (a jj rewind moved the bookmark backwards) can never land — GitHub
        rejects it non-fast-forward. Re-attempting that push every poll tick is
        the loop this guards. Fails CLOSED: if the remote commit isn't even
        present locally to compare (git errors, exit ≠ 0/1), treat it as "cannot
        fast-forward" and skip — never a blind push that would just be rejected
        again. `git merge-base --is-ancestor` is reflexive, so an identical head
        also returns True, but the caller has already excluded that case."""
        import subprocess

        from app.domain.workspace import service as ws

        if not remote_head or not local_head:
            return False
        repo_path = ws.ensure_repo(project_id)
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "merge-base",
                "--is-ancestor",
                remote_head,
                local_head,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    async def _repush_if_local_head_moved(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        token: str,
        remote_head: str,
        remote_branch: str = "",
    ) -> bool:
        """两阶段采纳: the platform side of the iterate loop — if 芝士 committed a
        fix since the last push, push it to the PR branch ourselves (芝士's
        sandbox has no GitHub credentials and no network to github.com, so it
        cannot do this itself; see `_nudge_pr_fix`). Compares the LOCAL branch
        head (cheap, no network) against `card.pr_head_sha` (last known
        pushed/remote head) so an unchanged branch costs nothing — never a
        blind force-push every poll tick. A push failure (expired token,
        network hiccup, non-fast-forward) degrades gracefully: logged, card
        left untouched, next poll tick just retries — never a permanent
        failure and never silent.

        Returns True only when a push actually landed (so the caller knows the
        PR head it read a moment ago may be stale).

        采纳即合并 (#296): before pushing, confirm the push CAN fast-forward the
        PR branch (`remote_head`, the head the caller just read live). A plain
        push of a local head that is behind / diverged from the remote branch is
        rejected non-fast-forward, and re-attempting it every 60s poll lands
        nothing forever (card 946bf5de). When it cannot fast-forward, the
        platform declines to push — it does not force-push over commits already
        on the PR — and says so once so 芝士 reconciles in its workspace.

        红鲱鱼警告 (2026-08-10): cards #210/#211 wore the note
        `⚠️ 平台自动重推失败（refusing to allow ... without workflows
        permission）` and that note says NOTHING about whether their work
        landed — it was a *symptom* of the stuck-card bug, not the cause. The
        PRs had already been merged by hand; the poller kept coming back here
        anyway, and a re-push first merges the base branch in, so main's
        `.github/workflows/build.yml` changes (#212/#214) became part of the
        payload and GitHub rejected the push for lacking the `workflows`
        scope. `_advance_pr_checks`'s up-front merged-check now returns before
        this function on the first tick that observes the merge, so the loop —
        and the noise — stops on its own."""
        from app.domain.review import github_pr
        from app.domain.workspace import service as ws

        local_head = await asyncio.to_thread(
            self._local_topic_branch_head, topic.project_id, topic.id
        )
        if local_head is None or local_head in (card.pr_head_sha, remote_head):
            return False
        # A plain push can only fast-forward. If the local branch was rewound /
        # diverged from the PR branch, pushing it is a doomed non-fast-forward —
        # skip it (never force-push over what's already on the PR) and leave one
        # named note for 芝士 to merge the PR branch in. Adopt the live remote
        # head as our record so the caller's own head-sync doesn't wipe the note.
        can_ff = await asyncio.to_thread(
            self._remote_head_ff_from_local,
            topic.project_id,
            remote_head,
            local_head,
        )
        if not can_ff:
            logger.warning(
                "pr_open card %s: local head %s cannot fast-forward PR branch "
                "%s (rewound/diverged) — not re-pushing, waiting on 芝士",
                card.id,
                local_head,
                remote_head,
            )
            if remote_head:
                card.pr_head_sha = remote_head
            if not card.note.startswith(_REPUSH_DIVERGED_PREFIX):
                card.note = (
                    f"{_REPUSH_DIVERGED_PREFIX}：本地话题分支（{local_head[:8]}）"
                    f"落后于/偏离了 PR 分支（{remote_head[:8]}），平台不会强推覆盖 PR "
                    "上已有的提交。请在这个话题的工作区里把 PR 分支的新提交合并进来"
                    "再提交，平台会自动把结果同步到这个 PR。"
                )[:2000]
            await self._session.flush()
            return False
        try:
            pushed = await asyncio.to_thread(
                ws.push_topic_branch_for_github_pr,
                topic.project_id,
                topic.id,
                owner=owner,
                repo=repo,
                # The PR's own head branch when the caller could read it off
                # the PR; the derived name only as a fallback (that is what
                # the personal-token lane's PRs are always called anyway).
                remote_branch=remote_branch or github_pr.pr_branch_name(topic.id),
                token=token,
            )
        except ValidationError as exc:
            logger.warning(
                "pr_open card %s: re-push of local commit %s failed (%s) — "
                "will retry next poll tick",
                card.id,
                local_head,
                exc,
            )
            # Visible on the card, not just logger (agent has no host SSH):
            # otherwise 芝士 believes its fix was pushed and just waits forever.
            # Dedup by prefix — this fires every 60s poll tick until the push
            # succeeds, and must not spam the note each time.
            if not card.note.startswith(_REPUSH_FAILED_PREFIX):
                card.note = (f"{_REPUSH_FAILED_PREFIX}（下一轮还会重试）：{exc}")[:2000]
                await self._session.flush()
            return False
        card.pr_head_sha = pushed["head_sha"]
        card.note = ""
        await self._session.flush()
        return True

    async def _resolve_pr_prerequisites(
        self, topic: Topic, decided_by: str
    ) -> tuple[tuple[str, str, str] | None, str]:
        """(token, owner, repo) when the PR path is usable — a connected
        GitHub token for the approver AND a project connected to a repo
        (#192) — paired with a human-readable, secret-free reason (empty
        string when prereqs resolved). Either missing → (None, reason), and
        the caller degrades to the old direct-merge path (拍板 decision 2:
        this is normal, not an error) with that reason surfaced on the card.
        """
        from app.domain.oauth.services import (
            get_github_user_token_for_handle_with_reason,
        )
        from app.domain.project.repositories import ProjectGitInstallationRepository

        token, reason = await get_github_user_token_for_handle_with_reason(
            self._session, decided_by
        )
        if not token:
            return None, _describe_token_unavailable(reason)
        installation = await ProjectGitInstallationRepository(
            self._session
        ).get_by_project(topic.project_id)
        if installation is None or "/" not in installation.repo:
            return None, "项目未连接 GitHub 仓库"
        owner, _, repo_name = installation.repo.partition("/")
        return (token, owner, repo_name), ""

    async def _open_pr_for_accept(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        token: str,
        owner: str,
        repo: str,
    ) -> AcceptCard:
        """Push the topic branch, open a NEW real PR, and hand the rest to the
        scheduler's PrPollRunner (SchedulerService.poll_open_prs /
        advance_pr_card) — this call does NOT wait for CI. Topic stays
        active; no merge_topic()/push_back()/archive here (拍板 decision 3:
        archive gates on the PR *and* its triggered deploy both succeeding).
        Distinct from `_accept_via_pr` below (#188 §5.1), which merges a PR
        that ALREADY exists on the card rather than opening a new one."""
        from app.domain.review import github_pr
        from app.domain.workspace import service as ws

        remote_branch = github_pr.pr_branch_name(topic.id)
        pushed = await asyncio.to_thread(
            ws.push_topic_branch_for_github_pr,
            topic.project_id,
            topic.id,
            owner=owner,
            repo=repo,
            remote_branch=remote_branch,
            token=token,
        )
        base = await asyncio.to_thread(ws.pr_base_branch, topic.project_id)
        client = github_pr.default_client()
        who = await identity.attribution(self._session, topic)
        pr = await client.open_pull_request(
            owner=owner,
            repo=repo,
            head=remote_branch,
            base=base,
            title=pr_text.change_subject(card, topic),
            body=pr_text.pr_body(topic, decided_by, card, who),
            token=token,
        )

        await self._repo.add_approval(card.id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.pr_open
        card.decided_by = decided_by
        card.decided_at = now
        card.pr_number = pr.number
        card.pr_repo = f"{owner}/{repo}"
        card.pr_url = pr.url
        card.pr_head_sha = pushed["head_sha"]
        # 人类授权动作前移: freeze what this human actually authorized. From here
        # on `pr_head_sha` follows every fix 芝士 pushes; this one does not, and
        # the poller diffs the two before it dares merge without asking again.
        card.pr_authorized_sha = pushed["head_sha"]
        card.pr_merged_at = None
        # A PR GitHub reports as already open on this head branch IS this
        # topic's PR (the branch name is derived from the topic id), so it is
        # adopted rather than opened — and saying "已开" for it would misreport
        # the one thing the timeline exists to record.
        pr_phrase = (
            f"已认领该分支上已存在的 PR #{pr.number}"
            if pr.already_existed
            else f"已开 PR #{pr.number}"
        )
        card.note = (
            f"{pr_phrase}，真 CI 现在才开始跑，全绿且没超出授权范围才自动合并：{pr.url}"
        )
        await self._session.flush()
        await self._session.refresh(card)
        self._notify_merge_result(
            topic,
            f"🔁 {decided_by} 授权了这次改动，{pr_phrase} —— 真 CI 现在才开始跑："
            f"{pr.url}\n"
            "话题保持 active（容器不停）。检查全绿、且改动没超出授权范围时平台自动"
            "合并，之后的迭代不用再问人；三种例外（新 diff 越界 / 根本没有 CI 会跑"
            " / 目标是 prod）会回来找人。PR 合并且部署也成功后才会归档。",
        )
        return card

    async def _pr_poll_credentials(
        self, card: AcceptCard, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        """Whose GitHub credentials drive THIS card's poll — paired with a
        human-readable reason when there are none (empty when there are).

        The two lanes answer differently, and picking the wrong one is how a
        card stalls forever:

        - **App forge**: the platform's own installation tokens. The approver is
          not necessarily connected to GitHub at all, and — the part that
          actually bites — is not necessarily allowed to write to `main`; the
          App is. Binding an already-authorized card's progress to someone's
          personal account state means a card that no one can move and no one
          can see why. Attribution does not need their token either: it rides
          the merge commit's `Reviewed-by` trailer. Two mints, not one: see
          `_GitHubCredentials` for why reading the checks with the write token
          is a 403 that presents as a card frozen at `pr_open`.
        - **personal-token forge**: unchanged (pre-#296 behaviour). Attribution
          IS the point there — the PR was opened as that human, and one OAuth
          token covers both roles.

        Never raises: `_resolve_forge` fails closed with a ValidationError when
        it cannot judge the binding, and a poll tick must degrade to "pause and
        retry", not to a card whose only trace is a stack trace in the log.
        """
        try:
            forge = await self._resolve_forge(topic.project_id)
        except Exception as exc:  # noqa: BLE001 — pause this tick, retry the next
            return None, f"判定不了项目的 GitHub 绑定状态（{type(exc).__name__}）"
        if forge.kind is forge_mod.ForgeKind.github_app:
            return await self._app_credentials(topic)

        from app.domain.oauth.services import (
            get_github_user_token_for_handle_with_reason,
        )

        token, reason = await get_github_user_token_for_handle_with_reason(
            self._session, card.decided_by or ""
        )
        if not token:
            return None, _describe_token_unavailable(reason)
        return _GitHubCredentials(write=token, read=token), ""

    async def _app_credentials(
        self, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        """The platform App's installation tokens for this project's repo."""
        from app.domain.agent.github_app import github_app_tokens_for_project

        tokens = await github_app_tokens_for_project(topic.project_id, self._session)
        if tokens is None:
            return None, "平台 GitHub App 对这个项目不可用"
        try:
            write, _ = await tokens.write_token()
            read, _ = await tokens.readonly_token()
        except Exception as exc:  # noqa: BLE001 — pause this tick, don't crash
            return None, f"平台 GitHub App 取 token 失败（{type(exc).__name__}）"
        return _GitHubCredentials(write=write, read=read), ""

    async def advance_pr_card(
        self, card_id: uuid.UUID, *, chat_service, runner
    ) -> None:
        """One polling step for a pr_open card — check the PR's CI, merge
        when green, then check the deploy workflow the merge triggers, and
        only archive once THAT is green too (2026-08-09 拍板: merge alone
        doesn't count). Called by SchedulerService.poll_open_prs(); never
        raises for a transient GitHub hiccup — the next poll just retries."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pr_open:
            return
        if not card.pr_repo or card.pr_number is None or not card.pr_head_sha:
            logger.error("pr_open card %s missing PR fields, cannot poll", card.id)
            return
        topic = await self._topic_or_404(card.topic_id)
        owner, _, repo = card.pr_repo.partition("/")

        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            logger.warning(
                "pr_open card %s has no usable GitHub token anymore (%s); "
                "skipping this poll (will retry next tick)",
                card.id,
                reason,
            )
            # Without this the card just sits at `pr_open` forever and looks
            # identical to "CI still running" — no signal anyone's token died.
            if not card.note.startswith(_POLL_PAUSED_PREFIX):
                card.note = (f"{_POLL_PAUSED_PREFIX}（下一轮还会重试）：{reason}")[
                    :2000
                ]
                await self._session.flush()
            return

        # Token is usable again → the pause note is stale. Clearing it here is
        # what makes the pause self-healing: it stops describing a condition
        # that no longer holds, AND it can no longer sit in front of a real CI
        # failure (which is how "轮询暂停" used to swallow CI 失败 notifications
        # — see _nudge_pr_fix). Only this exact prefix is cleared; 重推失败 /
        # 部署失败 / 拒绝合并 notes describe live conditions and stay put.
        if card.note.startswith(_POLL_PAUSED_PREFIX):
            card.note = ""
            await self._session.flush()

        from app.domain.review import github_pr

        client = github_pr.default_client()
        try:
            # One stage, since #206: a card in `pr_open` is waiting on the PR's
            # own checks and nothing else. It used to have a second stage that
            # waited for a deploy workflow — see this method's docstring.
            await self._advance_pr_checks(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                chat_service=chat_service,
                runner=runner,
            )
        except github_pr.GitHubPrError as exc:
            logger.warning(
                "GitHub API hiccup polling pr_open card %s: %s — retrying next tick",
                card.id,
                exc,
            )

    async def _advance_pr_checks(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        chat_service,
        runner,
    ) -> None:
        """Stage 1: the card's PR hasn't reached the deploy gate yet — either
        because it isn't merged, or because someone merged it on GitHub
        without us noticing."""
        number = card.pr_number
        if number is None:  # already guaranteed by poll_open_pr_card's guard
            return

        # FIRST, before anything else: did a human already handle this PR on
        # GitHub? This check has to be up here rather than folded into the
        # merge call's 405 branch, because a red `check_state` nudges 芝士 and
        # returns before the merge call ever happens — so on a PR merged by
        # hand while its checks were red (exactly what happened to #210/#211)
        # that 405 never arrives and the card polls at `pr_open` forever.
        status = await client.pull_request_status(
            owner=owner, repo=repo, number=number, token=creds.read
        )
        if status.merged:
            await self._settle_external_merge(card=card, topic=topic, status=status)
            return
        if status.state == "closed":
            self._note_pr_closed_unmerged(card=card, topic=topic)
            await self._session.flush()
            return

        # 人类授权动作前移: a card that opened its PR before this feature existed
        # has no recorded authorization baseline. Adopt the head it is riding
        # RIGHT NOW rather than leaving the valve off forever — that can only
        # ever govern pushes from here on, so an in-flight card is never
        # retroactively blocked for something it did before the rule existed.
        if card.pr_authorized_sha is None:
            card.pr_authorized_sha = card.pr_head_sha
            await self._session.flush()

        # 芝士 fixed something in its workspace — push it to the PR branch
        # before checking CI, or a fixed commit just sits local forever (see
        # _repush_if_local_head_moved's docstring for why 芝士 can't do this
        # push itself).
        pushed = await self._repush_if_local_head_moved(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            token=creds.write,
            remote_head=status.head_sha,
            # The branch THIS PR is open on, from the PR itself. It used to be
            # derived from the topic id, which is right for one lane and wrong
            # for the other (`cheesex/<hex8>` vs `topic/<hex8>`) — a derived
            # name pushes 芝士's fix onto a branch no PR is watching, so the
            # commit lands and the PR never moves.
            remote_branch=status.head_ref,
        )

        # Only re-read the head when the push above actually moved it;
        # otherwise `status` was fetched moments ago and says the same thing.
        # Keeps the steady-state cost at one GET /pulls/{n} per tick, same as
        # before the merged-check was added.
        live_head = (
            await client.pull_request_head_sha(
                owner=owner, repo=repo, number=number, token=creds.read
            )
            if pushed
            else status.head_sha
        )
        if live_head != card.pr_head_sha:
            # GitHub's actual head disagrees with what we have on record (e.g.
            # our push above just landed and GitHub is catching up, or someone
            # pushed to the PR branch directly) — GitHub is authoritative.
            # Clear any "already nudged" marker so a fresh failure on the NEW
            # commit still notifies (see the note-based dedup in _nudge_pr_fix).
            card.pr_head_sha = live_head
            card.note = ""
            await self._session.flush()

        state, tail = await client.check_state(
            owner=owner, repo=repo, ref=card.pr_head_sha, token=creds.read
        )
        if state == "pending":
            self._note_waiting_on_checks(card=card, tail=tail)
            await self._session.flush()
            return
        if state == "failure":
            self._nudge_pr_fix(
                card=card,
                topic=topic,
                tail=tail,
                stage="CI",
                chat_service=chat_service,
                runner=runner,
                repo_full_name=f"{owner}/{repo}",
            )
            await self._session.flush()
            return

        # Tier-2 阀一 (#468): required 检查按名单等——**缺席是 pending，不是
        # 通过**。#465 那次 `test` 因 path filter 根本没被触发，可见的检查全
        # skipped/绿，"看见的都绿" 就这么放行了从没跑过测试的合并。名单里的
        # 名字必须出现在 check-runs 里，没出现就继续等（真正"什么都不会跑"的
        # 情形由 no_checks 阀负责，走的是 needs-human，不是这里）。
        #
        # 但「缺席」有两种，2026-08-16 才分清（#470 上线当天就卡住了一批卡）：
        # workflow 自己带路径过滤，一个纯前端 PR 上 `test` **本来就不该出现**。
        # 所以名单项带上「对哪些改动有效」，只有 PR 真碰了那些路径才要求它——
        # 否则 #483/#485/#486 那样全绿的卡会永远等一个永远不会来的检查。
        required = _parse_required_checks(settings.accept_required_check_names)
        if required:
            seen = await client.check_run_names(
                owner=owner, repo=repo, ref=card.pr_head_sha, token=creds.read
            )
            absent = [r for r in required if r.name not in seen]
            # 只有真有缺席时才去问改动范围：正常情况（名单全在）零额外 API 调用，
            # 每 60 秒一轮的稳态开销和以前一样。
            missing = (
                await self._required_and_absent(
                    absent=absent,
                    card=card,
                    topic=topic,
                    owner=owner,
                    repo=repo,
                    token=creds.read,
                    client=client,
                )
                if absent
                else _AbsentRequired(names=[])
            )
            if missing.names:
                shown = ", ".join(sorted(missing.names))
                # 兜底：没有超时的等待会静默卡死。workflow 改名、被禁用、Actions
                # 额度断供（2026-08-13 真的断过一次）都会让一个该出现的检查永远
                # 不出现——等过头就交给人，**绝不因为等腻了就自动合并**。
                if self._required_check_grace_expired(card):
                    reason, explain = _absent_required_timeout(
                        shown,
                        missing.fallback,
                        settings.accept_required_check_grace_minutes,
                    )
                    self._note_needs_human(
                        card=card, topic=topic, reason=reason, explain=explain
                    )
                    await self._session.flush()
                    return
                self._note_waiting_on_checks(
                    card=card, tail=_absent_required_tail(shown, missing.fallback)
                )
                await self._session.flush()
                return

        # Tier-2 阀二 (#468): strict up-to-date——绿必须绿在**当前基线**上。
        # 各自绿在旧基上的两个 PR 合并相加可以是红的（2026-08-12 三头 alembic、
        # 2026-08-16 样式闸门叠加，都拦住过全队）。落后就自动换基（GitHub 的
        # Update branch），换基后 head 变化，下一轮从新 CI 重新等起。None（
        # GitHub 答非所问）不拦：ancestry 读不到不该冻结整条采纳路。
        ancestry = await client.compare_status(
            owner=owner,
            repo=repo,
            base="main",
            head=card.pr_head_sha,
            token=creds.read,
        )
        if ancestry in ("behind", "diverged"):
            rebases = card.note.count(_REBASE_NOTE_MARK)
            if rebases >= 3:
                self._note_needs_human(
                    card=card,
                    topic=topic,
                    reason=(
                        "分支反复落后于 main（已自动换基 3 次仍未赶上）——"
                        "main 移动太快或换基没生效，请人工处理"
                    ),
                )
                await self._session.flush()
                return
            updated = await client.update_branch(
                owner=owner, repo=repo, number=card.pr_number or 0, token=creds.read
            )
            outcome = (
                "已自动更新分支，等新一轮 CI。"
                if updated
                else "自动更新分支被拒，下一轮重试。"
            )
            card.note = (
                f"{_REBASE_NOTE_MARK}基线落后于 main（{ancestry}），"
                f"{outcome}{card.note}"
            )[:2000]
            await self._session.flush()
            return

        # 人类授权动作前移 (2026-08-10): 检查不红 ≠ 机器可以免人合并。人当初批的
        # 是「以我的名义开这个 PR、让 CI 真跑」，不是「这堆代码我看过了」——所以
        # 合并前还要过三道安全阀，任一命中就不合并、回来找人。
        withheld = await self._authorization_exception(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            token=creds.read,
            client=client,
            state=state,
            tail=tail,
        )
        if withheld is not None:
            self._note_needs_human(card=card, topic=topic, reason=withheld)
            await self._session.flush()
            return

        # Green → merge now. Trailers go on the merge commit too, not just
        # the PR description (2026-08-09 设计要点5: 标清芝士代表谁) — under
        # squash that means the body field, with the title passed separately.
        who = await identity.attribution(self._session, topic)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=card.pr_number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(
                topic, card.decided_by or "", card, who
            ),
        )
        if result.sha is None:
            # GitHub refused (405/409). NOT necessarily transient — a
            # merge_method the repo disabled refuses on every poll forever —
            # so the reason goes on the card rather than into the void.
            self._note_merge_blocked(
                card=card,
                topic=topic,
                reason=result.blocked_reason or "",
                chat_service=chat_service,
                runner=runner,
            )
            await self._session.flush()
            return
        card.pr_merged_at = datetime.now(UTC)
        card.pr_head_sha = result.sha  # the merge commit, for the record
        await self._finish_pr_accept(card=card, topic=topic)

    async def _required_and_absent(
        self,
        *,
        absent: list[_RequiredCheck],
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        token: str,
        client,
    ) -> _AbsentRequired:
        """名单里没有出现的那几项检查，哪些对**这次改动**确实是必需的。

        带路径条件的项要跟 PR 的实际 diff 对一次：一个纯前端 PR 上 `test`
        （`.github/workflows/test.yml` 只在 `backend/**` 上触发）没出现是正常的，
        不是「还没跑」。不带路径条件的项照旧无条件必需。

        算不出改动范围时**保守处理**（照样算必需）：拿不到 diff 就不知道这次有没有
        碰后端，此时放行等于用一次 API 失败换掉整道阀。等下去不会误合，超时兜底会
        把它交给人。

        但保守回退必须**能被外面看见**：结论一样（照旧必需）不等于理由一样，
        所以回退时连同「为什么没算出来」一起返回（`_AbsentRequired.fallback`），
        由卡面如实说出来 —— 它以前只落在 `logger.warning` 里，而后端日志的保留
        期只有「距上次部署多久」。"""
        from app.domain.workspace import service as ws

        names = [r.name for r in absent if not r.paths]
        scoped = [r for r in absent if r.paths]
        if not scoped:
            return _AbsentRequired(names=names)
        try:
            base = await asyncio.to_thread(ws.pr_base_branch, topic.project_id)
        except Exception as exc:  # noqa: BLE001 — 认不出基线就按"仍然必需"处理
            logger.warning(
                "card %s: cannot resolve PR base branch (%s) — "
                "keeping every required check mandatory",
                card.id,
                exc,
            )
            return _AbsentRequired(
                names=names + [r.name for r in scoped],
                fallback=f"认不出这个 PR 要合进哪条分支：{type(exc).__name__}: {exc}"[
                    :200
                ],
            )
        changed = await client.compare_files(
            owner=owner, repo=repo, base=base, head=card.pr_head_sha, token=token
        )
        if changed is None:
            logger.warning(
                "card %s: GitHub gave no file list for %s...%s — "
                "keeping every required check mandatory",
                card.id,
                base,
                card.pr_head_sha,
            )
            return _AbsentRequired(
                names=names + [r.name for r in scoped],
                fallback=(
                    "GitHub 没给出这次改动的文件清单"
                    "（改动超过 compare API 的 300 文件上限，或返回格式异常）"
                ),
            )
        return _AbsentRequired(
            names=names + [r.name for r in scoped if _diff_touches(r.paths, changed)]
        )

    def _required_check_grace_expired(self, card: AcceptCard) -> bool:
        """这张卡等一个没出现的 required 检查，是不是已经等过头了。

        时钟用 `decided_at`（人点采纳的那一刻）——卡上没有"当前 head 第一次被看见"
        的时间戳，而这里宁可偏早交给人也不要偏晚：超时的出口是找人，不是合并，早
        一点只是多问一句。"""
        minutes = settings.accept_required_check_grace_minutes
        if minutes <= 0:  # 0 = 关掉兜底，无限等（旧行为）
            return False
        since = card.decided_at
        if since is None:
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        return (datetime.now(UTC) - since).total_seconds() >= minutes * 60

    async def _authorization_exception(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        token: str,
        client,
        state: str,
        tail: str,
    ) -> str | None:
        """人类授权动作前移 (2026-08-10) 的安全阀：checks aren't red — may the
        machine merge WITHOUT going back to the human? Returns None for yes, or
        the human-readable reason it must ask, for the three exceptions the
        design names. Anything it cannot determine counts as "ask" (fail
        closed): the whole point of the human's click moving earlier is that
        nobody has looked at what the machine is about to merge.

        Deliberately NOT part of the polling state machine — `advance_pr_card`
        and its stages are reused as-is; this is one gate in front of the merge
        call, and returning None leaves the old behaviour byte for byte."""
        from app.domain.workspace import service as ws

        # 例外 2: 「12 项检查全过」和「没有 workflow 会对这次改动触发检查」是两件
        # 事。后者也让轮询停下来（零检查死锁的修复原样保留），但它意味着真 CI 从
        # 未跑过这段代码，所以不享受免人自动合并。
        if state == "no_checks":
            return f"没有任何 CI 真的跑过这次改动（{tail}）"

        # 例外 3: 目标是 prod —— 永远两次都要人。
        try:
            base = await asyncio.to_thread(ws.pr_base_branch, topic.project_id)
        except Exception as exc:  # noqa: BLE001 — 认不出目标分支就不敢替人决定
            logger.warning(
                "card %s: cannot resolve PR base branch (%s) — withholding merge",
                card.id,
                exc,
            )
            return f"认不出这个 PR 要合进哪条分支（{type(exc).__name__}），不敢替人决定"
        if _is_prod_base(base):
            return f"目标分支是 {base}，prod 永远要人自己合，机器不代劳"

        # 例外 1: 授权之后 head 又动了，且新 diff 超出当时授权的范围。
        authorized_sha = card.pr_authorized_sha
        if not authorized_sha or authorized_sha == card.pr_head_sha:
            # 人授权的就是现在这个 commit —— 没有"之后"，也就没有漂移。
            return None
        authorized = await client.compare_files(
            owner=owner, repo=repo, base=base, head=authorized_sha, token=token
        )
        current = await client.compare_files(
            owner=owner, repo=repo, base=base, head=card.pr_head_sha, token=token
        )
        if authorized is None or current is None:
            return (
                "改动太大，GitHub 没给出完整的文件列表，"
                "无法确认新提交有没有超出授权范围"
            )
        reasons = _drift_reasons(authorized, current)
        if not reasons:
            return None
        shown = "、".join(reasons[:5])
        if len(reasons) > 5:
            shown += f" 等 {len(reasons)} 处"
        return f"授权之后的新提交超出了当时授权的范围（{shown}）"

    def _waited_phrase(self, card: AcceptCard) -> str:
        """How long this card has been waiting, in 5-minute buckets."""
        if card.decided_at is None:
            return "刚开始等"
        since = card.decided_at
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        minutes = int((datetime.now(UTC) - since).total_seconds() // 60)
        if minutes < _WAIT_BUCKET_MINUTES:
            return "刚开始等"
        return f"已等约 {minutes // _WAIT_BUCKET_MINUTES * _WAIT_BUCKET_MINUTES} 分钟"

    def _note_waiting_on_checks(self, *, card: AcceptCard, tail: str) -> None:
        """约束一 (App 采纳等 CI 再合): a waiting card has to say what it is
        waiting for and how long it has been at it.

        Waiting is now the normal state of an accepted card — the accept stops
        being instant, and a card that shows nothing for 16 minutes reads as a
        card nobody is working. This is the note that keeps it legible.

        Two rules it must obey, both learned from notes that swallowed each
        other (docs 诊断信息搬上验收卡 的优先级说明):

        - **Lowest priority in the family.** It writes only into an empty note
          or over one of its own. `⚠️` (CI 红了 / 重推失败 / 轮询暂停)、`🌿`
          (分支分叉)、`🚫` (GitHub 拒绝合并)、`✋` (安全阀扣住)、`🚪` (PR 被关)、
          `❌` (部署失败) all describe something that needs a human and must
          never be replaced by "还在等".
        - **No churn.** The elapsed time is bucketed (`_WAIT_BUCKET_MINUTES`)
          and the write is skipped when the text is unchanged, so a 16-minute
          wait costs a handful of updates rather than one per 60s tick.
        """
        if card.note and not card.note.startswith(WAITING_CHECKS_PREFIX):
            return
        note = (
            f"{WAITING_CHECKS_PREFIX}（{self._waited_phrase(card)}）："
            f"{tail}。全绿后平台自动合并"
        )[:2000]
        if note == card.note:
            return
        card.note = note

    def _note_needs_human(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        reason: str,
        explain: str | None = None,
    ) -> None:
        """One of the three exceptions fired: say so on the card and in the
        room, and stop — never merge.

        `explain` is the room message's middle sentence — WHY the machine is
        declining. It defaults to the authorization-drift wording because that
        is what the three original exceptions are; a caller that is withholding
        for another reason (a required check that never reported) must pass its
        own, or the room gets told a confident falsehood about what happened.

        The ✋ prefix is deliberately none of the existing ones: `⚠️` is
        `_nudge_pr_fix`'s "已经叫过芝士了" marker (reusing it would silence the
        next real CI failure), `🚫` is GitHub refusing to merge, `❌` is a
        broken deploy. This is neither a failure nor a refusal — it is the
        machine declining to act on an authorization that no longer covers
        what's in the PR. `❌` still outranks it, same as for 🚫.

        Dedup by exact text rather than by prefix: the poll runs every 60s, and
        the reason can legitimately change (范围漂移 → 目标是 prod → …) while
        the card itself hasn't moved."""
        if card.note.startswith("❌"):
            return
        note = (
            f"✋ PR #{card.pr_number} 平台不会自动合并：{reason}。"
            "需要人来定：在卡片上「人工放行」（会记下是谁、什么时候、当时检查什么"
            "状态），自己在 GitHub 上合并这个 PR，或者作废这张卡。"
        )[:2000]
        if card.note == note:
            return  # already said once — the 60s poll must not repeat it
        card.note = note
        logger.warning("card %s: auto-merge withheld — %s", card.id, reason)
        why = explain or (
            f"这是「人类授权动作前移」的安全阀之一：{card.decided_by} 当初授权的是"
            "另一份改动，机器不替他把这一份也签下去。"
        )
        self._notify_merge_result(
            topic,
            f"✋ PR #{card.pr_number} 的检查没有拦住它，"
            f"但平台不会自动合并：{reason}。\n"
            f"{why}需要人来定：在卡片上「人工放行」（明知如此仍合并，平台会记名"
            "留痕），自己在 GitHub 上合并，或者作废这张卡。"
            f"\n{card.pr_url}",
        )

    async def _settle_external_merge(
        self, *, card: AcceptCard, topic: Topic, status: "PullRequestStatus"
    ) -> None:
        """Someone merged the PR on GitHub themselves (人工放行, another bot, the
        merge queue). Book it exactly like our own merge — because it is the same
        fact, and since #206 that fact is the whole of what the platform waits
        for."""
        card.pr_merged_at = status.merged_at or datetime.now(UTC)
        if status.merge_commit_sha:
            # Nice to have, not required: nothing downstream looks a run up by
            # this sha any more, it is just the truest record of what landed.
            card.pr_head_sha = status.merge_commit_sha
        await self._finish_pr_accept(card=card, topic=topic, merged_externally=True)

    def _note_pr_closed_unmerged(self, *, card: AcceptCard, topic: Topic) -> None:
        """The PR was closed on GitHub WITHOUT merging. Say so and stop there.

        No auto-settle and no fallback to the local merge path: unlike
        `_accept_via_pr` (where the accept hasn't landed anywhere yet and
        falling back is the graceful thing), this card's accept is already
        decided and its branch already pushed — a human closing the PR is
        them saying "not this", and merging it locally behind their back
        would be the opposite of what they asked for. A human reopens the PR
        or revokes the accept; either way the poller picks it up from there.

        Without this the card would keep reaching the merge call, take a 405,
        and wear a note that says its checks were green and GitHub refused —
        true but thoroughly misleading about what actually happened.
        """
        note = (
            f"🚪 PR #{card.pr_number} 已在 GitHub 被关闭且没有合并，平台不会自动合并。"
            "需要人决定：重开 PR，或撤销这次采纳。"
        )[:2000]
        if card.note == note:
            return  # already said once — the 60s poll must not repeat it
        card.note = note
        logger.warning(
            "card %s: PR #%s was closed unmerged — poller is now idling on it",
            card.id,
            card.pr_number,
        )
        self._notify_merge_result(
            topic,
            f"🚪 PR #{card.pr_number} 在 GitHub 上被关闭且没有合并，平台不会自动合并。"
            "话题保持 active，需要人决定：重开 PR，或撤销这次采纳。",
        )

    def _note_merge_blocked(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        reason: str,
        chat_service,
        runner,
    ) -> None:
        """Put GitHub's merge refusal on the card's `note` AND wake 芝士 up.
        Before this existed a refusal left `note` empty, so a permanently-
        unmergeable PR looked exactly like a healthy one still waiting on CI.

        The note alone was still not enough (2026-08-11): a note is something
        you have to be looking at. The most common refusal — merge conflicts —
        is exactly the kind 芝士 can fix in its own workspace, so this summons
        it the same way `_nudge_pr_fix` does for a red check. Without the
        summon nobody is working the card and the topic just sits at `pr_open`
        forever (真实案例: PR #242). Note that the conflict dispatch in
        `routes/accept.py` never covers this — that one only runs for the
        synchronous merge at the moment a human clicks 采纳, not for the poll.

        Three things the 60s poll makes mandatory:

        - **No spam.** The note is rewritten only when the text actually
          changes, so an unchanging reason costs one write, not one per poll.
          (Stricter than `_nudge_pr_fix`'s prefix check, which can't notice a
          405 turning into a 409.)
        - **One summon per reason.** The dispatch hangs off that same "the note
          really changed" test rather than a prefix check, so a 405 that turns
          into a 409 gets a fresh nudge while an unchanging one stays quiet.
        - **No clobbering.** `❌ 部署失败` outranks this and is never
          overwritten — that note describes a merged PR whose deploy broke,
          which is strictly more urgent than "not merged yet" — and, since it
          returns before the write, never summons either.
        """
        if card.note.startswith("❌"):
            return
        note = f"🚫 PR #{card.pr_number} 检查全绿，但 GitHub 拒绝合并（{reason}）"
        note = note[:2000]
        if card.note == note:
            return
        card.note = note
        logger.warning("PR merge refused for card %s: %s", card.id, reason)
        runner.submit(
            chat_service,
            topic.id,
            author="system",
            content=(
                f"PR #{card.pr_number}（{card.pr_url}）的检查全绿，"
                "但 GitHub 拒绝合并：\n"
                f"```\n{reason[:1500]}\n```\n"
                "最常见的原因是这个分支和主分支冲突了。请在这个话题的工作区里把主分支"
                "合并进来、解决冲突后提交（不需要、也没法自己推到 GitHub），平台会自动"
                "把新提交同步到这个 PR，检查会自动重新跑，能合并时平台会自动合并。\n"
                "如果原因不是冲突（比如仓库禁用了这种合并方式），工作区里改不动，"
                "请在话题里说清楚卡在哪、需要谁做什么。"
            ),
            summon=True,
            # 平台提示统一契约: the room gets one line; GitHub's own words ride in
            # `meta.detail` (nothing is dropped — `reason` is quoted whole, under
            # the same 1500-char bound the message body always used). `content`
            # above is unchanged and still goes to 芝士 as the prompt.
            nudge_event=f"🚫 PR #{card.pr_number} 全绿但 GitHub 拒绝合并 · 芝士在解",
            nudge_meta=notice(
                EVENT_MERGE_REFUSED,
                severity=SEVERITY_ERROR,
                who=WHO_CHEESE,
                detail=reason[:1500],
                detail_label="GitHub 给的理由",
            ),
        )

    def _nudge_pr_fix(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        tail: str,
        stage: str,
        chat_service,
        runner,
        repo_full_name: str = "",
    ) -> None:
        # Dedup, precisely (2026-08-10). This used to be `startswith("⚠️")`,
        # which treats the whole ⚠️ family as "already nudged" — so a
        # `⚠️ 轮询暂停` note left behind by a dead token silently swallowed
        # every subsequent CI failure: no message, no note, no trace, and the
        # only escape (pr_head_sha moving) needs a human to push first. Two
        # separate reasons to stay quiet, spelled out:
        #   1. we already nudged for THIS stage on this commit — don't spam;
        #   2. 重推失败/分支分叉 outrank a CI failure and must not be overwritten
        #      — both mean 芝士's fix never reached GitHub, so the red CI on
        #      record is stale (docs/topics/诊断信息搬上验收卡.md, 优先级说明).
        if (
            card.note.startswith(_nudge_note_prefix(stage))
            or card.note.startswith(_REPUSH_FAILED_PREFIX)
            or card.note.startswith(_REPUSH_DIVERGED_PREFIX)
        ):
            return
        # `tail` is now a headline PLUS per-job links and log excerpts (see
        # `github_pr._failure_detail`). The card's note is a one-line field in
        # the UI, so only the headline goes there — the detail is exactly what
        # the message is for, and duplicating it into a 2000-char column would
        # cost the note its glanceability for no reader's benefit.
        headline = tail.splitlines()[0] if tail else ""
        card.note = f"{_nudge_note_prefix(stage)}{headline}"[:2000]
        runner.submit(
            chat_service,
            topic.id,
            author="system",
            content=(
                f"PR #{card.pr_number}（{card.pr_url}）的{stage}检查没通过：\n"
                f"```\n{tail[:_NUDGE_TAIL_LIMIT]}\n```\n"
                f"{_ci_log_howto(repo_full_name)}"
                "请在这个话题的工作区里修复问题并提交（不需要、也没法自己推到 "
                "GitHub），平台会自动把新提交同步到这个 PR，检查会自动重新跑；"
                "转绿后平台会自动合并 PR。"
            ),
            summon=True,
            # 平台提示统一契约: this used to land in the room as a message from a
            # fake human called "system" — up to 4000 characters of job list and
            # log excerpts in a full chat bubble. Now the room sees one line and
            # the excerpt rides in `meta.detail`, byte-for-byte the same text
            # under the same `_NUDGE_TAIL_LIMIT` bound.
            nudge_event=f"⚠️ PR #{card.pr_number} 的 {stage} 检查没过 · 芝士在修",
            nudge_meta=notice(
                EVENT_CI_FAILED,
                severity=SEVERITY_ERROR,
                who=WHO_CHEESE,
                detail=tail[:_NUDGE_TAIL_LIMIT],
                detail_label=f"{stage} 日志",
            ),
        )

    async def _finish_pr_accept(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        merged_externally: bool = False,
        headline: str = "",
    ) -> None:
        """The PR is merged, so the work is accepted (#206).

        This used to also require the deploy workflow the merge triggered to
        reach success. That gate is gone: merged is a fact about git that holds
        for every project, while "deployed" is a per-project ops concept the
        platform was in no position to define — and cards waited on deploy runs
        that were sometimes never created at all (three real merges on main,
        2026-08-11, produced zero runs), which is a deadlock, not a safeguard.
        Watching the deploy is real work and it keeps a home: the webhook
        primitive already exists for a pipeline to post its outcome into the
        topic, and #190's ops room is where that judgment belongs.

        `headline` is prefixed onto the card's note when the merge was NOT the
        ordinary all-green one — today that means 人工放行 (`FORCE_MERGED_
        PREFIX`), whose whole point is that the card afterwards says who
        decided to merge red and why. It must survive this method, which
        otherwise rewrites `note` wholesale.
        """
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        by = card.decided_by
        how = (
            "已在 GitHub 上被人工合并（不是平台合的）"
            if merged_externally
            else "已合并"
        )
        settled = f"PR #{card.pr_number} {how}：{card.pr_url}"
        card.note = (f"{headline}；{settled}" if headline else settled)[:2000]
        await self._release_billed_compute(topic)
        # 交付完成 ≠ 话题结束 (#442 decision 1)：话题保持 active，归档由人来做。
        topic.accepted_by = by
        topic.accepted_at = now
        await self._session.flush()
        await self._session.refresh(card)
        self._notify_merge_result(
            topic,
            f"✅ 话题已被 {by} 采纳：PR #{card.pr_number} {how}。\n{card.pr_url}\n"
            "（这一次交付完成了，话题继续活跃——归档由人决定。要再交付一份改动，"
            "在房间里开一件新的事。）",
        )

    async def _resolve_forge(self, project_id: uuid.UUID) -> "forge_mod.Forge":
        """Which forge this project's accept goes through — the one place the
        lane is decided (see app.domain.review.forge)."""
        return await forge_mod.resolve(
            project_id=project_id,
            app_owns_prs=pr_publish.enabled(),
            is_github_bound=self._github_bound,
        )

    async def _github_bound(self, project_id: uuid.UUID) -> bool:
        """Is this project bound to GitHub — an App installation resolved for
        it AND a GitHub https upstream? The single judgment the accept path
        branches on (#363): bound → accepting merges a PR and only a PR;
        unbound → the platform IS the forge, and the local merge is the one
        legitimate accept semantics (not a degrade)."""
        from app.domain.agent.github_app import github_app_tokens_for_project
        from app.domain.review.github_pr import parse_github_repo
        from app.domain.workspace import service as ws

        tokens = await github_app_tokens_for_project(project_id, self._session)
        if tokens is None:
            return False
        upstream = await asyncio.to_thread(ws.get_upstream, project_id)
        return parse_github_repo(upstream) is not None

    async def _stop_accept_pr_unavailable(
        self, card: AcceptCard, topic: Topic, reason: str
    ) -> NoReturn:
        """绑定 GitHub 的项目采纳永不落 local merge (#363): when the card's PR
        cannot be merged right now (GitHub unreachable, PR closed unmerged, …)
        the accept STOPS — visibly and retryably — instead of bypassing the PR
        and its CI with a direct push. The note is persisted outside this
        transaction because the ValidationError below rolls it back.

        **Roll back BEFORE writing that note.** `_note_outside_accept_txn` uses
        its own connection, and by the time we get here this request's
        transaction may already hold a row lock on the very card it wants to
        write (`_publish_pr_for_accept` opens the PR and flushes `pr_number`
        onto the card, and SQLAlchemy's autoflush can push that UPDATE out even
        without an explicit flush). Two connections, one row, and the one
        holding the lock is the one waiting for the other — the request hangs
        until something times it out, and "采纳按钮点下去没反应" is the worst
        possible presentation of a path whose entire job is to fail visibly.
        The rollback loses nothing: this method always raises, so the accept
        transaction was never going to commit, and the PR itself was already
        recorded durably by `pr_publish.record_pr` on its own connection.

        The room notification is built and dispatched first, while `topic` is
        still live — after a rollback its attributes are expired and reading
        them would go back to the database for no reason."""
        why = reason or f"PR #{card.pr_number} 暂时无法推进"
        card_id = card.id
        note = (
            f"{_ACCEPT_PR_STALLED_PREFIX}（{why}）。绑定 GitHub 的项目采纳只通过"
            "合并 PR 完成，平台不会绕过 PR 直推上游；处理后重试采纳。"
        )
        self._notify_merge_result(
            topic,
            f"⛔ 采纳未完成：PR 未能合并（{why}）。平台不会绕过 PR 直推上游；"
            "处理后可重试采纳。",
        )
        await self._session.rollback()
        await self._note_outside_accept_txn(card_id, note)
        raise ValidationError(f"采纳未完成：PR 未能合并（{why}）。处理后重试采纳")

    async def _publish_pr_for_accept(self, card: AcceptCard, topic: Topic) -> None:
        """存量无 PR 卡在采纳现场补开 App PR（#296 stage 1 的生产回归修复）.

        Cards already pending when `accept_via_pr` went live never had a PR
        opened at filing time (and a fire-and-forget publish can also fail, or
        still be in flight) — #328's coexistence guard then dropped them into
        the local-merge branch, which direct-pushed merge commits to main with
        no PR at all (dev: c33cfabf, 8f9b9d94). The repair is to open the App
        PR HERE and let the normal PR accept path merge exactly that PR.

        Synchronous by design: the accept's outcome must depend on the publish
        result, and `_accept_via_pr` already runs pushes and the merge API
        call inside the accept request — one more push plus one create-PR call
        is the same latency class, so no dispatch/poll machinery is warranted.
        Racing a still-in-flight fire-and-forget publish is benign: the push
        is force-with-lease of the same branch, `open_pr` adopts an
        already-open PR for the head instead of failing, and `record_pr`
        writes the same numbers this method records.

        On success the PR is recorded on the card DURABLY, outside the accept
        transaction (`pr_publish.record_pr`): if the accept goes on to fail —
        a faithful 405 refusal from `_accept_via_pr` raises ValidationError
        and rolls this request back — the card must keep the PR it now rides,
        or the next attempt would look PR-less again. The caller then
        proceeds to `_accept_via_pr`. `open_pr_for_card` can still return
        None (its own not-applicable checks); with the caller pre-checking
        `_github_bound`, in practice that means a discussion-only topic with
        no branch — the card is left untouched and the local merge no-ops.
        When opening the PR FAILS, the accept STOPS: the reason is persisted
        on the card outside this transaction, the room is told, and
        ValidationError surfaces to the caller. Silently direct-pushing main
        without a PR is never a fallback on a bound project (#363, all
        commits go through PR)."""
        try:
            pr = await pr_publish.open_pr_for_card(
                self._session,
                card_id=card.id,
                topic_id=topic.id,
                project_id=topic.project_id,
            )
        except Exception as exc:  # noqa: BLE001 — surface on the card; never direct-push
            # Read every attribute we still need BEFORE the rollback below:
            # rollback expires the instance, and an expired attribute reloads
            # itself with synchronous IO that an AsyncSession cannot perform
            # (MissingGreenlet) — which would replace this readable failure
            # with an unreadable one.
            card_id = card.id
            logger.exception("accept-time PR publication failed for card %s", card_id)
            reason = f"{exc}"[:300]
            note = (
                f"{_ACCEPT_PR_OPEN_FAILED_PREFIX}（{reason}）。"
                "平台不会在没有 PR 的情况下把改动直推上游；修复后重试采纳。"
            )
            self._notify_merge_result(
                topic,
                f"❌ 采纳未完成：无法为这张卡开 PR（{reason}）。"
                "平台不会在没有 PR 的情况下把改动直推上游；处理后可重试采纳。",
            )
            # Roll back first, for the same reason as `_stop_accept_pr_
            # unavailable`: the out-of-transaction note writes the card row on
            # its own connection, and it must never be able to queue behind a
            # lock this doomed transaction is still holding.
            await self._session.rollback()
            await self._note_outside_accept_txn(card_id, note)
            raise ValidationError(
                "采纳未完成：无法为这张卡开 PR（原因已写在卡片上）。"
                "平台不会在没有 PR 的情况下把改动直推上游；修复后重试采纳"
            ) from exc
        if pr is None:
            return  # PR 路对这个项目/话题不适用 — 本地合并就是它唯一的采纳方式
        # Durable first (survives a later rollback of this request), then the
        # in-memory mirror so the rest of THIS accept sees the PR. Same
        # bind-not-global-factory reasoning as _note_outside_accept_txn.
        factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
        await pr_publish.record_pr(factory, card_id=card.id, pr=pr)
        card.pr_number = int(pr["number"])
        card.pr_url = str(pr.get("html_url") or "")[:255] or None
        if card.note.startswith(pr_publish.PR_OPEN_FAILED_PREFIX):
            card.note = ""  # mirror record_pr's stale-failure-note clearing
        await self._session.flush()

    async def _authorize_pr_for_accept(
        self, card: AcceptCard, topic: Topic, decided_by: str
    ) -> AcceptCard:
        """App forge (App 采纳等 CI 再合)：采纳把卡送进 `pr_open`，**不合并**。

        这是这条路上「合并」和「授权」的分家。在此之前 `_accept_via_pr` 读一次
        check-runs、把状态写进 note，然后立刻调合并 API —— 那句 note 是如实留痕，
        不是拦截，所以 PR #414 在开出 25 秒后就进了 main，而最后一项检查比合并晚
        了 16 分钟。绿是运气，门禁根本没等。

        改法不是新造一道闸门（andy 在 #362 定的原则是 mirror, don't gate，而
        andy 的前置闸门早已退役、代码也已删除），而是让这条路也走
        `github_user` 早就在走的两阶段：人点采纳 = 授权「以我的名义把这份改动送
        进 CI，全绿且没超出授权范围就合」，剩下的交给 `advance_pr_card`。等 CI
        全绿再合，读的正是 forge 自己的检查结论——这恰恰是 mirror。

        轮询器要的四个字段在这里一次补齐（`pr_repo` / `pr_head_sha` /
        `pr_authorized_sha`，外加 `pr_merged_at=None`）。App 这条路此前只写
        `pr_number` + `pr_url`，而 `advance_pr_card` 缺任何一个就只 log 一行
        error 然后 return —— 卡会永远停在 `pr_open`，不报错、不提醒、界面上看不
        出来。

        PR 当前状态在这里读一次，为的是保住 #363 已经定下的两条契约：GitHub 上
        已经合了 → 照单收下；PR 被关掉没合 → 采纳停下（forge 说了不，绝不改走
        本地合并直推）。GitHub 不可达同样停下、可重试。
        """
        from app.domain.review import github_pr
        from app.domain.review.github_pr import parse_github_repo
        from app.domain.workspace import service as ws

        number = card.pr_number
        assert number is not None  # caller checked; keeps the type checker honest

        creds, why = await self._app_credentials(topic)
        upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
        parsed = parse_github_repo(upstream)
        if creds is None or parsed is None:
            # `_github_bound` said yes moments ago, so this is config changing
            # under us. Stop rather than guess where the PR should land.
            await self._stop_accept_pr_unavailable(
                card,
                topic,
                f"读不到 PR #{number} 该合进哪个仓库（{why or 'App 或上游配置已变'}）",
            )
        owner, repo_name = parsed
        client = github_pr.default_client()
        try:
            status = await client.pull_request_status(
                owner=owner, repo=repo_name, number=number, token=creds.read
            )
        except Exception as exc:  # noqa: BLE001 — stop visibly; never local-merge
            logger.warning(
                "cannot read PR #%s for card %s at accept time: %s",
                number,
                card.id,
                exc,
            )
            await self._stop_accept_pr_unavailable(
                card, topic, f"PR #{number} 状态读取失败：{exc}"[:300]
            )

        if status.merged:
            # 人已经在 GitHub 上合了这个 PR —— 同一件事，照单收下。
            return await self._settle_pr_accept(
                card, topic, decided_by, note=f"PR #{number} 已在 GitHub 合并"
            )
        if status.state == "closed":
            await self._stop_accept_pr_unavailable(
                card, topic, f"PR #{number} 已在 GitHub 被关闭但未合并"
            )

        # 人授权的是「现在工作区里这一份」，所以最后的改动先推上去，再把推上去的
        # 那个 commit 冻结成授权基线。
        try:
            await asyncio.to_thread(
                ws.push_topic_branch, topic.project_id, topic.id, creds.write
            )
            head_sha = await client.pull_request_head_sha(
                owner=owner, repo=repo_name, number=number, token=creds.read
            )
        except Exception as exc:  # noqa: BLE001 — stop visibly; never local-merge
            logger.warning(
                "cannot push/refresh PR #%s for card %s: %s", number, card.id, exc
            )
            await self._stop_accept_pr_unavailable(
                card, topic, f"PR #{number} 推送话题分支失败：{exc}"[:300]
            )

        await self._repo.add_approval(card.id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.pr_open
        card.decided_by = decided_by
        card.decided_at = now
        card.pr_repo = f"{owner}/{repo_name}"
        card.pr_head_sha = head_sha
        # 人类授权动作前移: 冻结人此刻批的那个 commit。`pr_head_sha` 之后会跟着
        # 芝士推的每个修复走，这一个不会——轮询器合并前拿两者比对。
        card.pr_authorized_sha = head_sha
        card.pr_merged_at = None
        card.note = (
            f"{WAITING_CHECKS_PREFIX}（刚开始等）：已授权 PR #{number}，"
            f"检查全绿且没超出授权范围时平台自动合并：{card.pr_url or ''}"
        )[:2000]
        await self._session.flush()
        await self._session.refresh(card)
        self._notify_merge_result(
            topic,
            f"🔁 {decided_by} 授权了这次改动，PR #{number} 交给 CI —— "
            f"**采纳不再是秒回**，本仓库的检查要跑十几分钟。{card.pr_url or ''}\n"
            "话题保持 active（容器不停）。检查全绿、且改动没超出授权范围时平台自动"
            "合并并归档；三种例外（新 diff 越界 / 根本没有 CI 会跑 / 目标是 prod）"
            "会回来找人。\n"
            "⚠️ 等 CI 期间平台每 60 秒会把工作区的新提交同步到这个 PR —— 这时候改"
            "工作区会让 CI 从头重跑。",
        )
        return card

    async def _note_outside_accept_txn(self, card_id: uuid.UUID, note: str) -> None:
        """Persist a card note through its own session + commit, so it survives
        the rollback of the accept transaction it accompanies (the caller is
        about to raise). Sessions are minted off the request session's own
        engine — NOT the module-level `async_session_factory`, which the test
        harness binds to a different database than the request session.
        Best-effort: the raise this note accompanies must fire regardless."""
        try:
            factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
            async with factory() as session:
                fresh = await AcceptCardRepository(session).get(card_id)
                if fresh is None:
                    return
                fresh.note = note[:2000]
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("could not record the PR-open failure on card %s", card_id)

    async def _accept_via_pr(
        self, card: AcceptCard, topic: Topic, decided_by: str
    ) -> tuple[AcceptCard | None, str]:
        """Accept by merging the card's EXISTING GitHub PR (#188 §5.1) —
        distinct from `_open_pr_for_accept` above (两阶段采纳), which opens a
        NEW PR rather than merging one already recorded on the card.

        Returns (settled card, "") when the PR path finished the accept
        (accepted or conflict), or (None, reason) to fall back to the local
        merge path — config drift and GitHub outages must leave accept
        exactly as available as before PR-based accept existed. `reason` is
        a human-readable, secret-free explanation of WHY it fell back (empty
        when there's nothing worth surfacing, e.g. the App simply isn't
        configured for this project) — the caller folds it into the same
        `pr_degrade_reason` that ends up on the card's note.

        **No longer on the App forge** (App 采纳等 CI 再合, 2026-08-15). There,
        accepting authorizes and the poller merges once CI is green
        (`_authorize_pr_for_accept`) — precisely because the posture described
        below merged PR #414 twenty-five seconds after it was opened, sixteen
        minutes before its last check finished. What still reaches here is the
        personal-token / unbound world: a card carrying a `pr_number` on a
        project whose forge is NOT `github_app`.

        Mergeability posture (#362, 对齐 GitHub — see the module comment above
        `_checks_summary`): right before merging, the PR's check-runs are read
        once and their state is mirrored into the accept's note and room
        notification — never used to block. A 405 from the merge API is
        translated faithfully: a genuine conflict goes to the conflict flow
        (芝士 dispatched to resolve), anything else (draft, required reviews,
        …) surfaces GitHub's own message and stops the accept — sending 芝士
        to "resolve" a conflict that does not exist wastes a turn and writes
        a false history on the card.
        """
        from app.domain.agent.github_app import github_app_tokens_for_project
        from app.domain.review.github_pr import (
            GitHubPRClient,
            GitHubPRMergeBlocked,
            parse_github_repo,
        )
        from app.domain.workspace import service as ws

        assert card.pr_number is not None
        number = card.pr_number
        # #192: resolve the installation from the card's project, not a global.
        tokens = await github_app_tokens_for_project(topic.project_id, self._session)
        upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
        parsed = parse_github_repo(upstream)
        if tokens is None or parsed is None:
            return None, ""  # App unconfigured / upstream changed since PR opened
        client = GitHubPRClient(*parsed, tokens)
        branch = ws.branch_for_topic(topic.id)

        try:
            # Someone may have handled the PR on GitHub directly — respect it.
            view = await client.pr_view(number)
            if view.get("merged"):
                settled = await self._settle_pr_accept(
                    card, topic, decided_by, note=f"PR #{number} 已在 GitHub 合并"
                )
                return settled, ""
            if view.get("state") == "closed":
                logger.warning(
                    "PR #%s for card %s was closed unmerged — falling back "
                    "to the local merge path",
                    number,
                    card.id,
                )
                return None, f"PR #{number} 已在 GitHub 被关闭但未合并"

            # Re-push first: last-minute worktree edits and conflict fixes must
            # be what actually merges.
            token, _ = await tokens.write_token()
            await asyncio.to_thread(
                ws.push_topic_branch, topic.project_id, topic.id, token
            )
            # CI 镜像 (#362): read the branch tip's check-runs once and carry
            # their state into the note/notification below — the human saw the
            # same state in the accept UI (/pr-checks) before clicking, and
            # the platform never blocks on it. A failed read must not block
            # the merge either; it is reported as exactly that.
            try:
                checks = await client.check_runs(branch)
            except Exception:  # noqa: BLE001 — mirror-only, never blocks the accept
                logger.exception("pre-merge check-runs read failed for PR #%s", number)
                checks = None
            checks_line = _checks_summary(checks)
            # Same builders as every other merge path: this is the squash
            # commit that lands on the default branch, and it used to say
            # "采纳 topic/8f3a… → main (#7)" with the reviewer's handle for a
            # body — the branch it came from and who clicked, but nothing at
            # all about what changed.
            who = await identity.attribution(self._session, topic)
            await client.merge_pr(
                number,
                title=pr_text.merge_commit_title(card, topic, number),
                message=pr_text.merge_commit_message(topic, decided_by, card, who),
            )
        except GitHubPRMergeBlocked as blocked:
            # 405 covers a whole family of "cannot merge right now" reasons
            # (real conflict, draft, required reviews, …). 如实转译 (#362):
            # only a genuine conflict belongs in the conflict flow below —
            # for everything else, surface GitHub's own message and stop the
            # accept. GitHub's body names the reason; the PR view's
            # mergeable/mergeable_state corroborate. When neither identifies
            # the reason (message unparseable AND view undecided), keep the
            # conflict flow — the safe, previously-universal default.
            github_msg = _github_merge_refusal_message(blocked)
            mergeable_state = str(view.get("mergeable_state") or "").lower()
            is_conflict = (
                view.get("mergeable") is False
                or mergeable_state == "dirty"
                or (not github_msg)
                or "not mergeable" in github_msg.lower()
            )
            if not is_conflict:
                refusal = f"GitHub 拒绝合并 PR #{number}：{github_msg}"
                self._notify_merge_result(
                    topic, f"⛔ 采纳未合并：{refusal}（{card.pr_url or ''}）"
                )
                raise ValidationError(refusal) from blocked
            # Same contract as a local merge conflict: card → conflict, 芝士 is
            # dispatched (routes/accept.py), human retries. Sync the local base
            # first so the materialized conflict matches what GitHub sees.
            sync_failure_note = ""
            try:
                await asyncio.to_thread(ws.sync_upstream, topic.project_id)
            except Exception as sync_exc:  # noqa: BLE001 — conflict flow still works on a stale base
                logger.exception(
                    "sync_upstream after merge refusal failed for %s", topic.id
                )
                # Visible on the card: a materialized conflict built on a
                # stale base can show paths that no longer actually conflict.
                sync_failure_note = (
                    f"；同步上游失败，冲突可能基于陈旧的 base：{sync_exc}"
                )[:300]
            await self._repo.add_approval(card.id, decided_by)
            card.status = AcceptStatus.conflict
            card.decided_by = decided_by
            card.decided_at = datetime.now(UTC)
            card.note = (f"PR #{number} 合并冲突，已派芝士解决{sync_failure_note}")[
                :2000
            ]
            await self._session.flush()
            await self._session.refresh(card)
            return card, ""
        except Exception as exc:  # noqa: BLE001 — any non-conflict failure falls back
            logger.exception(
                "PR accept failed for card %s (PR #%s): %s — falling back "
                "to the local merge path",
                card.id,
                number,
                exc,
            )
            return None, f"PR #{number} 采纳失败：{exc}"[:300]

        settled = await self._settle_pr_accept(
            card,
            topic,
            decided_by,
            # 合并那一刻 forge 检查状态的留痕 (#362): a red or absent CI at
            # merge time was the human's call to make — but the call and its
            # context must be readable on the card afterwards.
            note=f"已通过 PR #{number} 合并到上游（合并时{checks_line}）",
        )
        return settled, ""

    async def _settle_pr_accept(
        self, card: AcceptCard, topic: Topic, decided_by: str, *, note: str
    ) -> AcceptCard:
        """Post-merge bookkeeping shared by the PR path: sync the platform's
        main down from upstream (the merge happened THERE), then mark the topic
        delivered — delivered, not archived (#442 decision 1)."""
        from app.domain.workspace import service as ws

        try:
            synced = await asyncio.to_thread(ws.sync_upstream, topic.project_id)
            if not synced.get("synced"):
                note += f"；本地同步待补：{synced.get('reason', '')}"
                # Say what to DO about it. The merge landed upstream, so this
                # note is the only trace the pull-down failed, and a conflict
                # here recurs on every later sync until someone resolves it —
                # 同步上游 now dispatches 芝士 at the materialized conflict
                # (workspace/upstream_conflict.py) instead of dead-ending.
                if synced.get("conflicts"):
                    note += "；到项目里点一次「同步上游」，芝士会去解这个冲突"
        except Exception as exc:  # noqa: BLE001 — never fail the accept itself
            note += f"；本地同步待补：{exc}"

        await self._repo.add_approval(card.id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now
        card.note = note[:2000]

        await self._release_billed_compute(topic)

        # 交付完成 ≠ 话题结束 (#442 decision 1)：话题保持 active，归档由人来做。
        topic.accepted_by = decided_by
        topic.accepted_at = now

        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def reject(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError("验收卡已处理，不能重复决议")
        if decided_by != card.reviewer_handle:
            raise ForbiddenError("你不是这张验收卡指定的验收人，无权驳回")

        card.status = AcceptStatus.rejected
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        card.note = note

        # Topic stays active on rejection.
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def revoke(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # Accept is revocable (spec §6.3): only an accepted card can be revoked.
        if card.status != AcceptStatus.accepted:
            raise ValidationError("只有已验收的卡才能撤销")

        # Only the person who accepted it, or the project owner/lead, may revoke
        # — not any arbitrary handle.
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        allowed = {card.decided_by}
        if project is not None and project.owner_handle:
            allowed.add(project.owner_handle)
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        allowed |= {m.user_handle for m in members if m.role == ProjectRole.lead}
        if decided_by not in allowed:
            raise ValidationError("只有原采纳人或项目组长能撤销采纳")

        card.status = AcceptStatus.revoked
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)

        # 撤销的是这次**验收记录**，不是这次合并 —— PR 已经在 main 上了，git 层面
        # revoke 什么都没撤。所以这里只清交付标记（话题回到"还没交付过"，因此
        # 又能递卡）。
        #
        # 归档状态一律不动，这是 2026-08-17 的对称面：`TopicService.unarchive` 的
        # docstring 说「取消归档不改写采纳记录，那要用撤回采纳」；反过来同理——
        # 撤回采纳不改写归档状态，那是人的决定（取消归档）。以前这里要把话题拉回
        # active，是因为采纳会顺手归档；采纳不再归档之后，一张卡的撤销没有理由
        # 覆盖某个人「把这个话题收起来」的动作。
        topic.accepted_by = None
        topic.accepted_at = None

        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def merge_despite_checks(
        self, *, card_id: uuid.UUID, decided_by: str, reason: str = ""
    ) -> AcceptCard:
        """约束二 (App 采纳等 CI 再合)：人明知检查没全绿，仍然决定合并——**署名的**
        显式出口。

        为什么必须有：红着合有时候是对的。CI 基础设施抽风、与本次改动无关的既有
        失败、赶时间的热修——真正不能接受的不是「红着合」，而是**没有人做过这个
        决定**。这正是这次改动要终结的东西：`_accept_via_pr` 读一次检查、把
        「合并时 CI 检查未全绿」写进 note，然后照合——默认放行、事后留痕。这条
        出口把它翻过来：**默认拒绝、显式放行**，而且放行必须签字。

        为什么不是把 andy 已经退役的前置闸门造回来（#296 退役,runner 与
        `run_check_command` 都已删除）：平台不重算「这段代码好不好」，它只是把
        forge 的结论如实呈上，然后让一个**具名的人**在上面按手印。

        它记什么：谁、什么时候、**当时的检查到底是什么状态**（现读一次，读不到
        就如实写读不到——但绝不因此拒绝放行，凭据坏了不该把人锁在门外）、以及
        人自己写的理由。这四样凑齐，事后才答得上「这次红着合，是谁决定的」。

        谁能点：这张卡的验收人、当初授权开 PR 的人、项目 owner、项目 lead。
        芝士被 `_forbid_ai` 挡在外面（跟 accept/approve/void 同一条线），路由也
        **故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`——照 `void` 的先例：
        不进白名单本身拦不住任何东西（没列进去的写路由压根不过那个中间件），真正
        拦住芝士的是这里的 `_forbid_ai` 加路由上的登录校验。
        """
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pr_open or card.pr_merged_at is not None:
            raise ValidationError("只有还在等检查的验收卡（pr_open）能人工放行合并")
        if card.pr_number is None or not card.pr_repo or not card.pr_head_sha:
            raise ValidationError("这张卡没有可合并的 PR")

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "人工放行合并")

        allowed = {card.reviewer_handle}
        if card.decided_by:
            allowed.add(card.decided_by)
        if project is not None and project.owner_handle:
            allowed.add(project.owner_handle)
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        allowed |= {m.user_handle for m in members if m.role == ProjectRole.lead}
        if decided_by not in allowed:
            raise ForbiddenError(
                "只有这张卡的验收人、授权人或项目 owner / 组长能人工放行"
            )

        creds, why = await self._pr_poll_credentials(card, topic)
        if creds is None:
            raise ValidationError(f"暂时拿不到合并这个 PR 用的 GitHub 凭据（{why}）")

        from app.domain.review import github_pr

        owner, _, repo = card.pr_repo.partition("/")
        client = github_pr.default_client()
        # 留痕用，不是门禁：读一次「此刻检查是什么状态」，读不到也照样放行。
        state: str | None = None
        try:
            state, tail = await client.check_state(
                owner=owner, repo=repo, ref=card.pr_head_sha, token=creds.read
            )
            checks_at_merge = f"{state}（{tail.splitlines()[0] if tail else ''}）"
        except Exception as exc:  # noqa: BLE001 — a broken read must not lock a human out
            logger.warning(
                "force-merge check read failed for card %s: %s", card.id, exc
            )
            state = None
            checks_at_merge = "读不到检查状态"
        # 读到的状态决定这句话怎么写：全绿时说「明知未全绿」是往历史里写一条从没
        # 发生过的决定（PR #520 真的这么记了一条）。
        verdict = _force_merge_verdict(state)

        number = card.pr_number
        who = await identity.attribution(self._session, topic)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(
                topic, card.decided_by or "", card, who
            ),
        )
        if result.sha is None:
            raise ValidationError(
                f"GitHub 拒绝合并 PR #{number}：{result.blocked_reason or '未说明原因'}"
            )

        now = datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%d %H:%M UTC")
        tail_reason = f"，理由：{reason.strip()}" if reason.strip() else ""
        headline = (
            f"{FORCE_MERGED_PREFIX}：<@{decided_by}> 于 {stamp} 人工放行合并"
            f"（{verdict}；合并时检查状态：{checks_at_merge}）{tail_reason}"
        )
        card.pr_merged_at = now
        card.pr_head_sha = result.sha
        await self._finish_pr_accept(card=card, topic=topic, headline=headline)
        logger.warning(
            "card %s: PR #%s force-merged by %s (checks: %s)",
            card.id,
            number,
            decided_by,
            checks_at_merge,
        )
        self._notify_merge_result(
            topic,
            f"🔨 <@{decided_by}> 人工放行了 PR #{number}：{verdict}"
            f"（合并时检查状态：{checks_at_merge}）{tail_reason}。\n"
            f"{card.pr_url or ''}",
        )
        return card

    async def void(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        """人工作废一张未决的验收卡 (pending_gate 孤儿卡出口, 2026-08-11).

        这是**唯一**能把非终态卡强制收尾的人工动作。它存在的理由是 `create_card`
        的互斥：一张卡卡在 `pending_gate` / `conflict` / `pr_open` 上，整个话题
        就再也递不出第二张卡，而 accept/reject/revoke/reassign 五条路由对这些状态
        全部是拒绝的——出口是零。

        ⚠️ **它把卡置为终态，不是"放行到 pending"**。放行等于让卡面的绿勾替一段
        从没被检查过的代码背书；作废 + 重递效果一样而且安全，这条区别是本功能的
        设计前提，不要"优化"掉。

        授权：卡上的验收人、项目 owner、项目 lead。它是授权类动作，所以芝士在
        collaborative 模式下被 `_forbid_ai` 挡住（跟 accept/approve 同一条线）
        —— 路由也**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`。
        """
        card = await self._card_or_404(card_id)
        if card.status not in archive.OPEN_CARD_STATUSES:
            raise ValidationError(f"这张验收卡已经是终态（{card.status}），不用作废")

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "作废")

        allowed = {card.reviewer_handle}
        if project is not None and project.owner_handle:
            allowed.add(project.owner_handle)
        members = await MemberRepository(self._session).list_for_project(
            topic.project_id
        )
        allowed |= {m.user_handle for m in members if m.role == ProjectRole.lead}
        if decided_by not in allowed:
            raise ForbiddenError("只有这张卡的验收人或项目 owner / 组长能作废它")

        was = card.status
        reason = f" 理由：{note.strip()}" if note.strip() else ""
        headline = (
            f"{VOIDED_PREFIX}：<@{decided_by}> 作废了这张卡（原状态：{was}）。"
            f"话题可以重新递卡。{reason}"
        )
        if was == AcceptStatus.pr_open and card.pr_merged_at is None:
            # 跟归档收敛同一条产品判断 (review/archive.py 的模块 docstring)：平台
            # 不拿别人的 token 去关别人名下的 PR。停止推进 + 留痕 + 通知授权人。
            headline = (
                f"{VOIDED_PREFIX}：<@{decided_by}> 作废了这张卡，平台已停止推进 "
                f"PR #{card.pr_number}。PR 未合并、仍开在 GitHub 上，合还是关由人"
                f"决定：{card.pr_url or '(无链接)'}{reason}"
            )
        card.status = AcceptStatus.revoked
        card.note = archive.prefix_note(card.note, headline)
        # 只在空的时候补：`pr_open` 的卡上 decided_by 记的是当初授权开 PR 的人，
        # 覆盖掉就丢了授权来源；作废人始终写在 note 里。
        if card.decided_by is None:
            card.decided_by = decided_by
        if card.decided_at is None:
            card.decided_at = datetime.now(UTC)

        await self._session.flush()
        await self._session.refresh(card)

        await BlockRepository(self._session).add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author="cheese",
            author_type=AuthorType.system,
            content=(
                f"🗑 <@{decided_by}> 作废了这张验收卡（原状态：{was}）。"
                f"这不是驳回，也不代表检查不通过——它只是把卡收尾，"
                f"好让这个话题能重新递卡。{reason}"
            ),
            kind=BlockKind.event,
            meta={"platform": True},
        )
        if was == AcceptStatus.pr_open and card.decided_by not in (None, decided_by):
            await AlertService(self._session).create(
                project_id=topic.project_id,
                level=AlertLevel.strong,
                kind=AlertKind.change_alert,
                title=f"话题「{topic.title}」的验收卡被作废，你的 PR 还开着",
                body=(
                    f"<@{decided_by}> 作废了这张验收卡，平台已停止推进它。"
                    f"PR #{card.pr_number} 是以你的身份开的，平台不会替你关掉："
                    f"{card.pr_url or '(无链接)'}"
                ),
                target_handle=card.decided_by or "",
                topic_id=topic.id,
            )
        return card
