"""Project routes."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.conditional import conditional_json
from app.api.deps import (
    get_chat_service,
    get_profile_registry,
)
from app.api.place import (
    channels_unseen,
    live_rooms_seen,
    rooms_seen,
)
from app.api.response import ok, page, typed_response
from app.api.write_access import CHEESE_ONLY_IN_PROJECT
from app.auth.project_access import may_read_project
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import (
    AuthenticationRequiredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import exception_text, say
from app.domain.agent.chat import ChatService
from app.domain.agent.github_app import (
    github_app_read_token_for_project,
)
from app.domain.agent.liveness import running_tasks
from app.domain.agent.profiles import ProfileRegistry
from app.domain.agent_instance.own import may_chat_with
from app.domain.block.queries import (
    awaiting_an_answer,
    talked_with_agent,
    weeklies_for_project,
)
from app.domain.conversation.services import rooms_of_inner
from app.domain.delivery.addressing import Event, address, hand_of
from app.domain.identity.actor import Actor
from app.domain.membership.services import MemberService
from app.domain.project.forge import follow_github_rename
from app.domain.project.models import Project
from app.domain.project.protection import (
    BRANCH_PROTECTION_KEY,
    GITHUB_UNBOUND,
    BranchProtection,
    apply_branch_protection_update,
    branch_protection_of,
    github_repo_snapshot,
)
from app.domain.project.repositories import (
    ProjectGitInstallationRepository,
    ProjectRepository,
)
from app.domain.project.schemas import (
    ForgeAttributionUpdate,
    GettingStartedOut,
    ProjectCreate,
    ProjectOut,
)
from app.domain.project.services import ProjectService
from app.domain.project_progress import feed as progress_feed
from app.domain.repository.identity import requester_credit_enabled
from app.domain.review.github_pr import parse_github_repo
from app.domain.review.queries import latest_cards_by_task
from app.domain.room_task import naming, presentation
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService
from app.domain.shell.catalog import Shell
from app.domain.shell.schemas import ShellOut
from app.domain.shell.service import effective_shells
from app.domain.task.claims import deadline_for_project
from app.domain.task.services import claim_backs_project
from app.domain.team.services import TeamLabel, team_service
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import user_by_handle

logger = logging.getLogger("cheesex.projects")


router = APIRouter(prefix="/projects", tags=["projects"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
Registry = Annotated[ProfileRegistry, Depends(get_profile_registry)]


def _shelled(project: Project, shell: Shell, team: TeamLabel | None) -> dict:
    """One project's wire payload: the 壳 it runs under, its team's handle and name.

    Neither is a column on `Project`, so neither can come from `model_validate`:
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


async def _project_payloads(db: DbSession, projects: list[Project]) -> list[dict]:
    """Every project's payload, without a query per project."""
    shells = await effective_shells(db, projects)
    teams = await team_service(db).labels_of(
        {p.team_id for p in projects if p.team_id is not None}
    )
    return [
        _shelled(p, shells[p.id], teams.get(p.team_id) if p.team_id else None)
        for p in projects
    ]


async def _project_payload(db: DbSession, project: Project) -> dict:
    return (await _project_payloads(db, [project]))[0]


