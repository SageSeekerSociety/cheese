"""Accept-card / Review routes — the 验收 state machine (spec §4.4, §6.3)."""

import asyncio
import logging
import shlex
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_work_runner
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, BaseError
from app.domain.agent.chat import ChatService
from app.domain.agent.github_app import github_app_tokens_for_project
from app.domain.agent.platform_notices import (
    EVENT_ACCEPT_CONFLICT,
    EVENT_CARD_REJECTED,
    SEVERITY_WARN,
    WHO_CHEESE,
    notice,
)
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.identity.actor import Actor
from app.domain.review import pr_publish
from app.domain.review.github_pr import (
    GitHubPRClient,
    GitHubPRError,
    parse_github_repo,
)
from app.domain.review.models import AcceptStatus
from app.domain.review.schemas import (
    AcceptCardCreate,
    AcceptCardDescribe,
    AcceptDecision,
    ApprovalCreate,
    AutoMergeDecision,
    ForceMergeDecision,
    RejectDecision,
    VoidDecision,
)
from app.domain.review.services import AcceptService
from app.domain.room_task.models import TaskStatus
from app.domain.room_task.services import TaskService
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.accept")

router = APIRouter(prefix="", tags=["accept"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _card_actor(
    card_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    """Bind credential scope to the card's room before review authorization."""
    service = AcceptService(db)
    card = await service._card_or_404(card_id)
    topic = await service._topic_or_404(card.topic_id)
    return await resolver.resolve(
        fallback_handle=None, project_id=topic.project_id, topic_id=topic.id
    )


async def _task_actor(
    topic_id: uuid.UUID, task_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    topic = await AcceptService(db)._topic_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, project_id=topic.project_id, topic_id=topic_id
    )
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    if not actor.authenticated:
        raise AuthenticationRequiredError()
    await TaskService(db).require_in_room(topic_id, task_id)
    return actor


@router.post("/topics/{topic_id}/tasks/{task_id}/accept-card")
async def create_accept_card(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    resolver: ActorResolverDep,
    body: AcceptCardCreate,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    await _task_actor(topic_id, task_id, db, resolver)
    svc = AcceptService(db)
    card = await svc.create_card(
        topic_id=topic_id,
        reviewer_handle=body.reviewer_handle,
        routing_reason=body.routing_reason,
        change_subject=body.change_subject,
        change_body=body.change_body,
        task_id=task_id,
    )
    # 采纳即合并 (docs/accept-is-merge.md #296, stage 1): the card is the
    # platform's view of a PR, so filing it opens that PR right away with the
    # App's installation token — no human's personal token, and no platform
    # gate. Fire-and-forget; the POST must not block on the push/open. The old
    # machine-gate dispatch is retired (cards are never born `pending_gate`
    # any more — see AcceptService.create_card).
    await db.commit()
    if pr_publish.enabled():
        project_id = await svc.project_id_for_topic(topic_id)
        pr_publish.dispatch(
            chat.session_factory,
            card_id=card.id,
            topic_id=topic_id,
            project_id=project_id,
        )
    return ok(await svc.describe(card))


@router.post("/topics/{topic_id}/tasks/{task_id}/push-fix")
async def push_fix(
    topic_id: uuid.UUID, task_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _task_actor(topic_id, task_id, db, resolver)
    result = await AcceptService(db).push_fix(task_id)
    await db.commit()
    return ok(result)


@router.post("/topics/{topic_id}/tasks/{task_id}/ready")
async def mark_ready(
    topic_id: uuid.UUID, task_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    await _task_actor(topic_id, task_id, db, resolver)
    result = await AcceptService(db).mark_ready(topic_id, task_id)
    await db.commit()
    return ok(result)


@router.post("/topics/{topic_id}/tasks/{task_id}/accept-card/describe")
async def describe_card(
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    body: AcceptCardDescribe,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """更正待处理验收卡的描述，并把 PR 正文一起改掉。

    评审说「这句话不对」的时候，能改的必须是**卡**，因为卡才是 PR 正文和最终
    squash 正文共同的源头。只改 GitHub 上那份 PR 正文的话，合进 main 的仍然是
    递卡那一刻的快照——#735 就是这么在 `1c298199a` 里留下一句与事实不符的
    历史陈述的。

    署名（`Cheese-Task:`）没有这样的入口，而且不该有：见
    `AcceptService.redescribe` 的 docstring。
    """
    actor = await _task_actor(topic_id, task_id, db, resolver)
    card = await AcceptService(db).redescribe(
        task_id,
        actor=actor.handle,
        change_subject=body.change_subject,
        change_body=body.change_body,
    )
    await db.commit()
    return ok(await AcceptService(db).describe(card))


@router.get("/topics/{topic_id}/accept-card")
async def list_accept_cards(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> dict:
    svc = AcceptService(db)
    cards, total = await svc.list_for_topic(topic_id)
    if task is not None:
        await _task_actor(topic_id, task, db, resolver)
        cards = [card for card in cards if card.task_id == task]
        total = len(cards)
    return ok(page([await svc.describe(c) for c in cards], total))


@router.get("/topics/{topic_id}/pr-checks")
async def topic_pr_checks(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    task: uuid.UUID | None = None,
) -> dict:
    """PR-based accept (#188 §5.1): live PR + check-run state for the newest
    card that rides a PR. Display only — never blocks anything. Answers
    {"available": false} instead of erroring so every caller (card UI, CLI)
    can poll it unconditionally.

    "Never erroring" has to hold for the whole body, not just the two GitHub
    calls it used to guard: this endpoint is polled on a timer, so anything
    that escapes here is not one 500 — it is a 500 every few seconds, each one
    posting a traceback into the room (`report_unhandled_to_room`). That is
    how a GitHub TLS blip turned into a wall of stack traces on 2026-08-17.
    The failure is still logged, and its reason is handed to the caller."""
    if task is not None:
        await _task_actor(topic_id, task, db, resolver)
    try:
        return ok(await _pr_checks_payload(topic_id, db, task_id=task))
    except BaseError:
        raise  # 404 for a topic that does not exist stays a 404
    except Exception as exc:  # noqa: BLE001 — display-only endpoint, see above
        logger.exception("pr-checks read failed for topic %s", topic_id)
        return ok({"available": False, "reason": f"{type(exc).__name__}: {exc}"[:200]})


async def _pr_checks_payload(
    topic_id: uuid.UUID, db: AsyncSession, *, task_id: uuid.UUID | None = None
) -> dict:
    svc = AcceptService(db)
    cards, _ = await svc.list_for_topic(topic_id)
    card = next(
        (
            c
            for c in cards
            if c.pr_number is not None and (task_id is None or c.task_id == task_id)
        ),
        None,
    )
    if card is None or card.pr_number is None:
        return {"available": False}
    topic = await svc._topic_or_404(topic_id)
    # #192: the installation to mint from is resolved per-project, not global.
    tokens = await github_app_tokens_for_project(topic.project_id, db)
    if tokens is None:
        return {"available": False}
    upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return {"available": False}
    client = GitHubPRClient(*parsed, tokens)
    try:
        view = await client.pr_view(card.pr_number)
        head_sha = (view.get("head") or {}).get("sha")
        if not head_sha:
            return {"available": False, "reason": "PR has no head commit"}
        checks = await client.check_runs(head_sha)
    except GitHubPRError as exc:
        return {"available": False, "reason": str(exc)[:200]}
    return {
        "available": True,
        "pr_number": card.pr_number,
        "pr_url": card.pr_url,
        "state": "merged" if view.get("merged") else view.get("state"),
        "mergeable": view.get("mergeable"),
        "checks": checks,
    }


@router.post("/accept-cards/{card_id}/approve")
async def approve_card(
    card_id: uuid.UUID, body: ApprovalCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """主分支保护 (spec §4.4): record one human approval toward the accept."""
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能批准验收卡")
    svc = AcceptService(db)
    card = await svc.approve(card_id=card_id, approver_handle=actor.handle)
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/accept")
async def accept_card(
    card_id: uuid.UUID,
    body: AcceptDecision,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能采纳验收卡")
    svc = AcceptService(db)
    card = await svc.accept(
        card_id=card_id, decided_by=actor.handle, head_sha=body.head_sha
    )
    if card.status == AcceptStatus.conflict:
        # 冲突不采纳 (spec §6.3): 芝士 first. Materialize the conflicted merge in
        # the topic's workspace, then dispatch a resolve turn. The reviewer
        # retries accept when 芝士 reports done.
        topic_id = card.topic_id
        task = await TaskService(db).get(card.task_id) if card.task_id else None
        actionable = task is not None and task.status == TaskStatus.open
        files = None
        preparation = "未取得冲突文件清单，需检查任务分支与目标分支。"
        try:
            if not actionable or card.task_id is None:
                raise ValueError("验收卡没有可执行的任务")
            files = await asyncio.to_thread(
                ws.prepare_conflict_resolution,
                (await AcceptService(db)._topic_or_404(topic_id)).project_id,
                card.task_id,
            )
            preparation = "平台已在任务分支准备冲突提交。"
        except Exception:  # noqa: BLE001 — dispatch anyway; the agent can dig
            logger.exception("prepare_conflict_resolution failed for %s", topic_id)
        listing = "、".join(files[:15]) if files else "未取得冲突文件清单"
        action = "原任务已关闭或不存在；如需继续修改，请由新任务承接。"
        if actionable and task is not None:
            branch = shlex.quote(f"origin/{task.branch_name}")
            action = (
                f'先执行 cd "$(cheese worktree {card.task_id})"，该命令会获取平台分支。'
                "先保留本地未提交的工作，"
                f"再执行 git merge {branch} 合入平台任务分支。"
                "若平台未准备成功，检查并合入任务实际目标分支。"
                "解决冲突标记，保留双方意图，跑相关测试并提交。"
                f"执行 cheese sync --task {card.task_id} 并确认成功。"
                + ("再用 cheese push-fix 更新原 PR。" if card.pr_number else "")
                + "简短汇报解决思路和验证结果，请验收人重新点采纳。"
            )
        runner.submit(
            chat,
            topic_id,
            author="system",
            content=(
                f"采纳任务 {card.task_id} 时合并冲突了。{preparation}"
                f"冲突文件：{listing}。{action}"
            ),
            summon=actionable,
            # 平台提示统一契约: 一行给房间，冲突文件清单进 meta.detail。detail 给的
            # 是**完整**清单（content 里那份为了可读只列前 15 个），收起来不等于删掉。
            nudge_event="采纳时合并冲突"
            + (f"，{len(files)} 个文件" if files is not None else "，未取得文件清单"),
            nudge_meta=notice(
                EVENT_ACCEPT_CONFLICT,
                severity=SEVERITY_WARN,
                who=WHO_CHEESE,
                detail="\n".join(files) if files else preparation,
                detail_label="冲突文件",
            ),
        )
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/reassign")
async def reassign_card(
    card_id: uuid.UUID,
    body: AcceptCardCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """改验收人 (spec §4.4)."""
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能改验收人")
    svc = AcceptService(db)
    card = await svc.reassign(
        card_id=card_id,
        reviewer_handle=body.reviewer_handle,
        reason=body.routing_reason,
    )
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/reject")
async def reject_card(
    card_id: uuid.UUID,
    body: RejectDecision,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """驳回一张验收卡 —— 并且**叫醒芝士去改**。

    `AcceptService.reject` only writes the row: no message, no summon. So a
    rejected topic used to sit there until a human happened to come back and
    poke it, while a CI failure on the same card DOES summon (`_dispatch_nudges`).
    Same card, same "去改代码" verdict, opposite behaviour — the difference was
    invisible from the room.

    The wake-up lives here rather than in the service on purpose: `chat`/`runner`
    are request-scoped dependencies the domain layer has no handle on, and the
    conflict branch of `accept_card` right above already does it this way.
    """
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能驳回验收卡")
    svc = AcceptService(db)
    card = await svc.reject(card_id=card_id, decided_by=actor.handle, note=body.note)
    described = await svc.describe(card)
    topic_id = card.topic_id
    decided_by = card.decided_by or actor.handle
    reason = (card.note or "").strip()
    # 理由必须过去，否则芝士只知道"被退了"、不知道退在哪，只能猜着重做一遍。
    reason_line = f"他给的理由：{reason}" if reason else "他没写理由。"
    task = await TaskService(db).get(card.task_id) if card.task_id else None
    actionable = task is not None and task.status == TaskStatus.open
    action = (
        f'先执行 cd "$(cheese worktree {card.task_id})" 进入任务目录。'
        "照着这条理由改，改完重新递卡（驳回不阻塞重递）。"
        "理由看不懂或者你不同意，在对话里说清分歧，请人决定。"
        if actionable
        else "原任务已关闭或不存在；如需继续修改，请由新任务承接。"
    )
    await db.commit()  # the card's new state must be readable by the woken turn
    runner.submit(
        chat,
        topic_id,
        author="system",
        content=(
            f"{decided_by} 驳回了任务 {card.task_id} 的验收卡。{reason_line}\n{action}"
        ),
        summon=actionable,
        nudge_event=f"{decided_by} 驳回了验收卡"
        + ("，芝士去改" if actionable else "，原任务已结束"),
        nudge_meta=notice(
            EVENT_CARD_REJECTED,
            severity=SEVERITY_WARN,
            who=WHO_CHEESE,
            detail=reason or None,
            detail_label="驳回理由",
        ),
    )
    return ok(described)


@router.post("/accept-cards/{card_id}/void")
async def void_card(
    card_id: uuid.UUID, body: VoidDecision, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """人工作废一张未决的验收卡 (pending_gate 孤儿卡出口, 2026-08-11).

    这是 `pending_gate` / `conflict` 唯一的人工出口——这两个状态被
    accept / reject / revoke / reassign 四条路由全部拒绝，而 `create_card` 又因为
    它们拒绝再建新卡，于是整个话题递不出卡。作废把卡置为终态解开这个死锁。

    **它不是"放行"**：卡进的是终态，不是 `pending`。放行等于让绿勾替一段没被检查
    过的代码背书；作废 + 重递效果一样且安全。

    路由**故意不在** `app/main.py` 的 `_CHEESE_WRITE_PATHS` 里——这是授权类动作，
    给人不给芝士。但"不加白名单"本身拦不住任何东西（没列进去的写路由压根不过那个
    中间件，症状是静默放行而不是 401），真正拦住芝士的是 `AcceptService.void` 里
    的 `_forbid_ai`，见 tests/integration/test_accept_gate_orphan.py 的
    `test_void_requires_a_logged_in_human`。
    """
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能作废验收卡")
    svc = AcceptService(db)
    card = await svc.void(card_id=card_id, decided_by=actor.handle, note=body.note)
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/merge-anyway")
async def merge_card_anyway(
    card_id: uuid.UUID,
    body: ForceMergeDecision,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """人工放行：明知检查没全绿，仍然合并这张卡的 PR。

    平台自己不合 forge 说没过的 PR，所以「红着合」需要一个出口——红着合有时候是
    对的（CI 基础设施抽风、与本次改动无关的既有失败）。不能接受的从来不是红着合，
    而是**没有人做过这个决定**。所以这条路由是**默认拒绝、显式放行**的那一半：
    平台自己永远不走它，人点一次算一次，卡面上留下谁、什么时候、当时检查什么
    状态、为什么。

    跟 `void` 同一条线：路由**故意不在** `app/main.py` 的 `_CHEESE_WRITE_PATHS`
    里——那是给芝士的白名单，这个动作不给芝士。但"不加白名单"本身拦不住任何东西
    （没列进去的写路由压根不过那个中间件），真正拦住芝士的是这里的登录校验加
    `AcceptService.merge_despite_checks` 里的 `_forbid_ai`。
    """
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能人工放行合并")
    svc = AcceptService(db)
    card = await svc.merge_despite_checks(
        card_id=card_id,
        decided_by=actor.handle,
        reason=body.reason,
        head_sha=body.head_sha,
    )
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/auto-merge")
async def set_auto_merge(
    card_id: uuid.UUID,
    body: AutoMergeDecision,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """绿了自动合 (#718)：验收人在 BLOCKED / BEHIND 时布防，规则满足时平台以
    布防人的名义合并；新提交作废采纳（dismiss_stale）同样解除布防。

    授权类动作：actor 只来自 session token，路由**故意不进**
    `_CHEESE_WRITE_PATHS`（同 void / merge-anyway），真正拦住芝士的是登录校验加
    `AcceptService.arm_auto_merge` 里的 `_forbid_ai`。
    """
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能设置自动合并")
    svc = AcceptService(db)
    card = await svc.arm_auto_merge(
        card_id=card_id,
        decided_by=actor.handle,
        enabled=body.enabled,
        head_sha=body.head_sha,
    )
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/revoke")
async def revoke_card(
    card_id: uuid.UUID, body: AcceptDecision, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await _card_actor(card_id, db, resolver)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能撤销采纳")
    svc = AcceptService(db)
    card = await svc.revoke(card_id=card_id, decided_by=actor.handle)
    return ok(await svc.describe(card))
