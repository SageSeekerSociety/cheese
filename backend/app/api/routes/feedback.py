"""反馈中心 —— 平台级的收件箱，不属于任何项目。

为什么不在 `/topics/{id}/feedback` 下面：反馈是**平台**的功能。一条反馈可能来自
某个话题，也可能来自一个 harness 在沙箱里撞到的墙 —— 后者根本没有话题可挂。挂在
话题下面等于要求每条反馈先找到一个话题，而找不到话题恰恰是它要报的那类问题。

为什么用 `ActorResolver` 而不是 `require_auth_user`：这里的调用者有两族身份 ——
带 user id 的主干 token，和只有 handle 的 cheesex 会话 token（`user_id=None`，
在数字身份那一层被当成访客）。要 `require_auth_user` 就会把第二种挡在门外，而
harness 正是用第二种在敲门。`/awaiting-me` 是同一个先例。

访客能读公开列表，写操作要身份。私密条目的可见性并集（管理员 ∪ 提交者本人 ∪ 提出它
的那个房间当时的成员、且今天还读得到那个房间）在 `services.FeedbackService.may_see`，
不在这一层 —— 路由拿不到判断权，就不会漏。

agent 的凭据只认一个房间，不带房间的 `/feedback/*` 请求会被拒（「This credential is
restricted to one room」）。所以读列表、读详情、读评论和领取都收 `?topic=<房间>`：在那个
房间里认人（`_in_room`），读的那几条再要求房间属于做平台本身的项目（`_reader`）。

Every route here that writes commits before it answers. ``get_db`` commits in
its teardown, which FastAPI runs after the response has been sent, so the read a
page makes right after a support or a comment can still find the old row — and
put the old count back on screen.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, BadRequestError
from app.core.sentences import say
from app.domain.admin.services import AdminService
from app.domain.authz import policy
from app.domain.feedback import services as feedback_services
from app.domain.feedback.models import (
    Feedback,
    FeedbackKind,
    FeedbackPriority,
    FeedbackStatus,
    FeedbackVisibility,
)
from app.domain.feedback.paging import THREAD_PAGE
from app.domain.feedback.schemas import (
    CommentCreate,
    CommentLikeOut,
    FeedbackCard,
    FeedbackCounts,
    FeedbackCreate,
    FeedbackMeta,
    SupportOut,
)
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/feedback", tags=["feedback"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_feedback_service(db: DbSession) -> feedback_services.FeedbackService:
    return feedback_services.FeedbackService(db)


FeedbackServiceDep = Annotated[
    feedback_services.FeedbackService, Depends(get_feedback_service)
]


async def _cards(
    service: feedback_services.FeedbackService,
    rows: list[Feedback],
    *,
    handle: str | None,
) -> list[dict]:
    """Turn a page of rows into JSON.

    Every counter and every face on the page costs one query for the page, never
    one per row: support counts, comment counts, who already supported, last
    activity, and the authors' avatars — the last of those is two queries
    (handles → users → profiles) because the avatar hangs off the profile and
    not off the user. The `hot` tab is *sorted by* the number a per-row fetch
    would be re-reading twenty times.
    """
    ids = [r.id for r in rows]
    supports = await service.support_counts(ids)
    comments = await service.comment_counts(ids)
    supported = await service.supported_ids(ids, handle)
    activity = await service.last_activity(ids)
    avatars = await service.chosen_avatars([r.author_handle for r in rows])
    return [
        FeedbackCard.from_row(
            row,
            supports=supports.get(row.id, 0),
            comments=comments.get(row.id, 0),
            supported=row.id in supported,
            avatars=avatars,
            last_activity_at=activity.get(row.id),
        ).model_dump(mode="json")
        for row in rows
    ]


async def _detail(
    service: feedback_services.FeedbackService,
    row: Feedback,
    *,
    handle: str | None,
    is_admin: bool,
    room_project_id: uuid.UUID | None = None,
) -> dict:
    view = await service.detail_of(
        row, handle=handle, is_admin=is_admin, room_project_id=room_project_id
    )
    return view.model_dump(mode="json")


async def _in_room(
    db: DbSession, resolver: ActorResolver, topic: uuid.UUID | None
) -> tuple[Actor, uuid.UUID | None]:
    """Who is calling, and the project of the room they call from, if any.

    A person in the feedback center names no room. An agent must: its credential
    is bound to one room, and `resolve()` without that room refuses it. With
    `topic`, the caller is resolved **in** that room and authorized for it —
    `enforce=True`, because the room's project is then evidence (whether the
    caller may read here, whether it may claim), so a room the caller is not in
    must not count.
    """
    if topic is None:
        return await resolver.resolve(), None
    place = await TopicService(db).place_or_404(topic)
    who = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        who, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return who, place.project_id


async def _reader(
    db: DbSession,
    service: feedback_services.FeedbackService,
    resolver: ActorResolver,
    topic: uuid.UUID | None,
) -> tuple[Actor, uuid.UUID | None]:
    """The caller of a read route; from a room, only from a platform room.

    Reading the feedback center from a room is reading it as that room's work,
    and what the center holds is work on the platform itself, so the room must
    be in a project that may claim (`FeedbackService.require_platform_room`).
    What the caller then sees is `may_see` for its own handle. An agent never
    gets the admin arm (`_is_admin` demotes any agent binding), so it sees what
    a project member who is not a feedback admin sees: the public reports, the
    ones it filed, and the private ones filed in a room it was in and can still
    read.
    """
    who, room_project = await _in_room(db, resolver, topic)
    if room_project is not None:
        await service.require_platform_room(room_project)
    return who, room_project


async def _is_admin(
    db: DbSession,
    service: feedback_services.FeedbackService,
    who: Actor,
) -> bool:
    """这个人在**这个面上**算不算反馈管理员 —— 全路由只有这一处答案。

    名单回答的是「是不是反馈管理员」（`FeedbackService.is_admin`），它不是全部：
    `authz.policy.refuse_management_action` 还要问两件名单问不到的事 —— 凭证是不是
    作用域设备凭证（`via == "cheese"`），以及这条 handle 带不带 agent 绑定。管理面
    （`/admin/*` 的 `require_platform_admin`）两个问题都问，而这里以前只问名单，
    于是同一条 handle 在两个面上得到两个答案：`/admin/feedback` 回 403，而
    `/feedback/{id}` 让它读私密条目、拿到内部管理备注、删掉别人的反馈 —— 全仓库唯一
    的「删整条反馈」路由恰好长在公共面上。

    **这里只降级、不拒绝**，和 `require_platform_admin` 不一样，因为两件事不是一回事：
    管理面的每个 endpoint 都只是一个管理动作，agent 来敲门就没有别的意思；而
    `/feedback/*` 是公共面，一个 agent 是来读**它自己提过的那条**的合法读者，整个
    403 掉会砸掉正常用法。所以问题不是「放不放它进来」，是「把管理员那一支给它吗」——
    不给。理由仍是 `refuse_management_action` 那两句，只是在这里读作「这条 handle 带
    agent 绑定（或拿着作用域凭证），于是它在这个面上不是管理员」。
    """
    if not who.authenticated or not who.handle:
        return False
    if not await service.is_admin(who.handle):
        return False
    return await _may_manage(db, who)


async def _may_manage(db: DbSession, who: Actor) -> bool:
    refusal = await policy.refuse_management_action(
        who, carries_agent_binding=IdentityService(db).is_agent
    )
    return refusal is None


async def _is_platform_admin(db: DbSession, who: Actor) -> bool:
    """The other roster, for the admin shell's other sections. Same demotion."""
    if not who.authenticated or not who.handle:
        return False
    if not await AdminService(db).is_admin(who.handle):
        return False
    return await _may_manage(db, who)


