"""Project routes."""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Mapped

from app.api.auth import ActorResolverDep
from app.api.conditional import conditional_json
from app.api.deps import (
    get_chat_service,
    get_profile_registry,
)
from app.api.member_settings import change_project_settings, member_setting_actor
from app.api.place import (
    channels_unseen,
    live_rooms_seen,
    rooms_seen,
)
from app.api.request_deps import DbSession
from app.api.response import ok, page, typed_response
from app.api.room_task_rows import incomplete_tasks, project_task_rows
from app.api.write_access import CHEESE_ONLY_IN_PROJECT, ROUTE_DECIDES
from app.auth.project_access import may_read_project
from app.auth.project_roles import Standing, is_owner, may, standing, standings_in
from app.core.config import settings
from app.core.errors import (
    AuthenticationRequiredError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import exception_text, say
from app.domain.agent.chat import ChatService
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent_instance.adapters import (
    default_teammate,
    named_teammate,
    project_team,
)
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.adapters import platform_line
from app.domain.block.queries import weeklies_for_project
from app.domain.conversation.services import rooms_of_inner
from app.domain.delivery.ledger import deliver, settle, settle_card
from app.domain.identity.actor import Actor
from app.domain.living_doc.overview import seed_overview
from app.domain.membership.services import (
    InvitationService,
    MemberService,
    OwnershipService,
)
from app.domain.onboarding.start import start_steps
from app.domain.project.forge import follow_github_rename, starter_push
from app.domain.project.github_app import (
    github_app_read_token_for_project,
    parse_github_repo,
)
from app.domain.project.protection import (
    BRANCH_PROTECTION_KEY,
    GITHUB_UNBOUND,
    BranchProtection,
    apply_branch_protection_update,
    branch_protection_of,
    github_repo_snapshot,
)
from app.domain.project.schemas import (
    CommitCredit,
    CommitCreditUpdate,
    ProjectCreate,
    ProjectOut,
    StartStepsOut,
)
from app.domain.project.services import ProjectService
from app.domain.project_permissions import PERMISSION_ORDER, Permission
from app.domain.project_progress import feed as progress_feed
from app.domain.project_situation import service as situation_service
from app.domain.project_situation.schemas import SituationOut
from app.domain.repository.identity import CREDIT_SETTING, credit_mode
from app.domain.review.archive import close_cards_for_archived_topic
from app.domain.room_task import naming
from app.domain.room_task.archive import close_room_tasks
from app.domain.room_task.services import TaskService
from app.domain.shell.service import effective_shells
from app.domain.shell_catalog import Shell, ShellOut
from app.domain.task import package
from app.domain.task.adapters import TaskTerms
from app.domain.task.claims import deadline_for_project, stages_for_project
from app.domain.team.services import TeamLabel, team_service
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage import referrals
from app.domain.usage.adapters import ComputeCredits

logger = logging.getLogger("cheesex.projects")


router = APIRouter(prefix="/projects", tags=["projects"])

Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]


# The route reads project attributes returned by ProjectService. Keep the route
# independent from the ORM class; the members are declared ``Mapped[...]``
# exactly as the model declares them, because pyright matches a caller's model
# against these *class* members — a plain ``int`` here would not accept
# ``Mapped[int]``. Reading one off an instance still yields the wrapped value.
# Same shape as ``SpaceRow`` in ``routes/spaces.py``.
class ProjectRow(Protocol):
    id: Mapped[uuid.UUID]
    name: Mapped[str]
    slug: Mapped[str]
    owner_handle: Mapped[str | None]
    team_id: Mapped[int]
    external_task_id: Mapped[int | None]


def _shelled(project: ProjectRow, shell: Shell, team: TeamLabel | None) -> dict:
    """One project's wire payload: the 壳 it runs under, its team's handle and name.

    Neither is a column on a project row, so neither can come from `model_validate`:
    the 壳 is resolved from the project's own settings, its 赛题 and that 赛题's
    项目集, and the handle is what links to the team go by. Every route that
    returns a project goes through `_project_payloads`, or the frontend would
    fall back to the default 壳 — or lose the way to the team — on some screens
    and not others.
    """
    return (
        ProjectOut.model_validate(project)
        .model_copy(
            update={
                "shell": ShellOut.of(shell),
                "team_handle": team.handle if team else None,
                "team_name": team.name if team else None,
            }
        )
        .model_dump(mode="json")
    )


