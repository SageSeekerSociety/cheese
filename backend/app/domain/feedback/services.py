"""Feedback rules.

Everything a route must not be trusted to remember lives here:

* **who can see a row** — one predicate, used by every read path, so a new
  endpoint cannot accidentally get it wrong (§4.3 of the design note).
* **status and timeline move together** — one method does both, in the caller's
  transaction. There is no other way to write `status`, which is what keeps the
  timeline from becoming a partial record of what happened.
* **comments are two levels deep** — the fold-up rule, copied from the prototype.

Three product decisions the user had not ruled on are taken here as defaults,
each in one place, each revertible without touching a route:

1. **Who is an admin** — **not decided here.** The judge is
   `AdminService.is_admin` in `app/domain/admin/services.py`: 根 ∪ 页面上加的,
   i.e. `settings.platform_admin_handles` (deploy-required, not removable from
   the page) and the `platform_admins` table (`/admin/admins`, the 成员管理
   screen). This module only *asks* — `FeedbackService.admins` and the three thin
   delegates below exist so a feedback route does not have to know where the
   answer lives, not because the answer is feedback's. It never was: the same
   list is what opens every other admin screen.
2. **`security` is a subtype of `private`, not a second axis.** A security report
   is invisible to non-admins exactly as a private one is; the flag only routes
   it into the admin's security tab. So `security=True` narrows visibility, and
   the narrowing is applied **at read time** in `may_see` — not by overwriting
   `visibility` when an admin sets the flag. Two reasons it goes that way:
   `visibility` is the reporter's own choice and rewriting it would edit their
   decision behind their back, and deciding who may see a row is a policy
   question, which the repository deliberately does not answer.
3. **Resolved items sink, but only bugs.** See `_visible_tab` in repositories —
   a resolved suggestion stays in the list. Hiding suggestions is the wider
   reading and much harder to notice going wrong; this is the narrow one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.project_access import may_read_topic
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    PreconditionFailedError,
)
from app.domain.admin.services import AdminService
from app.domain.feedback import repositories as repo
from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackStatus,
    FeedbackVisibility,
)
from app.domain.feedback.proposals import AcceptedProposal
from app.domain.feedback.schemas import (
    CommentOut,
    FeedbackCreate,
    FeedbackDetail,
    FeedbackPatch,
)
from app.domain.identity.services import IdentityService
from app.domain.user.services import chosen_avatars_by_handle

#: The admin surface's tiers, in the order the admin tab bar draws them.
ADMIN_TABS: tuple[str, ...] = ("public", "private", "agent", "security")

#: The public tabs, in the order the tab bar draws them.
PUBLIC_TABS: tuple[str, ...] = ("all", "hot", "active", "resolved")

#: The two orderings any list can be asked for. `new` is the default everywhere;
#: `supports` is what the public `hot` tab implies and what the admin console's
#: 最新/最热 toggle asks for by name. One tuple because the serializer
#: (`_list_stmt`) has exactly two branches: a third name here would be a value
#: that silently means `new`.
SORTS: tuple[str, ...] = ("new", "supports")

#: Movement order shown by the status ladder. One rung per thing the person who
#: filed it can see happen: 收录 → 处理 → 解决 → 部署. `resolved` and `deployed`
#: are reachable from any state, and a reopen (resolved → in_progress) is
#: allowed: reports do get re-opened, and a status set that forbids it gets
#: worked around by filing a duplicate instead — which loses the history that
#: makes the report useful.
STATUS_LADDER: tuple[FeedbackStatus, ...] = (
    FeedbackStatus.received,
    FeedbackStatus.in_progress,
    FeedbackStatus.resolved,
    FeedbackStatus.deployed,
)


class FeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = repo.FeedbackRepository(session)
        #: 平台管理员那份名单与判据 —— 一个请求一个实例，两边共用同一个 memo。
        self._admins = AdminService(session)

    # --- 权限 ---------------------------------------------------------------
    #
    # 「谁算平台管理员」不在这个域里：它是平台级的事实（`app/domain/admin/`），
    # 反馈只是**用**它 —— 私密条目谁能看见、评论能不能删，问的都是同一个答案。
    # 这里留一层薄委托，是因为反馈自己的可见性判断（`may_see` / `visible_row` /
    # `detail`）每一步都要问它，而让每个调用点各自去构造一个 `AdminService` 等于
    # 把同一个请求拆成几份各读一遍库。

    @property
    def admins(self) -> AdminService:
        """平台管理员那一半（名单、判据、页面上加删）—— 路由过的是它那道门。"""
        return self._admins

    async def admin_handles(self) -> frozenset[str]:
        """谁算平台管理员：**根 ∪ 页面上加的**。见 `AdminService.admin_handles`。"""
        return await self._admins.admin_handles()

    async def is_admin(self, handle: str | None) -> bool:
        return await self._admins.is_admin(handle)

    async def require_admin(self, handle: str | None) -> str:
        return await self._admins.require_admin(handle)

    async def may_see(
        self, row: Feedback, *, handle: str | None, is_admin: bool
    ) -> bool:
        """The visibility union — 公开 + 管理员 + 是我提的 + **提它的那个房间**.

        结论 47 的三档：开发者（管理员）看得见，提出它的那个房间的成员看得见，反馈
        中心里其他人看不见。前两档一直在这里；第三档是房间这一档，它补的是同一条结
        论的第二句——agent 提的内容在它的房间里全部留痕，所以对那个房间的人藏起来，
        藏掉的只是追踪它的那条路，藏不掉内容。

        A private report is visible to admins **and to the person who filed it**:
        the reporter must be able to follow their own report, and the agent that
        filed on their behalf counts as them. Everyone else cannot, and gets 404
        rather than 403 — see `visible_row`.

        房间这一档取的是**反馈提出那一刻**的名册（`filed_in_a_room_of`），不是现在
        的：按现在取的话，把谁加进这个房间就等于把这个房间历史上的每一条私密反馈一
        并交给他，而加人的那个人并不知道自己在授权。

        它**还要**今天读得到那个房间（`may_read_topic`），两句都成立才开门。少了后
        一句，这一档就是一扇撤不回的门：退房间是硬删名册行，所以撤得掉；退项目不是
        —— `MemberService.remove` 只删 `ProjectMember` 一行，被移出项目的人从此进
        不了这个项目的任何房间，而他在各房间的名册行原地不动。那些行今天是惰性的
        （房间门读的是项目读权，`may_read_topic`），本档把它们变成门，于是「一次加
        人变成一次授权」在离场那一侧原样长了回来。判据留在这里而不是靠名册干净：
        退项目、退队、不再是出题者是三条路，只有「今天还读得到吗」一句话同时管住。

        问的是**房间自己的项目**（`may_read_topic` 从 topic 解出来），不是
        `row.project_id`：这一档的授权键只有一个，就是 `topic_id`，那么项目也必须从它
        解出来。读第二列等于让两个可以各自漂开的字段回答同一个问题——房间被移去别的项
        目、或者哪天 `project_id` 换个来源，两列就不再说同一件事，而鉴权只能有一个答案。

        `security` narrows the public arm, so it is checked in the same breath:
        a row an admin flagged as a security matter is not public even though the
        reporter left `visibility` at its default. That flag is set on triage, by
        someone other than the reporter, and it is the one that must not leak.
        It does **not** narrow the room arm, for the same reason the author keeps
        access to a row that was flagged: 「不能泄露」说的是泄露给没看过它的人，而
        那个房间的人看过。

        Async because the roster and the project's claims are tables — the three
        arms above are on the row, this one is not. Those queries are reached
        only by a row the three cheap arms already refused, and `topic_id is
        None` (报在沙箱里、或房间已删) turns both of them off before either runs.
        """
        if row.visibility == FeedbackVisibility.public and not row.security:
            return True
        if is_admin:
            return True
        if not handle:
            return False
        if handle in (row.author_handle, row.submitted_by_handle):
            return True
        if row.topic_id is None:
            return False
        if not await self._repo.filed_in_a_room_of_mine(row.id, handle):
            return False
        return await may_read_topic(self._session, topic_id=row.topic_id, handle=handle)

    async def visible_row(
        self, feedback_id: uuid.UUID, *, handle: str | None, is_admin: bool
    ) -> Feedback:
        """Load a row or raise 404 — including when the row exists but is hidden.

        Not 403: a 403 on a private report confirms the report exists, which is
        itself the thing the reporter asked to keep quiet. The design note (§4.3)
        makes this the rule for every id-taking feedback endpoint.
        """
        row = await self._repo.get(feedback_id)
        if row is None or not await self.may_see(row, handle=handle, is_admin=is_admin):
            raise NotFoundError("反馈不存在")
        return row

    def may_delete_comment(
        self, row: FeedbackComment, *, handle: str | None, is_admin: bool
    ) -> bool:
        """Who may delete a comment — the author, or an admin.

        Deleting someone else's words is the kind of thing that should be
        visible as an admin action, so admins can — but the author always can.

        One predicate, asked from **two** places: `delete_comment` before it
        deletes, and `comments_out` to fill `CommentOut.can_delete`. Two
        spellings of it is how 「按钮画得出来、点下去 403」 gets invented, and
        this feature has already paid for that lesson once — the support button
        stayed lit on `deployed` rows because the client re-derived a rule the
        service had (see `CLOSED_STATUSES`). The client never re-derives this
        one: it draws the button when the server says `can_delete`.
        """
        return is_admin or (handle is not None and row.author_handle == handle)

    # --- 读 -----------------------------------------------------------------

    async def list_public(
        self, *, tab: str, q: str | None, sort: str, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        return await self._repo.list_public(
            tab=tab, q=q, sort=sort, limit=limit, offset=offset
        )

    async def list_admin(
        self,
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
            raise BadRequestError(f"未知的管理视图：{tab}")
        if sort not in SORTS:
            # Refused rather than coerced to `new`, same reasoning as `tab` above
            # and it bites harder here: a client asking for `hottest` and getting
            # `new` reads the top of the page as "the most supported reports".
            # The ordering is the answer, so answering in a different order is
            # answering a different question under the same heading.
            raise BadRequestError(f"未知的排序：{sort}")
        return await self._repo.list_admin(
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
        self, *, handle: str, limit: int, offset: int
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
        is_admin = await self.is_admin(handle)
        rows, total = await self._repo.list_related_to(
            handle,
            is_admin=is_admin,
            limit=limit,
            offset=offset,
        )
        kept = [
            row
            for row in rows
            if await self.may_see(row, handle=handle, is_admin=is_admin)
        ]
        return kept, total - (len(rows) - len(kept))

    async def counts(
        self, *, handle: str | None, is_admin: bool = False
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
        `resolved`, which keeps meaning 修复 + 上线 as a pair. See `_tab_where`.
        """
        counts = await self._repo.public_counts()
        if is_admin:
            counts["unassigned"] = await self._repo.unassigned_count()
        if handle:
            state = await self._repo.get_read_state(handle)
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
            counts["unread"] = await self._repo.count_activity_since(
                [handle], since, is_admin=is_admin or await self.is_admin(handle)
            )
        else:
            counts["unread"] = 0
        return counts

    # --- 平台看板的两个时间读 -------------------------------------------------
    #
    # 谁要看这两条折线（现在是 `platform_stats`）都得从这道门走：窗口读的判据
    # ——「到过」问的是时间线而不是列、同一个状态可以有多行所以数的是
    # `distinct feedback_id`——都写在仓储的 docstring 里，调用点自己拼一遍
    # `select` 就是各自重答一遍，而两处答出两个口径时两边看着都「对」。

    async def created_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, int]:
        """窗口内按 UTC 的天新建的反馈数，稀疏；补 0 由调用方做。"""
        return await self._repo.created_series(since=since, until=until)

    async def reached_series(
        self, *, status: FeedbackStatus, since: datetime, until: datetime
    ) -> dict[date, int]:
        """窗口内「到过」这个状态的反馈数，稀疏；`resolved` / `deployed` 各一条。"""
        return await self._repo.reached_series(status=status, since=since, until=until)

    async def support_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await self._repo.supports_counts(ids)

    async def comment_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        return await self._repo.comment_counts(ids)

    async def supported_ids(
        self, ids: list[uuid.UUID], handle: str | None
    ) -> set[uuid.UUID]:
        """Which of these the viewer already supported — the heart's filled state."""
        if not handle:
            return set()
        return await self._repo.supported_by(ids, handle)

    async def last_activity(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        """Newest comment-or-status time per row, for the card's 「刚刚」 line."""
        return await self._repo.latest_activity_of(ids)

    async def chosen_avatars(self, handles: Iterable[str]) -> dict[str, int]:
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
        return await chosen_avatars_by_handle(self._session, handles)

    async def thread_page(
        self,
        feedback_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = repo.THREAD_PAGE,
        replies_limit: int = repo.REPLIES_PAGE,
    ) -> repo.CommentPage:
        """一页评论。分页口径（楼为单位、回复另有一层上限）在仓储那一层。"""
        return await self._repo.page_comments(
            feedback_id, after=after, limit=limit, replies_limit=replies_limit
        )

    async def replies_page(
        self,
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
        parent = await self._repo.get_comment(parent_id)
        if parent is None or parent.feedback_id != feedback_id:
            raise NotFoundError("评论不存在")
        return await self._repo.page_replies(parent_id, after=after, limit=limit)

    async def comments_out(
        self,
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
            avatars = await self.chosen_avatars([row.author_handle for row in rows])
        like_counts = await self._repo.comment_like_counts(ids)
        liked = await self._repo.comment_liked_by(ids, handle) if handle else set()
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
                can_delete=self.may_delete_comment(
                    row, handle=handle, is_admin=is_admin
                ),
            )
            for row in rows
        ]

    async def detail(
        self, feedback_id: uuid.UUID, *, handle: str | None, is_admin: bool
    ) -> FeedbackDetail:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        return await self.detail_of(row, handle=handle, is_admin=is_admin)

    async def detail_of(
        self, row: Feedback, *, handle: str | None, is_admin: bool
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
        """
        page = await self._repo.page_comments(row.id)
        activity = await self._repo.latest_activity_of([row.id])
        # Notes are admin-only, so resolving faces for them is not extra work a
        # non-admin pays for: the list is empty and contributes no handles.
        notes = await self._repo.list_notes(row.id) if is_admin else []
        # One resolution pass for the report, every comment and every note. The
        # detail page draws all of them in one screen, so a per-author lookup
        # would be exactly the N+1 this method's callers avoid for counters.
        avatars = await self.chosen_avatars(
            [
                row.author_handle,
                *[c.author_handle for c in page.rows],
                *[n.author_handle for n in notes],
            ]
        )
        return FeedbackDetail.from_row(
            row,
            supports=await self._repo.supports_count(row.id),
            supported=(
                await self._repo.has_support(row.id, handle) if handle else False
            ),
            # 真总数，不是这一页的条数：评论分页之后 `len(thread)` 会变成一页的
            # 大小，卡片上那个数字就会随翻页往下掉。服务端自己数得出来，所以客户端
            # 拿到的永远是「这条反馈一共有多少条评论」。
            comments=(await self._repo.comment_counts([row.id])).get(row.id, 0),
            avatars=avatars,
            last_activity_at=activity.get(row.id),
            # The page-wide map goes straight back in: the comment authors are a
            # subset of the handles resolved above, so this costs nothing.
            thread=await self.comments_out(
                page.rows,
                handle=handle,
                is_admin=is_admin,
                avatars=avatars,
                reply_counts=page.reply_counts,
                reply_cursors=page.reply_cursors,
            ),
            thread_next_cursor=page.next_cursor,
            timeline=await self._repo.list_timeline(row.id),
            notes=notes,
        )

    async def mark_read(self, *, handle: str) -> datetime:
        """Move the cursor to now. Returns the new cursor for the client to echo."""
        now = datetime.now(UTC)
        await self._repo.bump_read_state(handle, now)
        return now

    # --- 写 -----------------------------------------------------------------

    async def create(
        self,
        body: FeedbackCreate,
        *,
        actor_handle: str,
        actor_user_id: int | None,
        proposal: AcceptedProposal | None = None,
    ) -> Feedback:
        """File a report.

        `security` is not a body field: it is set by an admin on triage
        (`PATCH /admin/feedback/{id}`), because the reporter's own `visibility`
        already covers "do not show this to everyone", and a reporter marking
        their own report as a security matter is a claim, not a fact.

        **An agent cannot publish on its own** (§5.2). Without this branch an
        agent that went sideways could write straight into the public list; with
        it, the only way an agent-authored report exists is that a person
        pressed send on the card. The refusal names the tool so the model gets
        a next step rather than a dead end.

        The same rule closes the other door: an agent sending an *existing*
        proposal is still an agent publishing itself, just in two steps. Both
        branches sit here rather than in the routes so that neither door depends
        on a caller remembering to pass the flag honestly — and the answer is
        read here too, from the agent-binding the handle does or does not carry,
        so the door no longer depends on a caller passing a flag at all.

        When `proposal` is set, authorship comes from the proposal (the agent
        that found it) and `submitted_by` from the verified caller (the person
        who sent it) — two fields, not one, so 「芝士提的反馈里有多少真的被人发出
        去了」 stays answerable. The *content* still comes from the request body:
        the drawer lets the sender edit before sending, and the person pressing
        send is accountable for what they send.
        """
        if await IdentityService(self._session).is_agent(actor_handle):
            raise ForbiddenError(
                "agent 不能直接发布反馈：用 `cheese feedback propose` 提案，"
                "由人确认后再发送"
            )
        if proposal is not None:
            return await self._create_from_proposal(
                body,
                proposal=proposal,
                submitted_by_handle=actor_handle,
                submitted_by_user_id=actor_user_id,
            )
        visibility = body.visibility
        row = await self._repo.add(
            title=body.title,
            summary=body.summary or body.title,
            kind=body.kind,
            visibility=visibility,
            problem=body.problem,
            author_handle=actor_handle,
            author_user_id=actor_user_id,
            # False, and not a variable: the branch above already refused every
            # agent, so the only author reaching this row is a person. An
            # agent-authored report exists solely via `_create_from_proposal`.
            author_is_agent=False,
            why=body.why,
            expectation=body.expectation,
            what_happened=body.what_happened,
            repro=body.repro,
            evidence=body.evidence,
            logs=body.logs,
            session_id=body.session_id,
            environment=body.environment,
            submitted_by_handle=None,
            submitted_by_user_id=None,
            # 没有房间来源，而且不是「客户端这次没填」：请求体里根本没有这个字段
            # （`FeedbackCreate`）。人从反馈中心提的一条不是在任何房间里说的话，
            # 所以房间那一档对它关着——`may_see` 的 `row.topic_id is None` 早退。
            topic_id=None,
            project_id=None,
            priority=body.priority,
            tags=body.tags,
        )
        # The first timeline entry, written in the same transaction as the row:
        # a report with no `received` entry would show an empty history for the
        # one event that definitely happened.
        await self._repo.append_timeline(row.id, FeedbackStatus.received, actor_handle)
        return row

    async def _create_from_proposal(
        self,
        body: FeedbackCreate,
        *,
        proposal: AcceptedProposal,
        submitted_by_handle: str,
        submitted_by_user_id: int | None,
    ) -> Feedback:
        row = await self._repo.add(
            title=body.title,
            summary=body.summary or body.title,
            kind=body.kind,
            visibility=body.visibility,
            problem=body.problem,
            author_handle=proposal.author_handle,
            author_user_id=None,
            author_is_agent=True,
            why=body.why,
            expectation=body.expectation,
            what_happened=body.what_happened,
            repro=body.repro,
            evidence=body.evidence,
            logs=body.logs,
            session_id=body.session_id,
            environment=body.environment,
            submitted_by_handle=submitted_by_handle,
            submitted_by_user_id=submitted_by_user_id,
            # 从卡上取，不从请求体取——和作者同一条理由，而房间比作者更要紧：它是
            # 可见性并集里「提出它的那个房间」那一档的授权键（`may_see`）。
            topic_id=proposal.topic_id,
            project_id=proposal.project_id,
            priority=body.priority,
            tags=body.tags,
        )
        # The timeline's first entry names the person, not the agent: the agent
        # did not move this report into `received`, the person did.
        await self._repo.append_timeline(
            row.id, FeedbackStatus.received, submitted_by_handle
        )
        return row

    async def set_status(
        self, feedback_id: uuid.UUID, status: FeedbackStatus, *, by_handle: str
    ) -> Feedback:
        """Move a report, writing the history entry with it.

        There is no route that writes `status` without going through here — the
        two writes share the caller's transaction, so the timeline cannot end up
        describing a state the row never had.
        """
        row = await self._repo.get(feedback_id)
        if row is None:
            raise NotFoundError("反馈不存在")
        if row.status == status:
            # Idempotent, but not silent: re-setting the same status is a no-op
            # rather than a second timeline entry, because two identical entries
            # a second apart read as a bug in the history.
            return row
        await self._repo.set_status(row, status)
        await self._repo.append_timeline(row.id, status, by_handle)
        return row

    async def patch_admin(
        self, feedback_id: uuid.UUID, body: FeedbackPatch, *, by_handle: str
    ) -> Feedback:
        row = await self._repo.get(feedback_id)
        if row is None:
            raise NotFoundError("反馈不存在")
        if body.priority is not None:
            await self._repo.set_priority(row, body.priority)
        if body.assignee_handle is not None:
            # Empty string clears the assignee; a handle assigns. `None` means
            # "not in this request" — see FeedbackPatch.
            await self._repo.set_assignee(row, body.assignee_handle or None)
        if body.security is not None and body.security != row.security:
            await self._repo.set_security(row, body.security)
            # Marking something a security matter is a routing decision the
            # reporter should be able to see, and it is also the moment the
            # report stops being visible to colleagues. Neither is obvious from
            # the row alone, so it goes in the history.
            await self._repo.append_timeline(row.id, row.status, by_handle)
        return row

    # --- 支持 / 评论 ----------------------------------------------------------

    async def support(
        self, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
    ) -> tuple[int, bool]:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        if row.status in repo.CLOSED_STATUSES:
            # Supporting something already closed changes nothing anyone will
            # look at, and it inflates the `hot` tab with items nobody can act
            # on. 412 rather than 403: the client's fix is to re-read the item,
            # not to stop asking. `deployed` counts as closed for the same
            # reason `resolved` does — 上线 is further along, not less finished.
            #
            # The message names the action, not a status: the reader may be
            # looking at either closed rung, and 「已解决的不再接受支持」 would be
            # wrong on a 已上线 row (it never said 已解决).
            raise PreconditionFailedError("这条反馈已经办完了，不再接受支持")
        await self._repo.add_support(row.id, handle)
        return await self._repo.supports_count(row.id), True

    async def unsupport(
        self, feedback_id: uuid.UUID, *, handle: str, is_admin: bool
    ) -> tuple[int, bool]:
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        await self._repo.remove_support(row.id, handle)
        return await self._repo.supports_count(row.id), False

    async def comment(
        self,
        feedback_id: uuid.UUID,
        body: str,
        parent_id: uuid.UUID | None,
        *,
        actor_handle: str,
        actor_user_id: int | None,
        is_admin: bool,
    ) -> tuple[FeedbackComment, Feedback]:
        """Add a comment. Whether its author is an agent is read from the
        handle's agent-binding, not taken from the caller: it is stored on the
        row (the 「芝士回的」 badge), and a stored fact a route hands in is one
        the route can hand in wrong."""
        actor_is_agent = await IdentityService(self._session).is_agent(actor_handle)
        row = await self.visible_row(
            feedback_id, handle=actor_handle, is_admin=is_admin
        )
        parent = None
        reply_to_handle = None
        if parent_id is not None:
            parent = await self._repo.get_comment(parent_id)
            if parent is None or parent.feedback_id != row.id:
                # A parent that belongs to another report, or to none: 400, not
                # 404 — the id the client sent is the problem, not a secret.
                raise BadRequestError("回复的评论不属于这条反馈")
            # The target, captured here because the very next line overwrites
            # the thing that points at it. Only when the comment being answered
            # is itself a reply: a reply to the 楼主 already renders directly
            # under it, so 「回复 楼主」 on every 楼内回复 would be noise, and
            # the reference platforms this was asked to match (B站/小红书) draw
            # the prefix exactly where the fold loses the target — which is
            # replies to replies, and only those.
            reply_to_handle = parent.author_handle if parent.parent_id else None
            # Two levels only, folded the same way the prototype does it
            # (`stores/feedback.ts::addComment`): a reply to a reply lands under
            # the same top-level comment. Storing depth-3 would mean the client
            # renders a thread it was not designed for, and the fold is cheaper
            # than teaching every reader to recurse.
            parent_id = parent.parent_id or parent.id
        created = await self._repo.add_comment(
            feedback_id=row.id,
            author_handle=actor_handle,
            author_user_id=actor_user_id,
            author_is_agent=actor_is_agent,
            body=body,
            parent_id=parent_id,
            reply_to_handle=reply_to_handle,
        )
        return created, row

    async def like_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> tuple[int, bool]:
        """点赞一条回复。`support`, one level down — the count after the write.

        **Deliberately no `CLOSED_STATUSES` guard**, unlike `support`. A support
        is an input to the 「热门」 tab, so letting people pile onto something
        already 办完了 inflates a ranking nobody can act on — that is why the
        report-level one answers 412. A like ranks nothing: it says 「这条回复说
        得对」, and that stays worth saying under a thread on a closed report,
        which is exactly where the conclusions («原来是这样，我也遇到了») end up.
        Putting the guard here would mean the last useful replies on finished
        items are the ones that cannot be marked.
        """
        await self._visible_comment(
            feedback_id, comment_id, handle=handle, is_admin=is_admin
        )
        await self._repo.add_comment_like(comment_id, handle)
        return await self._repo.comment_like_count(comment_id), True

    async def unlike_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> tuple[int, bool]:
        await self._visible_comment(
            feedback_id, comment_id, handle=handle, is_admin=is_admin
        )
        await self._repo.remove_comment_like(comment_id, handle)
        return await self._repo.comment_like_count(comment_id), False

    async def _visible_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> FeedbackComment:
        """The comment, once the caller is allowed to know the report exists.

        Two gates, in this order: the report first (404 for one the caller may
        not see — `visible_row`'s rule, and it must not be skipped or a like on
        an id from a private report answers differently from a read of it), then
        the comment's membership in it. The second is 404 too, not 400: by then
        the caller has already proved they may see the report, so a comment id
        that is not in it is just an id that is not there.
        """
        row = await self.visible_row(feedback_id, handle=handle, is_admin=is_admin)
        comment = await self._repo.get_comment(comment_id)
        if comment is None or comment.feedback_id != row.id:
            raise NotFoundError("评论不存在")
        return comment

    async def delete_comment(
        self,
        feedback_id: uuid.UUID,
        comment_id: uuid.UUID,
        *,
        handle: str,
        is_admin: bool,
    ) -> None:
        comment = await self._visible_comment(
            feedback_id, comment_id, handle=handle, is_admin=is_admin
        )
        # The same predicate the read path fills `CommentOut.can_delete` with,
        # so the affordance and the permission cannot disagree.
        if not self.may_delete_comment(comment, handle=handle, is_admin=is_admin):
            raise ForbiddenError("只能删除自己的评论")
        await self._repo.soft_delete_comment(comment)

    async def note(
        self, feedback_id: uuid.UUID, body: str, *, author_handle: str
    ) -> None:
        """Admin-only internal note. Append-only (see `FeedbackNote`)."""
        row = await self._repo.get(feedback_id)
        if row is None:
            raise NotFoundError("反馈不存在")
        await self._repo.add_note(row.id, author_handle, body)
