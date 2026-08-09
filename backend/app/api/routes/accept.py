"""Accept-card / Review routes — the 验收 state machine (spec §4.4, §6.3)."""

import asyncio
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_turn_runner
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError
from app.domain.agent.chat import ChatService
from app.domain.agent.github_app import github_app_tokens
from app.domain.agent.runtime import TurnRunner
from app.domain.review import gate, pr_publish
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
    RejectDecision,
)
from app.domain.review.services import AcceptService
from app.domain.workspace import service as ws

logger = logging.getLogger("cheesex.accept")

router = APIRouter(prefix="/api", tags=["accept"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/topics/{topic_id}/accept-card")
async def create_accept_card(
    topic_id: uuid.UUID,
    body: AcceptCardCreate,
    db: DbSession,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict:
    svc = AcceptService(db)
    card = await svc.create_card(
        topic_id=topic_id,
        reviewer_handle=body.reviewer_handle,
        routing_reason=body.routing_reason,
    )
    if card.status == AcceptStatus.pending_gate:
        # 机器闸门 (eval C2): run the project's check_command in the topic's
        # workspace in the background — green promotes the card to pending,
        # red fails it and nudges 芝士. The POST itself must not block on a
        # possibly-minutes-long check.
        project_id, command = await svc.gate_plan(topic_id)
        gate.dispatch(
            chat.session_factory,
            chat,
            runner,
            card_id=card.id,
            topic_id=topic_id,
            project_id=project_id,
            command=command or "",
        )
    elif card.status == AcceptStatus.pending and pr_publish.enabled():
        # PR-based accept (#188 §5.1): a card born pending (no gate) gets its
        # PR opened right away. Gated cards get theirs when the gate turns
        # green — see gate._run.
        project_id, _ = await svc.gate_plan(topic_id)
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
    can poll it unconditionally."""
    svc = AcceptService(db)
    cards, _ = await svc.list_for_topic(topic_id)
    card = next((c for c in cards if c.pr_number is not None), None)
    tokens = github_app_tokens()
    if card is None or card.pr_number is None or tokens is None:
        return ok({"available": False})
    topic = await svc._topic_or_404(topic_id)
    upstream = await asyncio.to_thread(ws.get_upstream, topic.project_id)
    parsed = parse_github_repo(upstream)
    if parsed is None:
        return ok({"available": False})
    client = GitHubPRClient(*parsed, tokens)
    try:
        view = await client.pr_view(card.pr_number)
        head_sha = (view.get("head") or {}).get("sha")
        checks = await client.check_runs(head_sha or ws.branch_for_topic(topic_id))
    except GitHubPRError as exc:
        return ok({"available": False, "reason": str(exc)[:200]})
    return ok(
        {
            "available": True,
            "pr_number": card.pr_number,
            "pr_url": card.pr_url,
            "state": "merged" if view.get("merged") else view.get("state"),
            "mergeable": view.get("mergeable"),
            "checks": checks,
        }
    )


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
    runner: Annotated[TurnRunner, Depends(get_turn_runner)],
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
    card_id: uuid.UUID, body: RejectDecision, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(fallback_handle=body.decided_by)
    if not actor.authenticated:
        raise AuthenticationRequiredError("需要登录才能驳回验收卡")
    svc = AcceptService(db)
    card = await svc.reject(card_id=card_id, decided_by=actor.handle, note=body.note)
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
