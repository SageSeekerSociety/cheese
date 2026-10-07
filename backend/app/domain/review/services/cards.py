"""The read side of an acceptance card: resolving the room, filing a card,
stamping delivery, the gate lifecycle fields, and rendering (`describe`)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import exception_text, listing, say
from app.domain.library import service as library
from app.domain.project import artifacts
from app.domain.review import (
    archive,
    commit_message,
    merge_state,
    notes,
)
from app.domain.review import forge as forge_mod
from app.domain.review.models import (
    AcceptCard,
    AcceptStatus,
    DeliverableKind,
    GateOutcome,
)
from app.domain.review.schemas import AcceptCardOut
from app.domain.room_task.models import Task, TaskStatus
from app.domain.room_task.place import PlaceResolver
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:  # `github_pr` stays a lazy import at every call site
    pass

from app.domain.review import services as pkg
from app.domain.review.services._shared import (
    _ALEMBIC_VERSIONS_DIR,
    _BLOCKED_BY_CARD_MESSAGES,
    _CARD_BLOCKS_NEW_CARD,
    _MISSING_SUBJECT,
    EVENT_MIGRATION_COLLISION,
    SEVERITY_WARN,
    WHO_HUMAN,
    ReviewerAdmission,
    _no_artifact_action,
    _one_artifact_action,
    _one_deliverable,
    _read_deliverable,
    approvals_required_of,
    notice,
)


async def _topic_or_404(self: pkg.AcceptService, topic_id: uuid.UUID) -> Topic:
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
    self: pkg.AcceptService,
    card: AcceptCard,
    topic: Topic,
    *,
    by: str | None,
    at: datetime | None,
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


async def _card_or_404(self: pkg.AcceptService, card_id: uuid.UUID) -> AcceptCard:
    card = await self._repo.get(card_id)
    if card is None:
        raise NotFoundError("Accept card not found")
    return card


async def create_card(
    self: pkg.AcceptService,
    *,
    topic_id: uuid.UUID,
    task_id: uuid.UUID,
    reviewer_handle: str | None = None,
    routing_reason: str = "",
    change_subject: str | None = None,
    change_body: str | None = None,
    artifact: str | None = None,
    new_artifact: str | None = None,
    about: str | None = None,
    deliver: str | None = None,
    deliver_url: str | None = None,
    admits_reviewer: ReviewerAdmission,
) -> AcceptCard:
    topic = await self._topic_or_404(topic_id)
    task = await TaskService(self._session).require_in_room(topic_id, task_id)
    if topic.status == TopicStatus.archived:
        raise ValidationError(say("topicArchivedNoReview"))
    if task.status != TaskStatus.open or not task.branch_name:
        raise ValidationError(say("taskEndedOrNoBranch"))
    subject = (change_subject or "").strip()
    if not subject:
        raise ValidationError(_MISSING_SUBJECT)
    try:
        subject = commit_message.check_subject(subject)
    except commit_message.InvalidSubject as exc:
        raise ValidationError(exception_text(exc)) from exc
    # 这次交付更新了哪一项产物 (#1085 结论三)。先验参数、后落行：一张递不上
    # 去的卡（分支没提交、已经有一张未决的卡）不该在清单上留下一项。
    #
    # 交出去的是什么，决定了产物还要不要声明 —— 所以先问这一句。合并交出去的
    # 是项目那个仓库，一个项目只有一个，平台自己认得出；只有交文件、交地址才
    # 真的有得选。
    _one_deliverable(deliver, deliver_url)
    hands_over_repository = (
        not (deliver or "").strip() and not (deliver_url or "").strip()
    )
    if hands_over_repository:
        _no_artifact_action(artifact, new_artifact, about)
    else:
        _one_artifact_action(artifact, new_artifact)
    about = artifacts.clean_about(about, subject=subject)
    existing = await self._repo.list_for_task(task.id)
    blocking = next((c for c in existing if c.status in _CARD_BLOCKS_NEW_CARD), None)
    if blocking is not None:
        raise ValidationError(_BLOCKED_BY_CARD_MESSAGES[blocking.status])
    from app.domain.repository.forge_files import ProjectFiles

    comparison = await ProjectFiles(
        self._session, task.project_id, task.id
    ).comparison()
    if not comparison or not comparison.get("total_commits"):
        raise ValidationError(
            say("taskBranchNothingToDeliver", branch=task.branch_name)
        )
    reviewer_handle = await self._reviewer_or_project_default(
        await self._projects.get(topic.project_id),
        reviewer_handle,
        from_work=[task],
    )
    await self._require_reviewer_in_room(topic, reviewer_handle, admits_reviewer)
    # 这一版交出去的那一份，在它还存在的时候读下来 (#1085 结论五)。构建产物只
    # 活在这一轮的工作目录里，采纳时那个目录可能已经不在了 —— 建卡是唯一抓得
    # 住它的时刻。读在声明之前：路径写错这张卡递不上去，而一张递不上去的卡不该
    # 在清单上留下一项。
    handed_over = (deliver or "").strip()
    snapshot = (
        await _read_deliverable(self._session, task.project_id, task.id, handed_over)
        if handed_over
        else None
    )
    is_new = bool((new_artifact or "").strip())
    if hands_over_repository:
        project = await self._projects.get(topic.project_id)
        declared = await artifacts.for_repository(
            self._session,
            project_id=topic.project_id,
            project_name=project.name if project else "项目",
        )
    elif is_new:
        declared = await artifacts.claim(
            self._session,
            project_id=topic.project_id,
            name=new_artifact or "",
            about=about,
            hidden=await artifacts.hidden_from_room(self._session, topic.id),
        )
    else:
        declared = await artifacts.reuse(
            self._session,
            project_id=topic.project_id,
            artifact_id=artifact or "",
            hidden=await artifacts.hidden_from_room(self._session, topic.id),
        )
        await artifacts.describe(self._session, declared, about=about)
    card = await self._repo.add(
        topic_id=topic_id,
        task_id=task.id,
        reviewer_handle=reviewer_handle,
        routing_reason=routing_reason,
        status=AcceptStatus.pending,
        change_subject=subject,
        change_body=change_body or None,
        delivered_task_ids=[task.id],
        artifact_id=declared.id,
        # 和上面那个「要不要声明产物」问的是同一句话，所以答案从同一个地方来：
        # 两处各算一次的话，有一天它们会对不上，而对不上的那一天没有任何报错。
        deliverable_kind=(
            DeliverableKind.merge
            if hands_over_repository
            else DeliverableKind.file
            if snapshot is not None
            else DeliverableKind.link
        ),
        deliverable_name=snapshot[0] if snapshot else None,
        deliverable_url=(deliver_url or "").strip() or None,
    )
    if snapshot is not None:
        await asyncio.to_thread(
            library.write_artifact_snapshot,
            task.project_id,
            card.id,
            snapshot[0],
            snapshot[1],
        )
    card.pr_number, card.pr_url = task.pr_number, task.pr_url
    await self._announce_filed(topic, card, task, artifact=declared.name)
    if is_new:
        await self._announce_new_artifact(topic, declared.name)
    await self._warn_about_a_second_pending_migration(topic, task.id)
    return card


async def _warn_about_a_second_pending_migration(
    self: pkg.AcceptService, topic: Topic, task_id: uuid.UUID
) -> None:
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
    from app.domain.repository.forge_files import ProjectFiles

    async def migrations(task_id: uuid.UUID) -> list[str]:
        try:
            comparison = await ProjectFiles(
                self._session, topic.project_id, task_id
            ).comparison()
            added = [
                entry["filename"]
                for entry in (comparison or {}).get("files", [])
                if entry.get("status") == "added"
            ]
        except Exception:  # noqa: BLE001 — a diagnostic must not break 递卡
            return []
        return [p for p in added if _ALEMBIC_VERSIONS_DIR in p]

    mine = await migrations(task_id)
    if not mine:
        return
    others = await self._repo.list_live_in_project(
        topic.project_id, statuses=_CARD_BLOCKS_NEW_CARD
    )
    collisions = [
        other
        for other in others
        if other.task_id is not None
        and other.task_id != task_id
        and await migrations(other.task_id)
    ]
    if not collisions:
        return
    rooms = listing([], quoted=True)
    for other in collisions:
        sibling = await self._topics.get(other.topic_id)
        rooms.items.append(sibling.title if sibling else str(other.topic_id))
    self._notify_merge_result(
        topic,
        say("migrationCollision"),
        task_id=task_id,
        meta=notice(
            EVENT_MIGRATION_COLLISION,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=say("migrationCollisionDetail", rooms=rooms),
            detail_label=say("labelReason"),
        ),
    )


async def project_id_for_topic(
    self: pkg.AcceptService, topic_id: uuid.UUID
) -> uuid.UUID:
    """The topic's project id — what the PR-publish dispatch needs to resolve
    the App installation and upstream (采纳即合并 #296)."""
    topic = await self._topic_or_404(topic_id)
    return topic.project_id


async def mark_gate_started(
    self: pkg.AcceptService, *, card_id: uuid.UUID
) -> AcceptCard:
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
    self: pkg.AcceptService,
    *,
    card_id: uuid.UUID,
    outcome: GateOutcome,
    output_tail: str,
) -> AcceptCard:
    """Settle a pending_gate card: 绿 → pending (卡片这才递到验收人手上),
    红 → gate_failed (卡片作废，芝士被 nudge 去修), 没跑成 → gate_blocked
    (同样不递出去，但检查对代码没有结论，别说成"未通过")."""
    card = await self._card_or_404(card_id)
    if card.status != AcceptStatus.pending_gate:
        raise ValidationError(say("reviewCheckOnlyWhenWaiting"))
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