@router.get("/meta")
async def get_feedback_meta(
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """词表。客户端不再硬编码有哪些状态、按什么顺序流动。

    颜色留在前端（那是视觉决定），**有哪些取值**从前端搬到这里 —— 因为加一个
    状态是后端改一处的事，而前端的常量表要靠发版才能跟上。
    """
    who = await resolver.resolve()
    meta = FeedbackMeta(
        kinds=list(FeedbackKind),
        statuses=list(FeedbackStatus),
        priorities=list(FeedbackPriority),
        visibilities=list(FeedbackVisibility),
        status_ladder=list(feedback_services.STATUS_LADDER),
        tabs=list(feedback_services.PUBLIC_TABS),
        admin_tabs=list(feedback_services.ADMIN_TABS),
        hot_score=feedback_services.repo.HOT_SCORE,
        hot_half_life_days=feedback_services.repo.HOT_HALF_LIFE_DAYS,
        hot_min_items=feedback_services.repo.HOT_MIN_ITEMS,
        is_admin=await _is_admin(db, service, who),
        is_platform_admin=await _is_platform_admin(db, who),
    )
    return ok(meta.model_dump(mode="json"))


@router.get("/counts")
async def get_feedback_counts(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """铃铛和标签页上的数字，单独一个入口。

    列表接口也带 counts，但铃铛要在没打开反馈中心的时候轮询，不该为了一个整数
    拉一整页数据。
    """
    who = await resolver.resolve()
    handle = who.handle if who.authenticated else None
    # No `unassigned` here: it is the admin queue's 「还没人管」, counted over the
    # private and security rows as well, and this endpoint answers anonymous
    # callers (the bell polls it before anyone logs in). An admin gets it from
    # `GET /admin/feedback`. See `FeedbackService.counts`.
    counts = await service.counts(handle=handle)
    payload = FeedbackCounts(
        all=counts["all"],
        hot=counts["hot"],
        active=counts["active"],
        resolved=counts["resolved"],
        deployed=counts["deployed"],
        unread=counts["unread"],
    )
    return ok(payload.model_dump(mode="json"))


@router.post("/read")
async def mark_feedback_read(
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """把未读游标推到此刻。

    游标而不是每条一个「已读」行：见 `FeedbackReadState`。返回游标值，客户端可以
    拿它做后续请求的下界，省掉一次「现在几点」的猜测。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    at = await service.mark_read(handle=who.handle)
    await db.commit()
    return ok({"last_read_at": at.isoformat()})


@router.get("/mine")
async def list_my_feedback(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    page_start: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """「我的反馈」：我提的 + 我替谁提的 + 指派给我的。

    访客拿到空列表而不是 401 —— 和 `/awaiting-me` 同一条理由：这个清单的定义是
    「点到我的那些」，没有身份就没有被点到。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        return ok(page([], 0))
    rows, total = await service.list_mine(
        handle=who.handle, limit=page_size, offset=page_start
    )
    items = await _cards(service, rows, handle=who.handle)
    return ok({**page(items, total), "counts": await service.counts(handle=who.handle)})


@router.get("")
async def list_feedback(
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    tab: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="new"),
    #: 四个筛选：作者 / 状态 / 类型 / 起始时间。**都是可选的，缺省即不筛** —— 所以
    #: 老的调用方（一个都不传）行为一字不变。不认识的 status / kind 报 400（见
    #: `services.list_public`），`author` 和 `since` 是自由值：前者是个人名，后者是
    #: 一个时刻，都不该由服务端维护一张词表。
    author: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    kind: str | None = Query(default=None, max_length=32),
    since: datetime | None = Query(default=None),
    page_start: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
    topic: uuid.UUID | None = Query(default=None),
) -> dict:
    who, _ = await _reader(db, service, resolver, topic)
    handle = who.handle if who.authenticated else None
    if tab not in feedback_services.PUBLIC_TABS:
        # Same refusal as the admin list: a tab the server does not know is a
        # client that has fallen behind, and answering with `all` renders a
        # plausible page under the wrong heading. A user cannot type a tab name,
        # so nobody reaches this by hand.
        raise BadRequestError(say("feedbackUnknownTab", tab=tab))
    rows, total = await service.list_public(
        tab=tab,
        q=q,
        sort=sort,
        limit=page_size,
        offset=page_start,
        author=author,
        status=status,
        kind=kind,
        since=since,
    )
    items = await _cards(service, rows, handle=handle)
    return ok({**page(items, total), "counts": await service.counts(handle=handle)})


@router.post("")
async def create_feedback(
    body: FeedbackCreate,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """提一条反馈。

    `visibility` 是提交者的选择，只在这一次决定：私密条目只有管理员、提交者本人、以及
    提出它的那个房间当时的成员看得见（并集在 `services.may_see`），而管理员**没有**
    把它改公开的入口 —— 那是唯一一个本人无法撤销的改动，所以不给。

    agent **不能**走这条路：`services.create` 会拒（§5.2）。agent 的入口是
    `cheese_feedback_propose`，它落的是一张提案卡，由人在卡上按发送 ——
    没有这道门，一个跑歪的循环可以直接往公开列表里灌东西。人在卡上按发送时走的是
    `POST /topics/{topic_id}/feedback-proposals/{block_id}/accept`，那条路会把作者
    记成提案的 agent、把提交者记成按按钮的人。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    row = await service.create(
        body,
        actor_handle=who.handle,
        actor_user_id=who.user_id,
    )
    await db.commit()
    return ok(
        await _detail(
            service, row, handle=who.handle, is_admin=await _is_admin(db, service, who)
        )
    )


@router.get("/{feedback_ref}")
async def get_feedback(
    feedback_ref: str,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    topic: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """一条反馈的全部内容。`feedback_ref` 是 `FB-12` 或 uuid：人和 agent 手上拿的
    都是编号。看不见的回 404，不是 403 —— 见 `visible_row`。"""
    who, room_project = await _reader(db, service, resolver, topic)
    handle = who.handle if who.authenticated else None
    is_admin = await _is_admin(db, service, who)
    row = await service.visible_ref(feedback_ref, handle=handle, is_admin=is_admin)
    return ok(
        await _detail(
            service,
            row,
            handle=handle,
            is_admin=is_admin,
            room_project_id=room_project,
        )
    )


@router.delete("/{feedback_id}")
async def delete_feedback(
    feedback_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """删掉一条反馈 —— **作者删自己的，平台管理员删任何一条**。

    两种 4xx 分得很清楚，因为它们是两件事：

    * **404** 是「你看不见这条」（`visible_row` 的答案）。私人反馈存不存在本身就不该
      被一个看不见它的人问出来，所以这里不告诉他自己没有权限 —— 那等于确认了它存在。
    * **403** 是「你看得见，但这不是你的」（`may_delete_feedback` 的答案）。

    删除是**软删**（`deleted_at`，连带评论）。管理员删的是别人写的东西，那是需要留痕
    的一类动作，而行还在就是那条痕。前端不自己判断能不能删：每一条上都有服务端算好的
    `can_delete`，按钮照它画。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    await service.delete_feedback(
        feedback_id,
        handle=who.handle,
        is_admin=await _is_admin(db, service, who),
    )
    await db.commit()
    return ok({"deleted": True})


@router.get("/{feedback_id}/comments")
async def list_feedback_comments(
    feedback_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    after: Annotated[str | None, Query(max_length=128)] = None,
    parent_id: Annotated[uuid.UUID | None, Query()] = None,
    # 默认值从 `paging.py` 拿，不从 `repositories.py`：路由 import 领域的 repository
    # 是架构守卫挡的一件事（路由不属于任何领域，所以它串的每一层都是跨域的），而
    # 「一页多大」正是路由和 repository 都要知道的那个数——所以它自己一个模块。
    limit: Annotated[int, Query(ge=1, le=100)] = THREAD_PAGE,
    topic: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """一页评论。

    两个取法同一个门：不给 `parent_id` 就是往下翻**顶层评论**（一页 `limit` 栋楼，
    每栋跟着它的回复走），给了就是取**那一栋楼里的下一段回复**。「展开更多评论」和
    「展开更多回复」于是共用一条路由、一套游标，客户端不用记两种形状。

    `parent_id` 指的那条评论**必须属于 `feedback_id` 这条反馈**：可见性是按帖子判
    的，不查这一下，别人私密报告里某条评论的 id 填进公开帖子的 URL 就能把那段对话
    取出来（`services.replies_page` 里的那一句就是为此）。

    游标 `after` 是服务端发出去的不透明字符串（`cursor_of`），客户端原样带回来；
    看不懂的游标是 400 而不是 500 —— 它是调用方递进来的东西，坏在它那一侧。

    一批取整页：脸、点赞数、调用者点过没有、每一条能不能删，`comments_out` 是评论
    变成 JSON 的唯一一处。分页之后这一批的 id 数由 `limit × (1 + replies_limit)`
    封顶 —— 在那之前它跟着整条线程走，是这条主路径上真正的上限（见 `_IN_BATCH`）。
    """
    who, _ = await _reader(db, service, resolver, topic)
    handle = who.handle if who.authenticated else None
    is_admin = await _is_admin(db, service, who)
    row = await service.visible_row(feedback_id, handle=handle, is_admin=is_admin)
    try:
        if parent_id is not None:
            replies, next_cursor = await service.replies_page(
                row.id, parent_id, after=after, limit=limit
            )
            comments = await service.comments_out(
                replies, handle=handle, is_admin=is_admin
            )
        else:
            page = await service.thread_page(row.id, after=after, limit=limit)
            comments = await service.comments_out(
                page.rows,
                handle=handle,
                is_admin=is_admin,
                reply_counts=page.reply_counts,
                reply_cursors=page.reply_cursors,
            )
            next_cursor = page.next_cursor
    except ValueError as exc:
        # 游标解不出来（不是我们发的那种字符串）。400：坏的是调用方递进来的东西。
        raise BadRequestError(say("feedbackCursorUnreadable")) from exc
    return ok(
        {
            "items": [c.model_dump(mode="json") for c in comments],
            "next_cursor": next_cursor,
        }
    )


@router.post("/{feedback_id}/comments")
async def create_feedback_comment(
    feedback_id: uuid.UUID,
    body: CommentCreate,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    is_admin = await _is_admin(db, service, who)
    comment, _ = await service.comment(
        feedback_id,
        body.body,
        body.parent_id,
        actor_handle=who.handle,
        actor_user_id=who.user_id,
        is_admin=is_admin,
    )
    await db.commit()
    # The same assembler the list uses, over the one new row: the freshly posted
    # comment renders in the thread immediately and must carry everything the
    # rows around it carry (`likes` is 0 and `liked` is False, but `can_delete`
    # is the interesting one — the author may delete the words they just wrote,
    # and the button is drawn from the server's answer). A separate one-row
    # builder here is exactly how the response to POST would drift from the
    # response to GET.
    created = await service.comments_out(
        [comment], handle=who.handle, is_admin=is_admin
    )
    return ok(created[0].model_dump(mode="json"))


@router.delete("/{feedback_id}/comments/{comment_id}")
async def delete_feedback_comment(
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    await service.delete_comment(
        feedback_id,
        comment_id,
        handle=who.handle,
        is_admin=await _is_admin(db, service, who),
    )
    await db.commit()
    return ok({"deleted": True})


@router.post("/{feedback_id}/comments/{comment_id}/likes")
async def like_feedback_comment(
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """点赞一条回复。重复点是幂等的，回的是**写完之后**的计数而不是增量。

    和 `support_feedback` 同一个形状，同一个理由（增量会让两个人同时点各自渲染出
    一个从来没存在过的数字）。区别在**不挡已办完的反馈**：点赞不参与排序，理由写在
    `FeedbackService.like_comment` 上。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    count, liked = await service.like_comment(
        feedback_id,
        comment_id,
        handle=who.handle,
        is_admin=await _is_admin(db, service, who),
    )
    await db.commit()
    return ok(CommentLikeOut(count=count, liked=liked).model_dump(mode="json"))


@router.delete("/{feedback_id}/comments/{comment_id}/likes")
async def unlike_feedback_comment(
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    count, liked = await service.unlike_comment(
        feedback_id,
        comment_id,
        handle=who.handle,
        is_admin=await _is_admin(db, service, who),
    )
    await db.commit()
    return ok(CommentLikeOut(count=count, liked=liked).model_dump(mode="json"))


@router.post("/{feedback_id}/supports")
async def support_feedback(
    feedback_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """支持一条。重复点是幂等的，回的是**写完之后**的计数而不是增量。

    返回增量的话，两个人同时点会各自渲染出一个从来没存在过的数字。
    """
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    count, supported = await service.support(
        feedback_id, handle=who.handle, is_admin=await _is_admin(db, service, who)
    )
    await db.commit()
    return ok(SupportOut(count=count, supported=supported).model_dump(mode="json"))


@router.delete("/{feedback_id}/supports")
async def unsupport_feedback(
    feedback_id: uuid.UUID,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve()
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    count, supported = await service.unsupport(
        feedback_id, handle=who.handle, is_admin=await _is_admin(db, service, who)
    )
    await db.commit()
    return ok(SupportOut(count=count, supported=supported).model_dump(mode="json"))


# --- 领取 -------------------------------------------------------------------


async def _claimer(
    db: DbSession, resolver: ActorResolver, topic: uuid.UUID | None
) -> tuple[Actor, uuid.UUID | None]:
    """Who is claiming, and the project of the room they claim from, if any.

    The room's project is evidence in `may_claim` (see `_in_room`).
    """
    who, project_id = await _in_room(db, resolver, topic)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError(say("signInRequired"))
    return who, project_id


@router.post("/{feedback_ref}/claim")
async def claim_feedback(
    feedback_ref: str,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    topic: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """领取一条反馈：记到调用者名下，从此刻算「处理中」。

    `feedback_ref` 是 `FB-12` 或 uuid。领的人取自凭证，请求体里没有名字。已经被
    别人领了回 **409**，带着持有人（`error.data.holder`）：领取存在的意义就是让第二
    个人知道别修了。看不见的是 404，看得见但不是在做知是本身的人是 403。回整条详情，
    和 `GET /feedback/{ref}` 同一个形状。
    """
    who, room_project = await _claimer(db, resolver, topic)
    is_admin = await _is_admin(db, service, who)
    row = await service.claim(
        feedback_ref,
        handle=who.handle,
        is_admin=is_admin,
        room_project_id=room_project,
    )
    await db.commit()
    return ok(
        await _detail(
            service,
            row,
            handle=who.handle,
            is_admin=is_admin,
            room_project_id=room_project,
        )
    )


@router.delete("/{feedback_ref}/claim")
async def release_feedback(
    feedback_ref: str,
    db: DbSession,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    topic: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """放弃领取：持有人自己或反馈管理员。没人领着时是空操作；状态不退回。"""
    who, _ = await _claimer(db, resolver, topic)
    is_admin = await _is_admin(db, service, who)
    row = await service.release(feedback_ref, handle=who.handle, is_admin=is_admin)
    await db.commit()
    return ok(await _detail(service, row, handle=who.handle, is_admin=is_admin))