async def _project_payloads(
    db: DbSession, projects: Sequence[ProjectRow], *, viewer: str | None = None
) -> list[dict]:
    """Every project's payload, without a query per project. With ``viewer``,
    each also says where they sit in it and what that lets them do (#3830)."""
    shells = await effective_shells(db, projects)
    teams = await team_service(db).labels_of(
        {p.team_id for p in projects if p.team_id is not None}
    )
    payloads = [
        _shelled(p, shells[p.id], teams.get(p.team_id) if p.team_id else None)
        for p in projects
    ]
    if viewer is not None:
        held = await standings_in(db, projects, viewer)
        for p, payload in zip(projects, payloads, strict=True):
            _with_standing(payload, held[p.id])
    return payloads


def _with_standing(payload: dict, held: Standing | None) -> None:
    """Where the caller sits and what that lets them do: every screen shows or
    hides its controls by these, never by working the rules out."""
    payload["my_role"] = held.seat.value if held is not None else None
    payload["my_permissions"] = (
        [p.value for p in PERMISSION_ORDER if p in held.permissions]
        if held is not None
        else []
    )


async def _project_payload(db: DbSession, project: ProjectRow) -> dict:
    return (await _project_payloads(db, [project]))[0]


async def _require_team_membership(db: DbSession, who: Actor, team_id: int) -> None:
    """把项目生在一个团队里的，只能是那个团队的人。

    ``body.team_id`` 以前原样送到 ``ProjectService.create``，于是任何人都能把项目
    种进别人的团队：它会出现在那个团队的 ``GET /projects?team_id=`` 列表里，挂着
    那个团队的名字和调用方自己的 ``owner_handle``。团队域的路由用
    ``needs(TeamPermission.x, on="teamId")`` 回答同一个问题；
    这里团队是**请求体**里给的、不是路径里的，所以同一个判断得显式做一遍。

    和 ``ActorResolver.authorize_team`` 的关键区别：这里**不挂**
    ``authz_enforce_topic_access`` 那个开关。开关管的是「谁能**读**别人项目的对话」，
    一个运维把它关掉，不该顺手把「写进这一行的那支外键」也变成不校验——那不是放宽
    可见性，那是让一次落库失去完整性。

    正常创建路径一点不受影响：前端送来的 ``team_id`` 永远是调用者自己的团队；个人项目
    根本不送 ``team_id``（由 ``_resolve_personal_team_id`` 从所有者推出来）。这里挡住的
    是「点名一个自己不在的团队」。调用方此时一定已登录（``create_project`` 先查了）。
    """
    if who.user_id is None or not await team_service(db).is_team_member(
        team_id, who.user_id
    ):
        raise ForbiddenError(say("teamProjectNotMember"))


@router.get("/resource-limits")
async def resource_limits() -> dict:
    """Creation defaults, available before a project exists."""
    return ok({"max_concurrent_turns": settings.max_concurrent_turns})


@router.post("", dependencies=[ROUTE_DECIDES])
async def create_project(
    body: ProjectCreate, db: DbSession, resolver: ActorResolverDep
) -> dict:
    # Whoever creates a project owns it, and who that is comes from the
    # credential alone. Nothing in the body can name another owner: the request
    # schema has no field for it. There is no "create on someone's behalf" flow
    # to serve — a 赛题 team's workspace is opened by the claim flow
    # (`ProjectService.for_participation`), and handing a project over is
    # `PUT /{id}/owner`, which only the project's steward may call.
    who = await resolver.resolve()
    if not who.authenticated:
        resolver.reject_failed_credential(who)
        raise AuthenticationRequiredError(say("projectCreateSignIn"))
    # 项目归团队 (v4) 的那支外键是**请求体**给的，所以要在这里过一道：问的不是
    # 「这个团队在不在」，是「你是不是这个团队的人」。
    if body.external_task_id is not None:
        raise BadRequestError(say("projectFromTaskByStarting"))
    if body.team_id is not None:
        await _require_team_membership(db, who, body.team_id)
    project = await ProjectService(
        db,
        default_teammate=default_teammate,
        compute_credits=ComputeCredits(db),
        overview=seed_overview,
        task_terms=TaskTerms(db),
    ).create(
        **body.model_dump(exclude={"id", "external_task_id"}),
        project_id=body.id,
        owner_handle=who.handle,
    )
    user_id = getattr(who, "user_id", None)
    if user_id is not None:
        await referrals.on_onboarded(db, user_id)
    # The caller can create a room as soon as this response arrives; the
    # request-scoped dependency commits only after sending the response.
    await db.commit()
    return ok(await _project_payload(db, project))


