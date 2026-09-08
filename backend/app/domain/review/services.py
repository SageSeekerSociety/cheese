"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, NoReturn

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.db import async_session_factory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_CONFLICT,
    EVENT_ACCEPT_DISMISSED,
    EVENT_ACCEPT_DONE,
    EVENT_ACCEPT_READY,
    EVENT_ACCEPT_STOPPED,
    EVENT_CARD_REDESCRIBED,
    EVENT_CARD_VOIDED,
    EVENT_CI_FAILED,
    EVENT_FORCE_MERGED,
    EVENT_MERGE_REFUSED,
    EVENT_MERGE_WITHHELD,
    EVENT_MIGRATION_COLLISION,
    EVENT_PR_CLOSED,
    EVENT_PR_CONFLICT,
    EVENT_PR_REVIEW,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_CHEESE,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)
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
    merge_state,
    notes,
    pr_publish,
    pr_signals,
    pr_text,
)
from app.domain.review import forge as forge_mod
from app.domain.review.merge_state import MergeVerdict, Who, whose_move
from app.domain.review.models import AcceptCard, AcceptStatus, GateOutcome
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.schemas import AcceptCardOut
from app.domain.room_task.models import Task, TreeStatus
from app.domain.room_task.place import PlaceResolver
from app.domain.room_task.services import TaskService, WorkTreeService
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.webhook import service as webhook_service
from app.domain.workspace import identity

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.project.protection import BranchProtection
    from app.domain.review.github_pr import PullRequestStatus

logger = logging.getLogger("cheesex.review")

# 卡上那句话的措辞。状态码在 review/notes.py，这里只有文案——两者分开之后，改一
# 句话不再改掉任何一处判断，所以这些常量存在的理由只剩「同一句话写在两处」。
#
# 不带 emoji：卡自己按 `note_level` 画轻重（前端 TopicAcceptCard），开头再放一个
# 表情就是同一件事说两遍——一遍是结构，一遍是屏幕阅读器会念出来的一个字符。
#: 本地话题分支与 PR 分支分叉 (采纳即合并 #296, 2026-08-12). `push_topic_branch_
#: for_github_pr` 是**非强制**推送，一旦本地分支被 rebase / reset 挪到了 PR
#: 分支的祖先或旁支上，plain push 就会
#: 被 GitHub 以 non-fast-forward 拒绝——而轮询每 60 秒无脑重试这条注定失败的推送，
#: 就是 card 946bf5de 每 ~70 秒失败一次的死循环。检测到不能快进就**不推**，留一条
#: note 交给芝士在工作区把 PR 分支合并进来，而不是替它强推覆盖 PR 上的提交。
_REPUSH_DIVERGED_PREFIX = "本地分支与 PR 分支已分叉"
#: 采纳现场补开 App PR 失败（存量无 PR 卡，#296 stage 1 的回归修复）。开不出 PR
#: 时采纳停下、原因亮在卡上——绑定 GitHub 的项目绝不静默本地合并直推 main
#: （all commits go through PR）。卡保持 pending，人处理后可直接重试采纳。
_ACCEPT_PR_OPEN_FAILED_PREFIX = "采纳未完成：开不出 PR"
#: 卡上有 PR 但此刻推进不了（GitHub 不可达 / PR 被关闭未合并 / …）。绑定 GitHub
#: 的项目采纳只通过合并 PR 完成 (#363)——这类失败停下亮出来，永不落 local merge。
_ACCEPT_PR_STALLED_PREFIX = "采纳未完成：PR 未能合并"
#: 卡带着交付主张（change_subject 非空），树的分支上却没有任何提交。2026-09-07
#: 卡 40be3e1a：改动被推到了别的分支，树分支从未存在，`open_pr_for_card` 把它
#: 当成讨论话题返回 None，采纳落进本地合并 no-op——卡标成 accepted，人以为交付
#: 完成，而改动没有合进任何地方。主张交付却无从交付的采纳必须停下；只有真正的
#: 存量讨论卡（change_subject 为 NULL，递于 subject 必填之前）才允许 no-op 采纳。
_ACCEPT_NO_BRANCH_PREFIX = "采纳未完成：这棵树的分支上没有任何提交"

#: pending_gate 孤儿卡 (2026-08-11). 判死的卡和检查真红了的卡都落在 `gate_failed`
#: 上，但对芝士意味着完全相反的下一步——「没跑完」= 原样重递，「没通过」= 去修
#: 代码。gate_sweep.py 把这句话同时写进 note 和 gate_output，也发给芝士。
GATE_ABANDONED_PREFIX = "检查没跑完"
#: 人工作废 (2026-08-11)。作废复用 `revoked` 终态（archive.py 收敛非终态卡时也
#: 用它），所以「谁作废的、为什么」只能写在 note 里。
VOIDED_PREFIX = "卡片已作废"
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


def _nudge_note_prefix(stage: str) -> str:
    return f"{stage} 检查未通过："


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
    where = f"PR #{pr_number} " if pr_number is not None else ""
    return f"{where}有新提交，你看到的版本已过时 —— 请重新看过再{action}"


def _never_shown_message(pr_number: int | None, action: str) -> str:
    """「卡面还没显示过任何版本」—— PR 上的卡，`pr_head_sha` 还是空的。

    这不是「没有版本可以过时」，而是**还不知道要合什么**：卡刚递上来、轮询器
    还没镜像过 head，屏幕上那张卡从来没写出过一个 sha，所以点下去只能拿现读
    GitHub 的 head 去合 —— 一个从未在任何界面上出现过的 commit。卡先刷新到当前
    head（`_refresh_stale_card`），人重新看一眼，那一版才算被看过。"""
    where = f"PR #{pr_number} " if pr_number is not None else ""
    return (
        f"{where}的卡还没显示过任何版本（刚递上来，还没读到 PR 的 head），"
        f"卡已刷新 —— 请重新看过再{action}"
    )


#: How much of the failure detail rides in the nudge message. The detail is
#: already bounded per job upstream (`github_pr._failure_detail`); this is the
#: backstop that keeps a pathological payload from flooding the topic.
_NUDGE_TAIL_LIMIT = 4000


def _ci_log_howto(repo_full_name: str) -> str:
    """The "where do I read the rest" paragraph of a CI-failure nudge.

    The excerpt above it is deliberately short, so the message has to say how
    to get the whole thing — and that path was undocumented everywhere 芝士
    can read (not in `.claude/`, not in `CLAUDE.md`): the token is a
    `cheese gh-token` away, but nothing told it so, and nothing told it the
    repo's name either, which `gh api repos/:owner/:repo/...` needs. Both are
    in hand right here, at the one moment they're wanted.
    """
    repo = repo_full_name or "<owner>/<repo>"
    return (
        "上面是失败 job 的名字、Actions 页面链接，以及日志里错误行附近的片段。"
        "要看完整日志，在本话题的工作区里跑：\n"
        "```bash\n"
        "export GH_TOKEN=$(cheese gh-token)   # 本仓库的 GitHub token，约 1 小时过期\n"
        f"gh api repos/{repo}/actions/jobs/<job_id>/logs\n"
        "```\n"
        "（`<job_id>` 就是上面 Actions 链接里 `/job/` 后面那串数字；"
        f"要重新列出这次提交的所有检查：`gh api "
        f"repos/{repo}/commits/<head_sha>/check-runs`。）\n"
    )


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