async def list_for_topic(
    self: pkg.AcceptService, topic_id: uuid.UUID
) -> tuple[list[AcceptCard], int]:
    cards = await self._repo.list_for_topic(topic_id)
    return cards, len(cards)


async def open_pr_card_ids(
    self: pkg.AcceptService, project_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    """Pending PR cards and returned batches awaiting external merge.

    只回 id 不回对象：调度器一张卡一个事务，跨事务复用 ORM 对象拿到的是过期状态。
    「哪张卡算在等」是本领域的知识，所以判断留在这里，而不是让调度器自己去查
    ``AcceptCardRepository``。

    孤儿卡修复 (2026-08-10) 的那条判据也在这里面：已归档话题上的卡不算——
    跟进它们等于拿 GitHub 凭据去动没人跟的活儿。
    """
    cards = await self._repo.list_awaiting_merge_on_active_topics(project_id)
    return [c.id for c in cards]


async def anybody_still_waiting(
    self: pkg.AcceptService, place_ids: list[uuid.UUID]
) -> bool:
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


async def latest_decision_at(
    self: pkg.AcceptService, place_ids: list[uuid.UUID]
) -> datetime | None:
    """这些地点上最后一张卡是什么时候有结果的 —— None = 从来没有过卡。

    给"卡决议之后留一个重新递卡的窗口"用：驳回的意思是回去改了再来，而归档
    话题递不出新卡，所以窗口从这一刻起算。
    """
    return await self._repo.latest_decision_at(place_ids)


async def reviewer_topic_ids(
    self: pkg.AcceptService, topic_ids: list[uuid.UUID], reviewer_handle: str
) -> dict[uuid.UUID, bool]:
    """{话题: 这上面还有没有一张卡在等这个人} —— 只有点过名给他的话题会出现。

    给话题列表的「与我的相关性」用，一次查完：**在不在 key 里**是「这话题
    点过我的名」（采纳完也还算我的事），**value** 是「现在就等我动手」。
    """
    return await self._repo.reviewer_topic_ids(topic_ids, reviewer_handle)


async def describe(self: pkg.AcceptService, card: AcceptCard) -> dict:
    """AcceptCardOut payload enriched with the vote state (approvals live in
    their own table; the requirement is a project setting)."""
    data = AcceptCardOut.model_validate(card).model_dump(mode="json")
    # 「这条 note 有多严重」是它的状态码算出来的（domain/review/notes.py），
    # 随卡下发。浏览器过去自己按 emoji 开头猜，而那份硬编码列表漏掉了后来加
    # 的 `🌿` 和 `🚪`——两条都是「停住了」，却和「还在等」画成同一个颜色。
    level = notes.note_level(card.note_code, card.note)
    data["note_level"] = level.value if level else None
    # 这次交付更新的是哪一项产物。卡面上要有它：验收的人正在决定这一版要不要
    # 成为《报告》的当前版本，而卡上别的字段一个都没说出这件事。
    # 版本数也按这个房间看得见的数：别的私密频道交付的那几版不算进来。
    declared = (
        await artifacts.summary(
            self._session,
            card.artifact_id,
            hidden=await artifacts.hidden_from_room(self._session, card.topic_id),
        )
        if card.artifact_id is not None
        else None
    )
    data["artifact"] = (
        None
        if declared is None
        else {
            "id": str(declared.id),
            "name": declared.name,
            # 这张卡自己是第几版。`ArtifactSummary.version` 数的是已采纳的卡
            # （`artifacts._claims`），所以还没采纳的这一张要自己加上一版：人
            # 正在决定的是「这一版要不要成为《报告》的当前版本」，卡上写着前
            # 一版的号码等于把他要定的那件事写错。
            "version": declared.version
            + (0 if card.status is AcceptStatus.accepted else 1),
        }
    )
    # 这一版交出去的是什么。卡面上要有它，因为验收的人要审的正是这一份：文件
    # 在递卡那一刻就落下来了，所以他能在点采纳之前打开它。
    data["deliverable"] = (
        None
        if card.deliverable_kind is None
        else {
            "kind": card.deliverable_kind.value,
            "filename": card.deliverable_name,
            "url": card.deliverable_url,
        }
    )
    data["approvals"] = await self._repo.list_approver_handles(card.id)
    topic = await self._topic_or_404(card.topic_id)
    project = await self._projects.get(topic.project_id)
    try:
        forge: forge_mod.Forge | None = await self._resolve_forge(
            topic.project_id, card=card
        )
    except ValidationError:
        # 读一张卡不是挑一条车道。采纳那一侧照旧 fail-closed（#362）：读不出
        # 事实就拒绝，绝不摸黑合一次。但这条读路径上同样的失败过去会把整个
        # 卡列表端点打成 422 —— 一个项目的 git 出问题，所有项目的卡都看不
        # 了。这里改成在卡面上如实说「暂时读不出」，失败一点没被盖住（I19），
        # 只是不再连累别的卡。
        forge = None
    caps = forge.capabilities if forge is not None else None
    # 托管方身份在卡生成的那一刻就在卡上（I23）：这一份是唯一的一份，卡片渲染
    # 「托管方是谁」只从这里取，人点完采纳之后不再补写任何一条 note。
    #
    # 「项目有没有绑外部仓库」不在这里（不变量 I21②）。它是一个能力位，由
    # `PlatformForge` 读去决定卡上说哪句话，说完就已经在 `declaration` 里；
    # 再发一遍，就是把那个布尔摆到产品面前请它自己分叉，而这正是按能力分派
    # 要取消的那件事。
    data["forge"] = (
        {
            "kind": forge.kind.value,
            "reports_checks": caps.reports_checks,
            "hosts_proposals": caps.hosts_proposals,
            "can_write_remote": caps.can_write_remote,
            "pushes_to_external_remote": caps.pushes_to_external_remote,
            "identity": caps.identity.value,
            "declaration": forge.declaration,
        }
        if forge is not None and caps is not None
        else {
            "kind": forge_mod.FORGE_KIND_UNKNOWN,
            # 一位都不敢说是，因为一位都没读出来。界面上每一处「这个托管方能
            # 做什么」的判断因此都收敛到最保守的那一边。
            "reports_checks": False,
            "hosts_proposals": False,
            "can_write_remote": False,
            "pushes_to_external_remote": False,
            "identity": forge_mod.ForgeIdentity.platform.value,
            "declaration": forge_mod.FORGE_UNKNOWN_DECLARATION,
        }
    )
    data["approvals_required"] = approvals_required_of(project)
    # External checks stay unknown until the PR has a mirrored state.
    # Local acceptance has no checks; only a recorded merge conflict blocks it.
    if caps is None:
        # 按钮灰着，理由就写在卡上：后端这会儿真去采纳也会拒（同一个失败），
        # 所以闸门和采纳还是同一条线。
        data["merge_state"] = {
            "state": "unknown",
            "who": "platform",
            "reasons": [
                {
                    "kind": "no_signal",
                    "checks": [],
                    "detail": forge_mod.FORGE_UNKNOWN_DECLARATION,
                }
            ],
            "head_sha": None,
            "checked_at": None,
            "since": None,
        }
    elif caps.reports_checks:
        mirror = card.merge_state if isinstance(card.merge_state, dict) else None
        data["merge_state"] = mirror or {
            "state": "unknown",
            "who": "platform",
            "reasons": [
                {
                    "kind": "no_signal",
                    "checks": [],
                    "detail": (
                        "平台还没看过这个 PR 的合并态"
                        if card.pr_number is not None
                        else "PR 尚未创建，检查状态未知"
                    ),
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
            caps is not None
            and branch_protection_of(project).auto_merge_allowed
            and caps.reports_checks
            and card.pr_number is not None
        ),
        "armed_by": card.auto_merge_armed_by,
        "armed_at": (
            card.auto_merge_armed_at.isoformat() if card.auto_merge_armed_at else None
        ),
    }
    # 快检说了什么。Gates nothing — the PR's real CI decides (#296) — but a
    # red one has to be in front of the person about to accept. A check
    # whose result goes nowhere is a check nobody runs.
    tree = (
        await TaskService(self._session).get(card.task_id)
        if card.task_id is not None
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


async def _enforce_protocol(
    self: pkg.AcceptService, topic: Topic, decided_by: str
) -> None:
    """机构协议 (spec §4.2/§4.4): a 赛题 may require a mentor to accept a
    particular topic, and a project created from that 赛题 accepted the terms.

    Reads them from the 赛题's 项目集 (with the 赛题's own override — #370
    option (c)), reached through `project.external_task_id`. That is the link
    the 赛题 page's 「从这道赛题创建项目」 button writes; the cheesex
    `project_task_links` chain it replaced pointed at a 题目 hierarchy that
    had no way to be created.
    """
    from app.domain.task.models import Task
    from app.domain.task.teaching import protocol_for_task

    project = await self._projects.get(topic.project_id)
    task_id = getattr(project, "external_task_id", None) if project else None
    if not task_id:
        return
    task = await self._session.get(Task, task_id)
    if task is None:
        return
    protocol = await protocol_for_task(self._session, task)
    if not protocol.mentor_required_for(topic.title):
        return
    # The condition asks for someone from outside the team to sign off: an
    # external member of this project.
    from app.domain.membership.roster import roster

    outside = {
        m.handle
        for m in await roster(self._session, topic.project_id)
        if m.source == "external"
    }
    if decided_by not in outside:
        raise ValidationError(say("protocolExternalAccept"))