@router.get("")
async def list_projects(
    db: DbSession,
    resolver: ActorResolverDep,
    team_id: int | None = None,
    archived: bool = False,
) -> dict:
    """One team's 项目 page with ``team_id`` (a personal team also folds in its
    owner's legacy team-less projects); otherwise the caller's OWN projects.
    Archived projects are in neither; ``archived=true`` lists the ones the
    caller owns, which is where an owner goes to bring one back.

    Without ``team_id`` this used to return every project to everyone. That is
    survivable while five exist and wrong as soon as a class does — a student
    would find every other team's work in their sidebar.
    """
    service = ProjectService(db)
    if archived:
        who = await resolver.require_verified_caller()
        projects = await service.list_archived_owned_by(who.handle)
        return ok(
            page(
                await _project_payloads(db, projects, viewer=who.handle),
                len(projects),
            )
        )
    if team_id is not None:
        # A team's project list is not a directory: every row carries the
        # project's `id`, and that id opens its roster, documents and usage. So
        # this answered "which projects does that team have, and what are their
        # ids" to anyone who asked — including callers with no credential at
        # all, while the SAME route without `team_id` was strict.
        viewer = await resolver.resolve()
        await resolver.authorize_team(viewer, team_id=team_id)
        projects = [
            p
            for p in await service.list_for_team(team_id, viewer=viewer.handle)
            if p.archived_at is None
        ]
        total = len(projects)
    else:
        who = await resolver.resolve()
        if who.authenticated:
            projects = await service.list_visible_to(
                handle=who.handle, user_id=who.user_id
            )
            total = len(projects)
        else:
            # 认不出人 ≠ 认识所有人。This used to return `service.list_all()`,
            # on the argument that every 2.0 route is reachable without a
            # credential anyway, so tightening one of them protects nothing.
            # That argument is wrong here, and measurably so: the failure mode
            # is not "an anonymous stranger browses", it is "a LOGGED-IN user's
            # token lapsed". Measured on dev 2026-08-12 — same browser, same
            # second: a valid token returns 1 project, `Bearer not.a.jwt`
            # returns 12, including four other people's. The 2.0 access token
            # lives about three minutes and this fetch layer has no refresh
            # (it is raw `fetch`, so the axios 401 interceptor never sees it),
            # so every user crosses that boundary constantly — which is exactly
            # what「有时候左边栏冒出一堆不是我的项目」was.
            #
            # Whatever else is open, THIS route's meaning without `team_id` is
            # "the caller's OWN projects" — with no caller, the honest answer is
            # none, not all.
            #
            # But "none" is only honest for a caller who presented nothing. The
            # user this bug was actually about DID present a token; it just
            # failed. Answering them 200-with-nothing swaps one undetectable
            # wrong answer for another — measured on dev 2026-08-12: a bearer
            # with a bad signature got `200 n=0` here while the same token got
            # 401 from `topic-unread`. So the sidebar sat empty until some other
            # route happened to 401 and trip the refresh. Say it here instead.
            resolver.reject_failed_credential(who)
            projects, total = [], 0
        viewer = who
    items = await _project_payloads(
        db, projects, viewer=viewer.handle if viewer.authenticated else None
    )
    return ok(page(items, total))


