"""Shared primitives for the review accept-card service: the module logger,
the injected reviewer-admission signature, card wording prefixes,
artifact/deliverable validation, the merge-verdict wording helpers, and the
GitHub-enforcement cache. Nothing here owns state.

本包摸到的三个别人的 repository（block / project / topic）也在这里入账：
`AcceptService.__init__` 建会话、`_refresh_stale_card` 刷新时另开一条读、
`void` 写一块。拆包前这三条边在 `review.services` 名下（`_EXEMPT` 与
`review.repositories` 的写法），拆包后发起方是这里——同一笔债换了发起方，
没有新增跨域的边。子模块从这里取名字，不再各自直连。"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_DISMISSED as EVENT_ACCEPT_DISMISSED,
)
from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_DONE as EVENT_ACCEPT_DONE,
)
from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_STOPPED as EVENT_ACCEPT_STOPPED,
)
from app.domain.agent.platform_notices import (
    EVENT_CARD_REDESCRIBED as EVENT_CARD_REDESCRIBED,
)
from app.domain.agent.platform_notices import (
    EVENT_CARD_VOIDED as EVENT_CARD_VOIDED,
)
from app.domain.agent.platform_notices import (
    EVENT_FORCE_MERGED as EVENT_FORCE_MERGED,
)
from app.domain.agent.platform_notices import (
    EVENT_MERGE_REFUSED as EVENT_MERGE_REFUSED,
)
from app.domain.agent.platform_notices import (
    EVENT_MIGRATION_COLLISION as EVENT_MIGRATION_COLLISION,
)
from app.domain.agent.platform_notices import (
    EVENT_PR_CLOSED as EVENT_PR_CLOSED,
)
from app.domain.agent.platform_notices import (
    SEVERITY_ERROR as SEVERITY_ERROR,
)
from app.domain.agent.platform_notices import (
    SEVERITY_INFO as SEVERITY_INFO,
)
from app.domain.agent.platform_notices import (
    SEVERITY_WARN as SEVERITY_WARN,
)
from app.domain.agent.platform_notices import (
    WHO_CHEESE as WHO_CHEESE,
)
from app.domain.agent.platform_notices import (
    WHO_HUMAN as WHO_HUMAN,
)
from app.domain.agent.platform_notices import (
    WHO_PLATFORM as WHO_PLATFORM,
)
from app.domain.agent.platform_notices import (
    notice as notice,
)
from app.domain.block.notice_text import NoticeText, say
from app.domain.block.repositories import BlockRepository as BlockRepository
from app.domain.project.models import Project
from app.domain.project.repositories import ProjectRepository as ProjectRepository
from app.domain.review import (
    commit_message,
)
from app.domain.review.models import (
    AcceptStatus,
)
from app.domain.topic.models import Topic
from app.domain.topic.repositories import TopicRepository as TopicRepository

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    pass

from app.domain.review import services as pkg

logger = logging.getLogger("cheesex.review")

#: 「这个房间会放这个人进来吗」——由路由注入（`api/routes/accept.py` 拿
#: `ActorResolver.topic_admits_handle`）。签名收 `topic` 而不是三个 id：注入方要的
#: 是「哪个房间、哪个人」，而不是这一域怎么拆 id。
ReviewerAdmission = Callable[[Topic, str], Awaitable[bool]]

# 卡上那句话的措辞。状态码在 review/notes.py，这里只有文案——两者分开之后，改一
# 句话不再改掉任何一处判断，所以这些常量存在的理由只剩「同一句话写在两处」。
#
# 不带 emoji：卡自己按 `note_level` 画轻重（前端 TopicAcceptCard），开头再放一个
# 表情就是同一件事说两遍——一遍是结构，一遍是屏幕阅读器会念出来的一个字符。
#: 采纳现场补开 App PR 失败（存量无 PR 卡，#296 stage 1 的回归修复）。开不出 PR
#: 时采纳停下、原因亮在卡上——绑定 GitHub 的项目绝不静默本地合并直推 main
#: （all commits go through PR）。卡保持 pending，人处理后可直接重试采纳。
_ACCEPT_PR_OPEN_FAILED_PREFIX = "采纳未完成：无法开 PR"
#: 卡上有 PR 但此刻推进不了（GitHub 不可达 / PR 被关闭未合并 / …）。绑定 GitHub
#: 的项目采纳只通过合并 PR 完成 (#363)——这类失败停下亮出来，永不落 local merge。
_ACCEPT_PR_STALLED_PREFIX = "采纳未完成：PR 未能合并"
#: 卡带着交付主张（change_subject 非空），树的分支上却没有任何提交。2026-09-07
#: 卡 40be3e1a：改动被推到了别的分支，树分支从未存在，`open_pr_for_card` 把它
#: 当成讨论话题返回 None，采纳落进本地合并 no-op——卡标成 accepted，人以为交付
#: 完成，而改动没有合进任何地方。主张交付却无从交付的采纳必须停下；只有真正的
#: 存量讨论卡（change_subject 为 NULL，递于 subject 必填之前）才允许 no-op 采纳。
_ACCEPT_NO_BRANCH_PREFIX = "采纳未完成：分支上没有任何提交"

#: pending_gate 孤儿卡 (2026-08-11). 判死的卡和检查真红了的卡都落在 `gate_failed`
#: 上，但对芝士意味着完全相反的下一步——「没跑完」= 原样重递，「没通过」= 去修
#: 代码。gate_sweep.py 把这句话同时写进 note 和 gate_output，也发给芝士。
GATE_ABANDONED_PREFIX = "检查未完成"
#: 人工作废 (2026-08-11)。作废复用 `revoked` 终态（archive.py 收敛非终态卡时也
#: 用它），所以「谁作废的、为什么」只能写在 note 里。
VOIDED_PREFIX = "已作废"
#: 人工放行。红着合有时是对的（CI 基础设施抽风、与本次改动无关的既有失败），
#: 不能接受的是**没有人做过这个决定**。这条 note 就是那个署名：谁、什么时候、
#: 当时检查是什么状态、理由。默认拒绝、显式放行。
FORCE_MERGED_PREFIX = "人工放行"


@dataclass(frozen=True)
class _GitHubCredentials:
    """驱动一张在途 PR 卡所需的 GitHub 凭据 —— **两把钥匙，不是一把**。

    个人 token 那条路上它们是同一个字符串（一个 OAuth token 什么都能干）。App
    这条路上它们不是，而且分不开就会坏：`GitHubAppTokens.write_token()` 逐项写死了
    `contents:write` + `pull_requests:write` + `workflows:write` + `metadata:read`
    （`_WRITE_PERMISSIONS`，为的是某项被撤销时在铸币那一刻就报出来），**里面没有
    `checks`**。拿它去读 `/commits/{ref}/check-runs` 会 403 —— 而轮询器把它当成一次
    GitHub 抖动，下一轮再来，于是卡永远推不动，卡面上什么都不会写。

    所以：GET 用 `read`（`installation_token()`，带 `checks:read`），推分支和合并用
    `write`。
    """

    write: str
    read: str


_MERGE_FAILED_MESSAGE = (
    "Acceptance could not complete because the topic could not be merged. "
    "The card remains pending and the topic stays active; repair the workspace "
    "and retry."
)


def _stale_view_message(pr_number: int | None, action: str) -> str:
    """「你看到的版本已过时」—— 卡面渲染时的 head 与卡当前的 head 不是一个。

    与「head 在你查看后变了」(`_refresh_stale_card` 那条) 是同一件事的两个发现
    时机：那条是点击时现读 GitHub 才发现漂移，这条是轮询器**已经**把卡刷到新
    head、只有浏览器里那份还停在旧版本。卡不用刷新（它已经是新的），要刷新的
    是人的眼睛。"""
    if pr_number is None:
        return say("staleView", action=action)
    return say("staleViewPr", pr=pr_number, action=action)


def _never_shown_message(pr_number: int | None, action: str) -> str:
    """「卡面还没显示过任何版本」—— PR 上的卡，`pr_head_sha` 还是空的。

    这不是「没有版本可以过时」，而是**还不知道要合什么**：卡刚递上来、轮询器
    还没镜像过 head，屏幕上那张卡从来没写出过一个 sha，所以点下去只能拿现读
    GitHub 的 head 去合 —— 一个从未在任何界面上出现过的 commit。卡先刷新到当前
    head（`_refresh_stale_card`），人重新看一眼，那一版才算被看过。"""
    if pr_number is None:
        return say("neverShown", action=action)
    return say("neverShownPr", pr=pr_number, action=action)


# ---- 采纳 = 当场调合并 API (#718) -------------------------------------------
#
# 「只在绿的时候合」由分支保护规则执行，规则算在一个地方：
# `merge_state.compute_merge_state`，点击时和轮询时都调它。GitHub 能判定的听
# GitHub（绑了 GitHub 且 GitHub 自己开了保护 → 裁决透传，点击直接调 API，405
# 就是被拦住）；判定不了的平台按项目的 `branch_protection` 配置补位。
#
# 红着合的出口是署名的那一个：`merge_despite_checks`（卡片上的「人工放行」）。
# 默认拒绝、显式放行。

#: 必跑检查「迟迟没报到」的宽限：镜像里同一个 (state, head) 组合持续超过这个
#: 时长仍缺必跑检查，就把等待交给人（workflow 改名/被禁用/Actions 断供都长这
#: 样）。出口是叫人，**绝不因为等腻了就自动合并**。曾是全局 config
#: （accept_required_check_grace_minutes），随平台级名单一起退役成常数 —— 它
#: 不是「本仓库应该等多久」的产品参数，只是「多久算不对劲」的兜底。
_REQUIRED_CHECK_GRACE_MINUTES: Final = 30

#: `github_repo_snapshot`（「GitHub 自己开没开保护」）的进程内缓存 TTL。这个
#: 判断走两三个 HTTP 请求、答案以天为单位才变，而轮询每 60 秒一拍。
_GITHUB_ENFORCES_TTL_S: Final = 900.0
_github_enforces_cache: dict[str, tuple[float, bool]] = {}


async def _github_enforces(repo: str, token: str | None) -> bool:
    """GitHub 自己在管这个仓库的主干吗（15 分钟缓存）。

    读不到（free 计划私有仓对保护接口一律 403）按 False：平台补位正是为这种
    仓库存在的。"""
    now = time.monotonic()
    hit = _github_enforces_cache.get(repo)
    if hit is not None and now - hit[0] < _GITHUB_ENFORCES_TTL_S:
        return hit[1]
    from app.domain.project.protection import github_repo_snapshot

    _, prot = await github_repo_snapshot(repo, token)
    _github_enforces_cache[repo] = (now, prot.enforced)
    return prot.enforced


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
    AcceptStatus.conflict: (
        "上一张验收卡卡在合并冲突上，解决冲突后由人重试采纳，不要再递一张"
    ),
}

#: Refusal when the branch genuinely holds nothing the base does not. This is
#: the empty-PR failure stated as what it is — a fact about the branch RIGHT
#: NOW, checked at 递卡 time, not inferred from "a card was accepted once".
#:
#: The inference was the bug (2026-08-18): a room outlives the work done in it,
#: so it delivers, then keeps working, and the next task's commits sit on the
#: same branch waiting for the next card. Blocking on history froze every room
#: after its first delivery — 一个 task 完成了可以再新开 task became 一个房间只
#: 能交付一次, which is the opposite of what 采纳后不再归档话题 (#536) was for.
_NOTHING_TO_DELIVER = (
    "这条分支相对 main 没有新提交，没有东西可以交付。"
    "这样开出来的 PR 是空的，GitHub 会拒绝，平台会降级成本地合并——"
    "卡看起来采纳了，实际什么都没交付。\n"
    "先把改动提交到工作区再递卡。"
)

_CARD_BLOCKS_NEW_CARD = tuple(_BLOCKED_BY_CARD_MESSAGES)

#: Refusal for a card filed with no commit subject at all. It is long on
#: purpose: the reader is an agent one turn away from re-filing, and an error
#: that only says "缺少 change_subject" costs a whole turn to act on. The
#: example is a real, valid subject — copy-pasteable, not a placeholder.
_MISSING_SUBJECT = (
    "递卡必须带提交标题（subject）。它不是给人看的说明，是这次改动留在 "
    "git 历史里的那一行：递卡开 PR 用它当标题，采纳时整个分支被压成一个"
    "提交，标题还是它。\n"
    "写法：`type(scope): description`，type 取值 "
    f"{', '.join(commit_message.TYPES)}；英文祈使句，"
    f"≤{commit_message.MAX_SUBJECT} 字符，结尾不加句号。\n"
    "例：\n"
    "  subject: fix(accept): open the PR as the requester, not the bot\n"
    "  body: PRs opened with the App token belong to the bot on GitHub, "
    "so the person whose work it is gets no attribution."
)

#: Where an alembic revision lives. Two live cards each ADDING a file under
#: here is the one overlap a machine can judge on its own (#314).
_ALEMBIC_VERSIONS_DIR = "alembic/versions/"

#: 声明了一条本房间没有的活。Almost always a copy-pasted id from another room's
#: 简报; naming the room is what makes that visible instead of "not found".
_NOT_THIS_ROOMS_WORK = (
    "这个房间里没有活 {task_id}。task 只认本房间派出的活的 id"
    "（`cheese_task` 当时返回的那个）。"
)


#: 人工放行时「当时检查是什么状态」对应的那半句话。以前它是写死的「明知检查未
#: 全绿仍合并」，而放行的常见场景之一恰恰是检查**已经全绿**、平台却还没合（比如
#: 在等一项对这次改动根本不会触发的 required 检查）：2026-08-17 的 PR #520 因此
#: 在卡上留下了一条自相矛盾的历史 ——「明知检查未全绿仍合并（合并时检查状态：
#: success（全部 5 项检查通过））」。写错的留痕比没有留痕更糟：事后追责会照着它
#: 去问一个从没发生过的决定。
_FORCE_MERGE_VERDICTS = {
    "failure": "forceMergedVerdictFailure",
    "success": "forceMergedVerdictSuccess",
    "pending": "forceMergedVerdictPending",
    "no_checks": "forceMergedVerdictNoChecks",
}


def _force_merge_verdict(state: str | None) -> str:
    """`None` = 那一刻根本没读到检查状态（凭据坏了不该把人锁在门外，所以照样
    放行）——它和「读到了，是红的」是两回事，卡面不能把前者写成后者。"""
    if state is None:
        return say("forceMergedVerdictUnread")
    if state in _FORCE_MERGE_VERDICTS:
        return say(_FORCE_MERGE_VERDICTS[state])
    return say("forceMergedVerdictOther", state=state)


def _capped(key: str, *, error: str, limit: int = 300, **params: object) -> NoticeText:
    """`key` with `error` trimmed so the whole Chinese line stays within `limit`
    characters — the cap the line had when it was a sliced f-string."""
    line = say(key, error=error, **params)
    if len(line) > limit:
        line = say(key, error=error[: len(error) - (len(line) - limit)], **params)
    return line


def approvals_required_of(project: Project | None) -> int:
    """主分支保护 (spec §4.4): distinct approvals an accept needs. Default 1 —
    the accepter's own accept counts, so unconfigured projects are unchanged.

    The canonical read lives with the rest of the branch-protection policy
    (issue #718); this is that read, importable where review code already is.
    """
    from app.domain.project.protection import branch_protection_of

    return branch_protection_of(project).approvals_required


#: 交文件、交地址的交付要说清动的是清单上哪一项 —— 沿用一项，或者声明一项新的
#: (#1085 结论三)。两个参数而不是一个，是因为「沿用」和「新建」是两个不同的动作：
#: 合成一个参数的话，写错的名字会被当成新建，而那是错得最安静的一种 ——
#: `报告` 和 `结题报告` 都是合法名字，清单于是多出一项看着像重复的东西，并且从此
#: 每一轮的开场都带着它。
#:
#: 合并型的交付走不到这里，见 `_ARTIFACT_ACTION_UNWANTED`。
_ARTIFACT_ACTION_MISSING = say("artifactActionMissing")

_ARTIFACT_ACTION_BOTH = say("artifactActionBoth")

#: 合并交出去的是项目那个仓库本身，而一个项目只有一个仓库 —— 没有什么可判断的，
#: 所以这里不收声明，平台自己认得出是哪一项。
#:
#: 打回而不是默默忽略：一个收下了却不起作用的参数，读起来跟起了作用一模一样，而
#: 传它的那一方正以为自己说清了一件要紧的事。
_ARTIFACT_ACTION_UNWANTED = say("artifactActionUnwanted")


def _one_artifact_action(artifact: str | None, new_artifact: str | None) -> None:
    reuse, claim = (artifact or "").strip(), (new_artifact or "").strip()
    if reuse and claim:
        raise ValidationError(_ARTIFACT_ACTION_BOTH)
    if not reuse and not claim:
        raise ValidationError(_ARTIFACT_ACTION_MISSING)


def _no_artifact_action(
    artifact: str | None, new_artifact: str | None, about: str | None
) -> None:
    """合并那条路上，这三个参数一个都不收。

    `about` 一起挡掉，理由和另外两个一样：那一句话是给下一次交付判断「我做出来的
    是不是它的新一版」用的，而合并那条路上没有这个判断 —— 交出去的是这个项目的仓
    库，它是哪一项不需要任何人读一句话才知道。收下一个不起作用的参数，读起来跟起
    了作用一模一样。
    """
    if (
        (artifact or "").strip()
        or (new_artifact or "").strip()
        or (about or "").strip()
    ):
        raise ValidationError(_ARTIFACT_ACTION_UNWANTED)


#: 这一版交出去的是什么 (#1085 结论五)。一份文件、一个地址，或者两个都不给 ——
#: 那就是交出去这次合并本身（代码仓库这类项目交的就是主干往前走一步）。
_DELIVERABLE_BOTH = say("deliverableBoth")

#: 单份交付物的上限。成品不进库，所以这个数管的是平台那块盘，而不是用户的仓库。
_DELIVERABLE_MAX_BYTES = 80 * 1024 * 1024


def _one_deliverable(deliver: str | None, deliver_url: str | None) -> None:
    path, url = (deliver or "").strip(), (deliver_url or "").strip()
    if path and url:
        raise ValidationError(_DELIVERABLE_BOTH)
    if url and not url.startswith(("http://", "https://")):
        raise ValidationError(say("deliverUrlInvalid"))


async def _read_deliverable(
    session: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID, path: str
) -> tuple[str, bytes]:
    """把交付物从这一轮的工作目录里读出来，连同它的文件名。

    读的是任务那棵树 —— 交付物是这条活做出来的，主干上还没有它。
    """
    from app.domain.repository.forge_files import ProjectFiles

    data, _ = await ProjectFiles(session, project_id, task_id).raw(path, "live")
    if len(data) > pkg._DELIVERABLE_MAX_BYTES:
        raise ValidationError(
            say(
                "deliverableTooLarge",
                path=path,
                size=len(data) // 1024 // 1024,
                max=pkg._DELIVERABLE_MAX_BYTES // 1024 // 1024,
            )
        )
    return PurePosixPath(path).name, data
