"""反馈的读：列表、计数、折线与详情 —— 从 `services.py` 的「读」两段拆出来。

平台看板的两条折线（现在是 `platform_stats` 在读）也从这道门走：窗口读的判据
——「到过」问的是时间线而不是列、同一个状态可以有多行所以数的是
`distinct feedback_id`——写在仓储的 docstring 里；调用点自己拼一遍 `select` 就是
各自重答一遍，而两处答出两个口径时两边看着都「对」。

`FeedbackService` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.feedback import repositories as repo
from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackKind,
    FeedbackStatus,
)
from app.domain.feedback.schemas import CommentOut, FeedbackDetail
from app.domain.feedback.services.vocabulary import ADMIN_TABS, SORTS
from app.domain.usage.incentives import CreditIncentives
from app.domain.user.services import chosen_avatars_by_handle

if TYPE_CHECKING:
    from app.domain.feedback import services as pkg


async def list_public(
    svc: pkg.FeedbackService,
    *,
    tab: str,
    q: str | None,
    sort: str,
    limit: int,
    offset: int,
    author: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
) -> tuple[list[Feedback], int]:
    """公开列表，四个可选的筛选叠在栏位之上。

    **不认识的取值一律 400，不退回「全不筛」** —— 和 `tab` / `sort` 同一条规矩：
    猜错一个筛选会让人以为「没有这样的反馈」，而它其实只是被别的条件挡住了。
    反馈中心那几个筛选是**控件**，控件里的值只能来自这份词表（服务端随 meta 下发
    的 `statuses` / `kinds`），所以走到这里的一定是客户端版本落后了，而不是有人
    手打了什么。
    """
    # 「办完了」那一栏装的是几种结局（修复、上线、不修复），状态筛选是**单级**的。
    # 两者不冲突：栏目先说「哪些还在桌上」，筛选再从那批里挑一级。所以这里不与
    # `_tab_where` 合并，只保证两边都成立。
    if status is not None and status not in {s.value for s in FeedbackStatus}:
        raise BadRequestError(say("feedbackUnknownStatus", status=status))
    if kind is not None and kind not in {k.value for k in FeedbackKind}:
        raise BadRequestError(say("feedbackUnknownKind", kind=kind))
    return await svc._repo.list_public(
        tab=tab,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
        author=author,
        status=status,
        kind=kind,
        since=since,
    )


async def list_admin(
    svc: pkg.FeedbackService,
    *,
    tab: str,
    assignee: str | None,
    q: str | None,
    sort: str = "new",
    limit: int,
    offset: int,
    since: datetime | None = None,
    resolved_since: datetime | None = None,
    deployed_since: datetime | None = None,
) -> tuple[list[Feedback], int]:
    if tab not in ADMIN_TABS:
        raise BadRequestError(say("feedbackUnknownView", tab=tab))
    if sort not in SORTS:
        # Refused rather than coerced to `new`, same reasoning as `tab` above
        # and it bites harder here: a client asking for `hottest` and getting
        # `new` reads the top of the page as "the most supported reports".
        # The ordering is the answer, so answering in a different order is
        # answering a different question under the same heading.
        raise BadRequestError(say("feedbackUnknownSort", sort=sort))
    return await svc._repo.list_admin(
        tab=tab,
        assignee=assignee,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
        since=since,
        resolved_since=resolved_since,
        deployed_since=deployed_since,
    )


async def list_mine(
    svc: pkg.FeedbackService, *, handle: str, limit: int, offset: int
) -> tuple[list[Feedback], int]:
    """「我的反馈」：清单里的每一条，详情页都开得了。

    两步，因为规则只有一份拼写。SQL 那一步（`visible_to`）把「指派给我的」收
    窄到一个**超集**——它带不动「今天还读得到那个房间」那半句，那是四张表四条
    主张，抄进 WHERE 就是第二份迟早漂开的答案。最后一刀在这里，用的就是详情页
    那个 `may_see`，所以清单和它自己的行不会各说一套。

    挡下来的是哪一条：被移出项目、又恰好是某条私密反馈的负责人、而那条反馈提
    出时他在那个房间里。详情页早就对他 404 了；少了这一步，清单还在把它的标题
    和状态递给他。

    总数跟着减这一页挡下的条数。一页装不下时它仍可能偏大（别的页上挡下的那几
    条还算在里面），但只会偏大、不会偏小，而且它是一个数字不是一条能点开的行
    ——前端拿它写「共 N 条」，一次只取一页。
    """
    is_admin = await svc.is_admin(handle)
    rows, total = await svc._repo.list_related_to(
        handle,
        is_admin=is_admin,
        limit=limit,
        offset=offset,
    )
    kept = [
        row for row in rows if await svc.may_see(row, handle=handle, is_admin=is_admin)
    ]
    return kept, total - (len(rows) - len(kept))


async def counts(
    svc: pkg.FeedbackService, *, handle: str | None, is_admin: bool = False
) -> dict[str, int]:
    """Tab numbers + the bell's unread count, in one call.

    `unread` is a live count against a cursor, not a stored flag: a stored
    flag is one row per person per item, and the cursor is one row per person
    — see `FeedbackReadState` and the design note §6.2.

    `unassigned` is added **only for an admin**, and it is the one key here
    that is not the public tabs' arithmetic: `unassigned_count` counts every
    unresolved row nobody has picked up, private and security included, so
    answering it to an anonymous caller would publish a number that describes
    rows they cannot open. It rides on `/admin/feedback` and nowhere else.

    `deployed` rides along from `public_counts` — a separate number beside
    `resolved`, which keeps meaning every closed status. See `_tab_where`.
    """
    counts = await svc._repo.public_counts()
    if is_admin:
        counts["unassigned"] = await svc._repo.unassigned_count()
    if handle:
        state = await svc._repo.get_read_state(handle)
        since = state.last_read_at if state else None
        # 这里**再问一次** `is_admin`，而不是用调用方传进来的那一位。
        #
        # 「指派给我的」那条胳膊要靠它收窄（见 `count_activity_since`），而
        # 收窄必须知道这个人是不是管理员：管理员对任何一行都不收窄，非管理员
        # 只能看见公开的。铃铛轮询的那条路（`GET /feedback/counts`）没有身份、
        # 也不该给 `unassigned`，所以它调的 `counts(handle=…)` 里 `is_admin`
        # 永远是默认的 False —— 只信那一位的话，管理员会因为这次收窄反过来丢掉
        # 自己的未读数。两处对同一个人的判断，宁可多一次（实例上已经 memo 过，
        # `list_related_to` 在同一个请求里问的是同一份）。
        counts["unread"] = await svc._repo.count_activity_since(
            [handle], since, is_admin=is_admin or await svc.is_admin(handle)
        )
    else:
        counts["unread"] = 0
    return counts


async def admin_board_counts(svc: pkg.FeedbackService) -> dict[str, dict[str, int]]:
    """管理看板那一块的计数 —— **全量口径**。

    和 :meth:`counts` 的分工：`counts` 是反馈中心那一行标签页的数（用户侧，被
    `PUBLIC_ONLY` 收窄，因为匿名读者不该从一个数字里得知私密反馈有多少）；
    这一条是管理看板问的「现在一共有多少事」，四栏、四级状态、总量一起给，
    一条私密反馈也要算进去。

    两个口径**不能互相替代**，这正是看板此前的 bug：它拿 `counts` 去填 KPI，
    于是卡片写的是公开那一臂、旁边那条曲线写的是全量，两边各自都看着对。
    """
    return await svc._repo.admin_counts()


async def created_series(
    svc: pkg.FeedbackService, *, since: datetime, until: datetime
) -> dict[date, int]:
    """窗口内按 UTC 的天新建的反馈数，稀疏；补 0 由调用方做。"""
    return await svc._repo.created_series(since=since, until=until)


async def reached_series(
    svc: pkg.FeedbackService,
    *,
    status: FeedbackStatus,
    since: datetime,
    until: datetime,
) -> dict[date, int]:
    """窗口内「到过」这个状态的反馈数，稀疏；`resolved` / `deployed` 各一条。"""
    return await svc._repo.reached_series(status=status, since=since, until=until)


async def count_created_between(
    svc: pkg.FeedbackService, *, since: datetime, until: datetime
) -> int:
    """窗口内新建的反馈总数（`created_series` 的合计版，看板环比用）。"""
    return await svc._repo.count_created_between(since=since, until=until)


async def count_reached_resolved_between(
    svc: pkg.FeedbackService, *, since: datetime, until: datetime
) -> int:
    """窗口内「到过 resolved」的反馈总数（`reached_series` 的合计版）。"""
    return await svc._repo.count_reached_resolved_between(since=since, until=until)


async def support_counts(
    svc: pkg.FeedbackService, ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    return await svc._repo.supports_counts(ids)


async def comment_counts(
    svc: pkg.FeedbackService, ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    return await svc._repo.comment_counts(ids)


async def supported_ids(
    svc: pkg.FeedbackService, ids: list[uuid.UUID], handle: str | None
) -> set[uuid.UUID]:
    """Which of these the viewer already supported — the heart's filled state."""
    if not handle:
        return set()
    return await svc._repo.supported_by(ids, handle)