@router.get("/by-task/{task_id}")
async def projects_for_task(
    task_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The 2.0 projects created from this 赛题.

    The 赛题 page uses it to show what already exists rather than offering to
    create a second one blindly — a 赛题 with three teams on it should read as
    three projects, not as a button that quietly makes a fourth.

    Who may ask is decided by the task, not by the project: every row carries a
    project id, a name and an owner handle, and the id is the key to the rest of
    these routes. So the door is ``authorize_task`` — the task's own visibility
    judgment — and the 出题者 (``Task.creator_id``) is the caller this exists
    for.
    """
    actor = await resolver.resolve()
    await resolver.authorize_task(actor, task_id=task_id)
    projects = await ProjectService(db).list_for_external_task(task_id)
    items = await _project_payloads(db, projects)
    return ok(page(items, len(items)))


@router.get("/by-team/{team_id}")
async def project_for_team(
    team_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The AI-workspace project for a 知是 Team (P4). ``data`` is null when the
    team has no project yet — the team page uses this to show/hide its 「AI 工作台」
    entry.

    ``/projects?team_id=`` next door has always answered only to a member of the
    team (``authorize_team``); this route hands out the same project by the same
    guessable id and had no door at all, so the parameter was the guarded way in
    and the path was the way around it.
    """
    actor = await resolver.resolve()
    await resolver.authorize_team(actor, team_id=team_id)
    project = await ProjectService(db).get_by_team(team_id, viewer=actor.handle)
    data = await _project_payload(db, project) if project is not None else None
    return ok(data)


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    payload = await _project_payload(db, project)
    # A project opened by claiming a challenge hands in by its claim's
    # deadline; the overview shows it on the reader's own clock.
    deadline = (
        await deadline_for_project(
            db, task_id=project.external_task_id, team_id=project.team_id
        )
        if project.external_task_id is not None
        else None
    )
    payload["challenge_deadline"] = (
        {"at": deadline.at.isoformat(), "mine": deadline.mine} if deadline else None
    )
    # A staged challenge shows its stages instead: each with its due date and
    # where this project's claim stands on it.
    payload["challenge_stages"] = [
        {
            "index": stage.index,
            "name": stage.name,
            "description": stage.description,
            "due_at": stage.due_at.isoformat() if stage.due_at else None,
            "status": stage.status,
        }
        for stage in (
            await stages_for_project(
                db, task_id=project.external_task_id, team_id=project.team_id
            )
            if project.external_task_id is not None
            else []
        )
    ]
    # The 题目's starter code this repository begins with (#4767): which
    # version, whether it has landed yet (`pending` / `done` / `failed`), and
    # the sentence a failed push is told with. Null without starter code.
    push = await starter_push(project_id, db)
    payload["starter"] = (
        {"revision": push.revision, "state": push.state, "error": push.error}
        if push is not None
        else None
    )
    # The 题目 itself while the project's `题目/` folder is shown — that follows
    # the claim the project came from, the same for everyone in it: where its
    # page and its version history are, and which version it is at now.
    found = await package.shown_task(db, project_id)
    payload["task"] = (
        {
            "id": found[0].id,
            "space_id": found[0].space_id,
            "name": found[0].name,
            "version": found[1],
        }
        if found is not None
        else None
    )
    _with_standing(
        payload,
        await standing(db, project_id, actor.handle) if actor.authenticated else None,
    )
    pending = await OwnershipService(db, deliver=deliver, settle=settle).pending_for(
        project_id
    )
    payload["pending_transfer"] = (
        {"id": str(pending.id), "to_handle": pending.to_handle}
        if pending is not None
        else None
    )
    return ok(payload)


@router.get("/{project_id}/weeklies")
async def list_weeklies(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """周报集 (spec §7.1): project-wide weekly blocks, newest first.

    Each carries the stretch it covers in `meta` (`since`/`until`). A weekly
    report says what happened over a piece of time rather than what the project
    looks like right now, so that window is what tells two of them apart.

    These are the project's own words, and each is traceable to the room it was
    written in via `topic_id`."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    blocks = await weeklies_for_project(db, project_id)
    # Only from the channels the caller reads: a private one's stay in it.
    seen = await rooms_of_inner(
        db, list(await rooms_seen(db, resolver, actor, project_id))
    )
    items = [b.model_dump(mode="json") for b in blocks if b.conversation_id in seen]
    return ok(page(items, len(items)))


@router.get("/{project_id}/tasks", response_model=None)
async def list_project_tasks(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    if_none_match: Annotated[str | None, Header()] = None,
    status: Annotated[Literal["open", "closed"] | None, Query()] = None,
    accepted: Annotated[bool | None, Query()] = None,
    channel: uuid.UUID | None = None,
    whose: Annotated[Literal["mine", "helping", "others"] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    before: str | None = None,
) -> Response:
    """Every thread in the project, each with the card it currently rides on.

    `status` keeps only threads in that state. The sidebar polls for the open
    ones: 128 of the 1,716 on dev (2026-10-08), and every row is rebuilt per
    read. `accepted` independently keeps tasks with an approved delivery
    (`true`) or without one (`false`); omitted, it keeps both. This applies to
    whole lists as well as pages, regardless of whether the task is open.

    With `limit` (and a `status`) it is a page instead, of the threads that
    most recently moved: the closed ones only grow, and the all-tasks page
    scrolls through them rather than reading them whole. A page can be narrowed
    to one `channel` and to `whose` the threads are to the reader (`mine`: they
    own it; `helping`: they are among its contributors; `others`: neither).
    `next` is the cursor to pass as `before` for the page after; `counts` says
    how many there are in all and by whose, for the same status, channel and
    acceptance filter.

    The rail draws rooms and the work inside them, so it needs both halves at
    once. Two round trips, not two per room and one per thread: a project here
    already holds ~170 rooms, and the per-room shape (`/topics/{id}/tasks`)
    would make painting one sidebar 170 requests before a single PR badge.

    `card` is the newest accept card ON THAT THREAD, narrowed to what a rail row
    can show — where the work stands and the PR it rides on. Null for a thread
    that has not been filed for acceptance, which is most of them while the work
    is still going.

    `presentation` is the board's answer for that row — which column it is in
    and the one phrase to print on it — derived here rather than in the client,
    so every client gives the same answer (`room_task/presentation.py`). Two
    round trips still: it is computed from the two batches already fetched.

    条件请求：侧栏每画一次 rail 就要整份清单，而一个项目这里有 ~1373 条活、2 MB
    出头。`ETag` 由整份信封的规范化 JSON 算出，`If-None-Match` 命中就回 304、空
    body —— 切页面时「没有新东西」不再重传这 2 MB。指纹算的是 body，所以任何一行的
    状态、哪张卡、谁在跑变了都会换一个 tag；`stalled` 是唯一会随时间自己翻的一列，
    翻的时候本来就该重画。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    # 四次批查询，各问一个领域。这条路由是拼装的人，所以它把三次窄读和一次服务
    # 调用按固定顺序摆在一起；每一次都走对方领域自己的公开读出口，不去碰别人的
    # repository —— `block` 那边问的是「这个项目的决策/周报」和「哪几条停在提问
    # 上」，`review` 那边问的是「这些活的卡」，`room_task` 那边问的是「这些活」和
    # 「它们各自最后一次说话」。
    # A task is seen where its channel is: a private channel's by its people.
    seen = await rooms_seen(db, resolver, actor, project_id)
    if limit is not None:
        if status is None:
            raise UnprocessableEntityError(say("taskPageNeedsStatus"))
        page_rows, has_more = await TaskService(db).page_in_project(
            project_id,
            rooms=seen,
            status=status,
            channel=channel,
            whose=whose,
            me=actor.handle or "",
            limit=limit,
            before=_task_cursor(before),
            accepted=accepted,
        )
        tasks = [task for task, _ in page_rows]
        counts = await TaskService(db).counts_in_project(
            project_id,
            rooms=seen,
            status=status,
            channel=channel,
            me=actor.handle or "",
            accepted=accepted,
        )
        incomplete = await incomplete_tasks(
            db, [t.id for t in tasks if t.status == "open"]
        )
        items = await project_task_rows(
            db, actor.handle or "", tasks, incomplete_of=incomplete.get
        )
        last = page_rows[-1] if page_rows and has_more else None
        return await conditional_json(
            ok(
                {
                    **page(items, len(items)),
                    "has_more": has_more,
                    "next": f"{last[1].isoformat()}|{last[0].id}" if last else None,
                    "counts": counts,
                }
            ),
            if_none_match,
        )
    tasks = await TaskService(db).list_in_project(
        project_id, accepted=accepted, rooms=seen, status=status
    )
    incomplete = await incomplete_tasks(db, [t.id for t in tasks if t.status == "open"])
    items = await project_task_rows(
        db, actor.handle or "", tasks, incomplete_of=incomplete.get
    )
    return await conditional_json(ok(page(items, len(items))), if_none_match)


def _task_cursor(before: str | None) -> tuple[datetime, uuid.UUID] | None:
    """The (moved, id) a page cursor names; a malformed one is refused."""
    if before is None:
        return None
    try:
        moved, task_id = before.split("|", 1)
        return datetime.fromisoformat(moved), uuid.UUID(task_id)
    except ValueError as exc:
        raise UnprocessableEntityError(say("taskPageCursorInvalid")) from exc


@router.get("/{project_id}/progress")
async def project_progress(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """项目总览的「最近进展」：最近两周这个项目里发生了什么，新的在前。

    每一条都从已有的事实算出来（`project_progress.feed`）；读者看不见的私密频道、
    已经归档的频道里的事不在里面。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    happened = await progress_feed.recent(
        db,
        project_id,
        rooms=await live_rooms_seen(db, resolver, actor, project_id),
        hidden_rooms=await channels_unseen(db, resolver, actor, project_id),
        now=datetime.now(UTC),
    )
    rows = [h.as_dict() for h in happened]
    return ok(page(rows, len(rows)))


@router.get("/{project_id}/situation", **typed_response(SituationOut))
async def project_situation(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
) -> dict:
    """项目总览最上面的「现状」：芝士上一次写下的那几句。

    从不等模型：上次之后有了新进展，就在后台用项目自己的模型重写
    （`project_situation.service` 说什么时候算），写完推一帧 ``situation``。
    整个项目一份，读的只有公开频道里的事，所以项目成员看到的都一样。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    found = await situation_service.read(db, project_id, models=chat)
    return ok(
        SituationOut(
            content=found.content, written_at=found.written_at, writing=found.writing
        ).model_dump(mode="json")
    )


@router.get("/{project_id}/start-steps", **typed_response(StartStepsOut))
async def project_start_steps(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """What the new-user guide needs to know about the caller in this project
    (`app/domain/onboarding/start.py`): handed 芝士 something, got a reply, had a
    delivery accepted."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    steps = await start_steps(db, project_id, actor.handle)
    return ok(StartStepsOut.model_validate(steps, from_attributes=True).model_dump())


@router.post("/{project_id}/memory", dependencies=[CHEESE_ONLY_IN_PROJECT])
async def add_memory(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """写记忆的旧入口 —— 已关闭，而且明说。

    这条端点是 `cheese_remember` 的后端。它写的是旧的条目池（`memory_entries`，
    按 agent 分池）；记忆换成「一条记忆一个 markdown 文件」之后（`MemoryFileStore`），
    那一份不再注入任何地方——它的索引不进提示词、它的正文没有读点。再往后端写只会
    得到一句「已记入」，而这条记忆以后谁都读不到：**静默丢失**。

    所以这里一律拒绝，并在这句话里说清该写哪儿。旧会话（上下文里还留着
    `cheese_remember` 那张工具表）里还在调它的调用方，读到的就是这句。

    参数一个都不看：这条路的授权、作用域、layer 现在都没有意义——它不是
    「写错了」而是「不该往这里写」，答一个「你没权限」只会把人引去要权限。
    """
    raise ValidationError(say("rememberRetired"))


@router.get("/{project_id}/private-chat")
async def get_private_chat(
    project_id: uuid.UUID,
    user_handle: str,
    db: DbSession,
    resolver: ActorResolverDep,
    peer_handle: str | None = None,
    agent_handle: str | None = None,
) -> dict:
    """Get-or-create a 1:1 private chat (spec §1).

    With ``peer_handle`` it is a person-to-person DM between the two humans,
    shared by both regardless of who opens it first. Without, it is the member's
    1:1 with one AI teammate — ``agent_handle`` picks which, defaulting to the
    project's default teammate.
    """
    actor = await resolver.require_verified_caller(project_id=project_id)
    if actor.authenticated:
        await resolver.authorize_project(actor, project_id=project_id)
        participants = {user_handle, peer_handle} - {None}
        if actor.handle not in participants:
            raise ForbiddenError(say("directMessageOwnOnly"))
    if peer_handle is not None:
        # Each of the two takes a seat, and a seat is a door into the project:
        # only people the project already has may sit in its DMs.
        seats = TopicMemberService(db)
        for handle in (user_handle, peer_handle):
            if not await seats.on_project(project_id, handle, team=project_team):
                raise ForbiddenError(say("privateChatProjectMembersOnly"))
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id,
        user_handle=user_handle,
        peer_handle=peer_handle,
        agent_handle=agent_handle,
        named_teammate=named_teammate,
    )
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.get("/{project_id}/forge")
async def get_project_forge(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    # deferred-import: tests patch this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    binding = await binding_for_project(project_id, db)
    return ok(
        {
            "kind": binding.kind
            if binding
            else (project.settings or {}).get("forge_kind", "forgejo"),
            "connected": binding is not None,
            "repo": binding.repo if binding else None,
            "url": binding.url.removesuffix(".git") if binding else None,
        }
    )


@router.get("/{project_id}/commit-credit", **typed_response(CommitCredit))
async def get_commit_credit(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok({"mode": credit_mode(project.settings or {})})


@router.put(
    "/{project_id}/commit-credit",
    dependencies=[ROUTE_DECIDES],
    **typed_response(CommitCredit),
)
async def save_commit_credit(
    project_id: uuid.UUID,
    body: CommitCreditUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await member_setting_actor(resolver, project_id)
    project = await ProjectService(db).get_or_404(project_id)
    await change_project_settings(
        db, project, {CREDIT_SETTING: body.mode}, by=actor.handle
    )
    return await get_commit_credit(project_id, db, resolver)


@router.get("/{project_id}/task-naming")
async def get_task_naming(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """任务命名: ``auto`` (the platform names tasks opened without a title and
    renames them when their direction changes; the default) or ``manual``
    (tasks are named by people). See ``room_task/naming.py``."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok(
        {
            "mode": naming.naming_mode(project.settings),
            "available": naming.available(),
        }
    )


@router.put("/{project_id}/task-naming", dependencies=[ROUTE_DECIDES])
async def set_task_naming(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Switch the project's tasks between automatic and manual naming. Tasks a
    person named keep their names either way. Anyone in the project may
    (``member_settings``)."""
    actor = await member_setting_actor(resolver, project_id)
    mode = body.get("mode")
    if mode not in naming.MODES:
        raise ValidationError(say("modeInvalid", modes=str(list(naming.MODES))))
    project = await ProjectService(db).get_or_404(project_id)
    await change_project_settings(
        db, project, {naming.SETTINGS_KEY: mode}, by=actor.handle
    )
    return await get_task_naming(project_id, db, resolver)


# --- Project stewardship: who answers for a project ---------------------------


async def require_project_settings(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> str:
    """Someone who may change the project's settings, verified — or a 404 that
    hides the project from everyone who is not in it.

    A credential alone does not grant authority to change the project's
    checks: a scoped one is refused like everyone without the permission.

    Returns the caller's handle so a route can record who acted.
    """
    actor = await resolver.resolve(project_id=project_id)
    if not actor.authenticated:
        raise NotFoundError("Project not found")
    if await ProjectService(db).get(project_id) is None:
        raise NotFoundError("Project not found")
    if actor.via != "cheese" and await may(
        db, project_id, actor.handle, Permission.edit_settings
    ):
        return actor.handle
    if await may_read_project(db, project_id=project_id, handle=actor.handle):
        raise ForbiddenError(say("projectPermissionDenied"))
    # Conceal project existence from anonymous callers and outsiders.
    raise NotFoundError("Project not found")


async def _project_owner(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    """The project's owner, verified. Archiving is the owner's alone — not an
    admin's — because it takes the project away from everyone in it.

    Deliberately not ``authorize_project``: that door refuses every write to an
    archived project, and unarchiving is the one write that has to get through.
    A member who is not the owner is told so; anyone else learns nothing.
    """
    actor = await resolver.require_verified_caller()
    project = await ProjectService(db).get_or_404(project_id)
    if actor.authenticated and await is_owner(db, project.id, actor.handle):
        return actor
    if actor.authenticated and await may_read_project(
        db, project_id=project_id, handle=actor.handle
    ):
        raise ForbiddenError(say("archiveOwnerOnly"))
    raise NotFoundError("Project not found")


@router.post("/{project_id}/archive", dependencies=[ROUTE_DECIDES])
async def archive_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """归档项目: hide it from everyone's lists and stop everything it runs.
    Nothing is deleted; ``/unarchive`` puts it back.

    The project's pending invitations are withdrawn here, not inside
    ``ProjectService.archive``: settling the invitee's notification lands on the
    delivery ledger, which sits above both ``project`` and ``membership``. An
    invitation to a project nobody can enter would sit in the invitee's inbox
    with no answer that means anything.
    """
    owner = await _project_owner(project_id, db, resolver)
    project = await ProjectService(
        db,
        settle_card=settle_card,
        cards=close_cards_for_archived_topic,
        close_tasks=close_room_tasks,
        events=platform_line,
    ).archive(project_id, by=owner.handle)
    await InvitationService(
        db, deliver=deliver, settle=settle, team=project_team
    ).revoke_all_pending(project_id)
    await db.commit()
    return ok(await _project_payload(db, project))


@router.post("/{project_id}/unarchive", dependencies=[ROUTE_DECIDES])
async def unarchive_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """取消归档: the project and the rooms archived with it come back."""
    owner = await _project_owner(project_id, db, resolver)
    project = await ProjectService(
        db, sessions=AgentSessionService(db), events=platform_line
    ).unarchive(project_id, by=owner.handle)
    await db.commit()
    return ok(await _project_payload(db, project))


def _branch_protection_payload(bp: BranchProtection) -> dict:
    return {
        "required_checks": [
            {"name": c.name, "paths": list(c.paths)} for c in bp.required_checks
        ],
        "strict": bp.strict,
        "dismiss_stale": bp.dismiss_stale,
        "auto_merge_allowed": bp.auto_merge_allowed,
        # None = unconfigured: the project's owner and leads may override.
        "override_handles": (
            list(bp.override_handles) if bp.override_handles is not None else None
        ),
        "approvals_required": bp.approvals_required,
        "default_reviewer": bp.default_reviewer,
    }


@router.get("/{project_id}/branch-protection")
async def get_branch_protection(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The project's branch-protection rules (issue #718), GitHub 那一页的顺序。

    平台补位 GitHub 判定不了的部分，所以规则存在这里；两块只读附注说明 GitHub
    那一侧的现实：``merge_method``（绑定项目从仓库设置读，未绑定固定 squash）和
    ``github_protection``（GitHub 自己开没开保护 —— 开了的话设置页把同名规则灰
    掉，两处都能改就是两套配置）。GitHub 查询失败一律降级成 unknown，绝不 500。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    bp = branch_protection_of(project)

    await follow_github_rename(project_id, db)
    installation = await ProjectService(db).installation_for_project(project_id)
    if installation is None:
        merge_method, gh = "squash", GITHUB_UNBOUND
    else:
        token: str | None = None
        try:
            token = await github_app_read_token_for_project(project_id, db)
        except Exception:  # display-only: degrade, never 500
            logger.warning(
                "branch-protection: token mint failed project=%s",
                project_id,
                exc_info=True,
            )
        merge_method, gh = await github_repo_snapshot(installation.repo, token)
    return ok(
        {
            **_branch_protection_payload(bp),
            "merge_method": merge_method,
            "github_protection": {
                "enforced": gh.enforced,
                "status": gh.status,
                "detail": gh.detail,
            },
        }
    )


_BRANCH_PROTECTION_KEYS = (
    "required_checks",
    "strict",
    "dismiss_stale",
    "auto_merge_allowed",
    "override_handles",
    "default_reviewer",
)


@router.put(
    "/{project_id}/branch-protection",
    dependencies=[ROUTE_DECIDES, Depends(require_project_settings)],
)
async def set_branch_protection(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Update branch-protection rules. Only the keys present in the body change.

    ``approvals_required`` predates this block and stays at
    ``settings["approvals_required"]`` — read and written here, never moved,
    never dual-written. Who may change review policy is the steward dependency's
    question: the project's owner, or an owner/admin of its team.
    """
    service = ProjectService(db)
    project = await service.get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    # What is written here is read from what is stored (the current rule, merged
    # with the body), so the read has to happen under the lock.
    await service.lock_settings(project)
    new_settings = {**(project.settings or {})}
    if any(key in body for key in _BRANCH_PROTECTION_KEYS):
        try:
            updated = apply_branch_protection_update(
                new_settings.get(BRANCH_PROTECTION_KEY), body
            )
        except ValueError as e:
            raise ValidationError(exception_text(e)) from None
        if updated:
            new_settings[BRANCH_PROTECTION_KEY] = updated
        else:
            new_settings.pop(BRANCH_PROTECTION_KEY, None)  # all defaults again
    if "approvals_required" in body:
        try:
            required = int(body.get("approvals_required") or 0)
        except (TypeError, ValueError):
            raise ValidationError(say("approvalsMustBeInteger")) from None
        if required < 1:
            raise ValidationError(say("approvalsAtLeastOne"))
        new_settings["approvals_required"] = required
    project.settings = new_settings
    await db.flush()
    return ok(_branch_protection_payload(branch_protection_of(project)))


@router.get("/{project_id}/upstream")
async def get_project_upstream(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The GitHub repository selected for the installation flow."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok({"url": (project.settings or {}).get("github_repository_url")})


@router.put("/{project_id}/upstream", dependencies=[ROUTE_DECIDES])
async def set_project_upstream(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Select a GitHub repository before binding its installation."""
    # deferred-import: tests patch this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require(project_id, actor, Permission.edit_settings)
    service = ProjectService(db)
    project = await service.get_or_404(project_id)
    if await binding_for_project(project_id, db) is not None:
        raise ConflictError(say("repoAlreadyConnected"))
    if (project.settings or {}).get("forge_kind") != "github_app":
        raise ConflictError(say("hostedNoGithubSwitch"))
    raw = str(body.get("url") or "").strip()
    parsed = parse_github_repo(raw) if raw else None
    if raw and parsed is None:
        raise ValidationError(say("githubRepoUrlRequired"))
    url = f"https://github.com/{parsed[0]}/{parsed[1]}" if parsed else None
    await service.merge_settings(project, {"github_repository_url": url})
    return ok({"url": url})