async def _require_team_membership(db: DbSession, who: Actor, team_id: int) -> None:
    """把项目生在一个团队里的，只能是那个团队的人。

    ``body.team_id`` 以前原样送到 ``ProjectService.create``，于是任何人都能把项目
    种进别人的团队：它会出现在那个团队的 ``GET /projects?team_id=`` 列表里，挂着
    那个团队的名字和调用方自己的 ``owner_handle``。团队域的路由用
    ``require_permission(Action.X, Resource.Y, "teamId")`` 回答同一个问题；
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


async def _require_claim(
    db: DbSession, who: Actor, task_id: int, team_id: int | None
) -> None:
    """用一道题建项目的，得先领了这道题。

    个人题看建项目的这个人有没有领，团队题看项目要挂的那个团队有没有领。等批的申请
    也算领了（领题那一刻就给它开了项目）；被拒绝或退出的不算。
    """
    if who.user_id is None:
        raise AuthenticationRequiredError(say("challengeProjectSignIn"))
    if not await claim_backs_project(
        db, task_id=task_id, user_id=who.user_id, team_id=team_id
    ):
        raise ForbiddenError(say("challengeProjectClaimFirst"))


@router.get("/resource-limits")
async def resource_limits() -> dict:
    """Creation defaults, available before a project exists."""
    return ok({"max_concurrent_turns": settings.max_concurrent_turns})


@router.post("")
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
    if body.team_id is not None:
        await _require_team_membership(db, who, body.team_id)
    # 领了这道题才能用它新建项目：项目带着题目的访问权和资源包，不能凭一个题号拿到。
    if body.external_task_id is not None:
        await _require_claim(db, who, body.external_task_id, body.team_id)
    project = await ProjectService(db).create(
        **body.model_dump(exclude={"id"}),
        project_id=body.id,
        owner_handle=who.handle,
    )
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
        return ok(page(await _project_payloads(db, projects), len(projects)))
    if team_id is not None:
        # A team's project list is not a directory: every row carries the
        # project's `id`, and that id opens its roster, documents and usage. So
        # this answered "which projects does that team have, and what are their
        # ids" to anyone who asked — including callers with no credential at
        # all, while the SAME route without `team_id` was strict.
        await resolver.authorize_team(await resolver.resolve(), team_id=team_id)
        projects = [
            p for p in await service.list_for_team(team_id) if p.archived_at is None
        ]
        total = len(projects)
    else:
        who = await resolver.resolve()
        if who.authenticated:
            projects = await ProjectRepository(db).list_visible_to(
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
    items = await _project_payloads(db, projects)
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
    projects = await ProjectRepository(db).list_for_external_task(task_id)
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
    project = await ProjectRepository(db).get_by_team(team_id)
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
    # Whether this caller runs the project's membership: its owner, or an
    # owner/admin of its team. The members page shows invite/remove by it.
    payload["can_manage_members"] = actor.authenticated and await MemberService(
        db
    ).manages(project_id, actor.handle)
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
    chat: Annotated[ChatService, Depends(get_chat_service)],
    if_none_match: Annotated[str | None, Header()] = None,
    status: Annotated[Literal["open", "closed"] | None, Query()] = None,
    channel: uuid.UUID | None = None,
    whose: Annotated[Literal["mine", "helping", "others"] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    before: str | None = None,
) -> Response:
    """Every thread in the project, each with the card it currently rides on.

    `status` keeps only threads in that state. The sidebar polls for the open
    ones: 128 of the 1,716 on dev (2026-10-08), and every row is rebuilt per
    read.

    With `limit` (and a `status`) it is a page instead, of the threads that
    most recently moved: the closed ones only grow, and the all-tasks page
    scrolls through them rather than reading them whole. A page can be narrowed
    to one `channel` and to `whose` the threads are to the reader (`mine`: they
    own it; `helping`: they are among its contributors; `others`: neither).
    `next` is the cursor to pass as `before` for the page after; `counts` says
    how many there are in all and by whose, for the same status and channel.

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
        )
        tasks = [task for task, _ in page_rows]
        counts = await TaskService(db).counts_in_project(
            project_id,
            rooms=seen,
            status=status,
            channel=channel,
            me=actor.handle or "",
        )
        items = await _project_task_rows(db, chat, actor.handle or "", tasks)
        last = page_rows[-1] if page_rows and has_more else None
        return conditional_json(
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
    tasks = [
        t
        for t in await TaskService(db).list_in_project(project_id)
        if t.room_id in seen and (status is None or t.status == status)
    ]
    items = await _project_task_rows(db, chat, actor.handle or "", tasks)
    return conditional_json(ok(page(items, len(items))), if_none_match)


def _task_cursor(before: str | None) -> tuple[datetime, uuid.UUID] | None:
    """The (moved, id) a page cursor names; a malformed one is refused."""
    if before is None:
        return None
    try:
        moved, task_id = before.split("|", 1)
        return datetime.fromisoformat(moved), uuid.UUID(task_id)
    except ValueError as exc:
        raise UnprocessableEntityError(say("taskPageCursorInvalid")) from exc


async def _project_task_rows(db, chat: ChatService, me: str, tasks: list) -> list[dict]:
    """The project rail's rows for ``tasks``: each with its board cell, its
    card, whether it waits on ``me``, whether it stalled, when it last moved."""
    task_ids = [t.id for t in tasks]
    cards = await latest_cards_by_task(db, task_ids)
    # 哪几条停在一个未回答的提问上 —— 第三次批查询，走只收提问那几行的部分索引
    # （`ix_blocks_questions`）。这是唯一会中断「运行中」的一格，所以不能留
    # 给调用方各自去问。
    asked = await awaiting_an_answer(db, task_ids)
    # 一次，给全部行用同一个「现在几点」：逐行取 now 会让同一批数据里两条本该
    # 一样的活分到不同格子，而那种差别没人再能复现。
    now = datetime.now(UTC)
    # 第四次批查询：哪几条此刻有一轮在跑（`agent.liveness`：不止看内存）。
    running = await running_tasks(chat, db, tasks)
    # 第五次：每条最后一次有人或芝士说话是什么时候 —— 侧栏按它排「最近有动静」。
    last_said = await TaskService(db).last_block_at_for_tasks(task_ids)
    items = []
    for task in tasks:
        card = cards.get(task.id)
        said_at = last_said.get(task.id)
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task,
                card,
                running=task.id in running,
                awaiting_answer=task.id in asked,
            ),
            now=now,
        )
        last_activity = said_at or task.created_at
        is_running = task.id in running
        # 这一条在不在等**看的这个人** —— 和「待办」同一个寻址（`address`），侧栏的
        # 点和任务列表的橙字都读它，不各自从列再推一遍。
        asking = asked.get(task.id)
        awaits = address(
            Event(
                reviewers=(
                    ()
                    if card is None or not card.reviewer_handle
                    else (card.reviewer_handle,)
                ),
                reporter=task.reporter_handle,
                asked=asking,
                # 题上没记着谁（平台发起的轮次）：等它的是这条活的人 —— 和通知、
                # 待办同一个名单（`announce.notify_question`）。题根本不在等，就
                # 没有这一层。
                asked_also=(task.people if task.id in asked and asking is None else ()),
                owner=(
                    task.owner_handle
                    if presentation.owner_acts_on(shown, running=is_running)
                    else None
                ),
            ),
            hand_of(shown.column),
        ).reason_for(me)
        items.append(
            {
                **TaskOut.model_validate(task).model_dump(mode="json"),
                "presentation": shown.as_dict(),
                "awaits_me": awaits is not None,
                "stalled": presentation.is_stalled(
                    shown, running=is_running, last_activity=last_activity, now=now
                ),
                "last_activity_at": last_activity.isoformat(),
                "card": None
                if card is None
                else {
                    "id": str(card.id),
                    "status": str(card.status),
                    "pr_number": card.pr_number,
                    "pr_url": card.pr_url,
                },
            }
        )
    return items


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


