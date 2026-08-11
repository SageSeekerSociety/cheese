"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.cx_task.repositories import TaskRepository, TaskTemplateRepository
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import AiMode, Project, ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.repositories import AcceptCardRepository
from app.domain.review.schemas import AcceptCardOut
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.webhook import service as webhook_service

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    from app.domain.review.github_pr import PullRequestStatus

logger = logging.getLogger("cheesex.review")

# note 前缀家族 (docs/topics/诊断信息搬上验收卡.md). 多个写入方共用一条 `note`,
# 靠前缀互相识别 —— 所以每个前缀都必须是**具名常量**, 不能靠 "⚠️" 这个共同的
# 表情去粗判 (2026-08-10 修的就是这个: 用 "⚠️" 粗判会让"轮询暂停"冒充"重推
# 失败", 把真正的 CI 失败通知整个吞掉, 见 _nudge_pr_fix).
_REPUSH_FAILED_PREFIX = "⚠️ 平台自动重推失败"
_POLL_PAUSED_PREFIX = "⚠️ 轮询暂停"


def _nudge_note_prefix(stage: str) -> str:
    return f"⚠️ {stage} 检查未通过："


_MERGE_FAILED_MESSAGE = (
    "Acceptance could not complete because the topic could not be merged. "
    "The card remains pending and the topic stays active; repair the workspace "
    "and retry."
)


def _pr_trailers(topic: Topic, decided_by: str) -> str:
    """两阶段采纳 (PR迭代式) 设计要点5: 标清芝士代表谁开的 PR. Requested-by = 话题
    发起人 (Topic.created_by)，Reviewed-by = 批准人 (AcceptCard.decided_by)。"""
    lines = []
    if topic.created_by:
        lines.append(f"Requested-by: {topic.created_by}")
    lines.append(f"Reviewed-by: {decided_by}")
    lines.append(f"Cheese-Topic: {topic.id}")
    return "\n".join(lines)


def _pr_body(topic: Topic, decided_by: str) -> str:
    return (
        f"由芝士代表 {decided_by} 通过 CheeseX 平台两阶段采纳流程开出。\n\n"
        f"{_pr_trailers(topic, decided_by)}"
    )


# The squash commit's title line. GitHub only auto-appends "(#N)" to the
# DEFAULT title (the one derived from the repo's `squash_merge_commit_title`
# setting); an explicit `commit_title` replaces that default wholesale, so the
# PR number has to be appended here or the repo's "… (#213)" history style
# breaks.
def _pr_merge_commit_title(topic: Topic, number: int) -> str:
    title = topic.title if len(topic.title) <= 60 else f"{topic.title[:59]}…"
    return f"采纳 {title} (#{number})"


def _pr_merge_commit_message(topic: Topic, decided_by: str) -> str:
    """The squash commit's BODY. Just the trailers — the "采纳 …" line lives in
    `_pr_merge_commit_title` now, and repeating it here would put it in the
    commit twice."""
    return _pr_trailers(topic, decided_by)


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


# 两阶段采纳: the one degrade that is a KNOWN, PERMANENT limitation instead of a
# failure — this card really does change `.github/workflows/`, and the platform's
# credential has no `workflows` scope, so no retry or sync can ever make the PR
# path work for it. Carried as an exact sentinel string (not a substring match)
# so a combined reason — an existing-PR degrade AND this one — deliberately
# falls back to the ⚠️ wording: that combination does need a human.
_WORKFLOW_SCOPE_DEGRADE_REASON = (
    "本卡改动了 .github/workflows/ 下的文件，平台的 GitHub App 没有 workflows "
    "权限，按已知限制无法走 PR（不是故障）"
)


def _is_known_workflow_scope_degrade(exc: BaseException) -> bool:
    """Did the two-phase push fail because this card genuinely changes workflow
    files? `push_topic_branch_for_github_pr` already absorbs the FIRST such
    rejection (it syncs GitHub's default branch in and pushes exactly once
    more), so a workflow-permission rejection that reaches this caller is the
    SECOND one — a precise signal, no file-tree diff needed. A sync that could
    not complete raises a different message ("…无法同步"), which does not match
    and stays in the ⚠️ bucket, correctly: that one does need a human."""
    from app.domain.workspace.service import _is_workflow_permission_rejection

    return isinstance(exc, ValidationError) and _is_workflow_permission_rejection(
        str(exc)
    )