async def last_activity(
    svc: pkg.FeedbackService, ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """Newest comment-or-status time per row, for the card's 「刚刚」 line."""
    return await svc._repo.latest_activity_of(ids)


async def chosen_avatars(
    svc: pkg.FeedbackService, handles: Iterable[str]
) -> dict[str, int]:
    """Faces for one screenful of feedback — report, comments and notes.

    A thin pass-through: the lookup itself is the user domain's, because
    "which avatar did this person pick" is a fact about `UserProfile` and
    the rule for reading it (a person who never chose is **absent**, not
    mapped to the global default) belongs to the domain that owns the
    column. See `chosen_avatars_by_handle`.

    It stays a method here so the five call sites — the public centre, the
    thread, a single new comment and the admin queue — ask for "the faces on
    this page" once, instead of each one importing another domain's service
    and re-deciding which handles are worth resolving.
    """
    return await chosen_avatars_by_handle(svc._session, handles)


async def thread_page(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    *,
    after: str | None = None,
    limit: int = repo.THREAD_PAGE,
    replies_limit: int = repo.REPLIES_PAGE,
) -> repo.CommentPage:
    """一页评论。分页口径（楼为单位、回复另有一层上限）在仓储那一层。"""
    return await svc._repo.page_comments(
        feedback_id, after=after, limit=limit, replies_limit=replies_limit
    )


async def replies_page(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    parent_id: uuid.UUID,
    *,
    after: str | None = None,
    limit: int = repo.REPLIES_PAGE,
) -> tuple[list[FeedbackComment], str | None]:
    """一栋楼里的下一段回复。

    `feedback_id` 不是多余的：可见性是**按帖子**判的（`visible_row`），而这条路
    按 `parent_id` 取回复 —— 少了这一步，把别人私密报告里某条评论的 id 填进
    自己这条公开帖子的 URL，取回来的就是那份私密报告里的对话。所以父亲必须属于
    被点名的那条反馈，否则 404（和别处同一个口径：藏起来的帖子不确认存在）。
    """
    parent = await svc._repo.get_comment(parent_id)
    if parent is None or parent.feedback_id != feedback_id:
        raise NotFoundError(say("commentNotFound"))
    return await svc._repo.page_replies(parent_id, after=after, limit=limit)


async def comments_out(
    svc: pkg.FeedbackService,
    rows: Sequence[FeedbackComment],
    *,
    handle: str | None,
    is_admin: bool,
    avatars: Mapping[str, int] | None = None,
    reply_counts: Mapping[uuid.UUID, int] | None = None,
    reply_cursors: Mapping[uuid.UUID, str] | None = None,
) -> list[CommentOut]:
    """The thread as the wire shape — the one place a comment becomes JSON.

    Three things ride along that are not on the comment's own row, and all
    three are asked for in **one query for the whole list**, never per row:

    * the like count and the viewer's own like (`comment_like_counts`,
      `comment_liked_by`) — the read side of a button that sits on every
      reply, so a per-comment lookup is N+1 on a page of replies;
    * `can_delete`, from `may_delete_comment`, so the client cannot draw a
      delete button the server would refuse.

    ``reply_counts`` is the server's own count of how many replies each
    top-level comment has, which is **not** the same as how many arrived in
    this page: the thread is paged, so a building can carry the first page of
    its replies and the client has to know there is more to ask for. Absent
    from the map means zero.

    ``reply_cursors`` is the other half of that pair: the cursor to ask for
    the *next* page **inside** one building. It is empty for a building whose
    replies all came with this page, which is why the two are read together
    rather than the client comparing counts alone — 「count > how many I hold」
    answers "is there more", but only the cursor answers "from where".
    Absent from the map means there is no next page.

    ``avatars`` exists so the detail screen resolves faces **once**. It draws
    the report, every comment and every admin note on one page, and it
    already builds a map covering all three; passing that in keeps this
    method from resolving the comment authors a second time. ``None`` means
    "resolve them for these rows", which is what the comments endpoint
    (which has nothing but the thread) wants.
    """
    if not rows:
        return []
    ids = [row.id for row in rows]
    if avatars is None:
        avatars = await svc.chosen_avatars([row.author_handle for row in rows])
    like_counts = await svc._repo.comment_like_counts(ids)
    liked = await svc._repo.comment_liked_by(ids, handle) if handle else set()
    counts = reply_counts or {}
    cursors = reply_cursors or {}
    return [
        CommentOut.from_row(
            row,
            avatars=avatars,
            likes=like_counts.get(row.id, 0),
            liked=row.id in liked,
            reply_count=counts.get(row.id, 0),
            replies_next_cursor=cursors.get(row.id),
            can_delete=svc.may_delete_comment(row, handle=handle, is_admin=is_admin),
        )
        for row in rows
    ]


async def detail(
    svc: pkg.FeedbackService,
    feedback_id: uuid.UUID,
    *,
    handle: str | None,
    is_admin: bool,
) -> FeedbackDetail:
    row = await svc.visible_row(feedback_id, handle=handle, is_admin=is_admin)
    return await svc.detail_of(row, handle=handle, is_admin=is_admin)


async def detail_of(
    svc: pkg.FeedbackService,
    row: Feedback,
    *,
    handle: str | None,
    is_admin: bool,
    room_project_id: uuid.UUID | None = None,
) -> FeedbackDetail:
    """The assembled detail view — schema, not a bag of parts.

    It returns the schema because every caller wants exactly this view and
    four of them built it by hand as ``FeedbackDetail.from_row(row, **parts)``.
    One of those copies shipped a ``"row"`` key inside the parts, which
    collides with the argument of the same name and made every one of them
    raise; a shape that cannot be mis-unpacked is the fix, not four careful
    call sites.

    ``notes`` are admin-only: the field is always present so the frontend has
    one shape, and it is the service that empties it. The empty list is the
    absence, not a redaction the client is trusted to honour.

    ``room_project_id`` is the room an agent reads from, which is how it
    qualifies to claim (`may_claim`); without it ``can_claim`` would tell an
    agent on the platform that it cannot.
    """
    page = await svc._repo.page_comments(row.id)
    activity = await svc._repo.latest_activity_of([row.id])
    # 和 `delete_feedback` 共用同一个判据，所以按钮和权限不会分家。
    can_delete = svc.may_delete_feedback(row, handle=handle, is_admin=is_admin)
    # 领取和放弃也一样：和 `claim` / `release` 同一处判据。
    holder = row.assignee_handle
    can_claim = holder is None and await svc.may_claim(
        handle, is_admin=is_admin, room_project_id=room_project_id
    )
    can_release = holder is not None and (is_admin or holder == handle)
    # Notes are admin-only, so resolving faces for them is not extra work a
    # non-admin pays for: the list is empty and contributes no handles.
    notes = await svc._repo.list_notes(row.id) if is_admin else []
    # One resolution pass for the report, every comment and every note. The
    # detail page draws all of them in one screen, so a per-author lookup
    # would be exactly the N+1 this method's callers avoid for counters.
    avatars = await svc.chosen_avatars(
        [
            row.author_handle,
            *[c.author_handle for c in page.rows],
            *[n.author_handle for n in notes],
        ]
    )
    detail = FeedbackDetail.from_row(
        row,
        supports=await svc._repo.supports_count(row.id),
        supported=(await svc._repo.has_support(row.id, handle) if handle else False),
        # 真总数，不是这一页的条数：评论分页之后 `len(thread)` 会变成一页的
        # 大小，卡片上那个数字就会随翻页往下掉。服务端自己数得出来，所以客户端
        # 拿到的永远是「这条反馈一共有多少条评论」。
        comments=(await svc._repo.comment_counts([row.id])).get(row.id, 0),
        avatars=avatars,
        last_activity_at=activity.get(row.id),
        # The page-wide map goes straight back in: the comment authors are a
        # subset of the handles resolved above, so this costs nothing.
        thread=await svc.comments_out(
            page.rows,
            handle=handle,
            is_admin=is_admin,
            avatars=avatars,
            reply_counts=page.reply_counts,
            reply_cursors=page.reply_cursors,
        ),
        thread_next_cursor=page.next_cursor,
        timeline=await svc._repo.list_timeline(row.id),
        notes=notes,
        can_delete=can_delete,
        can_claim=can_claim,
        can_release=can_release,
    )
    detail.reward = await CreditIncentives(svc._session).reward(f"feedback:{row.id}")
    return detail


async def mark_read(svc: pkg.FeedbackService, *, handle: str) -> datetime:
    """Move the cursor to now. Returns the new cursor for the client to echo."""
    now = datetime.now(UTC)
    await svc._repo.bump_read_state(handle, now)
    return now
