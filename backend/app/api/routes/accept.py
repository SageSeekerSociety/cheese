"""Accept-card / Review routes — the 验收 state machine (spec §4.4, §6.3)."""

import asyncio
import logging
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
from app.domain.review import pr_publish
from app.domain.review.github_pr import (
    GitHubPRClient,
    GitHubPRError,
    parse_github_repo,
)
from app.domain.review.models import AcceptStatus
from app.domain.review.schemas import (
    AcceptCardCreate,
    AcceptDecision,
    ApprovalCreate,
    ForceMergeDecision,
    RejectDecision,
    VoidDecision,
)
from app.domain.review.services import AcceptService
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.accept")

router = APIRouter(prefix="", tags=["accept"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/topics/{topic_id}/accept-card")
async def create_accept_card(
    topic_id: uuid.UUID,
    body: AcceptCardCreate,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    svc = AcceptService(db)
    card = await svc.create_card(
        topic_id=topic_id,
        reviewer_handle=body.reviewer_handle,
        routing_reason=body.routing_reason,
        change_subject=body.change_subject,
        change_body=body.change_body,
    )
    # 采纳即合并 (docs/accept-is-merge.md #296, stage 1): the card is the
    # platform's view of a PR, so filing it opens that PR right away with the
    # App's installation token — no human's personal token, and no platform
    # gate. Fire-and-forget; the POST must not block on the push/open. The old
    # machine-gate dispatch is retired (cards are never born `pending_gate`
    # any more — see AcceptService.create_card).
    if pr_publish.enabled():
        project_id = await svc.project_id_for_topic(topic_id)
        pr_publish.dispatch(
            chat.session_factory,
            card_id=card.id,
            topic_id=topic_id,
            project_id=project_id,
        )
    return ok(await svc.describe(card))


@router.get("/topics/{topic_id}/accept-card")
async def list_accept_cards(topic_id: uuid.UUID, db: DbSession) -> dict:
    svc = AcceptService(db)
    cards, total = await svc.list_for_topic(topic_id)
    return ok(page([await svc.describe(c) for c in cards], total))


@router.get("/topics/{topic_id}/pr-checks")
async def topic_pr_checks(topic_id: uuid.UUID, db: DbSession) -> dict:
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
    try:
        return ok(await _pr_checks_payload(topic_id, db))
    except BaseError:
        raise  # 404 for a topic that does not exist stays a 404
    except Exception as exc:  # noqa: BLE001 — display-only endpoint, see above
        logger.exception("pr-checks read failed for topic %s", topic_id)
        return ok({"available": False, "reason": f"{type(exc).__name__}: {exc}"[:200]})


async def _pr_checks_payload(topic_id: uuid.UUID, db: AsyncSession) -> dict:
    svc = AcceptService(db)
    cards, _ = await svc.list_for_topic(topic_id)
    card = next((c for c in cards if c.pr_number is not None), None)
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
        checks = await client.check_runs(head_sha or ws.branch_for_topic(topic_id))
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
    actor = await resolver.resolve(fallback_handle=body.approver_handle)
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
    actor = await resolver.resolve(fallback_handle=body.decided_by)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能采纳验收卡")
    svc = AcceptService(db)
    card = await svc.accept(card_id=card_id, decided_by=actor.handle)
    if card.status == AcceptStatus.conflict:
        # 冲突不采纳 (spec §6.3): 芝士 first. Materialize the conflicted merge in
        # the topic's workspace, then dispatch a resolve turn. The reviewer
        # retries accept when 芝士 reports done.
        topic_id = card.topic_id
        try:
            files = ws.prepare_conflict_resolution(
                (await AcceptService(db)._topic_or_404(topic_id)).project_id,
                topic_id,
            )
        except Exception:  # noqa: BLE001 — dispatch anyway; the agent can dig
            logger.exception("prepare_conflict_resolution failed for %s", topic_id)
            files = []
        listing = "、".join(files[:15]) or "（见工作区冲突标记）"
        runner.submit(
            chat,
            topic_id,
            author="system",
            content=(
                "采纳这个话题时合并冲突了，暂时没归档。平台已把主分支合进你的工作区，"
                f"冲突标记就在这些文件里：{listing}。请打开这些文件解决所有 "
                "<<<<<<< 冲突标记（保留双方意图，语义化合并，不要机械二选一），"
                "跑相关测试确认没破坏，然后简短汇报解决思路——验收人会重新点采纳。"
            ),
            summon=True,
            # 平台提示统一契约: 一行给房间，冲突文件清单进 meta.detail。detail 给的
            # 是**完整**清单（content 里那份为了可读只列前 15 个），收起来不等于删掉。
            nudge_event=f"采纳时合并冲突，{len(files)} 个文件，芝士在解",
            nudge_meta=notice(
                EVENT_ACCEPT_CONFLICT,
                severity=SEVERITY_WARN,
                who=WHO_CHEESE,
                detail="\n".join(files) or "（见工作区冲突标记）",
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
    actor = await resolver.resolve(fallback_handle=None)
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
    poke it, while a CI failure on the same card DOES summon (`_nudge_pr_fix`).
    Same card, same "去改代码" verdict, opposite behaviour — the difference was
    invisible from the room.

    The wake-up lives here rather than in the service on purpose: `chat`/`runner`
    are request-scoped dependencies the domain layer has no handle on, and the
    conflict branch of `accept_card` right above already does it this way.
    """
    actor = await resolver.resolve(fallback_handle=body.decided_by)
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
    await db.commit()  # the card's new state must be readable by the woken turn
    runner.submit(
        chat,
        topic_id,
        author="system",
        content=(
            f"{decided_by} 驳回了你递的验收卡。{reason_line}\n"
            "话题没归档，工作区还是你的：照着这条理由改，改完重新递卡"
            "（驳回不阻塞重递）。理由看不懂或者你不同意，别默默按自己的理解改 —— "
            "在对话里简短回一句问清楚。"
        ),
        summon=True,
        nudge_event=f"{decided_by} 驳回了验收卡，芝士去改",
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

    这是 `pending_gate` / `conflict` / `pr_open` 唯一的人工出口——那三个状态被
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
    actor = await resolver.resolve(fallback_handle=None)
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
    """人工放行：明知检查没全绿，仍然合并这张卡的 PR (App 采纳等 CI 再合)。

    等 CI 全绿再合之后，「红着合」需要一个出口——因为红着合有时候是对的（CI 基础
    设施抽风、与本次改动无关的既有失败）。不能接受的从来不是红着合，而是**没有人
    做过这个决定**。所以这条路由是**默认拒绝、显式放行**的那一半：平台自己永远
    不走它，人点一次算一次，卡面上留下谁、什么时候、当时检查什么状态、为什么。

    跟 `void` 同一条线：路由**故意不在** `app/main.py` 的 `_CHEESE_WRITE_PATHS`
    里——那是给芝士的白名单，这个动作不给芝士。但"不加白名单"本身拦不住任何东西
    （没列进去的写路由压根不过那个中间件），真正拦住芝士的是这里的登录校验加
    `AcceptService.merge_despite_checks` 里的 `_forbid_ai`。
    """
    actor = await resolver.resolve(fallback_handle=None)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能人工放行合并")
    svc = AcceptService(db)
    card = await svc.merge_despite_checks(
        card_id=card_id, decided_by=actor.handle, reason=body.reason
    )
    return ok(await svc.describe(card))


@router.post("/accept-cards/{card_id}/revoke")
async def revoke_card(
    card_id: uuid.UUID, body: AcceptDecision, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=body.decided_by)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能撤销采纳")
    svc = AcceptService(db)
    card = await svc.revoke(card_id=card_id, decided_by=actor.handle)
    return ok(await svc.describe(card))