@router.get("/{project_id}/getting-started", **typed_response(GettingStartedOut))
async def project_getting_started(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """「开始清单」里要问服务端的那一条：这个人在项目里跟 AI 队友说上过话没有。

    说话可能发生在任务对话里，频道那一栏读不到那里，所以按整个项目问
    （`block.queries.talked_with_agent`）。问的是调用者自己。
    """
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await ProjectService(db).get_or_404(project_id)
    talked = await talked_with_agent(db, project_id, actor.handle)
    return ok(GettingStartedOut(talked=talked).model_dump())


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
    if peer_handle is None:
        project = await ProjectService(db).get_or_404(project_id)
        if not await may_chat_with(
            db, project_id, agent_handle, user_handle, project.settings
        ):
            raise ForbiddenError(say("ownAgentOwnerOnlyChat"))
    topic = await TopicService(db).get_or_create_private(
        project_id=project_id,
        user_handle=user_handle,
        peer_handle=peer_handle,
        agent_handle=agent_handle,
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


@router.get("/{project_id}/forge-attribution")
async def get_forge_attribution(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    project = await ProjectService(db).get_or_404(project_id)
    return ok(
        {
            "requester_coauthor": (project.settings or {}).get(
                "forge_requester_coauthor"
            ),
            "effective": requester_credit_enabled(project.settings or {}),
            "deployment_default": settings.forge_attribution_default,
        }
    )


@router.put("/{project_id}/forge-attribution")
async def save_forge_attribution(
    project_id: uuid.UUID,
    body: ForgeAttributionUpdate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    service = ProjectService(db)
    project = await service.get_or_404(project_id)
    if body.requester_coauthor is None:
        await service.merge_settings(project, {}, remove=("forge_requester_coauthor",))
    else:
        await service.merge_settings(
            project, {"forge_requester_coauthor": body.requester_coauthor}
        )
    return await get_forge_attribution(project_id, db, resolver)


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
    can_manage = True
    try:
        await MemberService(db).require_manager(project_id, actor)
    except ForbiddenError:
        can_manage = False
    return ok(
        {
            "mode": naming.naming_mode(project.settings),
            "available": naming.available(),
            "can_manage": can_manage,
        }
    )


@router.put("/{project_id}/task-naming")
async def set_task_naming(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Switch the project's tasks between automatic and manual naming. Tasks a
    person named keep their names either way."""
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
    mode = body.get("mode")
    if mode not in naming.MODES:
        raise ValidationError(say("modeInvalid", modes=str(list(naming.MODES))))
    service = ProjectService(db)
    project = await service.get_or_404(project_id)
    await service.merge_settings(project, {naming.SETTINGS_KEY: mode})
    return await get_task_naming(project_id, db, resolver)


# --- Project stewardship: who answers for a project ---------------------------


async def require_project_steward(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> str:
    """A verified manager of the project — its owner, or an owner/admin of its
    team — or a 404 that hides it.

    People and agents need the same standing. A credential alone does
    not grant authority to change ownership or the project's checks.

    Returns the caller's handle so a route can record who acted.
    """
    actor = await resolver.resolve(project_id=project_id)
    if not actor.authenticated:
        raise NotFoundError("Project not found")
    handle = actor.handle
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    if await MemberService(db).manages(project_id, handle):
        return handle
    # Conceal project existence from anonymous callers and outsiders.
    raise NotFoundError("Project not found")


async def _project_owner(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> Actor:
    """The project's owner, verified. Archiving is the owner's alone — not a
    team admin's — because it takes the project away from everyone in it.

    Deliberately not ``authorize_project``: that door refuses every write to an
    archived project, and unarchiving is the one write that has to get through.
    A member who is not the owner is told so; anyone else learns nothing.
    """
    actor = await resolver.require_verified_caller()
    project = await ProjectService(db).get_or_404(project_id)
    if actor.authenticated and project.owner_handle == actor.handle:
        return actor
    if actor.authenticated and await may_read_project(
        db, project_id=project_id, handle=actor.handle
    ):
        raise ForbiddenError(say("archiveOwnerOnly"))
    raise NotFoundError("Project not found")


@router.post("/{project_id}/archive")
async def archive_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """归档项目: hide it from everyone's lists and stop everything it runs.
    Nothing is deleted; ``/unarchive`` puts it back."""
    owner = await _project_owner(project_id, db, resolver)
    project = await ProjectService(db).archive(project_id, by=owner.handle)
    await db.commit()
    return ok(await _project_payload(db, project))


@router.post("/{project_id}/unarchive")
async def unarchive_project(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """取消归档: the project and the rooms archived with it come back."""
    owner = await _project_owner(project_id, db, resolver)
    project = await ProjectService(db).unarchive(project_id, by=owner.handle)
    await db.commit()
    return ok(await _project_payload(db, project))


@router.put("/{project_id}/owner")
async def set_project_owner(
    project_id: uuid.UUID,
    body: dict,
    db: DbSession,
    steward: Annotated[str, Depends(require_project_steward)],
) -> dict:
    """Hand the project to someone else — or claim it when nobody holds it.

    ``owner_handle`` had seven readers and exactly one writer: ``POST /projects``.
    A project created without one could therefore never acquire one, and on the
    dogfood project it never did (#315): the field sat NULL for six days while
    every one of those readers quietly fell back to someone else, collapsing
    every owner-level decision onto them and reporting no error anywhere.

    Where the new owner lands, as of 2026-09-27:

    * **On the project's team** — the owner changes and the team does not: the
      project stays the team's. (Unchanged: this was the only case allowed
      before.)
    * **Off it, when the project is the transferor's own** — a project whose
      team is the transferor's *personal* team is nobody else's; there is no
      third party to hand it to and no team it has to stay in, so the project
      MOVES into the recipient's personal team and the transferor is gone from
      it for good. Without the move it would not be a transfer at all: the
      project would sit in the transferor's team, where ``may_read_project``
      still reads it for them and ``MemberService.manages`` — team owner — is
      still true, so the same route could take the owner right back. On this
      branch only, their channel seats are taken away too
      (:meth:`TopicMemberService.revoke_project_seats`): a seat admits you to
      its channel by itself, so without that the giver could keep speaking in
      the channels they had joined, which is 借 again, one floor down. On the
      branch above the giver stays in the project on purpose and their seats
      are left alone.
    * **Off it, otherwise** — refused: the project is some team's, and the only
      people who may own it are that team's.

    (This docstring used to say an outside owner "would own a project they
    cannot open". That was false: ``may_read_project`` grants the project's own
    ``owner_handle`` regardless of team, and ``MemberService.manages``
    short-circuits on the owner too. The real reason is the paragraph above.)
    """
    handle = str(body.get("owner_handle") or "").strip()
    if not handle:
        raise ValidationError(say("ownerHandleRequired"))
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    team_changed_from: int | None = None
    if handle != project.owner_handle:
        user = await user_by_handle(db, handle)
        if user is None:
            raise ValidationError(say("accountNotFound", handle=handle))
        if not await team_service(db).is_team_member(project.team_id, user.id):
            # Not on the project's team. Only a personal project of the
            # transferor's can leave it — see the docstring.
            if not await _project_is_personal_to_its_owner(db, project):
                raise ValidationError(say("transferOutsideTeam", handle=handle))
            team = await team_service(db).ensure_personal_team(user.id)
            team_changed_from = project.team_id
            project.team_id = team.id
    previous = project.owner_handle
    project.owner_handle = handle
    await db.flush()
    if team_changed_from is not None and previous is not None:
        # The project left the transferor's team with them still seated in its
        # channels, and a seat is a reason to reach a channel on its own
        # (`authorize_topic_access` reads it before project membership). Taking
        # the seats away is what makes 「转完你就真的出去了」 true. Only on this
        # branch: on the project's own team the giver stays a member on purpose.
        moved = await TopicMemberService(db).revoke_project_seats(
            project_id=project_id, member_handle=previous
        )
        logger.info(
            "project seats revoked on transfer project=%s from=%s to=%s rooms=%s by=%s",
            project_id,
            previous,
            handle,
            len(moved),
            steward,
        )
    # Ownership moves are rare, consequential, and (per #315) previously
    # impossible — worth a permanent record of who moved it and from what.
    logger.info(
        "project owner set project=%s from=%s to=%s by=%s",
        project_id,
        previous,
        handle,
        steward,
    )
    if team_changed_from is not None:
        logger.info(
            "project team moved project=%s from=%s to=%s by=%s",
            project_id,
            team_changed_from,
            project.team_id,
            steward,
        )
    return ok(await _project_payload(db, project))


async def _project_is_personal_to_its_owner(db: AsyncSession, project: Project) -> bool:
    """Whether the project sits in its own owner's personal team.

    Then its team is a one-person team that exists only to hold this person's
    things, so the project has no other stakeholder to stay with — which is
    what lets a transfer move it. A shared team's project, or an ownerless
    project (no user behind ``owner_handle``), is not.
    """
    if project.owner_handle is None or project.team_id is None:
        return False
    team = await team_service(db).get_team(project.team_id)
    if team is None or team.personal_owner_user_id is None:
        return False
    owner = await user_by_handle(db, project.owner_handle)
    return owner is not None and owner.id == team.personal_owner_user_id


# --- Branch protection (issue #718): 平台侧的分支保护规则 ---------------------


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
    project = await ProjectRepository(db).get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    bp = branch_protection_of(project)

    await follow_github_rename(project_id, db)
    installation = await ProjectGitInstallationRepository(db).get_by_project(project_id)
    if installation is None:
        merge_method, gh = "squash", GITHUB_UNBOUND
    else:
        token: str | None = None
        try:
            token = await github_app_read_token_for_project(project_id, db)
        except Exception:  # noqa: BLE001 — display-only: degrade, never 500
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
    dependencies=[Depends(require_project_steward)],
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
    repo = ProjectRepository(db)
    project = await repo.get(project_id)
    if project is None:
        raise NotFoundError("Project not found")
    # What is written here is read from what is stored (the current rule, merged
    # with the body), so the read has to happen under the lock.
    await repo.lock_settings(project)
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


@router.put("/{project_id}/upstream")
async def set_project_upstream(
    project_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Select a GitHub repository before binding its installation."""
    # deferred-import: tests patch this name on app.domain.project.forge
    from app.domain.project.forge import binding_for_project

    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    await MemberService(db).require_manager(project_id, actor)
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


#: How many of my tasks a channel's row in the sidebar carries under it.
RAIL_TASKS = 5


async def rail_tasks(
    db: AsyncSession,
    chat: ChatService,
    actor: Actor,
    room_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    """What the sidebar hangs under each channel, keyed by channel: how many
    tasks are underway there (`open`, the count after 「全部任务」), and the ones
    of them the caller owns or helps on (`shown`) — owned first, then helped,
    each most recently moved first, at most `RAIL_TASKS`, leaving out one that
    stalled. Every channel in ``room_ids`` gets an entry; the rows are the
    project rail's (`_project_task_rows`).

    The project's open tasks are not read for this: the count is one grouped
    query, and only the caller's own tasks are made into rows."""
    me = actor.handle or ""
    service = TaskService(db)
    counts = await service.underway_counts(room_ids)
    mine = await service.underway_with(room_ids, me) if actor.authenticated else []
    rows = await _project_task_rows(db, chat, me, mine)
    shown: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda row: row["last_activity_at"], reverse=True):
        if not row["stalled"]:
            shown.setdefault(row["room_id"], []).append(row)
    out: dict[uuid.UUID, dict] = {}
    for room in room_ids:
        here = shown.get(str(room), [])
        owned = [row for row in here if row["owner_handle"] == me]
        helped = [row for row in here if row["owner_handle"] != me]
        out[room] = {
            "shown": (owned + helped)[:RAIL_TASKS],
            "open": counts.get(room, 0),
        }
    return out