def _with_pr_degrade_note(base: str, pr_degrade_reason: str) -> str:
    """Prefix a local-merge accept note with WHY the two-phase PR path was
    skipped, so a card that fell back reads as "两阶段采纳没走成，原因是 X；
    然后走了老路径，结果是 Y" instead of looking identical to a topic that
    was never eligible for the PR path at all. No-op when the PR path never
    even attempted a degrade for this accept (`pr_degrade_reason` empty).

    Two shapes, so a reader can tell 正常 from 需要处理 at a glance: the known
    workflow-scope limitation gets a calm ℹ️ sentence and no git output (the
    300-char rejection tail is pure noise for a card whose whole point is that
    it edits workflow files), everything else keeps the ⚠️ + raw-error form."""
    if not pr_degrade_reason:
        return base
    if pr_degrade_reason == _WORKFLOW_SCOPE_DEGRADE_REASON:
        prefix = f"ℹ️ {pr_degrade_reason}"
    else:
        prefix = f"⚠️ 未走 PR 采纳（{pr_degrade_reason}）"
    return (f"{prefix}；{base}" if base else prefix)[:2000]


# 递卡互斥 (2026-08-10): a topic may have at most one card that is still
# "live" — awaiting a decision, mid-delivery, or blocked mid-accept. Each gets
# its own message because the way OUT differs (改验收人 / 等交付 / 解冲突).
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
}
_CARD_BLOCKS_NEW_CARD = tuple(_BLOCKED_BY_CARD_MESSAGES)


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
    """机器闸门 (eval C2): the project's configured check command, or None."""
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

    async def create_card(
        self,
        *,
        topic_id: uuid.UUID,
        reviewer_handle: str,
        routing_reason: str = "",
    ) -> AcceptCard:
        topic = await self._topic_or_404(topic_id)
        # 采纳是一次性交付 (spec §6.3): a frozen topic can't be re-submitted.
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
        # Every non-terminal status blocks now; `gate_failed` deliberately does
        # not (a red gate voids the card, and re-递卡 after fixing IS the flow).
        existing = await self._repo.list_for_topic(topic_id)
        blocking = next(
            (c for c in existing if c.status in _CARD_BLOCKS_NEW_CARD), None
        )
        if blocking is not None:
            raise ValidationError(_BLOCKED_BY_CARD_MESSAGES[blocking.status])
        # 机器闸门 (spec §4.4/§9, eval C2): with a check_command configured the
        # card is born pending_gate; the platform runs the check in the topic's
        # workspace and only a green result promotes it to pending. The check
        # runs in the background (it can take minutes) — see review/gate.py.
        project = await self._projects.get(topic.project_id)
        status = (
            AcceptStatus.pending_gate
            if check_command_of(project)
            else AcceptStatus.pending
        )
        return await self._repo.add(
            topic_id=topic_id,
            reviewer_handle=reviewer_handle,
            routing_reason=routing_reason,
            status=status,
        )

    async def gate_plan(self, topic_id: uuid.UUID) -> tuple[uuid.UUID, str | None]:
        """(project_id, check_command) for a topic — what the gate should run."""
        topic = await self._topic_or_404(topic_id)
        project = await self._projects.get(topic.project_id)
        return topic.project_id, check_command_of(project)

    async def finish_gate(
        self, *, card_id: uuid.UUID, passed: bool, output_tail: str
    ) -> AcceptCard:
        """Settle a pending_gate card: green → pending (卡片这才递到验收人手上),
        red → gate_failed (卡片作废，芝士被 nudge 去修)."""
        card = await self._card_or_404(card_id)
        if card.status != AcceptStatus.pending_gate:
            raise ValidationError("只有等待检查的验收卡能记录检查结果")
        card.gate_output = output_tail
        if passed:
            card.status = AcceptStatus.pending
            card.gate_passed_at = datetime.now(UTC)
        else:
            card.status = AcceptStatus.gate_failed
        await self._session.flush()
        await self._session.refresh(card)
        return card

    async def list_for_topic(self, topic_id: uuid.UUID) -> tuple[list[AcceptCard], int]:
        cards = await self._repo.list_for_topic(topic_id)
        return cards, len(cards)

    async def describe(self, card: AcceptCard) -> dict:
        """AcceptCardOut payload enriched with the vote state (approvals live in
        their own table; the requirement is a project setting)."""
        data = AcceptCardOut.model_validate(card).model_dump(mode="json")
        data["approvals"] = await self._repo.list_approver_handles(card.id)
        topic = await self._topics.get(card.topic_id)
        project = (
            await self._projects.get(topic.project_id) if topic is not None else None
        )
        data["approvals_required"] = approvals_required_of(project)
        return data

    async def _enforce_protocol(self, topic: Topic, decided_by: str) -> None:
        """Task Template conditions (spec §4.2/§4.4): if a linked template
        requires a topic like this to be accepted by a mentor, enforce it."""
        links = await self._projects.list_links(topic.project_id)
        if not links:
            return
        tasks = TaskRepository(self._session)
        templates = TaskTemplateRepository(self._session)
        conditions: list[dict] = []
        for link in links:
            task = await tasks.get(link.task_id)
            if task is None:
                continue
            tmpl = await templates.get(task.template_id)
            if tmpl is not None:
                conditions.extend(tmpl.conditions or [])
        # A condition applies only when its required_topic is non-empty AND
        # matches this topic. An empty required_topic must NOT match every topic
        # (that would force mentor review on the whole project).
        needs_mentor = any(
            c.get("reviewer_role") == "mentor"
            and (c.get("required_topic") or "").strip()
            and c["required_topic"].strip() in topic.title
            for c in conditions
        )
        if not needs_mentor:
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
        vote for) its own work — a human must. Autonomous mode allows it."""
        if (
            project is not None
            and project.ai_mode == AiMode.collaborative
            and handle == "cheese"
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

        Fire-and-forget (asyncio.create_task, mirroring workspace/service.py's
        watch_dogfood_push): post_with_retries can sleep up to 35s across its
        retries, and the accepter's HTTP response must not wait on a room
        notification succeeding — only on the merge itself."""
        asyncio.get_running_loop().create_task(
            webhook_service.post_with_retries(
                async_session_factory,
                project_id=topic.project_id,
                topic_id=topic.id,
                content=content,
                source="accept",
            )
        )

    async def accept(self, *, card_id: uuid.UUID, decided_by: str) -> AcceptCard:
        card = await self._card_or_404(card_id)
        # 机器闸门 (eval C2): the card isn't in the reviewer's hands yet / died.
        if card.status == AcceptStatus.pending_gate:
            raise ValidationError("平台检查还在进行中，检查通过后才能采纳")
        if card.status == AcceptStatus.gate_failed:
            raise ValidationError("平台检查未通过，等芝士修复后重新递卡")
        # pending → first attempt; conflict → retry after 芝士 resolved.
        if card.status not in (AcceptStatus.pending, AcceptStatus.conflict):
            raise ValidationError("验收卡已处理，不能重复验收")
        # 递给某个具体的人 (spec §4.4): only the routed reviewer may accept —
        # decided_by is the caller's verified actor handle, never body-trusted.
        if decided_by != card.reviewer_handle:
            raise ForbiddenError("你不是这张验收卡指定的验收人，无权采纳")

        topic = await self._topic_or_404(card.topic_id)
        # 采纳一次性 (spec §6.3): can't re-accept an already-archived topic.
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

        # PR-based accept (#188 §5.1): a card that ALREADY rides a real PR
        # (published fire-and-forget by pr_publish.py when the card turned
        # pending, behind settings.accept_via_pr — off by default) is accepted
        # by merging THAT PR via the API, never by opening a second one. Falls
        # through to the local path when GitHub is unreachable (availability
        # must never regress) — the merge commit landing on main closes the
        # PR anyway.
        pr_degrade_reason = ""
        if card.pr_number is not None:
            settled, existing_pr_degrade_reason = await self._accept_via_pr(
                card, topic, decided_by
            )
            if settled is not None:
                return settled
            pr_degrade_reason = existing_pr_degrade_reason

        # 两阶段采纳 (PR迭代式, 2026-08-09): no PR yet — try opening a NEW one via
        # the approver's own connected GitHub token. Any missing prerequisite
        # (no connected token / no connected repo) or any GitHub-side failure
        # (push/API) degrades to the old direct-merge path below — a normal
        # degrade, never an accept failure (拍板 decision 2). Resolving the
        # prerequisites themselves must degrade the same way: a DB hiccup here
        # is exactly as "mechanism unavailable" as a missing token.
        #
        # `pr_degrade_reason` makes WHY visible (this card's whole reason for
        # existing): every path below that falls through to the local-merge
        # branch sets it to a human-readable, secret-free explanation, and it
        # gets prefixed onto card.note further down so "looks like account
        # not connected" and "账号连了但密文坏了" are no longer
        # indistinguishable in the UI.
        try:
            pr_prereqs, two_phase_degrade_reason = await self._resolve_pr_prerequisites(
                topic, decided_by
            )
        except Exception as exc:  # noqa: BLE001 — degrade, don't fail the accept
            logger.warning(
                "could not resolve PR prerequisites for topic=%s, degrading to "
                "direct merge: %s",
                topic.id,
                exc,
            )
            pr_prereqs = None
            two_phase_degrade_reason = f"检查 PR 前提条件时出错（{type(exc).__name__}）"
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
            except Exception as exc:  # noqa: BLE001 — degrade, don't fail the accept
                logger.warning(
                    "PR-based accept unavailable for topic=%s, degrading to "
                    "direct merge: %s",
                    topic.id,
                    exc,
                )
                if _is_known_workflow_scope_degrade(exc):
                    # Known limitation, not a failure — say so plainly and drop
                    # the raw git rejection entirely (see the sentinel above).
                    two_phase_degrade_reason = _WORKFLOW_SCOPE_DEGRADE_REASON
                else:
                    # exc is either GitHubPrError (GitHub's own response body,
                    # capped at 300 chars) or a ValidationError from a git push
                    # failure (the token travels via an env-var credential
                    # helper, never argv/URL — see _token_push_env — so git's
                    # stderr can't contain it either); safe to surface verbatim,
                    # same as the existing push_back() failure note below.
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

        # Topic is done → free its long-lived sandbox container (it would be
        # recreated on demand if the archived topic is ever resumed).
        try:
            ws.stop_topic_container(topic.id)
        except Exception:  # noqa: BLE001 — best effort, never fatal
            pass

        # 采纳即归档 (spec §6.3).
        topic.status = TopicStatus.archived
        topic.accepted_by = decided_by
        topic.accepted_at = now
        topic.archived_at = now

        await self._session.flush()
        await self._session.refresh(card)
        success_msg = f"✅ 话题已被 {decided_by} 采纳并合并。"
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
            ws.snapshot_worktree(project_id, topic_id, "两阶段采纳 CI 轮询前快照")
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

    async def _repush_if_local_head_moved(
        self, *, card: AcceptCard, topic: Topic, owner: str, repo: str, token: str
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
        if local_head is None or local_head == card.pr_head_sha:
            return False
        try:
            pushed = await asyncio.to_thread(
                ws.push_topic_branch_for_github_pr,
                topic.project_id,
                topic.id,
                owner=owner,
                repo=repo,
                remote_branch=github_pr.pr_branch_name(topic.id),
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
        pr = await client.open_pull_request(
            owner=owner,
            repo=repo,
            head=remote_branch,
            base=base,
            title=f"[cheesex] {topic.title}"[:250],
            body=_pr_body(topic, decided_by),
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

        from app.domain.oauth.services import (
            get_github_user_token_for_handle_with_reason,
        )

        token, reason = await get_github_user_token_for_handle_with_reason(
            self._session, card.decided_by or ""
        )
        if not token:
            logger.warning(
                "pr_open card %s has no usable GitHub token anymore (%s); "
                "skipping this poll (will retry next tick)",
                card.id,
                reason,
            )
            # Without this the card just sits at `pr_open` forever and looks
            # identical to "CI still running" — no signal anyone's token died.
            if not card.note.startswith(_POLL_PAUSED_PREFIX):
                card.note = (
                    f"{_POLL_PAUSED_PREFIX}（下一轮还会重试）："
                    f"{_describe_token_unavailable(reason)}"
                )[:2000]
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
            if card.pr_merged_at is None:
                await self._advance_pr_checks(
                    card=card,
                    topic=topic,
                    owner=owner,
                    repo=repo,
                    token=token,
                    client=client,
                    chat_service=chat_service,
                    runner=runner,
                )
            else:
                await self._advance_deploy_checks(
                    card=card,
                    topic=topic,
                    owner=owner,
                    repo=repo,
                    token=token,
                    client=client,
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
        token: str,
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
            owner=owner, repo=repo, number=number, token=token
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
            card=card, topic=topic, owner=owner, repo=repo, token=token
        )

        # Only re-read the head when the push above actually moved it;
        # otherwise `status` was fetched moments ago and says the same thing.
        # Keeps the steady-state cost at one GET /pulls/{n} per tick, same as
        # before the merged-check was added.
        live_head = (
            await client.pull_request_head_sha(
                owner=owner, repo=repo, number=number, token=token
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
            owner=owner, repo=repo, ref=card.pr_head_sha, token=token
        )
        if state == "pending":
            return
        if state == "failure":
            self._nudge_pr_fix(
                card=card,
                topic=topic,
                tail=tail,
                stage="CI",
                chat_service=chat_service,
                runner=runner,
            )
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
            token=token,
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
        result = await client.merge_pull_request(
            owner=owner,
            repo=repo,
            number=card.pr_number,
            token=token,
            commit_title=_pr_merge_commit_title(topic, number),
            commit_message=_pr_merge_commit_message(topic, card.decided_by or ""),
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
        merge_sha = result.sha
        card.pr_merged_at = datetime.now(UTC)
        card.pr_head_sha = merge_sha  # now tracking the merge commit (stage 2)
        card.note = f"PR #{card.pr_number} 检查全绿，已自动合并，等部署也成功后才归档。"
        await self._session.flush()
        self._notify_merge_result(
            topic,
            f"✅ PR #{card.pr_number} 的检查全绿，已自动合并。"
            "等部署也成功后话题才会归档。",
        )

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

    def _note_needs_human(self, *, card: AcceptCard, topic: Topic, reason: str) -> None:
        """One of the three exceptions fired: say so on the card and in the
        room, and stop — never merge.

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
            "需要人来定：自己在 GitHub 上合并这个 PR，或者撤销这次采纳。"
        )[:2000]
        if card.note == note:
            return  # already said once — the 60s poll must not repeat it
        card.note = note
        logger.warning("card %s: auto-merge withheld — %s", card.id, reason)
        self._notify_merge_result(
            topic,
            f"✋ PR #{card.pr_number} 的检查没有拦住它，"
            f"但平台不会自动合并：{reason}。\n"
            f"这是「人类授权动作前移」的安全阀之一：{card.decided_by} 当初授权的是"
            "另一份改动，机器不替他把这一份也签下去。需要人来定：自己在 GitHub 上"
            f"合并，或者撤销这次采纳。\n{card.pr_url}",
        )

    async def _advance_deploy_checks(
        self,
        *,
        card: AcceptCard,
        topic: Topic,
        owner: str,
        repo: str,
        token: str,
        client,
    ) -> None:
        """Stage 2 (2026-08-09 拍板): the PR merged — now wait for the deploy
        workflow it triggered before the topic is allowed to archive."""
        state, tail = await client.workflow_run_state(
            owner=owner,
            repo=repo,
            workflow_file=settings.accept_deploy_workflow_file,
            head_sha=card.pr_head_sha,
            token=token,
        )
        if state == "pending":
            return
        if state == "failure":
            # wangchangxin 建议的默认值，评估后采纳为最终方案：topic 保持
            # active，复用卡5 webhook 通知房间，不自动重试，交给人判断。
            if not card.note.startswith("❌"):
                card.note = f"❌ 部署失败：{tail}"[:2000]
                await self._session.flush()
                self._notify_merge_result(
                    topic,
                    f"❌ PR #{card.pr_number} 已合并，但触发的部署失败：{tail}\n"
                    "话题保持 active，需要人判断下一步（不会自动重试）。",
                )
            return

        await self._finish_pr_accept(card=card, topic=topic)

    async def _settle_external_merge(
        self, *, card: AcceptCard, topic: Topic, status: "PullRequestStatus"
    ) -> None:
        """Someone merged the PR on GitHub themselves (人工放行, another bot,
        the merge queue). Book it exactly like our own successful merge —
        `pr_merged_at` + head moved to the merge commit — so the card falls
        through to stage 2 and the deploy gate on the next tick. The only
        差别 from the platform-merged path is where the sha and the timestamp
        come from.

        Deliberately does NOT archive here: 拍板 2026-08-09 says merge alone
        never archives, and an externally merged PR triggers the same deploy
        workflow ours does.
        """
        merge_sha = status.merge_commit_sha
        if not merge_sha:
            # Never settle onto a sha we don't actually have. Stage 2 looks
            # the deploy run up BY `pr_head_sha`; pointing it at the PR branch
            # head (or anything else invented here) means the card waits for a
            # deploy run that will never exist. Staying in stage 1 costs one
            # more poll; guessing costs the card forever.
            note = (
                f"⚠️ PR #{card.pr_number} 已在 GitHub 合并，但 GitHub 没返回合并提交 "
                "sha，无法确认要等哪次部署（下一轮还会重试）"
            )[:2000]
            if card.note != note:
                card.note = note
                await self._session.flush()
                logger.warning(
                    "card %s: PR #%s reports merged with no merge_commit_sha",
                    card.id,
                    card.pr_number,
                )
            return

        card.pr_merged_at = status.merged_at or datetime.now(UTC)
        card.pr_head_sha = merge_sha  # now tracking the merge commit (stage 2)
        card.note = (
            f"PR #{card.pr_number} 已在 GitHub 侧被人工合并，等部署也成功后才归档。"
        )
        await self._session.flush()
        self._notify_merge_result(
            topic,
            f"✅ PR #{card.pr_number} 已在 GitHub 上被人工合并（不是平台合的）。"
            "等部署也成功后话题才会归档。",
        )

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
    ) -> None:
        # Dedup, precisely (2026-08-10). This used to be `startswith("⚠️")`,
        # which treats the whole ⚠️ family as "already nudged" — so a
        # `⚠️ 轮询暂停` note left behind by a dead token silently swallowed
        # every subsequent CI failure: no message, no note, no trace, and the
        # only escape (pr_head_sha moving) needs a human to push first. Two
        # separate reasons to stay quiet, spelled out:
        #   1. we already nudged for THIS stage on this commit — don't spam;
        #   2. 重推失败 outranks a CI failure and must not be overwritten —
        #      it means 芝士's fix never reached GitHub, so the red CI on
        #      record is stale (docs/topics/诊断信息搬上验收卡.md, 优先级说明).
        if card.note.startswith(_nudge_note_prefix(stage)) or card.note.startswith(
            _REPUSH_FAILED_PREFIX
        ):
            return
        card.note = f"{_nudge_note_prefix(stage)}{tail}"[:2000]
        runner.submit(
            chat_service,
            topic.id,
            author="system",
            content=(
                f"PR #{card.pr_number}（{card.pr_url}）的{stage}检查没通过：\n"
                f"```\n{tail[:1500]}\n```\n"
                "请在这个话题的工作区里修复问题并提交（不需要、也没法自己推到 "
                "GitHub），平台会自动把新提交同步到这个 PR，检查会自动重新跑；"
                "转绿后平台会自动合并 PR。"
            ),
            summon=True,
        )

    async def _finish_pr_accept(self, *, card: AcceptCard, topic: Topic) -> None:
        from app.domain.workspace import service as ws

        now = datetime.now(UTC)
        card.status = AcceptStatus.accepted
        card.note = f"PR #{card.pr_number} 已合并且部署成功：{card.pr_url}"
        try:
            ws.stop_topic_container(topic.id)
        except Exception:  # noqa: BLE001 — best effort, never fatal
            pass
        topic.status = TopicStatus.archived
        topic.accepted_by = card.decided_by
        topic.accepted_at = now
        topic.archived_at = now
        await self._session.flush()
        await self._session.refresh(card)
        self._notify_merge_result(
            topic,
            f"✅ 话题已被 {card.decided_by} 采纳：PR #{card.pr_number} 合并且部署成功，"
            f"话题归档。\n{card.pr_url}",
        )

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
            await client.merge_pr(
                number,
                title=f"采纳 {branch} → {view.get('base', {}).get('ref', 'main')} "
                f"(#{number})",
                message=f"验收人：{decided_by}\n\n{card.routing_reason}".strip(),
            )
        except GitHubPRMergeBlocked:
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
            card, topic, decided_by, note=f"已通过 PR #{number} 合并到上游"
        )
        return settled, ""

    async def _settle_pr_accept(
        self, card: AcceptCard, topic: Topic, decided_by: str, *, note: str
    ) -> AcceptCard:
        """Post-merge bookkeeping shared by the PR path: sync the platform's
        main down from upstream (the merge happened THERE), then archive."""
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

        try:
            ws.stop_topic_container(topic.id)
        except Exception:  # noqa: BLE001 — best effort, never fatal
            pass

        # 采纳即归档 (spec §6.3).
        topic.status = TopicStatus.archived
        topic.accepted_by = decided_by
        topic.accepted_at = now
        topic.archived_at = now

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

        # Un-archive the topic: back to active, clear accept/archive markers.
        topic.status = TopicStatus.active
        topic.accepted_by = None
        topic.accepted_at = None
        topic.archived_at = None

        await self._session.flush()
        await self._session.refresh(card)
        return card