#: 声明了一条本房间没有的活。Almost always a copy-pasted id from another room's
#: 简报; naming the room is what makes that visible instead of "not found".
_NOT_THIS_ROOMS_WORK = (
    "这个房间里没有活 {task_id}。--task 只认本房间派出的活的 id"
    "（`cheese split` 当时打印的那个）。"
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
    the accepter's own accept counts, so unconfigured projects are unchanged.

    The canonical read lives with the rest of the branch-protection policy
    (issue #718); this is that read, importable where review code already is.
    """
    from app.domain.project.protection import branch_protection_of

    return branch_protection_of(project).approvals_required


class AcceptService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = AcceptCardRepository(session)
        self._topics = TopicRepository(session)
        self._projects = ProjectRepository(session)
        self._machines = MachineService(session)

    async def _topic_or_404(self, topic_id: uuid.UUID) -> Topic:
        """The ROOM a place id names — a card is read in a room either way.

        `topic_id` here is a place id and is usually a thread's: a card is what a
        piece of work ends in. Everything this service does with the answer —
        rendering, notifying, the branch it pushes — belongs to the room, so the
        room is what it returns; which thread the card is FOR is on the card.
        """
        place = await PlaceResolver(self._session).resolve(topic_id)
        if place is None:
            raise NotFoundError("Topic not found")
        return place.room

    async def _stamp_delivery(
        self, card: AcceptCard, topic: Topic, *, by: str | None, at: datetime | None
    ) -> None:
        """Mark what was delivered — the THREAD when the card is a thread's.

        交付完成 ≠ 这个地方结束 (#442 decision 1): this is the delivery marker and
        nothing else; `status` is untouched and putting a place away stays a
        person's decision.

        Which row carries it matters: a room accumulates work forever, so
        stamping the room would say "this room was delivered" every time any one
        piece of work in it was, and the next reader cannot tell which. Passing
        `by=None` clears it (撤回采纳).
        """
        target: Topic | Task = topic
        if card.task_id is not None:
            thread = await TaskService(self._session).get(card.task_id)
            if thread is not None:
                target = thread
        target.accepted_by = by
        target.accepted_at = at

    async def _card_or_404(self, card_id: uuid.UUID) -> AcceptCard:
        card = await self._repo.get(card_id)
        if card is None:
            raise NotFoundError("Accept card not found")
        return card

    async def _release_billed_compute(self, topic: Topic) -> None:
        """Release the delivered topic's BILLED compute — its Cloud VM — and
        nothing else.

        This used to tear down the working surface too — a device topic's
        screen plus its remote work dir. It doesn't any more, because 交付完成 no
        longer means 话题结束 (#442 decision 1: 一个话题往往是连续的): the room
        keeps working after the merge, and killing its screen mid-life is not
        free — a rebuilt one loses everything installed alongside it, so the next
        turn pays for a teardown nobody asked for. That surface has its own idle
        reaper (``scheduler.reap_idle_device_screens``), which is where
        reclaiming it belongs: the question "is anyone still using this" is about
        activity, not about whether a branch landed.

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

    async def _reviewer_or_project_default(
        self,
        project: Project | None,
        reviewer_handle: str | None,
        *,
        from_work: list[Task] | None = None,
    ) -> str:
        """谁验收：显式指定 > 这批活派出去时定的人 > 项目默认验收人 (#718 设置表).

        显式指定优先 —— the person filing knows something neither the work nor
        the setting can: which change THIS is, and who understands that part of
        the code. A default that overrode them would make the setting a ceiling
        instead of a floor.

        Then the work's own reviewer, because that is who this work was HANDED
        TO when it was dispatched (`Task.reviewer_handle`, resolved from the
        same setting at that moment). Reading the project setting again instead
        would silently re-route work dispatched under an older policy.

        Work that disagrees is refused rather than resolved. Two threads handed
        to two different people, delivered in one batch, is a real question
        about who gets to say this may land, and any answer this code invented —
        the first, the newest, the most common — would route somebody's review
        to somebody else and look correct doing it.

        Nothing anywhere is an error rather than a guess, for the same reason:
        routing to the project owner, the room's owner, or whoever accepted last
        would each hand a real delivery to someone who never agreed to look at
        it, and the card would sit there looking correctly routed.
        """
        from app.domain.project.protection import branch_protection_of

        explicit = (reviewer_handle or "").strip()
        if explicit:
            return explicit
        handed_to = sorted(
            {t.reviewer_handle for t in (from_work or []) if t.reviewer_handle}
        )
        if len(handed_to) > 1:
            raise ValidationError(
                "这批活派出去时定的验收人不是同一个人（"
                + "、".join(handed_to)
                + "），平台不替你选。递卡时点名一个。"
            )
        if handed_to:
            return handed_to[0]
        default = branch_protection_of(project).default_reviewer
        if default:
            return default
        raise ValidationError(
            "没说验收卡递给谁，项目也没有设默认验收人。"
            "点名一个人（`cheese members` 查准确 handle），"
            "或者在项目设置的「分支保护 → 任务默认 reviewer」里填一个。"
        )

    async def create_card(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handle: str | None = None,
        routing_reason: str = "",
        change_subject: str | None = None,
        change_body: str | None = None,
        task_ids: list[uuid.UUID] | None = None,
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
        # Whose work this is, as the room says (#189). Checked here — before a
        # tree is opened or a row is written — because a bad id is the filer's
        # to fix in the same breath, and because what it ends up as is a line in
        # permanent history.
        delivered = await self._declared_work(topic, task_ids or [])
        # One card at a time, not a broadcast (spec §4.4): re-route / wait
        # instead of stacking a new one.
        #
        # 2026-08-10: the guard used to cover only pending/pending_gate, so a
        # card stuck on a merge `conflict` did not block a second card. The
        # frontend only ever renders the NEWEST card, so the older one — and
        # the PR it was driving — vanished from the UI.
        # Every non-terminal status blocks now; `gate_failed` and `gate_blocked`
        # deliberately do not (a red gate voids the card, and re-递卡 after fixing
        # IS the flow — same for a gate that never ran: 芝士 fixes the check
        # environment and re-files. Adding either here locks 芝士 out for good).
        # 一棵树一个 PR: the thing that may not happen twice at once is two PRs
        # on ONE branch. Asking the room instead would refuse a second batch its
        # own PR, which is precisely what a second tree exists to allow.
        trees = WorkTreeService(self._session)
        tree = await trees.ensure_open(project_id=topic.project_id, room_id=topic.id)
        if trees.started_a_batch:
            # 开一批活落在两个地方，而它们不能一起回滚：`work_trees` 的行，和
            # `ws.bind_tree` 在磁盘上写的「这个房间写哪棵树」。这个方法底下还有
            # 好几道会 raise 的闸（空树守卫首当其冲），raise 走的是请求事务的
            # 回滚 —— 行没了，映射还在，房间从此指着一棵不存在的树。
            #
            # 而那道空树守卫恰恰要**点名一条分支**让人把提交推上去。它报的名字
            # 来自刚开的这棵树，422 又把这棵树烧掉，下次递卡开的是另一棵、报的
            # 是另一个名字 —— 照着推永远白推（房间 2026-09-08 实测）。
            #
            # 所以这一行先落地，跟这次递卡成不成没有关系。它本来就与递卡无关：
            # 房间开着一棵空树是每两批活之间的常态，而磁盘已经这么认为了。
            await self._session.commit()
        # Plus this place's tree-less cards. A card filed before trees existed
        # kept `tree_id IS NULL` wherever the backfill had no honest value to
        # give it, so asking the tree alone makes a live card from that era
        # invisible — and a second card would open a second PR on the same
        # branch. That is exactly the failure the guard was widened for on
        # 2026-08-10, arriving by a new route.
        existing = [
            *await self._repo.list_for_tree(tree.id),
            *await self._repo.list_treeless_for_topic(topic_id),
        ]
        blocking = next(
            (c for c in existing if c.status in _CARD_BLOCKS_NEW_CARD), None
        )
        if blocking is not None:
            raise ValidationError(_BLOCKED_BY_CARD_MESSAGES[blocking.status])
        # 有活才有卡：这张卡带的 change_subject 是交付主张，绑定 GitHub 的项目
        # 会拿它去开 PR，而 PR 要有分支可骑。树的分支不存在时 PR 开不出来，本地
        # 合并也是 no-op——2026-09-07 卡 40be3e1a 就这样被标成 accepted，而改动
        # 其实被推到了别的分支，没有合进任何地方。在递卡这一刻就拒绝，并点名该
        # 推哪条分支。Best-effort：绑定状态或分支探测出错不拦递卡——采纳路径上
        # 的 `_stop_accept_no_branch` 才是硬闸门（那边判定不了会 fail closed）。
        from app.domain.workspace import service as ws

        try:
            tree_branch_missing = await self._github_bound(
                topic.project_id
            ) and not await asyncio.to_thread(
                ws.topic_branch_exists, topic.project_id, topic_id
            )
        except Exception:  # noqa: BLE001 — 判定不了不拦递卡
            tree_branch_missing = False
        if tree_branch_missing:
            branch = await asyncio.to_thread(
                lambda: ws.branch_for_tree(ws.tree_for_place(topic_id))
            )
            raise ValidationError(
                f"这棵树的分支（{branch}）上没有任何提交，没有东西可以交付。"
                f"改动可能被提交到了别的分支——把提交推上 {branch} 后再递卡。"
            )
        # Nothing to deliver is a fact about the branch, so ask the branch. It
        # used to be inferred from "this topic already had a card accepted",
        # which is true only until somebody commits again — and rooms do, that
        # is what a room is for. Only for a topic that HAS delivered before:
        # a first card on a branch with no commits is a different failure
        # (nothing was ever written) and the PR path already reports it with
        # the detail this check cannot see.
        if any(c.status == AcceptStatus.accepted for c in existing):
            has_new = await asyncio.to_thread(
                ws.has_undelivered_commits, topic.project_id, topic_id
            )
            if not has_new:
                raise ValidationError(_NOTHING_TO_DELIVER)
        # 采纳即合并 (docs/accept-is-merge.md #296, stage 1): the card is always
        # born `pending`. The old machine gate (`check_command` → born
        # `pending_gate`, platform runs the check, only green promotes to
        # pending) is retired: a card is the platform's view of a PR, and real
        # CI on that PR — not a private platform check — is what decides whether
        # a change is good. `pending_gate`/`gate_failed`/`gate_blocked` are no
        # longer entered; existing rows keep their historical values and their
        # exits (review/gate_sweep.py, AcceptService.void) stay in place.
        # 递卡沿用派活时定的验收人 (#718 设置表)。The work this card says it
        # carries is the right set to ask — a batch's tree also holds threads
        # whose code is NOT in this delivery, and routing by those would hand
        # the card to somebody whose work is not in it. An undeclared delivery
        # falls back to everyone on the batch, which is the best available
        # answer when the card names nothing.
        tasks = TaskService(self._session)
        handed_to = (
            await tasks.list_by_ids(delivered)
            if delivered
            else await WorkTreeService(self._session).tasks_on(tree.id)
        )
        reviewer_handle = await self._reviewer_or_project_default(
            await self._projects.get(topic.project_id),
            reviewer_handle,
            from_work=handed_to,
        )
        card = await self._repo.add(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            tree_id=tree.id,
            status=AcceptStatus.pending,
            change_subject=change_subject,
            change_body=(change_body or None),
            delivered_task_ids=delivered,
        )
        # 这批活的 PR 早就开着了 (#718 拍板①)：draft PR 在这棵树第一次提交时就
        # 开出来了，卡认领它，而不是再开一个。GitHub 那边也认领得了（一条 head
        # 上只能有一个开着的 PR，`open_pr` 撞上 422 会去找它），但那要一次失败的
        # POST 加一次 GET 才知道号码；树上记着，卡当场就有 PR 可显示。
        if tree.pr_number is not None:
            card.pr_number = tree.pr_number
            card.pr_url = tree.pr_url
        await self._warn_about_a_second_pending_migration(topic)
        return card

    async def _declared_work(
        self, topic: Topic, task_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """The work this delivery says it carries, validated (#189).

        The room declares it, because the room is the only party that knows.
        The platform cannot derive it: a task's `tree_id` records which batch
        was open when `cheese split` ran, not where its code eventually landed,
        so the tree's membership names whoever happened to be sitting on it —
        and the commits cannot be asked either, since inside the sandbox they
        are all authored by the requester and co-authored by the model.

        Exactly ONE thing is checkable, and it is checked rather than trusted,
        because a wrong `Cheese-Task:` is permanent and reads exactly like a
        right one: **the work belongs to THIS room**. A pasted id from another
        room's brief would otherwise credit that room's worker on this change.

        **为什么没有防重复。** 这里曾经还拦一条：「这条活被某张已采纳的卡声明过
        了」。它跟本仓写明的语义直接冲突 —— `TaskStatus` 的 docstring 说 a task
        can be delivered and still open (someone keeps pushing to the same
        branch)。连续交付是既有语义：一条活参与上一批、之后继续写代码、真实地写
        进下一批，是长命房间的常态，那条校验会把这条**真实**的声明拒掉。同一条
        活出现在两次交付的历史里不是错误，它确实写了两批的代码。

        剩下唯一算得上「同一批被署了两次」的形状，也不需要一条校验来防：
        - 一次请求里报两遍 —— 下面的 `dict.fromkeys` 去重，它跟报一遍说的是同
          一件事；
        - 一棵树上两张卡各报一次 —— 一张卡采纳后树就 merged，`ensure_open` 给下
          一批开的是新树，所以「同一棵树的第二张卡」只在前一张 **rejected /
          voided** 之后才存在（`_CARD_BLOCKS_NEW_CARD` 只拦非终态）。那两种前一
          张卡都没有交付过任何东西，重递并重报正是补救的路，拦它才是错的。

        「重复署名却没有新贡献」是另一回事，而平台判不了它：能被机器判定的只有
        分支上有没有新提交，那道闸已经在 `_NOTHING_TO_DELIVER`。再发明一条近似
        规则，只会重新开始拒真放假。

        What is deliberately NOT checked is whether the work "looks finished" —
        a closed thread can have delivered nothing and an open one can have
        written the whole change, so any such rule would reject true
        declarations while still admitting false ones.

        Empty in, empty out, and no inference: an undeclared delivery lands with
        no `Cheese-Task:` line at all. One card naming the same work twice is
        deduped rather than refused — it says nothing different from naming it
        once.
        """
        wanted = list(dict.fromkeys(task_ids))
        if not wanted:
            return []
        in_room = await TaskService(self._session).list_in_room(topic.id)
        mine = {t.id for t in in_room}
        for task_id in wanted:
            if task_id not in mine:
                raise ValidationError(_NOT_THIS_ROOMS_WORK.format(task_id=task_id))
        return wanted

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
            "另一张未决的验收卡也新建了迁移",
            meta=notice(
                EVENT_MIGRATION_COLLISION,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=(
                    "另一张卡在：" + "、".join(rooms) + "。\n"
                    "两张卡各带一个 alembic revision，合到一起会让迁移链分叉，"
                    "而且往往说明同一件事被做了两遍。\n"
                    "平台不拦这次采纳。请先比对两张卡的改动：如果确实是两件事，"
                    "照常采纳，先合的那张合完后另一张需要 rebase。"
                ),
                detail_label="为什么提醒",
            ),
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
        """等着被镜像/推进的验收卡 id（pending 且骑着 PR）—— 调度器每轮的输入。

        只回 id 不回对象：调度器一张卡一个事务，跨事务复用 ORM 对象拿到的是过期状态。
        「哪张卡算在等」是本领域的知识，所以判断留在这里，而不是让调度器自己去查
        ``AcceptCardRepository``。

        孤儿卡修复 (2026-08-10) 的那条判据也在这里面：已归档话题上的卡不算——
        跟进它们等于拿 GitHub 凭据去动没人跟的活儿。
        """
        cards = await self._repo.list_awaiting_merge_on_active_topics()
        return [c.id for c in cards]

    async def anybody_still_waiting(self, place_ids: list[uuid.UUID]) -> bool:
        """这些地点里，还有没有一张卡等着人决议 —— 收起/归档前必须问的那一句。

        归档会把非终态的卡当场收敛掉（`review/archive.py`），所以任何**平台自己
        发起**的归档（结论卡默认采信就是）都得先问这一句，否则会把一张验收人还
        没看见的卡作废掉。判据（哪些状态算"还等着"）留在本领域，调用方不该自己
        去数状态——这正是 `close_cards_for_archived_topic` 收敛的那一张表。

        一组而不是一个：归档一个房间会把它里面的活一起收起，那些活的卡同样会
        被收掉，所以它们同样构成「先别动手」的理由。
        """
        return bool(
            await self._repo.list_live_for_places(
                place_ids, statuses=archive.OPEN_CARD_STATUSES
            )
        )

    async def latest_decision_at(self, place_ids: list[uuid.UUID]) -> datetime | None:
        """这些地点上最后一张卡是什么时候有结果的 —— None = 从来没有过卡。

        给"卡决议之后留一个重新递卡的窗口"用：驳回的意思是回去改了再来，而归档
        话题递不出新卡，所以窗口从这一刻起算。
        """
        return await self._repo.latest_decision_at(place_ids)

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
        # 「这条 note 有多严重」是它的状态码算出来的（domain/review/notes.py），
        # 随卡下发。浏览器过去自己按 emoji 开头猜，而那份硬编码列表漏掉了后来加
        # 的 `🌿` 和 `🚪`——两条都是「停住了」，却和「还在等」画成同一个颜色。
        level = notes.note_level(card.note_code, card.note)
        data["note_level"] = level.value if level else None
        data["approvals"] = await self._repo.list_approver_handles(card.id)
        topic = await self._topics.get(card.topic_id)
        project = (
            await self._projects.get(topic.project_id) if topic is not None else None
        )
        data["approvals_required"] = approvals_required_of(project)
        # 卡上的状态＝合并态 (#718)。骑 PR 的卡读轮询器的镜像（还没镜像过 =
        # unknown，下一拍收敛）；未绑 GitHub 的项目 (#363 拍板) 分支保护默认
        # 关、没有检查可读，卡直接是 CLEAN —— 唯一会推翻它的信号是「上次合并
        # 撞了冲突」（conflict 状态）→ dirty。who 恒为 human：那里的采纳本来
        # 就纯粹是人的判断。
        if card.pr_number is not None:
            mirror = card.merge_state if isinstance(card.merge_state, dict) else None
            data["merge_state"] = mirror or {
                "state": "unknown",
                "who": "platform",
                "reasons": [
                    {
                        "kind": "no_signal",
                        "checks": [],
                        "detail": "平台还没看过这个 PR 的合并态",
                    }
                ],
                "head_sha": card.pr_head_sha,
                "checked_at": None,
                "since": None,
            }
        else:
            local = merge_state.local_merge_state(
                conflicts_with_trunk=(card.status == AcceptStatus.conflict)
            )
            data["merge_state"] = {
                "state": local.state,
                "who": "human",
                "reasons": [
                    {"kind": r.kind, "checks": list(r.checks), "detail": r.detail}
                    for r in local.reasons
                ],
                "head_sha": None,
                "checked_at": None,
                "since": None,
            }
        # 绿了自动合 (#718)：开了 auto_merge_allowed 的项目，验收人可以在
        # BLOCKED / BEHIND 时布防。
        from app.domain.project.protection import branch_protection_of

        data["auto_merge"] = {
            "allowed": (
                branch_protection_of(project).auto_merge_allowed
                and card.pr_number is not None
            ),
            "armed_by": card.auto_merge_armed_by,
            "armed_at": (
                card.auto_merge_armed_at.isoformat()
                if card.auto_merge_armed_at
                else None
            ),
        }
        # 快检说了什么。Gates nothing — the PR's real CI decides (#296) — but a
        # red one has to be in front of the person about to accept. A check
        # whose result goes nowhere is a check nobody runs.
        tree = (
            await WorkTreeService(self._session).get(card.tree_id)
            if card.tree_id is not None
            else None
        )
        data["quick_check"] = (
            None
            if tree is None or tree.last_check_at is None
            else {
                "ok": tree.last_check_ok,
                "at": tree.last_check_at.isoformat(),
                "detail": tree.last_check_detail,
            }
        )
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
        self,
        *,
        card_id: uuid.UUID,
        reviewer_handle: str | None = None,
        reason: str = "",
    ) -> AcceptCard:
        """改验收人 (spec §4.4): anyone can re-route a pending accept card to a
        different reviewer — or, naming nobody, back to the project's default
        one, which is the same ladder 递卡 climbs."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending:
            raise ValidationError("只有待处理的验收卡能改验收人")
        topic = await self._topic_or_404(card.topic_id)
        card.reviewer_handle = await self._reviewer_or_project_default(
            await self._projects.get(topic.project_id), reviewer_handle
        )
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

    async def arm_auto_merge(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        enabled: bool,
        head_sha: str | None = None,
    ) -> AcceptCard:
        """绿了自动合 (#718)，GitHub auto-merge 的对应物。

        布防不是决议：卡留在 pending，规则满足时轮询器以布防人的名义合并（布防
        人的那票算进批准数）。新提交作废采纳（dismiss_stale）同样解除布防 ——
        机器合的永远是布防人看过的那份，或者不合。

        谁能布防：这张卡的验收人（跟采纳同一个人 —— 布防就是「提前采纳」）。
        项目要先开 `auto_merge_allowed`。解除给同一个人加布防人自己。

        布防等于提前采纳，所以它跟采纳一样要声明「我看的是哪一版」
        （`_seen_head_or_refresh`）：屏幕上那版已经过时、或者卡面还没显示过任何
        版本的话，布防就是替一段没人看过的代码预先按下同意。
        **这跟合并态是不是 blocked 无关**——这个开关本来就只在
        BLOCKED / BEHIND 出现，规则没满足正是布防的前提，拒的理由只有「旧 SHA」
        一个。解除布防不需要看过任何版本：撤销自己的同意什么都不会合并。
        """
        from app.domain.project.protection import branch_protection_of

        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending or card.pr_number is None:
            raise ValidationError("只有骑着 PR、还在等采纳的验收卡能设置自动合并")
        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "布防自动合并")
        if enabled and not branch_protection_of(project).auto_merge_allowed:
            raise ValidationError("这个项目没有开启「绿了自动合」（项目设置）")
        allowed = {card.reviewer_handle}
        if card.auto_merge_armed_by:
            allowed.add(card.auto_merge_armed_by)
        if decided_by not in allowed:
            raise ForbiddenError("只有这张卡的验收人能设置自动合并")
        if enabled:
            await self._seen_head_or_refresh(card, topic, head_sha, "布防")
            card.auto_merge_armed_by = decided_by
            card.auto_merge_armed_at = datetime.now(UTC)
        else:
            card.auto_merge_armed_by = None
            card.auto_merge_armed_at = None
        await self._session.flush()
        await self._session.refresh(card)
        return card

    def _notify_merge_result(
        self, topic: Topic, content: str, *, meta: dict | None = None
    ) -> None:
        """merge 后结果回房间: post the accept's merge outcome into the topic
        timeline via the webhook primitive's internal function (卡1) — no HTTP
        hop, no token check, this call is trusted by construction. Uses its
        own session (async_session_factory), independent of self._session, so
        the notice lands even when the accept itself is about to be rolled
        back by a raised ValidationError.

        `content` is the one line the room shows; everything else — why, what
        to do about it, the service's own words — goes in `meta`'s detail and
        is opened only by whoever wants it (platform_notices.notice).

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
                meta=meta,
            ),
            name=f"accept notice topic={topic.id}",
        )

    @staticmethod
    def _seen_head(card: AcceptCard, head_sha: str | None, action: str) -> str | None:
        """合的是**人看到的**那个 commit：核对请求声明的 head，并把它交回去用。

        `head_sha` 是前端渲染这张卡时卡面上的 head（`merge_state.head_sha`）。
        它必须仍然是卡当前的 `pr_head_sha`——不一致意味着轮询器在渲染与点击之间
        把卡刷到了新 commit，而屏幕上那份还是旧的：点下去合的会是一段**没有人
        看过**的代码 (`advance_pr_card` 每 60s 跑一次，这个窗口天天都在)。
        `dismiss_stale` 保护不了它，那条只清批准票，而采纳本身就是一票。

        None 与 None 相等只在**没有 GitHub PR 的那条 lane 上**成立：卡面显示的
        就是「没有 sha」，合的是 diff 视图展示的那条分支本身，没有哪一版可以过
        时。骑着 PR 的卡不是这样——那种情况下「卡上没有 sha」意味着还不知道要合
        哪个 commit，`_seen_head_or_refresh` 在进这里之前就把它拦下了。反过来，
        卡上有 head 而请求什么都不带（老客户端）就是不相等，照样拒——不带 sha
        不是绕过这道闸的方式。

        返回值是**请求带的**那个 sha，调用方拿它去调合并 API：GitHub 的 sha
        参数会在点击瞬间再拦一次漂移（409）。
        """
        seen = (head_sha or "").strip() or None
        if seen != (card.pr_head_sha or None):
            raise ValidationError(_stale_view_message(card.pr_number, action))
        return seen

    async def _seen_head_or_refresh(
        self, card: AcceptCard, topic: Topic, head_sha: str | None, action: str
    ) -> str | None:
        """`_seen_head`，外加「PR 上的卡必须先有过一个展示出来的 head」这道闸。

        骑着 PR 的卡在 `pr_head_sha` 还是空的时候（刚递上来、轮询器 60s 才跑一
        次），卡面从未写出过任何 sha。此时放行等于让下游拿**现读 GitHub** 的
        head 去合，而那个 commit 从来没有在任何界面上显示过——采纳、人工放行、
        自动合布防三个入口都会走到那里，是同一个洞。

        所以这不是「没有版本可以过时」，是「还不知道要合什么」：把当前 head 镜像
        到卡上，让人重新看一眼，看过的那一版才谈得上被采纳。没有 PR 的本地 lane
        不进这道闸——那条路合的就是 diff 视图展示的分支本身。
        """
        if card.pr_number is not None and not (card.pr_head_sha or "").strip():
            await self._refresh_never_shown_card(card, topic, action)
        return self._seen_head(card, head_sha, action)

    async def _refresh_never_shown_card(
        self, card: AcceptCard, topic: Topic, action: str
    ) -> NoReturn:
        """把 PR 当前的 head 镜像到一张从没显示过 sha 的卡上，然后要求重看。

        读 head 是尽力而为：读不到就刷新一张没有 head 的卡（轮询器下一跳会补
        上），但**绝不**因此放行——放行的前提是人看过某一版，读不到 head 恰恰
        说明没有任何一版可看。"""
        from app.domain.review import github_pr

        number = card.pr_number
        assert number is not None  # PR lane only; the caller checked
        live = ""
        creds, why = await self._app_credentials(topic)
        if creds is None:
            logger.warning("card %s: no credentials to read PR head (%s)", card.id, why)
        else:
            try:
                owner, repo = await self._pr_repo_of(card, topic)
                live = await github_pr.default_client().pull_request_head_sha(
                    owner=owner, repo=repo, number=number, token=creds.read
                )
            except Exception as exc:  # noqa: BLE001 — refuse anyway; refresh with what we have
                logger.warning("card %s: first-look head read failed: %s", card.id, exc)
        await self._refresh_stale_card(
            card,
            topic,
            live_head=live,
            action=action,
            headline=(
                f"PR #{number} 的 head 还没镜像到卡上，卡面没显示过任何版本；"
                "已刷新到当前 head"
            ),
        )
        raise ValidationError(_never_shown_message(number, action))

    async def accept(
        self, *, card_id: uuid.UUID, decided_by: str, head_sha: str | None = None
    ) -> AcceptCard:
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
        # 合的是人看到的那个 commit：屏幕上那一版还在，才谈得上采纳它。
        seen_head = await self._seen_head_or_refresh(card, topic, head_sha, "采纳")

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
        # wherever that main lives. WHERE is the forge (app.domain.review.forge):
        #
        #   - the App forge (bound project): a PR is the only way in. A card
        #     without one — a fire-and-forget publish that failed or is still
        #     in flight — gets its PR opened right here, and ANY failure on the
        #     PR path stops the accept visibly rather than falling through to
        #     the local merge (#362/#363). Opening it does NOT merge it this
        #     click: the card never showed a head, so nothing on screen names
        #     the commit that would land. The PR stays (it is the useful half),
        #     its head goes onto the card, and the next click has something to
        #     match. Accepting MERGES, here and now (#718) for every card that
        #     already rode a PR: the merge-state rules said the button may
        #     light, and the merge API is called with the head the human saw.
        #     The one PR-less case that legitimately proceeds is a legacy card
        #     with no delivery claim (change_subject IS NULL, filed before
        #     subjects were required) on a branchless topic, where the local
        #     merge no-ops and bypasses nothing. A card that DOES claim a
        #     change but has no tree branch to carry it stops instead
        #     (2026-09-07, 卡 40be3e1a: the work sat on another branch and
        #     "accepted" merged nothing at all).
        #   - the platform forge (unbound): the local merge IS this project's
        #     accept (#363), and `forge.note` says so on the card so it can
        #     never read as a bound project that skipped its PR.
        forge = await self._resolve_forge(topic.project_id)
        if forge.requires_pr:
            if card.pr_number is None:
                await self._publish_pr_for_accept(card, topic)
                if card.pr_number is not None:
                    # PR 是这一秒才开出来的：卡面在此之前没有、现在也还没有一个
                    # 被展示过的 head。「人看的是同一条分支」不等于「同一个
                    # commit」—— 浏览器从来没有声明过它渲染的 diff 是哪个 sha，
                    # 而分身边干边推是常态。所以这次不合，PR 留着（开 PR 是有价
                    # 值的副作用，下次采纳就有 head 可比），head 镜像上卡，人重
                    # 新看过再点。
                    await self._refresh_never_shown_card(card, topic, "采纳")
            if card.pr_number is not None:
                assert seen_head is not None  # the guard above rules None out
                return await self._merge_pr_for_accept(
                    card, topic, decided_by, seen_head=seen_head
                )
            if (card.change_subject or "").strip():
                await self._stop_accept_no_branch(card, topic)
        unbound_note = forge.note

        # 采纳 = merge (spec §6.3) — and the merge DECIDES the outcome. A
        # conflict must never silently archive the topic while the work is
        # stranded on its branch (that shipped a lie once): the card moves to
        # `conflict`, 芝士 gets dispatched to resolve, a human retries.
        #
        # The platform forge squashes (#363), same shape as the GitHub lane's
        # product: one commit, the card's subject and body, the pr_text
        # trailers, authored by the requester (committer stays 芝士). The
        # message and author are resolved HERE because merge_topic has no DB
        # session to read the card or the roster with.
        from app.domain.workspace import service as ws

        who = await identity.attribution(self._session, topic, card=card)
        try:
            merged = await asyncio.to_thread(
                ws.merge_topic,
                topic.project_id,
                topic.id,
                message=pr_text.local_merge_commit_message(
                    topic, decided_by, card, who
                ),
                author=who.author,
            )
        except Exception as exc:  # noqa: BLE001 — surface, don't invent success
            logger.exception(
                "accept merge raised for project=%s topic=%s",
                topic.project_id,
                topic.id,
            )
            self._notify_merge_result(
                topic,
                "采纳未完成：合并出错",
                meta=notice(
                    EVENT_ACCEPT_STOPPED,
                    severity=SEVERITY_ERROR,
                    who=WHO_HUMAN,
                    detail=f"请检查工作区状态后重试采纳。\n{exc}",
                    detail_label="合并报错",
                ),
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
                notes.record(
                    card, notes.NoteCode.merge_conflict, merged.get("reason", "")
                )
                await self._session.flush()
                await self._session.refresh(card)
                self._notify_merge_result(
                    topic,
                    "采纳未完成：合并冲突",
                    meta=notice(
                        EVENT_ACCEPT_CONFLICT,
                        severity=SEVERITY_ERROR,
                        who=WHO_CHEESE,
                        detail=(
                            f"芝士解决冲突后重试采纳。\n{card.note}"
                            if card.note
                            else "芝士解决冲突后重试采纳。"
                        ),
                        detail_label="冲突详情",
                    ),
                )
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
                    topic,
                    "采纳未完成：合并失败",
                    meta=notice(
                        EVENT_ACCEPT_STOPPED,
                        severity=SEVERITY_ERROR,
                        who=WHO_HUMAN,
                        detail="请检查工作区状态后重试采纳。",
                        detail_label="怎么办",
                    ),
                )
                raise ValidationError(_MERGE_FAILED_MESSAGE)

        # Merged (or nothing to merge — e.g. a discussion topic with no branch
        # work): the accept completes as before.
        await self._repo.add_approval(card_id, decided_by)
        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.decided_by = decided_by
        card.decided_at = now
        # The merge into the platform's own repo is where this accept ends:
        # nothing is pushed anywhere (#718) — the platform holds no credential
        # for a remote it is not bound to, and a project on the App forge never
        # takes this path.
        notes.clear(card)
        if unbound_note:
            # 平台即 forge (#363): 如实标注，而不是让这张卡看起来像绕过了 PR。
            notes.annotate(card, unbound_note)

        # 这次改动交付完了 → 释放计费算力，工作面留着（见 _release_billed_compute）。
        await self._release_billed_compute(topic)

        # 交付完成 ≠ 话题结束 (#442 decision 1). accepted_by/accepted_at 是这一刻
        # 自动打上的交付标记；status 不动，归档只由人来做（POST /topics/{id}/archive）。
        await self._stamp_delivery(card, topic, by=decided_by, at=now)

        await self._session.flush()
        await self._session.refresh(card)
        done_detail = "话题保持活跃，归档由人决定。"
        if card.note:
            done_detail += f"\n{card.note}"
        self._notify_merge_result(
            topic,
            f"{decided_by} 采纳了这次改动，已合并",
            meta=notice(
                EVENT_ACCEPT_DONE,
                severity=SEVERITY_INFO,
                who=WHO_PLATFORM,
                detail=done_detail,
                detail_label="交付说明",
            ),
        )
        return card

    # ---- 两阶段采纳 (PR迭代式, 2026-08-09) ----------------------------------

    def _local_topic_branch_head(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> str | None:
        """The topic branch head as it stands — local-only (no network), used to
        decide whether a re-push to the PR branch is needed before touching
        GitHub at all.

        It READS the branch and never moves it. It used to snapshot the
        workspace first, which turned every poll tick into a potential new
        commit: any write at all — a scratch file, a line in the living doc —
        became a commit, the commit moved the head, the head moved the PR, and
        `cancel-in-progress` killed the CI run that was already going. One
        branch measured 17 runs of the backend suite, 14 of them cancelled, for
        a single PR. Nothing was wrong with the code; the poller was racing the
        agent.

        The head still moves on its own at a boundary that means something —
        the end-of-turn snapshot — and `push_fix` is the way to move it on
        purpose in between. Both are intentional; a 60-second timer is not.

        None if the repo/branch genuinely doesn't exist yet (nothing to push).
        """
        import subprocess

        from app.domain.workspace import service as ws

        repo_path = ws.ensure_repo(project_id)
        branch = ws.branch_for_tree(ws.tree_for_place(topic_id))
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
        branch (a reset moved the branch backwards) can never land — GitHub
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
        cannot do this itself; see `_ci_nudge`). Compares the LOCAL branch
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
                "card %s: local head %s cannot fast-forward PR branch "
                "%s (rewound/diverged) — not re-pushing, waiting on 芝士",
                card.id,
                local_head,
                remote_head,
            )
            if remote_head:
                card.pr_head_sha = remote_head
            if card.note_code is not notes.NoteCode.repush_diverged:
                notes.record(
                    card,
                    notes.NoteCode.repush_diverged,
                    f"{_REPUSH_DIVERGED_PREFIX}：本地话题分支（{local_head[:8]}）"
                    f"落后于/偏离了 PR 分支（{remote_head[:8]}），平台不会强推覆盖 PR "
                    "上已有的提交。请在这个话题的工作区里把 PR 分支的新提交合并进来"
                    "再提交，平台会自动把结果同步到这个 PR。",
                )
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
                "card %s: re-push of local commit %s failed (%s) — "
                "will retry next poll tick",
                card.id,
                local_head,
                exc,
            )
            # Visible on the card, not just logger (agent has no host SSH):
            # otherwise 芝士 believes its fix was pushed and just waits forever.
            # Dedup by code — this fires every 60s poll tick until the push
            # succeeds, and must not spam the note each time.
            if card.note_code is not notes.NoteCode.repush_failed:
                notes.record(
                    card,
                    notes.NoteCode.repush_failed,
                    f"平台自动重推失败，下一轮还会重试：{exc}",
                )
                await self._session.flush()
            return False
        card.pr_head_sha = pushed["head_sha"]
        # 芝士推了新东西 → 换基的账重新算。上限管的是「同一段落后里换了几次还没
        # 赶上」，不是一张卡一辈子的额度。
        card.rebase_count = 0
        notes.clear(card)
        await self._session.flush()
        return True

    async def _pr_repo_of(self, card: AcceptCard, topic: Topic) -> tuple[str, str]:
        """(owner, repo) the card's PR lives in — from the card when recorded,
        else resolved from the project's upstream and backfilled onto the card
        (pr_publish records only pr_number/pr_url at filing time)."""
        from app.domain.review.github_pr import parse_github_repo
        from app.domain.workspace import service as ws

        if card.pr_repo and "/" in card.pr_repo:
            owner, _, repo = card.pr_repo.partition("/")
            return owner, repo
        upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
        parsed = parse_github_repo(upstream)
        if parsed is None:
            raise ValidationError(
                f"读不到 PR #{card.pr_number} 该合进哪个仓库（上游不是 GitHub）"
            )
        card.pr_repo = f"{parsed[0]}/{parsed[1]}"
        return parsed

    async def _pr_verdict(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status: "PullRequestStatus",
        ref: str,
    ) -> tuple[MergeVerdict, Who, "BranchProtection", bool, list[merge_state.CheckRun]]:
        """The one merge-state computation (#718): gather this PR's raw
        signals and hand them to `merge_state.compute_merge_state`. Click-time
        and poll-time both come through here — the judgment exists once.

        Returns (verdict, whose move it is, the project's protection policy,
        whether GitHub itself is enforcing protection, the raw check runs —
        the poller's event table reads facts the verdict may have folded away:
        a conflicted PR is `dirty` no matter what its checks say, but a red
        check on it is still 芝士's to fix and must still reach it)."""
        from app.domain.project.protection import branch_protection_of
        from app.domain.workspace import service as ws

        project = await self._projects.get(topic.project_id)
        protection = branch_protection_of(project)
        enforces = await _github_enforces(f"{owner}/{repo}", creds.read)

        raw_runs = await client.list_check_runs(
            owner=owner, repo=repo, ref=ref, token=creds.read
        )
        runs = [
            merge_state.CheckRun(
                name=str(r.get("name") or ""),
                status=str(r.get("status") or ""),
                conclusion=(
                    str(r["conclusion"]) if r.get("conclusion") is not None else None
                ),
            )
            for r in raw_runs
        ]
        required = tuple(
            merge_state.RequiredCheck(name=rc.name, paths=rc.paths)
            for rc in protection.required_checks
        )

        # The two compare reads cost API calls, so each happens only when a
        # rule actually consumes it: ancestry for strict, the file list for a
        # path-scoped required check. Failures degrade to None — the verdict's
        # documented conservative fallbacks take over (scope unknown = the
        # check stays required; ancestry unknown = strict does not block).
        base = await asyncio.to_thread(ws.pr_base_branch, topic.project_id)
        ancestry: str | None = None
        if protection.strict:
            try:
                ancestry = await client.compare_status(
                    owner=owner, repo=repo, base=base, head=ref, token=creds.read
                )
            except Exception:  # noqa: BLE001 — ancestry unreadable ≠ blocked
                logger.warning("card %s: compare_status failed", card.id, exc_info=True)
        changed_paths: list[str] | None = None
        if any(rc.paths for rc in required):
            try:
                files = await client.compare_files(
                    owner=owner, repo=repo, base=base, head=ref, token=creds.read
                )
            except Exception:  # noqa: BLE001 — scope unknown handled conservatively
                logger.warning("card %s: compare_files failed", card.id, exc_info=True)
                files = None
            changed_paths = None if files is None else [path for _, path in files]

        verdict = merge_state.compute_merge_state(
            github_mergeable_state=status.mergeable_state,
            github_mergeable=status.mergeable,
            check_runs=runs,
            changed_paths=changed_paths,
            required_checks=required,
            strict=protection.strict,
            base_ancestry=ancestry,
            github_enforces=enforces,
            draft=status.draft,
        )
        return verdict, whose_move(verdict), protection, enforces, runs

    def _write_merge_mirror(
        self, card: AcceptCard, verdict: MergeVerdict, who: Who, head_sha: str
    ) -> None:
        """Mirror the verdict onto the card — what the card UI shows (#718).

        `since` is when this (state, head) pair started holding, carried over
        from the previous mirror when unchanged: the required-check grace
        clock reads it."""
        now_iso = datetime.now(UTC).isoformat()
        prev = card.merge_state if isinstance(card.merge_state, dict) else {}
        since = (
            prev.get("since") or now_iso
            if prev.get("state") == verdict.state and prev.get("head_sha") == head_sha
            else now_iso
        )
        card.merge_state = {
            "state": verdict.state,
            "who": who,
            "reasons": [
                {"kind": r.kind, "checks": list(r.checks), "detail": r.detail}
                for r in verdict.reasons
            ],
            "head_sha": head_sha,
            "checked_at": now_iso,
            "since": since,
        }

    async def _merge_pr_for_accept(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        seen_head: str,
    ) -> AcceptCard:
        """App forge: 采纳 = 当场调合并 API，合的是人看到的那个 commit (#718).

        The gate keeping "只在绿的时候合" is the same merge-state computation
        the poller mirrors onto the card — evaluated fresh right here, because
        the mirror may be up to a poll interval stale. GitHub 自己开了保护的
        项目直接调 API（405 就是被拦住，平台一个字不重算）；其余项目 clean /
        unstable（红的不在必跑名单）才合，非绿拒绝采纳并把状态和原因写进响应。

        The merge call carries the head the human saw — `seen_head`, the sha
        the BROWSER rendered, already checked against the card by
        `_seen_head_or_refresh`, and never anything else: there is no "just
        read the live head" fallback, because a live head is by definition one
        no screen has shown. Any push that landed after their look — before the
        click (live head differs) or during it (GitHub answers 409) — refreshes
        the card instead of merging: head updated, approvals cleared when the
        project dismisses stale accepts, and the human asked to look again.
        #422's whole authorize-then-drift apparatus is replaced by this one API
        parameter plus dismiss-stale.
        """
        from app.domain.review import github_pr

        number = card.pr_number
        assert number is not None  # caller checked; keeps the type checker honest

        creds, why = await self._app_credentials(topic)
        if creds is None:
            await self._stop_accept_pr_unavailable(
                card, topic, f"拿不到平台 GitHub 凭据（{why}）"
            )
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except ValidationError as exc:
            await self._stop_accept_pr_unavailable(card, topic, str(exc))

        client = github_pr.default_client()
        try:
            status = await client.pull_request_status(
                owner=owner, repo=repo, number=number, token=creds.read
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
            # 有人已经在 GitHub 上合了这个 PR —— 同一件事，照单收下。
            card.pr_merged_at = status.merged_at or datetime.now(UTC)
            await self._mark_cards_tree_merged(card)
            if status.merge_commit_sha:
                card.pr_head_sha = status.merge_commit_sha
            return await self._conclude_pr_accept(
                card, topic, decided_by, merged_externally=True
            )
        if status.state == "closed":
            await self._stop_accept_pr_unavailable(
                card, topic, f"PR #{number} 已在 GitHub 被关闭但未合并"
            )

        # 合的是人看到的那个 commit：浏览器渲染时卡面上的 head，一个字都不兜底。
        # GitHub 上的合并永远不用「现取的 head」——那种 commit 没有在任何界面上
        # 出现过（`_seen_head_or_refresh` 是这条规矩的入口闸）。
        seen = seen_head
        if status.head_sha != seen:
            await self._refresh_stale_card(card, topic, live_head=status.head_sha)
            raise ValidationError(
                f"PR #{number} 的 head 在你查看后变了，卡已刷新 —— 请重新看过再采纳"
            )

        try:
            verdict, who, protection, enforces, _runs = await self._pr_verdict(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                status=status,
                ref=seen,
            )
        except Exception as exc:  # noqa: BLE001 — stop visibly; never guess green
            logger.warning(
                "cannot compute merge state for PR #%s at accept time: %s",
                number,
                exc,
            )
            await self._stop_accept_pr_unavailable(
                card, topic, f"PR #{number} 合并态读取失败：{exc}"[:300]
            )
        self._write_merge_mirror(card, verdict, who, seen)
        # GitHub enforcing → its merge API is the gate (405 = blocked, 如实转
        # 译). Platform enforcing → only clean, or unstable whose reds are not
        # on the required roster, may merge; everything else is refused with
        # the state and its reasons — the UI shouldn't have lit the button,
        # this is the defensive twin of that rule.
        if not enforces and verdict.state not in ("clean", "unstable"):
            detail = "；".join(r.detail for r in verdict.reasons if r.detail)
            raise ValidationError(
                f"现在不能采纳（合并态：{verdict.state}）：{detail or '规则未满足'}"
            )

        attribution = await identity.attribution(self._session, topic, card=card)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(
                topic, decided_by, card, attribution
            ),
            sha=seen,
        )
        if result.stale_head:
            # 芝士在点击和合并之间又推了 —— GitHub 拦下了那个没人看过的 commit。
            live = ""
            try:
                live = await client.pull_request_head_sha(
                    owner=owner, repo=repo, number=number, token=creds.read
                )
            except Exception:  # noqa: BLE001 — refresh with what we know
                logger.warning("card %s: post-409 head read failed", card.id)
            await self._refresh_stale_card(card, topic, live_head=live)
            raise ValidationError(
                f"PR #{number} 的 head 在采纳瞬间变了（GitHub 409），卡已刷新 —— "
                "请重新看过再采纳"
            )
        if result.sha is None:
            # A faithful 405: GitHub (or its enforced protection) said no.
            reason = result.blocked_reason or "未说明原因"
            self._notify_merge_result(
                topic,
                f"采纳未完成：GitHub 拒绝合并 PR #{number}",
                meta=notice(
                    EVENT_MERGE_REFUSED,
                    severity=SEVERITY_ERROR,
                    who=WHO_HUMAN,
                    detail=f"{reason}\n{card.pr_url or ''}",
                    detail_label="GitHub 的回复",
                ),
            )
            raise ValidationError(f"GitHub 拒绝合并 PR #{number}：{reason}")

        card.pr_merged_at = datetime.now(UTC)
        await self._mark_cards_tree_merged(card)
        card.pr_head_sha = result.sha  # the merge commit, for the record
        return await self._conclude_pr_accept(card, topic, decided_by)

    async def _conclude_pr_accept(
        self,
        card: AcceptCard,
        topic: Topic,
        decided_by: str,
        *,
        merged_externally: bool = False,
    ) -> AcceptCard:
        """Shared tail of a click-time PR accept: record the decision and vote,
        then the common merged-PR bookkeeping (`_finish_pr_accept`)."""
        await self._repo.add_approval(card.id, decided_by)
        card.decided_by = decided_by
        card.decided_at = datetime.now(UTC)
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        await self._finish_pr_accept(
            card=card, topic=topic, merged_externally=merged_externally
        )
        await self._session.refresh(card)
        return card

    async def _refresh_stale_card(
        self,
        card: AcceptCard,
        topic: Topic,
        *,
        live_head: str,
        action: str = "采纳",
        headline: str | None = None,
    ) -> None:
        """新提交作废已有的采纳 (#718, dismiss_stale — GitHub 的「Dismiss stale
        pull request approvals」，这里默认开): the head moved out from under
        the reviewer, so the card refreshes — new head, stale mirror dropped,
        approvals cleared (when the project dismisses stale accepts), and the
        auto-merge arm disarmed.

        Written OUTSIDE the accept transaction (its caller is about to raise).
        **Rolls the request transaction back first**, for the same reason as
        `_stop_accept_pr_unavailable`: this request may already hold a row
        lock on the very card the fresh session is about to write, and two
        connections on one row with one waiting on the other is a hang, not a
        refresh. Every attribute needed later is read before the rollback
        (expired attributes reload with sync IO an AsyncSession cannot do)."""
        from app.domain.project.protection import branch_protection_of

        card_id = card.id
        number = card.pr_number
        project_id = topic.project_id
        await self._session.rollback()
        try:
            factory = async_sessionmaker(self._session.bind, expire_on_commit=False)
            async with factory() as session:
                project = await ProjectRepository(session).get(project_id)
                dismiss = branch_protection_of(project).dismiss_stale
                repo = AcceptCardRepository(session)
                fresh = await repo.get(card_id)
                if fresh is None:
                    return
                if live_head:
                    fresh.pr_head_sha = live_head
                fresh.merge_state = None  # mirrored for the old head — stale
                if dismiss:
                    await repo.clear_approvals(card_id)
                    fresh.auto_merge_armed_by = None
                    fresh.auto_merge_armed_at = None
                notes.record(
                    fresh,
                    None,
                    (headline or f"PR #{number} 有新提交，之前看到的版本已过时")
                    + ("；已有的批准一并作废" if dismiss else "")
                    + f"，请重新查看后再{action}",
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — the raise this accompanies must fire
            logger.exception("could not refresh stale card %s", card_id)

    async def _pr_poll_credentials(
        self, card: AcceptCard, topic: Topic
    ) -> tuple[_GitHubCredentials | None, str]:
        """The App's GitHub credentials for this card's PR — the only lane
        left (#718 deleted the personal-token one). Two mints, not one: see
        `_GitHubCredentials` for why reading checks with the write token is a
        403 that presents as a card frozen forever. Never raises — a poll
        tick degrades to "pause and retry"."""
        del card  # one lane now; the signature stays call-site-stable
        return await self._app_credentials(topic)

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
            read, _ = await tokens.installation_token()
        except Exception as exc:  # noqa: BLE001 — pause this tick, don't crash
            return None, f"平台 GitHub App 取 token 失败（{type(exc).__name__}）"
        return _GitHubCredentials(write=write, read=read), ""

    async def advance_pr_card(
        self, card_id: uuid.UUID, *, chat_service, runner
    ) -> None:
        """One polling step for a card awaiting accept on a PR (#718). The
        poller does three things and nothing else: mirror the merge state
        onto the card, send the events the 「谁的活」 table names (deduped
        through the nudge ledger), and merge a card whose auto-merge is armed
        once the rules are satisfied. Called by
        SchedulerService.poll_open_prs(); never raises for a transient GitHub
        hiccup — the next poll just retries."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending or card.pr_number is None:
            return
        topic = await self._topic_or_404(card.topic_id)
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except Exception as exc:  # noqa: BLE001 — pause this tick, retry next
            logger.warning("card %s: cannot resolve PR repo: %s", card.id, exc)
            return

        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            logger.warning(
                "card %s has no usable GitHub token anymore (%s); "
                "skipping this poll (will retry next tick)",
                card.id,
                reason,
            )
            # Without this the card just sits there forever and looks
            # identical to "CI still running" — no signal anyone's token died.
            if card.note_code is not notes.NoteCode.poll_paused:
                notes.record(
                    card,
                    notes.NoteCode.poll_paused,
                    f"轮询暂停，下一轮还会重试：{reason}",
                )
                await self._session.flush()
            return

        # Token is usable again → the pause note is stale. Clearing it here is
        # what makes the pause self-healing: it stops describing a condition
        # that no longer holds, AND it can no longer sit in front of a real CI
        # failure (which is how "轮询暂停" used to swallow CI 失败 notifications
        # — see the 2026-08-10 note in `_ci_nudge`). Only this exact prefix is
        # cleared; 重推失败 /
        # 拒绝合并 / 检查未通过 notes describe live conditions and stay put.
        if card.note_code in (notes.NoteCode.poll_paused, notes.NoteCode.poll_failed):
            notes.clear(card)
            await self._session.flush()

        from app.domain.review import github_pr

        client = github_pr.default_client()
        try:
            await self._poll_pending_card(
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
                "GitHub API hiccup polling card %s: %s — retrying next tick",
                card.id,
                exc,
            )
            # And say it on the CARD. A log line is only readable by whoever has
            # a shell on the host, and the person waiting is looking at a card
            # whose note still says 「等 CI」 — so a poll that fails every tick
            # forever is indistinguishable from checks that are simply slow.
            # That is how #575/#582 sat green-but-unmerged with nothing on
            # screen to explain it. Same treatment the credential branch above
            # already gets, for the same reason.
            self._note_poll_failed(card, exc)
            await self._session.flush()

    async def _mark_cards_tree_merged(self, card: AcceptCard) -> None:
        """The batch landed — so close it AND start the next one, here.

        The tree row stays: the work that produced it still points here, and a
        task whose tree vanished could not say where its changes went.

        Opening the next batch in the same breath is the load-bearing half.
        Marking a tree `merged` used to be the whole of it, and the on-disk
        「这个房间写哪棵树」marker (`ws.bind_tree`) kept naming the tree that had
        just landed until somebody happened to call `ensure_open` — which is
        递卡, i.e. the END of the next batch. Everything in between wrote to a
        delivered branch: the room commits, `git log` looks healthy, and the
        commits sit on a branch whose PR is already squashed into main, so they
        are ahead of nothing and reachable from nothing. That is not a
        hypothetical — this repository's own room sat on `topic/229e3403` after
        its PR merged as `1c298199a`, with its head not an ancestor of main.

        A room is between batches most of the time and holding an empty open
        tree is that state's normal shape (`create_card` already commits one on
        sight), so there is nothing to defer: the moment a batch lands is
        exactly the moment the room needs somewhere else to write.
        """
        if card.tree_id is None:
            return
        trees = WorkTreeService(self._session)
        tree = await trees.get(card.tree_id)
        if tree is None:
            return
        if tree.status is not TreeStatus.merged:
            await trees.mark_merged(tree)
        await trees.ensure_open(project_id=tree.project_id, room_id=tree.room_id)

    async def _app_pr_client(self, topic: Topic):  # noqa: ANN202 — GitHubPRClient
        """The App-token client for this project's upstream, or None when the
        project has no GitHub side at all (no installation, or an upstream that
        is not a GitHub https remote)."""
        from app.domain.agent.github_app import github_app_tokens_for_project
        from app.domain.review.github_pr import GitHubPRClient, parse_github_repo
        from app.domain.workspace import service as ws

        tokens = await github_app_tokens_for_project(topic.project_id, self._session)
        if tokens is None:
            return None
        upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
        parsed = parse_github_repo(upstream)
        if parsed is None:
            return None
        return GitHubPRClient(*parsed, tokens)

    async def _live_pr_number(self, place_id: uuid.UUID, topic: Topic) -> int | None:
        """Which PR this place is writing into right now.

        The card first, because a filed card IS the delivery and its
        `pr_number` is what every other path here already trusts; the room's
        open batch second, which is the answer BEFORE anyone files a card —
        the draft PR opened at the first commit (#718 拍板①) hangs there.
        """
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending,)
        )
        for card in cards:
            if card.pr_number is not None:
                return card.pr_number
        tree = await WorkTreeService(self._session).current(topic.id)
        return tree.pr_number if tree is not None else None

    async def mark_ready(self, place_id: uuid.UUID) -> dict:
        """`cheese ready`: take this batch's PR out of draft. Nothing else.

        Not a delivery and not an accept — it flips one boolean on GitHub, the
        one that means「这份东西可以看了」. 递卡 flips the same boolean (递卡 的
        语义就是请人来看) and does the rest; this exists for the case where the
        work is worth showing before anybody is ready to ask for a review.

        Flipping it is the one thing here that REST cannot do — see
        `GitHubPRClient.mark_ready_for_review`, which is why a GraphQL request
        appears in this codebase at all.

        Returns a dict the CLI prints rather than raising for "there was
        nothing to flip": a PR that is already ready is the state the caller
        wanted, and an exception for it would teach agents to avoid the
        command. A FAILED flip does raise — a draft that silently stayed draft
        is a delivery sitting where no reviewer will look for it.
        """
        topic = await self._topic_or_404(place_id)
        number = await self._live_pr_number(place_id, topic)
        if number is None:
            return {
                "ready": False,
                "reason": (
                    "这个房间还没有 PR —— 先提交点东西"
                    "（有提交平台就会开一个 draft PR）"
                ),
            }
        client = await self._app_pr_client(topic)
        if client is None:
            return {"ready": False, "reason": "这个项目没有绑定 GitHub，没有 PR 可以翻"}
        view = await client.pr_view(number)
        url = str(view.get("html_url") or "")
        if not view.get("draft"):
            return {
                "ready": False,
                "already": True,
                "pr_number": number,
                "pr_url": url,
                "reason": f"PR #{number} 本来就不是 draft",
            }
        node_id = str(view.get("node_id") or "")
        if not node_id:
            raise ValidationError(f"GitHub 没给 PR #{number} 的 node_id，翻不了 ready")
        await client.mark_ready_for_review(node_id)
        return {"ready": True, "pr_number": number, "pr_url": url}

    async def redescribe(
        self,
        place_id: uuid.UUID,
        *,
        actor: str,
        change_subject: str | None = None,
        change_body: str | None = None,
    ) -> AcceptCard:
        """更正这张卡的描述 —— and rewrite the PR from it in the same breath.

        **Why this may be corrected while `Cheese-Task:` may not.** A delivery
        claim is an ASSERTION OF FACT about who wrote the code; letting it be
        edited after filing is letting somebody put another agent's name on a
        change, and the wrong name in permanent history reads exactly like the
        right one. A description is an EXPLANATION of the change, and having a
        reviewer say "that reasoning is wrong" is what review IS. Refusing to
        correct it does not protect history — it guarantees the correction
        happens on the PR page only, and main receives the sentence everyone
        already agreed was false. PR #735 是活例子：评审把 PR 正文改对了，
        `1c298199a` 里留下的仍是递卡那一刻的快照。

        The PR is rewritten from the card, never read back into it. The card is
        the single source of both texts, so they cannot disagree — and the
        trailers (`Cheese-Task`, `Requested-by`, `Cheese-Agent`) stay something
        the platform asserts rather than something anybody can retype in a
        GitHub textarea.

        Only while the card is `pending`. Once it is accepted the commit is
        already in main and there is nothing left to correct here; that case
        belongs in a correction the room records, not in a row nobody reads
        again.
        """
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending,)
        )
        if not cards:
            raise ValidationError("这个话题手上没有待处理的验收卡，没有描述可以改")
        card = cards[0]
        topic = await self._topic_or_404(card.topic_id)
        before_subject, before_body = card.change_subject, card.change_body or ""

        subject = (change_subject or "").strip()
        if subject:
            try:
                card.change_subject = commit_message.check_subject(subject)
            except commit_message.InvalidSubject as exc:
                raise ValidationError(str(exc)) from exc
        if change_body is not None:
            card.change_body = change_body.strip() or None
        if (card.change_subject, card.change_body or "") == (
            before_subject,
            before_body,
        ):
            return card
        await self._session.flush()

        if card.pr_number is not None:
            client = await self._app_pr_client(topic)
            if client is not None:
                who = await identity.attribution(self._session, topic, card=card)
                view = await client.pr_view(card.pr_number)
                await pr_publish.sync_pr_text(
                    client,
                    view,
                    title=pr_text.change_subject(card, topic),
                    body=pr_text.pr_body(topic, "", card, who),
                )
        # 留痕：谁在什么时候把描述从什么改成了什么。这条入口的存在本身需要可追溯，
        # 否则它就是一条能悄悄改「这次改动会在历史里说什么」的路。
        self._notify_merge_result(
            topic,
            f"{actor} 改了验收卡的描述（PR 正文已同步）",
            meta=notice(
                EVENT_CARD_REDESCRIBED,
                severity=SEVERITY_INFO,
                who=WHO_CHEESE,
                detail=(
                    f"改前标题：{before_subject or '（空）'}\n"
                    f"改后标题：{card.change_subject or '（空）'}\n\n"
                    f"改前正文：{before_body or '（空）'}\n\n"
                    f"改后正文：{card.change_body or '（空）'}"
                ),
                detail_label="改了什么",
            ),
        )
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def push_fix(self, place_id: uuid.UUID) -> dict:
        """Put this place's branch on the PR it is riding, NOW.

        The agent commits its own work and pushes the branch back here; this is
        how it then says "put that on the PR now" rather than waiting for the
        next poll. A push corresponds to somebody deciding the work is worth
        showing.

        Returns a dict the CLI prints verbatim rather than raising for the
        ordinary "nothing to do" answers: no card, no PR, nothing new to push.
        None of those are errors, and an agent that gets an exception for "your
        work was already pushed" learns to stop calling this.
        """
        cards = await self._repo.list_live_for_places(
            [place_id], statuses=(AcceptStatus.pending,)
        )
        cards = [c for c in cards if c.pr_number is not None]
        if not cards:
            return {"pushed": False, "reason": "这个话题手上没有骑着 PR 的验收卡"}
        card = cards[0]
        assert card.pr_number is not None  # filtered above; for the type checker
        topic = await self._topic_or_404(card.topic_id)
        try:
            owner, repo = await self._pr_repo_of(card, topic)
        except ValidationError as exc:
            return {"pushed": False, "reason": str(exc)}

        creds, reason = await self._pr_poll_credentials(card, topic)
        if creds is None:
            return {"pushed": False, "reason": f"拿不到可用的 GitHub 凭据：{reason}"}

        from app.domain.review import github_pr

        client = github_pr.default_client()
        try:
            status = await client.pull_request_status(
                owner=owner, repo=repo, number=card.pr_number, token=creds.read
            )
            pushed = await self._repush_if_local_head_moved(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                token=creds.write,
                remote_head=status.head_sha,
                remote_branch=status.head_ref,
            )
        except github_pr.GitHubPrError as exc:
            # Same reasoning as the poll path: say it on the card, because the
            # person waiting is looking at the card and not at a log file.
            self._note_poll_failed(card, exc)
            await self._session.flush()
            return {"pushed": False, "reason": f"GitHub 暂时不通：{exc}"}
        await self._session.flush()
        return {
            "pushed": pushed,
            "pr_number": card.pr_number,
            "pr_url": card.pr_url,
            "reason": "" if pushed else "分支上没有 PR 还不知道的提交",
        }

    async def note_poll_crashed(self, card_id: uuid.UUID, exc: BaseException) -> None:
        """Same explanation as `_note_poll_failed`, for a poll that died on
        something other than a GitHub error (the scheduler's own catch-all).

        Its caller rolled the failed tick back, so this runs on a fresh session
        and is a no-op for a card that has since settled or lost its PR.
        """
        card = await AcceptCardRepository(self._session).get(card_id)
        if card is None or card.status != AcceptStatus.pending:
            return
        self._note_poll_failed(card, exc)
        await self._session.flush()

    def _note_poll_failed(self, card: AcceptCard, exc: BaseException) -> None:
        """Record that this tick could not read GitHub, without burying a note
        that describes something worse.

        Only overwrites a note this same failure wrote, or an ordinary 「等 CI」
        line. A 检查未通过 / 拒绝合并 / 需要人来看 note names a live condition the
        reader has to act on; a transient poll error must not push it off the
        card. The next successful poll re-derives the real state and replaces
        this line, so it is self-healing the same way `poll_paused` is.
        """
        if card.note_code not in (
            None,
            notes.NoteCode.waiting_checks,
            notes.NoteCode.poll_failed,
        ):
            return
        detail = " ".join(str(exc).split())[:200] or exc.__class__.__name__
        notes.record(
            card,
            notes.NoteCode.poll_failed,
            f"读不到这个 PR 的状态，卡暂时推不动（下一轮还会重试）：{detail}",
        )

    async def _poll_pending_card(
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
        """The poller's three jobs on one card (#718): mirror the merge state,
        send the events the 「谁的活」 table names, and merge an armed card
        when the rules are satisfied. It merges NOTHING otherwise — accepting
        is the human's click, evaluated at click time."""
        number = card.pr_number
        if number is None:  # already guaranteed by advance_pr_card's guard
            return

        # FIRST: did someone already handle this PR on GitHub? A merged-by-hand
        # PR is invisible to every other signal here, and without this the
        # card polls forever.
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

        live = status.head_sha
        if card.pr_head_sha != live:
            if card.pr_head_sha:
                # 新提交作废已有的采纳 (#718, dismiss_stale): the head the
                # reviewer saw moved. Clear what the old head earned —
                # approvals and the auto-merge arm — and tell the reviewer.
                await self._dismiss_stale_accept(card=card, topic=topic)
                # Whatever the note said, it described the old commit.
                notes.clear(card)
            card.pr_head_sha = live
            await self._session.flush()

        verdict, who, protection, enforces, runs = await self._pr_verdict(
            card=card,
            topic=topic,
            owner=owner,
            repo=repo,
            creds=creds,
            client=client,
            status=status,
            ref=live,
        )
        self._write_merge_mirror(card, verdict, who, live)

        # —— 按表发事件，每件各排一条待发，谁都不许把别人挡掉 (pr_signals) ——
        #
        # 收集途中读 GitHub 失败，不能把已经排好队的其它待发一起丢掉。错误推迟
        # 到发完再抛：卡上照样记下这次轮询出过错（advance_pr_card 的 except），
        # 而已经排好的那条已经送到。
        pending: list[pr_signals.PendingNudge] = []
        deferred: Exception | None = None
        kinds = {r.kind for r in verdict.reasons}
        # 红检查按 runs 本身判，不按 verdict 的 reasons：一个和 main 冲突的 PR
        # 的 verdict 是 dirty（冲突最优先），但它上面的红检查照样是芝士要修的
        # 事 —— 三件事互不蕴含，谁都不许把别人吞掉（pr_signals 的规矩）。
        has_red_check = any(
            r.conclusion in ("failure", "timed_out", "cancelled", "action_required")
            for r in runs
            if r.status == "completed"
        )
        if has_red_check or kinds & {"required_check_failed", "check_failed"}:
            # 检查红了 → 事件到做活的 agent，带哪个检查红、日志怎么取。
            # `check_state` 是取失败详情（job 链接 + 日志片段）的那条路。
            try:
                state_word, tail = await client.check_state(
                    owner=owner, repo=repo, ref=live, token=creds.read
                )
                if state_word == "failure":
                    ci = self._ci_nudge(
                        card=card, tail=tail, stage="CI", owner=owner, repo=repo
                    )
                    if ci is not None:
                        pending.append(ci)
            except Exception as exc:  # noqa: BLE001 — 先发完，再抛
                deferred = exc
        try:
            review = await self._review_nudge(
                card=card,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                status=status,
            )
            if review is not None:
                pending.append(review)
        except Exception as exc:  # noqa: BLE001 — 见上：先发完，再抛
            deferred = exc
        if verdict.state == "dirty":
            conflict = self._conflict_nudge(card=card, status=status)
            if conflict is not None:
                pending.append(conflict)
        self._dispatch_nudges(
            card=card,
            topic=topic,
            pending=pending,
            chat_service=chat_service,
            runner=runner,
        )
        if deferred is not None:
            await self._session.flush()  # the mirror and dispatched ledger keep
            raise deferred

        if verdict.state == "clean":
            # CLEAN → 通知验收人（按 head 去重）。
            self._notify_ready(card, topic)
        elif verdict.state == "blocked" and "required_check_missing" in kinds:
            # BLOCKED 必跑检查没报到 → 等 CI，不发；超过宽限期转人 ——
            # workflow 改名、被禁用、Actions 断供都长这样，等下去没有尽头，
            # 而出口是叫人，绝不因为等腻了就自动合并。
            if self._required_absence_overdue(card):
                missing = ", ".join(
                    sorted(
                        {
                            name
                            for r in verdict.reasons
                            if r.kind == "required_check_missing"
                            for name in r.checks
                        }
                    )
                )
                self._note_needs_human(
                    card=card,
                    topic=topic,
                    reason=(
                        f"必跑检查 {missing} 迟迟没有报到"
                        f"（已等超过 {_REQUIRED_CHECK_GRACE_MINUTES} 分钟）——"
                        "多半是 workflow 没被触发、被改名或被禁用，"
                        "平台不会替人判定它可以不跑"
                    ),
                    explain=(
                        "这不是检查红了，是它根本没报到：平台只能确认"
                        "「没人跑过这项检查」，不能替人认定它不需要跑。"
                    ),
                )
        elif verdict.state == "behind":
            # BEHIND（strict 才出现）→ 平台自己 update-branch；撞冲突的话
            # 下一拍这个 PR 就是 dirty，冲突事件自然转给 agent。
            await self._update_behind_branch(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
            )
            return

        if card.auto_merge_armed_by and verdict.state in ("clean", "unstable"):
            await self._merge_armed_card(
                card=card,
                topic=topic,
                owner=owner,
                repo=repo,
                creds=creds,
                client=client,
                protection=protection,
                chat_service=chat_service,
                runner=runner,
            )
            return
        await self._session.flush()

    async def _update_behind_branch(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
    ) -> None:
        """BEHIND is the platform's move: GitHub's Update branch, capped.

        绿必须绿在当前基线上（strict）。各自绿在旧基上的两个 PR 相加可以是红
        的。换基后 head 变化，下一轮从新 CI 重新等起；反复换基追不上 main 就
        叫人（上限 3，芝士推新提交时清零 —— `_repush_if_local_head_moved`）。"""
        if card.rebase_count >= 3:
            self._note_needs_human(
                card=card,
                topic=topic,
                reason=(
                    "分支反复落后于 main（已自动换基 3 次仍未赶上）——"
                    "main 移动太快或换基没生效，请人工处理"
                ),
                explain="平台的自动更新分支追不上 main 的移动速度。",
            )
            await self._session.flush()
            return
        # `write`, not `read` — this PUSHES a merge of main onto the PR branch
        # (the read mint has no `contents:write`; PR #575/#582 froze on that).
        updated = await client.update_branch(
            owner=owner, repo=repo, number=card.pr_number or 0, token=creds.write
        )
        outcome = (
            "已自动更新分支，等新一轮 CI。"
            if updated
            else "自动更新分支被拒，下一轮重试。"
        )
        card.rebase_count += 1
        notes.annotate(card, f"基线落后于 main，{outcome}")
        await self._session.flush()

    async def _dismiss_stale_accept(self, *, card: AcceptCard, topic: Topic) -> None:
        """新提交作废已有的采纳 (#718)。

        GitHub 的「Dismiss stale pull request approvals when new commits are
        pushed」，这里默认开着 —— GitHub 默认关，因为它假设推代码的是可信的
        人；这里推代码的是拿着 App 写权限的芝士。清掉旧 head 挣到的一切
        （批准票、auto-merge 布防），且只在真有东西被作废时通知验收人。"""
        from app.domain.project.protection import branch_protection_of

        project = await self._projects.get(topic.project_id)
        if not branch_protection_of(project).dismiss_stale:
            return
        approvers = await self._repo.list_approver_handles(card.id)
        armed = card.auto_merge_armed_by
        if not approvers and not armed:
            return
        await self._repo.clear_approvals(card.id)
        card.auto_merge_armed_by = None
        card.auto_merge_armed_at = None
        voided = "、".join(sorted({*approvers, *((armed,) if armed else ())}))
        self._notify_merge_result(
            topic,
            f"PR #{card.pr_number} 有新提交，已有的采纳批准被作废",
            meta=notice(
                EVENT_ACCEPT_DISMISSED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=(
                    f"被作废的批准：{voided}。\n"
                    "新提交作废已有的采纳（项目分支保护，默认开）。"
                    "请重新查看这个 PR 后再采纳。\n"
                    f"{card.pr_url or ''}"
                ),
                detail_label="为什么作废",
            ),
        )

    def _notify_ready(self, card: AcceptCard, topic: Topic) -> None:
        """CLEAN → 通知验收人，按 (head, clean) 经账本去重 —— 一个 head 只说
        一次「可以采纳了」，重跑的检查、反复的轮询都不重复。"""
        ledger = pr_signals.NudgeLedger.load(card.nudge_state)
        signature = pr_signals.signature("ready", card.pr_head_sha or "")
        if ledger.already_sent(pr_signals.NudgeKind.ready, signature):
            return
        self._notify_merge_result(
            topic,
            f"PR #{card.pr_number} 可以合并了，等 {card.reviewer_handle} 采纳",
            meta=notice(
                EVENT_ACCEPT_READY,
                severity=SEVERITY_INFO,
                who=WHO_HUMAN,
                detail=(
                    f"这个 PR 满足项目的合并规则，采纳即当场合并。\n{card.pr_url or ''}"
                ),
                detail_label="下一步",
            ),
        )
        ledger.record(pr_signals.NudgeKind.ready, signature)
        card.nudge_state = ledger.dump()

    def _required_absence_overdue(self, card: AcceptCard) -> bool:
        """这张卡等一个没报到的必跑检查，是不是已经等过头了。

        时钟是镜像里的 `since`——「这个 (state, head) 组合从什么时候开始成立」。
        镜像还没写过（第一拍）永远不算过头。"""
        mirror = card.merge_state if isinstance(card.merge_state, dict) else {}
        raw = mirror.get("since")
        if not isinstance(raw, str):
            return False
        try:
            since = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - since).total_seconds()
        return elapsed >= _REQUIRED_CHECK_GRACE_MINUTES * 60

    async def _merge_armed_card(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        protection: "BranchProtection",
        chat_service,
        runner,
    ) -> None:
        """绿了自动合 (#718)：the armed card's rules are satisfied — merge it
        with the armer's name on the decision, still guarded by the head sha
        (a push racing this merge gets a 409 and the next tick's
        dismiss-stale handles it, exactly like the click path)."""
        number = card.pr_number
        assert number is not None  # caller checked; keeps the type checker honest
        armer = card.auto_merge_armed_by or ""
        approvers = await self._repo.list_approver_handles(card.id)
        votes = len(set(approvers) | {armer})
        if votes < protection.approvals_required:
            self._note_needs_human(
                card=card,
                topic=topic,
                reason=(
                    f"绿了自动合已布防，但批准人数不足"
                    f"（{votes}/{protection.approvals_required}）"
                ),
                explain="规则满足了，但布防人的一票凑不够项目要求的批准数。",
            )
            await self._session.flush()
            return
        attribution = await identity.attribution(self._session, topic, card=card)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(
                topic, armer, card, attribution
            ),
            sha=card.pr_head_sha,
        )
        if result.stale_head:
            # head 在这一拍里又动了 —— 下一拍镜像到新 head，作废条款接手。
            await self._session.flush()
            return
        if result.sha is None:
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
        await self._mark_cards_tree_merged(card)
        card.pr_head_sha = result.sha
        await self._repo.add_approval(card.id, armer)
        card.decided_by = armer
        card.decided_at = datetime.now(UTC)
        await self._finish_pr_accept(card=card, topic=topic)

    def _note_needs_human(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        reason: str,
        explain: str | None = None,
    ) -> None:
        """The machine ran out of moves on this card: say so on the card and
        in the room, and stop — never merge.

        `explain` is the room message's middle sentence — WHY the machine is
        stopping. Callers must say their own (a required check that never
        reported, a rebase cap, short votes), or the room gets told a
        confident falsehood about what happened.

        Dedup by exact text rather than by code: the poll runs every 60s, and
        the reason can legitimately change while the card itself hasn't
        moved."""
        note = (
            f"PR #{card.pr_number} 平台不会自动推进：{reason}。"
            "需要人来定：人工放行（override 名单里的人）、自己在 GitHub 上处理，"
            "或者作废这张卡。"
        )
        if card.note == note:
            return  # already said once — the 60s poll must not repeat it
        notes.record(card, notes.NoteCode.merge_withheld, note)
        logger.warning("card %s: poller stopped — %s", card.id, reason)
        why = explain or reason
        self._notify_merge_result(
            topic,
            f"PR #{card.pr_number} 平台不会自动合并",
            meta=notice(
                EVENT_MERGE_WITHHELD,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=(
                    f"{reason}。\n{why}\n"
                    "需要人来定：在卡片上人工放行（平台会记下是谁、什么时候、"
                    "当时检查是什么状态），自己在 GitHub 上合并，或者作废这张卡。"
                    f"\n{card.pr_url}"
                ),
                detail_label="为什么扣住",
            ),
        )

    async def _settle_external_merge(
        self, *, card: AcceptCard, topic: Topic, status: "PullRequestStatus"
    ) -> None:
        """Someone merged the PR on GitHub themselves (人工放行, another bot, the
        merge queue). Book it exactly like our own merge — because it is the same
        fact, and since #206 that fact is the whole of what the platform waits
        for."""
        card.pr_merged_at = status.merged_at or datetime.now(UTC)
        await self._mark_cards_tree_merged(card)
        if status.merge_commit_sha:
            # Nice to have, not required: nothing downstream looks a run up by
            # this sha any more, it is just the truest record of what landed.
            card.pr_head_sha = status.merge_commit_sha
        await self._finish_pr_accept(card=card, topic=topic, merged_externally=True)

    def _note_pr_closed_unmerged(self, *, card: AcceptCard, topic: Topic) -> None:
        """The PR was closed on GitHub WITHOUT merging. Say so and stop there.

        No auto-settle and no local-merge fallback: a human closing the PR is
        them saying "not this", and merging behind their back would be the
        opposite of what they asked for. A human reopens the PR or voids the
        card; either way the poller picks it up from there.
        """
        note = (
            f"PR #{card.pr_number} 已关闭且没有合并，平台不会自动合并。"
            "需要人决定：重开 PR，或作废这张卡。"
        )
        if card.note == note:
            return  # already said once — the 60s poll must not repeat it
        notes.record(card, notes.NoteCode.pr_closed_unmerged, note)
        logger.warning(
            "card %s: PR #%s was closed unmerged — poller is now idling on it",
            card.id,
            card.pr_number,
        )
        self._notify_merge_result(
            topic,
            f"PR #{card.pr_number} 已关闭且没有合并",
            meta=notice(
                EVENT_PR_CLOSED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=(
                    "平台不会自动合并一个被人关掉的 PR。话题保持活跃，"
                    "需要人决定：重开 PR，或作废这张卡。"
                ),
                detail_label="怎么办",
            ),
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
        it the same way `_ci_nudge` does for a red check. Without the
        summon nobody is working the card and the topic just sits there
        forever (真实案例: PR #242). Note that the conflict dispatch in
        `routes/accept.py` never covers this — that one only runs for the
        synchronous merge at the moment a human clicks 采纳, not for the poll.

        Three things the 60s poll makes mandatory:

        - **No spam.** The note is rewritten only when the text actually
          changes, so an unchanging reason costs one write, not one per poll.
          (Stricter than the nudge ledger's content signature, which cannot
          notice a 405 turning into a 409 — same reason, same string.)
        - **One summon per reason.** The dispatch hangs off that same "the note
          really changed" test, so a 405 that turns into a 409 gets a fresh
          nudge while an unchanging one stays quiet.
        """
        # GitHub 的原话是外部字符串，而它要被贴进芝士的终端（见
        # `pr_signals.sanitize_external`）。
        reason = pr_signals.sanitize_external(reason)
        note = f"PR #{card.pr_number} 检查全绿，但 GitHub 拒绝合并：{reason}"
        if card.note == note:
            return
        notes.record(card, notes.NoteCode.merge_refused, note)
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
            nudge_event=f"PR #{card.pr_number} 全绿，但 GitHub 拒绝合并",
            nudge_meta=notice(
                EVENT_MERGE_REFUSED,
                severity=SEVERITY_ERROR,
                who=WHO_CHEESE,
                detail=reason[:1500],
                detail_label="GitHub 给的理由",
            ),
        )

    def _ci_nudge(
        self,
        *,
        card: AcceptCard,
        tail: str,
        stage: str,
        owner: str,
        repo: str,
    ) -> pr_signals.PendingNudge | None:
        """「这个 PR 的检查红了」排成一条待发，或者 None（这轮不该说）。

        只剩**一个**理由不说：重推失败 / 分支分叉。两者都意味着芝士的修复根本没到
        GitHub，所以 PR 上那片红是旧的，催它再修一遍是催错了对象
        （docs/topics/诊断信息搬上验收卡.md 的优先级说明）。

        「已经叫过了」不再是这里的判断。它以前是 —— 判据是 `note_code ==
        checks_failed`，也就是拿**卡面状态**当去重键，于是任何别的东西写一次 note
        就能顶掉它（2026-08-10 那次 `⚠️ 轮询暂停` 吞掉全部 CI 失败，就是这条的极端
        形态）。现在去重按内容走账本（`_dispatch_nudges`），卡面爱怎么写怎么写。
        """
        if card.note_code in (
            notes.NoteCode.repush_failed,
            notes.NoteCode.repush_diverged,
        ):
            return None
        # CI 日志是仓库外的人能控制的字符串（谁都能提个 PR 让 workflow 打印任意
        # 字节），而它最终会被贴进芝士的终端。控制字符在这里就洗掉。
        clean = pr_signals.sanitize_external(tail)
        # `tail` is a headline PLUS per-job links and log excerpts (see
        # `github_pr._failure_detail`). The card's note is a one-line field in
        # the UI, so only the headline goes there — the detail is exactly what
        # the message is for, and duplicating it into a 2000-char column would
        # cost the note its glanceability for no reader's benefit.
        headline = clean.splitlines()[0] if clean else ""
        return pr_signals.PendingNudge(
            kind=pr_signals.NudgeKind.ci,
            # 签名取 commit + headline，**不取整段 tail**：headline 正是
            # `_summarize_runs` 拼出来的「哪几个 job 挂了」，多挂一个、换一个都会
            # 变；而 tail 里还有日志片段，同一批失败重读一次就可能微妙地不一样，
            # 拿它当签名等于每轮都重发。带上 commit，是因为芝士推了新提交之后同样
            # 的失败是**新事实**，必须再说一次。
            signature=pr_signals.signature(card.pr_head_sha or "", headline),
            event=f"PR #{card.pr_number} 的 {stage} 检查没通过",
            content=(
                f"PR #{card.pr_number}（{card.pr_url}）的{stage}检查没通过：\n"
                f"```\n{clean[:_NUDGE_TAIL_LIMIT]}\n```\n"
                f"{_ci_log_howto(f'{owner}/{repo}')}"
                "请在这个话题的工作区里修复问题并提交（不需要、也没法自己推到 "
                "GitHub），平台会自动把新提交同步到这个 PR，检查会自动重新跑；"
                "转绿后平台会自动合并 PR。"
            ),
            # 平台提示统一契约: the room sees one line and the excerpt rides in
            # `meta.detail`, under the same `_NUDGE_TAIL_LIMIT` bound the message
            # body always used.
            detail=clean[:_NUDGE_TAIL_LIMIT],
            detail_label=f"{stage} 日志",
            note=f"{_nudge_note_prefix(stage)}{headline}",
            note_code=notes.NoteCode.checks_failed,
            event_type=EVENT_CI_FAILED,
        )

    async def _review_nudge(
        self,
        *,
        card: AcceptCard,
        owner: str,
        repo: str,
        creds: _GitHubCredentials,
        client,
        status,
    ) -> pr_signals.PendingNudge | None:
        """「有人在 PR 上说话了」排成一条待发。

        去重键是这些意见的 **id 集合**：又来一条新意见必然换签名，同一批被轮询读
        到十次必然不换。GitHub 的 REST v3 说不出一条评论「解决了没有」（那是
        GraphQL 的 review thread 才有的字段），所以这里不假装知道 —— 一条意见叫
        过一次就算说到了，人再说一句就是新的 id、就再叫一次。

        `REVIEW_NUDGE_LIMIT` 到顶之后只写卡面、不再叫芝士：见那个常量的说明。

        列 review 一定要问一次 GitHub；列行内评论只在 PR 自己报了有评论时才问 ——
        绝大多数轮次那个数是 0，省下来的就是每张在飞的卡每分钟一次请求。
        """
        if card.pr_number is None:
            return None
        signals = await client.review_signals(
            owner=owner,
            repo=repo,
            number=card.pr_number,
            token=creds.read,
            with_comments=getattr(status, "review_comment_count", 0) > 0,
        )
        if not signals:
            return None
        ledger = pr_signals.NudgeLedger.load(card.nudge_state)
        signature = pr_signals.signature(*sorted(s.id for s in signals))
        capped = ledger.rounds(
            pr_signals.NudgeKind.review
        ) >= pr_signals.REVIEW_NUDGE_LIMIT and not ledger.already_sent(
            pr_signals.NudgeKind.review, signature
        )
        body = "\n".join(s.line() for s in signals)
        asked = sum(1 for s in signals if s.kind == "changes_requested")
        head = "有人在 PR 上要求改动" if asked else "有人在 PR 上留了评审意见"
        if capped:
            return pr_signals.PendingNudge(
                kind=pr_signals.NudgeKind.review,
                signature=signature,
                event=f"PR #{card.pr_number} 的评审意见已来回 "
                f"{pr_signals.REVIEW_NUDGE_LIMIT} 轮，需要人介入",
                content="",
                note=(
                    f"评审意见已自动回流 {pr_signals.REVIEW_NUDGE_LIMIT} 轮仍未收敛，"
                    "需要人来看一眼"
                ),
                note_code=notes.NoteCode.accept_pr_stalled,
                capped=True,
            )
        return pr_signals.PendingNudge(
            kind=pr_signals.NudgeKind.review,
            signature=signature,
            event=f"PR #{card.pr_number} 上{head}",
            content=(
                f"{head}（PR #{card.pr_number}，{card.pr_url}）：\n"
                f"{body}\n\n"
                "请在这个话题的工作区里按意见改并提交（不需要、也没法自己推到 "
                "GitHub），平台会自动把新提交同步到这个 PR。如果你不同意某条意见，"
                "在话题里说清理由，让人来定。"
            ),
            detail=body,
            detail_label="评审意见原文",
            note=f"{head}（{len(signals)} 条）",
            note_code=notes.NoteCode.accept_pr_stalled,
            event_type=EVENT_PR_REVIEW,
        )

    def _conflict_nudge(
        self, *, card: AcceptCard, status
    ) -> pr_signals.PendingNudge | None:
        """「这个 PR 和主分支冲突了」排成一条待发。

        判据是 GitHub 的 `mergeable is False` —— **不是** falsy。它在 GitHub 还没
        算完的时候是 None，而刚推完一次的 PR 每次都会经过那个 None：把 None 当冲
        突，等于每次推送都报一次假冲突。

        没有上限。冲突和 CI 失败一样是客观的：解掉它就消失，所以多叫几轮不会白叫
        （评审意见不是，见 `REVIEW_NUDGE_LIMIT`）。

        叠加 PR（stacked PR）在这里不需要判断：一棵树 = 一个分支 = 一个 PR，而
        `pr_base_branch()` 永远给仓库的默认分支，所以我们开出去的 PR 不可能叠在另
        一个没合的 PR 上。没有这个概念就不造一个出来。
        """
        if getattr(status, "mergeable", None) is not False:
            return None
        # 分支名是 provider 可控的字符串，而它要被贴进芝士的终端。
        branch = pr_signals.sanitize_external(getattr(status, "head_ref", "") or "")
        where = f"分支 {branch} " if branch else ""
        return pr_signals.PendingNudge(
            kind=pr_signals.NudgeKind.conflict,
            # commit 变了就重新算一次：芝士推了一次合并上来，冲突还在，那是新事实。
            signature=pr_signals.signature("conflict", card.pr_head_sha or ""),
            event=f"PR #{card.pr_number} 和主分支冲突了",
            content=(
                f"PR #{card.pr_number}（{card.pr_url}）的{where}和主分支冲突了，"
                "GitHub 现在合不了它。\n"
                "请在这个话题的工作区里把主分支合并进来、解决冲突后提交"
                "（不需要、也没法自己推到 GitHub），平台会自动把新提交同步到这个 "
                "PR，检查会自动重新跑。\n"
                "如果冲突解不动、或者不该由你来解，在话题里说清楚卡在哪。"
            ),
            note=f"PR #{card.pr_number} 和主分支冲突，已叫芝士来解",
            note_code=notes.NoteCode.merge_conflict,
            event_type=EVENT_PR_CONFLICT,
        )

    def _dispatch_nudges(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        pending: list[pr_signals.PendingNudge],
        chat_service,
        runner,
    ) -> None:
        """把这一轮排好的待发，一条不落地发出去。

        三条顺序上的讲究，每一条都是踩出来的：

        - **每条各自去重。** 一条待发的签名和账本上记着的一样就跳过它，**只跳过它
          自己** —— 一条 CI 失败被去重掉，不能顺手把同一轮的评审意见也带走。
        - **卡面只留优先级最高的那一句**（`pr_signals.NOTE_PRIORITY`）。卡面是一
          行，而消息不是：被排掉的那条照样发出去了，只是没占住卡上那一行。
        - **先发，再改内存，最后落盘。** 落盘失败最多让芝士被多叫一次；反过来（先
          落盘再发、中间崩了）会**静默丢掉一条真的通知** —— 账本上写着「说过了」，
          而房间里一个字都没有。多说一次是噪音，少说一次是事故。
        """
        ledger = pr_signals.NudgeLedger.load(card.nudge_state)
        fresh = [p for p in pending if not ledger.already_sent(p.kind, p.signature)]
        if not fresh:
            return
        for nudge in fresh:
            if nudge.capped:
                continue
            runner.submit(
                chat_service,
                topic.id,
                author="system",
                content=nudge.content,
                summon=True,
                nudge_event=nudge.event,
                nudge_meta=notice(
                    nudge.event_type,
                    severity=SEVERITY_ERROR,
                    who=WHO_CHEESE,
                    detail=nudge.detail or None,
                    detail_label=nudge.detail_label or None,
                ),
            )
        loudest = max(fresh, key=lambda n: pr_signals.NOTE_PRIORITY[n.kind])
        if loudest.note:
            notes.record(card, loudest.note_code, loudest.note)
        for nudge in fresh:
            ledger.record(nudge.kind, nudge.signature)
        card.nudge_state = ledger.dump()

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
        # 合完同步本地 base (#718 点名的旧账): the merge happened on GitHub, so
        # the platform's own main is now behind it — pull it down here, in the
        # same act, instead of leaving the workspace stale until the next
        # scheduled sync. Best-effort: the merge is already a fact, so a sync
        # failure annotates the note (and its recurring conflict has its own
        # dispatch, workspace/upstream_conflict.py) rather than failing the
        # accept.
        try:
            from app.domain.agent.github_app import github_app_read_token_for_project
            from app.domain.workspace import service as ws

            token = await github_app_read_token_for_project(
                topic.project_id, self._session
            )
            synced = await asyncio.to_thread(
                ws.sync_upstream, topic.project_id, token=token
            )
            if not synced.get("synced"):
                settled += f"；本地同步待补：{synced.get('reason', '')}"
                if synced.get("conflicts"):
                    settled += "；到项目里点一次「同步上游」，芝士会去解这个冲突"
        except Exception as exc:  # noqa: BLE001 — never fail the accept itself
            settled += f"；本地同步待补：{exc}"
        notes.record(card, None, f"{headline}；{settled}" if headline else settled)
        await self._release_billed_compute(topic)
        # 交付完成 ≠ 话题结束 (#442 decision 1)：话题保持 active，归档由人来做。
        await self._stamp_delivery(card, topic, by=by, at=now)
        await self._session.flush()
        await self._session.refresh(card)
        accepted_line = (
            f"{by} 采纳了这次改动，PR #{card.pr_number} {how}"
            if by
            else f"PR #{card.pr_number} {how}"
        )
        self._notify_merge_result(
            topic,
            accepted_line,
            meta=notice(
                EVENT_ACCEPT_DONE,
                severity=SEVERITY_INFO,
                who=WHO_PLATFORM,
                detail=(
                    f"{card.pr_url}\n"
                    "话题保持活跃，归档由人决定。要再交付一份改动，"
                    "在房间里开一件新的事。"
                ),
                detail_label="交付说明",
            ),
        )

    async def _resolve_forge(self, project_id: uuid.UUID) -> "forge_mod.Forge":
        """Which forge this project's accept goes through — the one place the
        lane is decided (see app.domain.review.forge)."""
        return await forge_mod.resolve(
            project_id=project_id,
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
            "采纳未完成：PR 未能合并",
            meta=notice(
                EVENT_ACCEPT_STOPPED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=(
                    f"{why}。\n"
                    "绑定 GitHub 的项目只通过合并 PR 完成采纳，平台不会绕过 PR "
                    "直推上游。处理后可重试采纳。"
                ),
                detail_label="为什么停下",
            ),
        )
        await self._session.rollback()
        await self._note_outside_accept_txn(
            card_id, notes.NoteCode.accept_pr_stalled, note
        )
        raise ValidationError(f"采纳未完成：PR 未能合并（{why}）。处理后重试采纳")

    async def _stop_accept_no_branch(self, card: AcceptCard, topic: Topic) -> NoReturn:
        """带交付主张的卡开不出 PR，因为这棵树的分支上没有任何提交（2026-09-07
        卡 40be3e1a：改动被推到了别的分支）。旧路径把 `open_pr_for_card` 的 None
        当「纯讨论话题」落进本地合并 no-op——卡标成 accepted，人以为交付完成，而
        改动没有合进任何地方。主张交付却无从交付：停下，把该推哪条分支写在卡上，
        推上后重试采纳。

        Same rollback-first dance as `_stop_accept_pr_unavailable`, same reason:
        the note is written on its own connection and must never queue behind a
        row lock this doomed transaction still holds. Everything the note and
        notification need is read while the instances are live, before the
        rollback expires them."""
        from app.domain.workspace import service as ws

        card_id = card.id
        subject = (card.change_subject or "").strip()
        branch = await asyncio.to_thread(
            lambda: ws.branch_for_tree(ws.tree_for_place(topic.id))
        )
        note = (
            f"{_ACCEPT_NO_BRANCH_PREFIX}（{branch}），开不出能承载"
            f"「{subject}」的 PR。改动可能被提交到了别的分支——"
            f"把提交推上 {branch} 后重试采纳。"
        )
        self._notify_merge_result(
            topic,
            "采纳未完成：树上没有可交付的提交",
            meta=notice(
                EVENT_ACCEPT_STOPPED,
                severity=SEVERITY_ERROR,
                who=WHO_HUMAN,
                detail=(
                    f"这张卡主张交付「{subject}」，但这棵树的分支（{branch}）上"
                    "没有任何提交：开不出 PR，也没有东西可以合并。改动可能在"
                    f"别的分支上；把提交推上 {branch} 后重试采纳。"
                ),
                detail_label="为什么停下",
            ),
        )
        await self._session.rollback()
        await self._note_outside_accept_txn(
            card_id, notes.NoteCode.accept_no_branch, note
        )
        raise ValidationError(
            f"采纳未完成：这棵树的分支（{branch}）上没有任何提交，无法开 PR。"
            f"改动可能在别的分支上；把提交推上 {branch} 后重试采纳"
        )

    async def _publish_pr_for_accept(self, card: AcceptCard, topic: Topic) -> None:
        """无 PR 卡在采纳现场补开 App PR（#296 stage 1 的生产回归修复）.

        A fire-and-forget publish can fail or still be in flight when the
        human clicks — dropping such a card into the local-merge branch
        direct-pushed merge commits to main with no PR at all (dev: c33cfabf,
        8f9b9d94). The repair is to open the App PR HERE and let the click
        merge exactly that PR.

        Synchronous by design: the accept's outcome must depend on the publish
        result, and the click already runs the merge API call inside the
        accept request — one more push plus one create-PR call is the same
        latency class. Racing a still-in-flight fire-and-forget publish is
        benign: the push is force-with-lease of the same branch, `open_pr`
        adopts an already-open PR for the head instead of failing, and
        `record_pr` writes the same numbers this method records.

        On success the PR is recorded on the card DURABLY, outside the accept
        transaction (`pr_publish.record_pr`): if the accept goes on to fail —
        a refusal from the merge call raises ValidationError and rolls this
        request back — the card must keep the PR it now rides, or the next
        attempt would look PR-less again. `open_pr_for_card` can still return
        None (its own not-applicable checks); with the caller pre-checking
        `_github_bound`, in practice that means a topic with no tree branch.
        The card is left untouched, and the CALLER decides what a branchless
        topic means: a legacy card with no delivery claim proceeds into the
        no-op local merge, while a card claiming a change stops the accept
        (`_stop_accept_no_branch` — 2026-09-07 卡 40be3e1a).
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
                "采纳未完成：开不出 PR",
                meta=notice(
                    EVENT_ACCEPT_STOPPED,
                    severity=SEVERITY_ERROR,
                    who=WHO_HUMAN,
                    detail=(
                        f"{reason}。\n"
                        "平台不会在没有 PR 的情况下把改动直推上游。"
                        "处理后可重试采纳。"
                    ),
                    detail_label="为什么停下",
                ),
            )
            # Roll back first, for the same reason as `_stop_accept_pr_
            # unavailable`: the out-of-transaction note writes the card row on
            # its own connection, and it must never be able to queue behind a
            # lock this doomed transaction is still holding.
            await self._session.rollback()
            await self._note_outside_accept_txn(
                card_id, notes.NoteCode.accept_pr_open_failed, note
            )
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
        if card.note_code is notes.NoteCode.pr_open_failed:
            notes.clear(card)  # mirror record_pr's stale-failure-note clearing
        await self._session.flush()

    async def _note_outside_accept_txn(
        self, card_id: uuid.UUID, code: notes.NoteCode, note: str
    ) -> None:
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
                notes.record(fresh, code, note)
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("could not record the PR-open failure on card %s", card_id)

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
        notes.record(card, None, note)

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
        await self._stamp_delivery(card, topic, by=None, at=None)

        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def merge_despite_checks(
        self,
        *,
        card_id: uuid.UUID,
        decided_by: str,
        reason: str = "",
        head_sha: str | None = None,
    ) -> AcceptCard:
        """人明知规则没满足，仍然决定合并——**署名的**显式出口（人工放行）。

        为什么必须有：红着合有时候是对的。CI 基础设施抽风、与本次改动无关的既有
        失败、赶时间的热修——真正不能接受的不是「红着合」，而是**没有人做过这个
        决定**。平台自己的默认因此是拒绝（`_merge_pr_for_accept`：合并态不是
        clean/unstable 就不合），而这条出口是另一半：**显式放行，且放行必须
        签字**。

        平台不重算「这段代码好不好」：它只把 forge 的结论如实呈上，然后让一个
        **具名的人**在上面按手印。它记什么：谁、什么时候、**当时的检查到底是
        什么状态**（现读一次，读不到就如实写读不到——但绝不因此拒绝放行，凭据坏
        了不该把人锁在门外）、以及人自己写的理由。

        谁能点 (#718)：项目分支保护的人工放行名单（`override_handles`，没配置
        = owner + lead）。芝士被 `_forbid_ai` 挡在外面（跟 accept/approve/void
        同一条线），路由也**故意不进** `app/main.py` 的 `_CHEESE_WRITE_PATHS`——
        照 `void` 的先例：不进白名单本身拦不住任何东西（没列进去的写路由压根不
        过那个中间件），真正拦住芝士的是这里的 `_forbid_ai` 加路由上的登录校验。

        放行**放的是规则，不是眼睛**：它跟采纳一样要声明「我看的是哪一版」
        （`_seen_head_or_refresh`）。签字的人要为一段具体的代码背书，屏幕上那版
        已经不在了、或者卡面压根没显示过任何版本的时候，这个签名就落到了别的东西
        上。
        """
        from app.domain.project.protection import branch_protection_of

        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending or card.pr_merged_at is not None:
            raise ValidationError("只有还在等采纳的验收卡能人工放行合并")
        if card.pr_number is None:
            raise ValidationError("这张卡没有可合并的 PR")

        topic = await self._topic_or_404(card.topic_id)
        project = await self._projects.get(topic.project_id)
        self._forbid_ai(project, decided_by, "人工放行合并")

        protection = branch_protection_of(project)
        if protection.override_handles is not None:
            allowed = set(protection.override_handles)
        else:
            allowed = set()
            if project is not None and project.owner_handle:
                allowed.add(project.owner_handle)
            members = await MemberRepository(self._session).list_for_project(
                topic.project_id
            )
            allowed |= {m.user_handle for m in members if m.role == ProjectRole.lead}
        if decided_by not in allowed:
            raise ForbiddenError(
                "只有项目分支保护的人工放行名单里的人能放行"
                "（未配置名单时是项目 owner / 组长）"
            )
        # 骑着 PR 的卡在这里必然带着一个被展示过的 sha：卡面从没显示过 head 的
        # （刚递、轮询器还没镜像）会被刷新并要求重看，而不是拿现读的 head 去合。
        seen_head = await self._seen_head_or_refresh(card, topic, head_sha, "放行")
        assert seen_head is not None  # PR lane; the guard above rules None out

        creds, why = await self._pr_poll_credentials(card, topic)
        if creds is None:
            raise ValidationError(f"暂时拿不到合并这个 PR 用的 GitHub 凭据（{why}）")

        from app.domain.review import github_pr

        owner, repo = await self._pr_repo_of(card, topic)
        client = github_pr.default_client()
        # 留痕用，不是门禁：读一次「此刻检查是什么状态」，读不到也照样放行。
        state: str | None = None
        try:
            state, tail = await client.check_state(
                owner=owner, repo=repo, ref=seen_head, token=creds.read
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
        who = await identity.attribution(self._session, topic, card=card)
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=number,
            token=creds.write,
            commit_title=pr_text.merge_commit_title(card, topic, number),
            commit_message=pr_text.merge_commit_message(topic, decided_by, card, who),
            # 放行合的也是人看到的那个 commit：head 变了 GitHub 409，卡刷新。
            sha=seen_head,
        )
        if result.stale_head:
            await self._refresh_stale_card(card, topic, live_head="", action="放行")
            raise ValidationError(
                f"PR #{number} 的 head 在放行瞬间变了（GitHub 409），卡已刷新 —— "
                "请重新看过再放行"
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
        await self._mark_cards_tree_merged(card)
        card.pr_head_sha = result.sha
        await self._repo.add_approval(card.id, decided_by)
        card.decided_by = decided_by
        card.decided_at = now
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
            f"<@{decided_by}> 人工放行了 PR #{number}",
            meta=notice(
                EVENT_FORCE_MERGED,
                severity=SEVERITY_WARN,
                who=WHO_HUMAN,
                detail=(
                    f"{verdict}。合并时检查状态：{checks_at_merge}{tail_reason}。\n"
                    f"{card.pr_url or ''}"
                ),
                detail_label="放行记录",
            ),
        )
        return card

    async def void(
        self, *, card_id: uuid.UUID, decided_by: str, note: str = ""
    ) -> AcceptCard:
        """人工作废一张未决的验收卡 (pending_gate 孤儿卡出口, 2026-08-11).

        这是**唯一**能把非终态卡强制收尾的人工动作。它存在的理由是 `create_card`
        的互斥：一张卡卡在 `pending_gate` / `conflict` 上，整个话题就再也递不出
        第二张卡，而 accept/reject/revoke/reassign 四条路由对这些状态全部是拒绝
        的——出口是零。

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
            f"{VOIDED_PREFIX}：<@{decided_by}> 作废于状态「{was}」。"
            f"话题可以重新递卡。{reason}"
        )
        if card.pr_number is not None and card.pr_merged_at is None:
            # 跟归档收敛同一条产品判断 (review/archive.py 的模块 docstring)：平台
            # 不拿别人的 token 去关别人名下的 PR。停止跟进 + 留痕。
            headline = (
                f"{VOIDED_PREFIX}：<@{decided_by}> 作废了这张卡，平台已停止跟进 "
                f"PR #{card.pr_number}。PR 未合并、仍开在 GitHub 上，合还是关由人"
                f"决定：{card.pr_url or '(无链接)'}{reason}"
            )
        card.status = AcceptStatus.revoked
        notes.record(
            card, notes.NoteCode.voided, archive.prefix_note(card.note, headline)
        )
        # 只在空的时候补：作废人始终写在 note 里，已有的决议痕迹不覆盖。
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
            content=f"<@{decided_by}> 作废了这张验收卡",
            kind=BlockKind.event,
            meta={
                "platform": True,
                **notice(
                    EVENT_CARD_VOIDED,
                    severity=SEVERITY_INFO,
                    who=WHO_CHEESE,
                    detail=(
                        f"作废于状态「{was}」。这不是驳回，也不代表检查不通过——"
                        f"它只是把卡收尾，好让这个话题能重新递卡。{reason}"
                    ),
                    detail_label="作废说明",
                ),
            },
        )
        return card
