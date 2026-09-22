"""Feedback data access. No policy in here — visibility lives in `services.py`.

Every method takes and returns rows; "who is allowed to see this" is a service
question, so that a route can never get it right by accident and a test can
exercise the rule without a database.

`HOT_SCORE` / `HOT_HALF_LIFE_DAYS` / `HOT_MIN_ITEMS` define the 「热门」 tab and
live here rather than in a query string: the tab, its count and the detail card's
「热门」 badge have to agree, and two copies of a threshold is how they stop
agreeing. `hot_score()` is the single expression behind all three.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, and_, exists, func, or_, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackCommentLike,
    FeedbackKind,
    FeedbackNote,
    FeedbackPriority,
    FeedbackReadState,
    FeedbackStatus,
    FeedbackSupport,
    FeedbackTimeline,
    FeedbackVisibility,
)

# 这两个数**住在 `paging.py`**（理由写在那儿：路由也要用它们，而路由不许 import
# repository）。在这里再导出一遍，是因为本模块和 `services.py` 一直按 `THREAD_PAGE`
# 的名字用它，名字留在原处比让每个调用点改一行强。不是给路由用的。
from app.domain.feedback.paging import REPLIES_PAGE, THREAD_PAGE
from app.domain.platform_stats.windows import utc_day
from app.domain.topic.models import TopicMembership

#: 「热门」要多少分 —— 按支持数算，不按浏览量。原型给过理由（「浏览是路过，
#: 支持是表态」），这里照抄。
#:
#: **这是热度分，不是支持数。** 第一版拿 `supports >= 5` 当判据，于是**没有任何时间
#: 因素**：上周爆的和这周爆的同权，一条三个月前攒够 5 票的反馈永远占着这一栏，而这一
#: 栏的名字叫「热门」——它读的是**此刻**，不是「曾经」。压着不动的那一栏最后会变成一
#: 份不再更新的榜单，而读者看不出它已经停止更新。
#:
#: 衰减取半衰期，不取 Hacker News 那条 `(v-1)/(age+2)^1.8`：支持数是几个小整数，读者
#: 应该能自己心算这一栏。半衰期能心算——「两周前的一票算今天半票」——而 `(v-1)/(t+2)^1.8`
#: 不能，一条解释不了的排序规则，维护它的人只能靠试。
#:
#: 这一条线换算成人话（`HOT_HALF_LIFE_DAYS = 14`）：**今天 2 票、一周前 3 票、两周前
#: 4 票**，一样热。三周前的 5 票（5×0.354=1.77）进不来，今天刚发的 1 票也进不来。
HOT_SCORE = 2.0

#: 一个支持数打对折要几天。改动它等于同时改上面那句人话，所以写在紧挨着的地方。
HOT_HALF_LIFE_DAYS = 14.0

#: 「热门」最少显示几条 —— **门槛是稳态标定，而新板子不在稳态**。
#:
#: 5 票这个量级是按一个已经跑起来的平台定的，可平台头一两个月根本到不了：需求方问的
#: 就是这件事（「是不是有点要求太高了？低于一定数量的话应该至少放几个上去」）。一栏
#: 空的「热门」教给读者的是**这一栏坏了**，而不是「还没有东西够热」——而它在开板后
#: 的整段时间里都会是空的。所以够线的照常全收，不够线时按热度补到这个数：这一栏至少
#: 是一个「现在最值得看的几条」，而不是一片空白。
#:
#: 补进来的仍然按同一个分数排序，所以它不是「随便填几条」——是新板子上**排序依然成
#: 立**，只是还没有东西越过那条线。
HOT_MIN_ITEMS = 5


def hot_score() -> Any:
    """一条反馈的热度：支持数按半衰期打折。**排序和判据共用这一个表达式。**

    写成函数而不是在两处各拼一遍：`list_public` 用它排、`public_counts` 和判据用它筛，
    三份拷贝迟早会有一处漏掉衰减——那一处的症状是数字和列表对不上，而两边各自看着都对。

    `func.now()` 在 Postgres 里是 `transaction_timestamp()`，**一条语句里对每一行是同一
    个瞬间**。换成在 Python 里按行取当前时间，同一个查询里每一行会比下一行老一点点，
    排序就不再是那个查询自己的答案（同一条请求重放两次可以给出不同的顺序，OFFSET 翻页
    于是漏行或重复）。这个理由和 `created_at` 需要 `display_no` 做二级排序是同一个。

    `count` 走 LEFT JOIN，所以 0 票的行得 0 分而不是整行消失——它们要能出现在「补足」
    那一段里（新板子上第一条反馈的支持数就是 0）。
    """
    age_days = func.extract("epoch", func.now() - Feedback.created_at) / 86400.0
    return func.count(FeedbackSupport.id) * func.power(
        0.5, age_days / HOT_HALF_LIFE_DAYS
    )


#: What "public" means, as one reusable predicate. `security` is in here as well
#: as in `FeedbackService.may_see`, and the duplication is deliberate: the write
#: path sets the flag and the read path honours it, so the two are separate links
#: of one chain, and a list query that only checked `visibility` would publish a
#: security report while the detail endpoint still hid it. The two have to agree,
#: and this constant is what makes them one definition instead of two copies.
#:
#: A tuple, and copied with ``list(...)`` at every use: a shared *mutable* list of
#: WHERE clauses is one ``extend`` away from growing a clause per request, and the
#: symptom (a list that narrows a little more every day) is not one you would
#: trace back here.
PUBLIC_ONLY: tuple[Any, ...] = (
    Feedback.visibility == FeedbackVisibility.public,
    Feedback.security.is_(False),
)

#: The two rungs that mean 「办完了」. Three places ask "is this still open?" and
#: all three mean the same thing by it — the working tabs (a finished item stops
#: competing for attention), the agent's daily quota, and the 分诊台's 「还没人管」
#: count. Written once so a fifth rung lands in all three at the same time; the
#: shape of the bug when it does not is a tab whose number disagrees with its list.
CLOSED_STATUSES: tuple[FeedbackStatus, ...] = (
    FeedbackStatus.resolved,
    FeedbackStatus.deployed,
)


#: 一次 `IN (...)` 里最多放几个值。**这不是微优化**：asyncpg 把参数个数编进 int16，
#: 超过 32767 直接抛 `InterfaceError` —— 那是谁都过不去的 500，而且楼里评论一多，
#: 详情页对**所有人**一起打不开、一直不恢复。分批之后每一批都远在线的这一侧。
_IN_BATCH = 500


def batched(values: Sequence[Any], size: int | None = None) -> Iterator[Sequence[Any]]:
    """把一串值切成每段最多 `size` 个。见 `_IN_BATCH` 里那条不这么做的后果。

    `size` 是 `None` 时读**当时**的 `_IN_BATCH`，而不是把 `_IN_BATCH` 当默认值写死
    在签名里：写成默认值的话，这个模块常量就再也改不动了（默认值在 def 那一刻就
    定下来了），连测试也没法把批大小压小 —— 而「分批之后合并出来的答案对不对」正是
    需要在小批大小下验的那件事（见
    `test_chunked_in_queries_answer_the_same_as_one_big_one`）。
    """
    items = list(values)
    chunk = _IN_BATCH if size is None else size
    for start in range(0, len(items), chunk):
        yield items[start : start + chunk]


def cursor_of(row: FeedbackComment) -> str:
    """一条评论的游标：`(created_at, id)`，编成不透明字符串给客户端原样带回来。

    带**值**而不是只带 id，是为了翻页不需要再查一次：拿 id 反查时间戳的话，那条
    评论在这中间被删掉，游标当场指向一个查不到的行，翻页就断在那里。带值的话，
    锚点没了也照样接着往下走。

    两列一起，是因为 `created_at` 会撞 —— `NOW()` 在一条语句里对每一行是同一个值，
    批量种子数据尤其明显。只按它排序，同一时刻里的几条在两次查询里顺序可以不一样，
    翻页会漏行或者重复；`id` 是主键，补上它才是全序。
    """
    return f"{row.created_at.isoformat()}|{row.id}"


def parse_cursor(after: str) -> tuple[datetime, uuid.UUID]:
    """游标的逆运算。看不懂的游标抛 `ValueError`，由路由翻成 400。"""
    at, sep, raw = after.partition("|")
    if not sep:
        raise ValueError(f"not a cursor: {after!r}")
    return datetime.fromisoformat(at), uuid.UUID(raw)


def after_cursor(stmt: Select[Any], after: str) -> Select[Any]:
    """把「从这个游标之后接着取」接到一条已排好序的语句上。"""
    at, last_id = parse_cursor(after)
    return stmt.where(
        or_(
            FeedbackComment.created_at > at,
            and_(FeedbackComment.created_at == at, FeedbackComment.id > last_id),
        )
    )


def _group_by_parent(
    rows: Sequence[FeedbackComment],
) -> dict[uuid.UUID, list[FeedbackComment]]:
    """把一堆回复按 `parent_id` 分堆，堆内保持传进来的顺序。

    `dict` 保插入顺序，而 `rows` 是全局按 `(created_at, id)` 升序取回来的，所以分出来
    的每一堆内部也是升序 —— 「前 N 条」和「最后一条当游标」这两件事都靠它成立。
    楼里那条回复的父亲一定在楼里（`parent_id.in_(top_ids)` 已经筛过），所以不处理
    `None`。
    """
    grouped: dict[uuid.UUID, list[FeedbackComment]] = {}
    for row in rows:
        assert row.parent_id is not None
        grouped.setdefault(row.parent_id, []).append(row)
    return grouped


@dataclass(frozen=True)
class CommentPage:
    """一页评论：至多 `limit` 栋楼，连同每栋楼各自的回复。

    `next_cursor` 是下一页的起点，`None` 表示取完了。`reply_counts` 是每栋楼
    **服务端知道**的回复总数 —— 页里那一栋可能只带了前若干条，客户端拿这个数决定
    「展开更多」是把手上已经有的摊开，还是去取下一页。
    """

    rows: list[FeedbackComment]
    next_cursor: str | None
    reply_counts: dict[uuid.UUID, int]
    #: 每栋楼**楼内**的下一页游标；楼里回复已经带全了就不在里面。
    #:
    #: 客户端不自己拼这个串（「最后一条的时间戳 + id」）。拼得出来，但那是把服务端
    #: 的排序规则抄了第二份，改排序的那天两边会漂开 —— 所以游标只由服务端发、客户端
    #: 原样送回来。缺键（不是空串）表示这一栋取完了，和 `reply_counts` 一起读。
    reply_cursors: dict[uuid.UUID, str]


def filed_in_a_room_of(handle: str) -> Any:
    """「这条反馈是在我当时在的那个房间里提的」，写成一条 WHERE 子句。

    结论 47 的第二档。反馈中心是平台级的收件箱，一条私密反馈在那里对所有人是 404；
    但它是在某个房间里提出来的，而**那个房间里的人本来就看过它的内容**——agent 提
    的东西在它的房间里全部留痕（结论 47 第二句）。所以对这些人藏起来只藏掉了追踪它
    的那条路，藏不掉内容本身。

    名册按**反馈提出的那一刻**取（``created_at <= Feedback.created_at``），和补发
    投递取名册同一条规则（结论 58）：否则今天把谁加进这个房间，谁就能回头翻出这个
    房间历史上提过的每一条私密反馈——一次加人变成一次授权，而加人的那个人并不知道
    自己在授权。

    ``topic_id`` 是 ``ON DELETE SET NULL``（反馈比提它的房间活得久），房间没了这一
    档就自动关上，**不用额外写一句**：``topic_id`` 是 NULL 的时候
    ``tm.topic_id = feedback.topic_id`` 恒为 unknown，``EXISTS`` 本来就假。没有房间，
    就没有「那个房间的成员」。

    这条子句是「当时在不在那个房间里」，**不是**整档判据：门还要问「今天还读得到这
    个房间」，那一句在 :meth:`FeedbackService.may_see` 里 —— 见那里的理由。
    """
    return (
        select(TopicMembership.id)
        .where(
            TopicMembership.topic_id == Feedback.topic_id,
            TopicMembership.member_handle == handle,
            TopicMembership.created_at <= Feedback.created_at,
        )
        .exists()
    )


def visible_to(handle: str | None, *, is_admin: bool) -> Any:
    """A deliberate **superset** of `FeedbackService.may_see`, as a WHERE clause.

    Only one reader needs it: 「我的反馈」 is an `OR` over three columns, and the
    third arm (指派给我的) is not by itself a reason to see a row — so it has to
    be pre-filtered in the database rather than fetched whole and thrown away.

    Superset and not equal, on purpose. `may_see`'s room arm also asks 「今天还读
    得到那个房间」 (`may_read_topic`), which is four claims across four tables
    (`app/auth/project_access.py`); spelling those in SQL is the second copy that
    `PUBLIC_ONLY` warns about, and it is the copy that drifts. So the last cut is
    made in Python instead: `FeedbackService.list_mine` puts every row this
    clause returns through `may_see` before answering, which is why the rule has
    exactly one spelling even though this predicate is not all of it.

    What that leaves this clause responsible for: be **wide** enough to lose no
    row `may_see` would open (a row dropped here is never seen again), and narrow
    enough that a page is not mostly rows the caller will never be shown. The
    room arm is here for the first half — without it, a private report filed in
    my room and assigned to me would never reach `may_see` at all.

    The room arm is also not a second spelling of itself: `may_see` asks this very
    clause about one row (`FeedbackRepository.filed_in_a_room_of_mine`) rather
    than re-deciding it in Python, because it needs the roster and the roster is
    in the database either way.

    A second reader of this predicate has to narrow with `may_see` the same way.
    Trusting this clause alone hands out the title and status of a report whose
    detail route answers 404 — 「一条规则两个答案，取决于你问哪一个」.
    """
    if is_admin:
        # may_see's second arm: an admin is never narrowed. Said once here rather
        # than at each call site so the two definitions stay shaped alike.
        return true()
    arms: list[Any] = [and_(*PUBLIC_ONLY)]
    if handle:
        arms.append(Feedback.author_handle == handle)
        arms.append(Feedback.submitted_by_handle == handle)
        arms.append(filed_in_a_room_of(handle))
    return or_(*arms)


def live_comment_clause() -> Any:
    """一条评论算「还在」的条件：自己没被软删，**而且**它的楼还在。

    Why the second half exists, since `deleted_at` alone looks sufficient:
    `soft_delete_comment` stamps a top-level comment and its replies in one
    transaction, but that cascade **cannot be made airtight against a reply
    arriving at the same moment**. Postgres's FK check takes `FOR KEY SHARE` on
    the parent row; the delete's `UPDATE` takes `FOR NO KEY UPDATE`; those two do
    not conflict, so an insert that read the parent a moment earlier commits
    after the cascade has already run, and its reply lands under a parent nobody
    can see. Serialising would mean `SELECT ... FOR UPDATE` on the parent in the
    delete path — which is the lock that *does* conflict with `FOR KEY SHARE` —
    and that would make every like on a comment queue behind a delete.

    Reading the invariant is cheaper and cannot lose the race: visibility is
    decided when the thread is read, so no interleaving of writes can produce a
    visible orphan. It also repairs rows that were orphaned **before** this
    existed, which a write-side fix cannot do for history.

    Used by every reader that counts or returns comments, so the two cannot
    disagree — a card that counts an invisible reply is its own bug, and a
    thread that returns one draws nothing (the client builds the tree from
    top-level rows and their children, so an orphan is silently dropped).

    The write side still cascades and the migration backfills: those are what
    keep `deleted_at` in the table telling the truth, so the next query written
    against this table does not have to know this clause exists.
    """
    parent = aliased(FeedbackComment)
    return and_(
        FeedbackComment.deleted_at.is_(None),
        or_(
            FeedbackComment.parent_id.is_(None),
            select(parent.id)
            .where(
                parent.id == FeedbackComment.parent_id,
                parent.deleted_at.is_(None),
            )
            .exists(),
        ),
    )


def matching(q: str) -> Any:
    """What a search term is matched against, written once for both lists.

    Four columns. The public centre matched two of them (标题 + 摘要) and the
    missing pair is the pair a reader is most likely to remember: **正文**
    (`problem` — the text the submitter actually typed) and **作者**. The summary
    is generated from the body's first 60 characters, so a phrase from the middle
    of a report matched neither column and the row was unreachable by the one
    word its author would have used to find it again.

    The admin queue and the public centre are one search box over overlapping
    rows, so they take the same predicate: a second spelling is how "it comes up
    in the admin queue but not on the centre" gets invented, and the person who
    reports that bug is comparing two screens they believe ask the same question.

    What the reader typed is a **literal**, so the three characters `LIKE` reads
    as syntax are escaped before they become a pattern. Without this, `%` (the
    reader asked for a per-cent sign, or mistyped one) is "any run of characters"
    and the search silently answers with every row; `_` is "any single character"
    and answers with rows that do not contain the character at all. Both are
    wrong in the direction that looks like it worked — a full list, or a list of
    near misses — so neither gets reported as a bug.

    Backslash goes first: it is the escape character, so a reader who typed one
    would otherwise escape whatever follows it and turn a literal into syntax a
    second way.
    """
    literal = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{literal}%"
    return or_(
        Feedback.title.ilike(pattern, escape="\\"),
        Feedback.summary.ilike(pattern, escape="\\"),
        Feedback.problem.ilike(pattern, escape="\\"),
        Feedback.author_handle.ilike(pattern, escape="\\"),
    )


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- 主表 ---------------------------------------------------------------

    async def lock_author(self, handle: str) -> None:
        """Serialize one author's publishes, for the length of this transaction.

        The daily cap below is a read-then-insert, so without this two requests
        that arrive together both read a count below the cap and both insert —
        the cap fails exactly when it is doing its job, which is the one moment
        it has to hold. A lock is the same answer `proposals.py` gives its own
        cap, and for the same reason: it covers the window that needs covering
        and nothing longer, and dying mid-transaction releases it.

        Namespaced with a string so two features cannot collide on the hash of
        one handle.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"feedback-reports:{handle}"},
        )

    async def count_author_since(self, handle: str, since: datetime) -> int:
        """How many reports this person has filed since a moment.

        Counts what they WROTE, not what they sent: `author_handle` is the same
        column the cap's message names, and on the direct path the author is the
        caller. Deleted rows count too — the cap is about how much somebody is
        producing, and deleting a report is not a way to buy more quota.
        """
        stmt = select(func.count(Feedback.id)).where(
            Feedback.author_handle == handle,
            Feedback.created_at >= since,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def add(
        self,
        *,
        title: str,
        summary: str,
        kind: FeedbackKind,
        visibility: FeedbackVisibility,
        problem: str,
        author_handle: str,
        author_user_id: int | None,
        author_is_agent: bool,
        why: str | None = None,
        expectation: str | None = None,
        what_happened: str | None = None,
        repro: str | None = None,
        evidence: str | None = None,
        logs: str | None = None,
        session_id: str | None = None,
        environment: str | None = None,
        submitted_by_handle: str | None = None,
        submitted_by_user_id: int | None = None,
        topic_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        priority: FeedbackPriority = FeedbackPriority.normal,
        tags: list[str] | None = None,
    ) -> Feedback:
        row = Feedback(
            title=title,
            summary=summary,
            kind=kind,
            status=FeedbackStatus.received,
            visibility=visibility,
            priority=priority,
            problem=problem,
            why=why,
            expectation=expectation,
            what_happened=what_happened,
            repro=repro,
            evidence=evidence,
            logs=logs,
            session_id=session_id,
            environment=environment,
            author_handle=author_handle,
            author_user_id=author_user_id,
            author_is_agent=author_is_agent,
            submitted_by_handle=submitted_by_handle,
            submitted_by_user_id=submitted_by_user_id,
            topic_id=topic_id,
            project_id=project_id,
            tags=tags or [],
        )
        self._session.add(row)
        # flush, not commit: the caller's transaction spans the timeline append
        # that must land with it (`services.create`).
        await self._session.flush()
        return row

    async def get(self, feedback_id: uuid.UUID) -> Feedback | None:
        stmt: Select[tuple[Feedback]] = select(Feedback).where(
            Feedback.id == feedback_id, Feedback.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def filed_in_a_room_of_mine(
        self, feedback_id: uuid.UUID, handle: str
    ) -> bool:
        """:func:`filed_in_a_room_of`, asked about one row.

        `may_see` runs the very clause the list query runs, against this one id,
        instead of re-deciding the rule in Python: the answer needs the roster,
        the roster is a table, and a second spelling of a visibility rule is what
        `PUBLIC_ONLY` and `visible_to` both exist to prevent.

        「当时在不在那个房间里」 only. 「今天还读得到那个房间」 is the other half
        and stays in `may_see`.
        """
        stmt = select(Feedback.id).where(
            Feedback.id == feedback_id, filed_in_a_room_of(handle)
        )
        return (await self._session.scalar(stmt)) is not None

    def _list_stmt(
        self,
        *,
        where: Sequence[Any],
        sort: str,
        limit: int,
        offset: int,
    ) -> Select[tuple[Feedback]]:
        # Total on purpose: `supports` is the only non-default branch, so any
        # other string means newest-first rather than "no ordering". Callers that
        # must refuse an unknown sort do it before arriving here (`SORTS` in
        # `services.py`); this function never has to know the vocabulary.
        stmt = select(Feedback).where(Feedback.deleted_at.is_(None), *where)
        if sort == "hot":
            # 「热门」按**热度**排，不按支持数排。两者在有时间衰减之后不是一回事了：
            # 一条两周前的 4 票和一条今天的 2 票热度相同（都是 2.0），按原始支持数排会
            # 把旧的那条放在前面，于是上面那行「两周前的 4 票和今天的 2 票一样热」当场
            # 被这个排序推翻。判据用哪个分，排序就用哪个分。
            stmt = (
                stmt.outerjoin(
                    FeedbackSupport, FeedbackSupport.feedback_id == Feedback.id
                )
                .group_by(Feedback.id)
                .order_by(
                    hot_score().desc(),
                    Feedback.created_at.desc(),
                    Feedback.display_no.desc(),
                )
            )
        elif sort == "supports":
            # `hot` sorts by support count. `GROUP BY feedback.id` rather than a
            # denormalised counter column: MVP lists 20 rows, and this repo has
            # no precedent for a redundant counter (`BlockReaction` has none,
            # `comments.count_votes` is a live aggregate).
            stmt = (
                stmt.outerjoin(
                    FeedbackSupport, FeedbackSupport.feedback_id == Feedback.id
                )
                .group_by(Feedback.id)
                # `display_no` as the tiebreak, same as the `new` branch below:
                # `created_at` comes from the application clock, so two rows made
                # in the same millisecond compare equal and an OFFSET page can
                # repeat or skip one between two requests. An ordering that two
                # rows can tie on is not an ordering.
                .order_by(
                    func.count(FeedbackSupport.id).desc(),
                    Feedback.created_at.desc(),
                    Feedback.display_no.desc(),
                )
            )
        else:
            stmt = stmt.order_by(Feedback.created_at.desc(), Feedback.display_no.desc())
        return stmt.limit(limit).offset(offset)

    async def _count(self, where: Sequence[Any]) -> int:
        stmt = select(func.count(Feedback.id)).where(
            Feedback.deleted_at.is_(None), *where
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    def _hot_ids(self, base: Sequence[Any]) -> Any:
        """「热门」那一栏的行集，作为一个子查询——**列表和计数问的是同一个**。

        两段并起来：够线的（热度 ≥ `HOT_SCORE`）全收，再加上按热度排的前
        `HOT_MIN_ITEMS` 条。后者是补足，不是「随便填几条」：新板子上一条都不够线时，
        它给出的是这个平台上**现在最值得看的五条**，顺序照样成立。

        `base` 是那一栏自己的可见性与沉底条件（`PUBLIC_ONLY` + `_tab_where("hot")`），
        补足必须在**同一批行**里挑。把补足写在整个表上，症状是标签上写 5、点开只有 2
        条——数出来的行和画出来的行来自两个不同的集合。这一段的返回值直接喂给
        `Feedback.id.in_(...)`，列表和 `public_counts` 各用一次，所以那句「数字和列表
        不许对不上」不是靠人记住，是靠它们没有第二份定义可用。
        """
        scored = (
            select(
                Feedback.id.label("id"),
                hot_score().label("score"),
                Feedback.created_at.label("created_at"),
            )
            .outerjoin(FeedbackSupport, FeedbackSupport.feedback_id == Feedback.id)
            .where(Feedback.deleted_at.is_(None), *base)
            .group_by(Feedback.id)
            .subquery()
        )
        above = select(scored.c.id).where(scored.c.score >= HOT_SCORE)
        # 补足那一段要**确定性**排序：`created_at` 来自应用时钟，同一毫秒建的
        # 两行比相等，
        # 而 `LIMIT` 在一个能并列的排序上每次可以给出不同的那几条——翻页时会看到一条
        # 忽有忽无。`id` 是最后那个不会并列的问题。
        floor = (
            select(scored.c.id)
            .order_by(
                scored.c.score.desc(), scored.c.created_at.desc(), scored.c.id.desc()
            )
            .limit(HOT_MIN_ITEMS)
        )
        return above.union(floor)

    def _tab_where(self, tab: str) -> list[Any]:
        """The four public tabs, defined once so list and counts cannot drift.

        A finished item stops competing for attention, so it sinks out of the
        working tabs — but only a finished **bug** does. A finished suggestion
        is a feature the team decided to do and then did; it is still worth
        reading, and hiding it was the wider reading that the product decision
        (§8.23) did not take. The narrow one is also the easier to notice going
        wrong: the wide version quietly removes content nobody is looking for.

        「办完了」 is `CLOSED_STATUSES` — both `resolved` and `deployed`. The
        `resolved` tab is therefore 修复 **和** 上线: to the person who filed it
        those are two halves of one answer (「我的问题没人管了」 is false the moment
        either happens), and listing only the first makes the deployed ones look
        like they went missing. The tab is labelled 「已完成」 rather than after
        either status, because it is named for the pair. `active` is the single
        working rung (`in_progress`); 「已收录」 is not in it, because nobody has
        picked those up yet and 「活跃」 would then mean "everything that is not
        done".

        Consequence worth stating: the tabs are filters, not a partition. A
        finished suggestion is in both `all` and `resolved`, so the tab numbers
        do not sum to a total. That is the decision, not an accounting bug — if
        it ever needs to be a partition, this is the one line to change.

        `public_counts` now reports a `deployed` count **beside** this one, so
        the dashboard can draw 「解决」 and 「上线」 as two lines. The tab's own
        numbers are unchanged: `resolved` still means the pair, and it still
        overlaps `all`. The extra number narrows nothing.
        """
        if tab == "resolved":
            return [Feedback.status.in_(list(CLOSED_STATUSES))]
        sunk = or_(
            Feedback.status.not_in(list(CLOSED_STATUSES)),
            Feedback.kind != FeedbackKind.bug,
        )
        if tab == "active":
            return [sunk, Feedback.status == FeedbackStatus.in_progress]
        return [sunk]

    async def list_public(
        self,
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
        # Everything that narrows the tab **except** the hot rule itself. The hot
        # rule is then expressed against this list rather than appended to it —
        # `where.append(x(_hot_ids(where)))` reads as if the subquery could see the
        # append, and the next person to reorder these two lines would make that
        # true. `q` is part of it too: searching inside 「热门」 asks 「这一栏里哪些
        # 命中」，而补足的那几条也必须来自同一批命中，否则搜完之后这一栏又会多出几条
        # 不匹配的行。
        where: list[Any] = list(PUBLIC_ONLY)
        where.extend(self._tab_where(tab))
        if q:
            where.append(matching(q))
        # 四个筛选**加在栏位之上**，不替换它：它们是同一批行的进一步收窄
        # （`tab` 说的是「哪一栏」，这四个说的是「那一栏里哪些」）。全部走等值比较
        # 与 `>=`，所以都能吃到 `ix_feedback_visibility_status_created` 那一组索引的
        # 前缀；`author` 另有 `ix_feedback_author_created`。
        if author is not None:
            where.append(Feedback.author_handle == author)
        if status is not None:
            where.append(Feedback.status == status)
        if kind is not None:
            where.append(Feedback.kind == kind)
        if since is not None:
            where.append(Feedback.created_at >= since)
        if tab == "hot":
            # `hot` is a filter **and** an ordering, and both come from
            # `hot_score()` — the tab's definition, not just its sort.
            where.append(Feedback.id.in_(self._hot_ids(where)))
            sort = "hot"
        # No membership test here: `_list_stmt` is total (anything that is not
        # `supports` is newest-first), so the vocabulary lives in one place —
        # `services.SORTS`. The public route keeps accepting an unknown sort
        # silently; the admin one refuses it, because there it is a control the
        # client draws and a stale client would render the wrong ordering under
        # the right heading. See `services.list_admin`.
        rows = list(
            (
                await self._session.execute(
                    self._list_stmt(where=where, sort=sort, limit=limit, offset=offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, await self._count(where)

    async def public_counts(self) -> dict[str, int]:
        """The four tab numbers, in one round trip each — returned together.

        Four separate requests would render a row of 0s and then jump, and the
        thing that jumps is the first thing on the page.
        """
        where = list(PUBLIC_ONLY)
        all_count = await self._count([*where, *self._tab_where("all")])
        active_count = await self._count([*where, *self._tab_where("active")])
        resolved_count = await self._count([*where, *self._tab_where("resolved")])
        # 「上线」那一个数，单独给。`resolved` 那一栏装的是**修复 + 上线**这一对
        # （见 `_tab_where` 的 docstring），那个口径不改：对提交的人来说那是同一个
        # 答复的两半。这只是**另外**多报一个数，让看板上「解决」和「上线」两条线画
        # 得出来 —— 缺了它，「上线了多少」在这个平台上从来没有被数过。
        deployed_count = await self._count(
            [*where, Feedback.status == FeedbackStatus.deployed]
        )
        hot_base = [*where, *self._tab_where("hot")]
        # `_tab_where("hot")`, not a second copy of the condition: this is the
        # number on the tab and the rows behind it, and the two were one status
        # apart — a finished suggestion is in the hot list (it does not sink; see
        # the sink rule above) but was not in the number, so the tab counted fewer
        # than it opened. Note the second thing that went wrong there: whoever
        # wrote the count spelled 「办完了」 as a literal `!= resolved`, and when
        # `deployed` was added the copy was silently a status behind.
        # `CLOSED_STATUSES` is why that cannot happen to this one.
        #
        # The docstring above promises one definition for list and counts; this is
        # what that costs when it is not kept.
        # `_hot_ids(hot_base)` — the same subquery `list_public` filters with. The
        # floor makes this number *not* "how many cleared the bar": on a young board
        # it is 「这一栏至少有 5 条」, which is what the tab will open to. Counting
        # only the qualifiers would print 0 on a tab that shows 5 rows.
        hot_count = await self._count(
            [*hot_base, Feedback.id.in_(self._hot_ids(hot_base))]
        )
        return {
            "all": all_count,
            "hot": hot_count,
            "active": active_count,
            "resolved": resolved_count,
            "deployed": deployed_count,
        }

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
        where: list[Any] = []
        if tab == "private":
            # 私密非安全: the column an admin works through when the reporter
            # asked for it not to be public but it has no security implication.
            where.append(Feedback.visibility == FeedbackVisibility.private)
            where.append(Feedback.security.is_(False))
        elif tab == "agent":
            where.append(Feedback.author_is_agent.is_(True))
        elif tab == "security":
            where.append(Feedback.security.is_(True))
        elif tab == "public":
            # The same predicate `list_public` uses, so the admin's "public" view
            # is what the public actually sees. A looser one here would put rows
            # in front of an admin labelled public that no one else can open.
            where.extend(PUBLIC_ONLY)
        if assignee:
            where.append(Feedback.assignee_handle == assignee)
        if q:
            where.append(matching(q))
        if since is not None:
            where.append(Feedback.created_at >= since)
        if resolved_since is not None:
            where.append(self._reached_since(FeedbackStatus.resolved, resolved_since))
        if deployed_since is not None:
            where.append(self._reached_since(FeedbackStatus.deployed, deployed_since))
        rows = list(
            (
                await self._session.execute(
                    self._list_stmt(where=where, sort=sort, limit=limit, offset=offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, await self._count(where)

    # --- 平台看板的两个时间读 --------------------------------------------------

    @staticmethod
    def _reached_since(status: FeedbackStatus, since: datetime) -> Any:
        """这条反馈在 `since` 之后**到过**这个状态 —— 一条 `EXISTS`。

        状态变迁只记在 `feedback_timeline` 上，所以这里问的是时间线，`at >= since`
        走 `ix_feedback_timeline_at`（跨反馈的范围条件，既有那条复合索引的打头列是
        `feedback_id`，用不上）。

        **不为此给 `feedback` 加 `resolved_at` / `deployed_at`**：那要回填历史（时间线
        里有、但列上没有），还要在每个写状态的路径上双写，而两处记同一件事迟早会漂开
        —— 时间线本来就把「什么时候到过这里」记着了（见 `FeedbackTimeline` 的
        docstring：「什么时候到过这里，不是现在在哪」）。
        """
        return exists().where(
            FeedbackTimeline.feedback_id == Feedback.id,
            FeedbackTimeline.status == status,
            FeedbackTimeline.at >= since,
        )

    async def created_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, int]:
        """窗口内按 **UTC 的天**新建的反馈数，稀疏；补 0 由调用方做。"""
        day = utc_day(Feedback.created_at)
        stmt = (
            select(day.label("day"), func.count(Feedback.id))
            .where(
                Feedback.deleted_at.is_(None),
                Feedback.created_at >= since,
                Feedback.created_at < until,
            )
            .group_by(day)
            .order_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row[0].date(): int(row[1]) for row in rows}

    async def reached_series(
        self, *, status: FeedbackStatus, since: datetime, until: datetime
    ) -> dict[date, int]:
        """窗口内按 **UTC 的天**「到过」这个状态的反馈数，稀疏。

        `count(DISTINCT feedback_id)` 而不是 `count(*)`：同一个状态可以有第二行
        （改了又改回来，见 `FeedbackTimeline` 的 docstring），行数是「到过几次」，
        而这条折线画的是「几条反馈」。
        """
        day = utc_day(FeedbackTimeline.at)
        stmt = (
            select(
                day.label("day"),
                func.count(func.distinct(FeedbackTimeline.feedback_id)),
            )
            .where(
                FeedbackTimeline.status == status,
                FeedbackTimeline.at >= since,
                FeedbackTimeline.at < until,
            )
            .group_by(day)
            .order_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row[0].date(): int(row[1]) for row in rows}

    async def list_related_to(
        self, handle: str, *, is_admin: bool, limit: int, offset: int
    ) -> tuple[list[Feedback], int]:
        """「我的反馈」：我提的 + agent 替我提的 + 指派给我的（且我看得到的）。

        One query with an OR rather than three: the list is one list, and the
        `author_handle` / `submitted_by_handle` / `assignee_handle` indexes each
        serve one arm of it.

        The 指派给我的 arm is the only narrowed one. Being handed a report is work,
        not access — the visibility union is 提交者 ∪ 管理员 ∪ 提出它的房间, and
        being the assignee grants no management power (§8.9). So `visible_to` is
        ANDed onto that arm. Without it this was the one read path that skipped
        the predicate in the module docstring, handing a third party the title of
        a report that `may_see` then refused with a 404 — a list that disagrees
        with its own rows.

        `visible_to` is a superset of `may_see`, not the whole of it, so the rows
        this returns are **not yet** the answer: `FeedbackService.list_mine` makes
        the last cut with `may_see` itself. Both halves are needed and neither is
        optional — see `visible_to` for which half lives where and why.
        """
        where = [
            or_(
                Feedback.author_handle == handle,
                Feedback.submitted_by_handle == handle,
                and_(
                    Feedback.assignee_handle == handle,
                    visible_to(handle, is_admin=is_admin),
                ),
            )
        ]
        rows = list(
            (
                await self._session.execute(
                    self._list_stmt(where=where, sort="new", limit=limit, offset=offset)
                )
            )
            .scalars()
            .all()
        )
        return rows, await self._count(where)

    async def set_status(self, row: Feedback, status: FeedbackStatus) -> None:
        row.status = status
        await self._session.flush()

    async def set_priority(self, row: Feedback, priority: FeedbackPriority) -> None:
        row.priority = priority
        await self._session.flush()

    async def set_assignee(self, row: Feedback, assignee: str | None) -> None:
        row.assignee_handle = assignee
        await self._session.flush()

    async def set_security(self, row: Feedback, security: bool) -> None:
        row.security = security
        await self._session.flush()

    async def open_count_by(self, handles: Sequence[str]) -> int:
        """Unresolved reports filed by these handles — the agent quota's read."""
        if not handles:
            return 0
        stmt = select(func.count(Feedback.id)).where(
            Feedback.deleted_at.is_(None),
            Feedback.author_handle.in_(list(handles)),
            Feedback.status.not_in(list(CLOSED_STATUSES)),
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    # --- 支持 -----------------------------------------------------------------

    async def supports_count(self, feedback_id: uuid.UUID) -> int:
        stmt = select(func.count(FeedbackSupport.id)).where(
            FeedbackSupport.feedback_id == feedback_id
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def supports_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Support counts for a page, in one query.

        The alternative — a count per row — is N+1 on the very list that sorts
        by this number.
        """
        if not ids:
            return {}
        stmt = (
            select(FeedbackSupport.feedback_id, func.count(FeedbackSupport.id))
            .where(FeedbackSupport.feedback_id.in_(list(ids)))
            .group_by(FeedbackSupport.feedback_id)
        )
        return {
            row[0]: int(row[1]) for row in (await self._session.execute(stmt)).all()
        }

    async def supported_by(
        self, ids: Sequence[uuid.UUID], handle: str
    ) -> set[uuid.UUID]:
        """Which of these the viewer already supported — the heart's filled state."""
        if not ids:
            return set()
        stmt = select(FeedbackSupport.feedback_id).where(
            FeedbackSupport.feedback_id.in_(list(ids)),
            FeedbackSupport.author_handle == handle,
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def has_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        stmt = select(FeedbackSupport.id).where(
            FeedbackSupport.feedback_id == feedback_id,
            FeedbackSupport.author_handle == handle,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def add_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        """Idempotent: a repeat POST is a no-op, not a second row.

        **`ON CONFLICT DO NOTHING` rather than read-then-insert**, and that is a
        correctness fix rather than a micro-optimisation. The pair of statements
        it replaces — `has_support`, then `add` — is a race the unique constraint
        turns into a 500: two taps of the same button overlapping in flight (a
        double-click on a slow connection is enough — the button is not disabled
        while the request is out) both read "not supported", both insert, and the
        loser's `flush` raises `IntegrityError` out of the route. One statement
        cannot lose that race, and it also drops a round trip.

        `RETURNING id` is what keeps the boolean honest: the row comes back only
        when the insert really happened, so "was it written" costs nothing extra.
        """
        stmt = (
            pg_insert(FeedbackSupport)
            .values(feedback_id=feedback_id, author_handle=handle)
            .on_conflict_do_nothing(index_elements=["feedback_id", "author_handle"])
            .returning(FeedbackSupport.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def remove_support(self, feedback_id: uuid.UUID, handle: str) -> bool:
        """Also idempotent — deleting a support that is not there answers 200."""
        stmt = select(FeedbackSupport).where(
            FeedbackSupport.feedback_id == feedback_id,
            FeedbackSupport.author_handle == handle,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    # --- 评论 -----------------------------------------------------------------

    async def page_comments(
        self,
        feedback_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = THREAD_PAGE,
        replies_limit: int = REPLIES_PAGE,
    ) -> CommentPage:
        """一页评论。**这是取评论的唯一入口**，没有「一次把整条都取回来」那个版本。

        以前有（`list_comments`，无上限）。它的代价不是慢，是三个一起到：
        一个几千条回复的帖子会把整条线程materialize成 ORM 对象和 JSON；同一条线程的
        评论 id 会进 `comment_like_counts` 的 `IN (...)`，过了 32767 个参数就是一条
        500，而这是详情页的主路径 —— 也就是说那条帖子之后**对所有人**打不开，删
        评论才会好。分页把这三件事一起关掉：一页的 id 数由 `limit × (1 + replies_limit)`
        封顶。

        「楼」是分页单位而不是「条」：客户端按 `parent_id` 分组来画，一栋楼拆到两页
        里就会出现一条回复挂不住的父亲。所以顶层按游标取 `limit` 栋，回复跟着每栋楼
        走，楼内那层由 `replies_limit` 单独封顶。
        """
        tops_stmt = (
            select(FeedbackComment)
            .where(
                FeedbackComment.feedback_id == feedback_id,
                FeedbackComment.parent_id.is_(None),
                # 顶层没有父亲，`live_comment_clause()` 在这里就是这一句。
                FeedbackComment.deleted_at.is_(None),
            )
            .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
            # 多取一条只用来回答「还有没有下一页」，它不进返回值。
            .limit(limit + 1)
        )
        if after:
            tops_stmt = after_cursor(tops_stmt, after)
        tops = list((await self._session.execute(tops_stmt)).scalars().all())
        more = len(tops) > limit
        tops = tops[:limit]
        if not tops:
            return CommentPage(
                rows=[], next_cursor=None, reply_counts={}, reply_cursors={}
            )

        top_ids = [top.id for top in tops]
        # 每栋楼多取一条：多的那一条不进返回值，只用来回答「这一栋楼里还有下一页
        # 吗」。取 `limit` 条再猜「大概取完了吧」是不行的 —— 一栋正好 50 条的楼会被
        # 判成还有下一页，客户端于是多发一次必然取到空页的请求。
        fetched = await self._replies_of(top_ids, limit=replies_limit + 1)
        replies: list[FeedbackComment] = []
        cursors: dict[uuid.UUID, str] = {}
        for parent_id, rows in _group_by_parent(fetched).items():
            if len(rows) > replies_limit:
                cursors[parent_id] = cursor_of(rows[replies_limit - 1])
                rows = rows[:replies_limit]
            replies.extend(rows)
        return CommentPage(
            rows=[*tops, *replies],
            next_cursor=cursor_of(tops[-1]) if more else None,
            reply_counts=await self._reply_counts(top_ids),
            reply_cursors=cursors,
        )

    async def page_replies(
        self,
        parent_id: uuid.UUID,
        *,
        after: str | None = None,
        limit: int = REPLIES_PAGE,
    ) -> tuple[list[FeedbackComment], str | None]:
        """一栋楼里的下一段回复。返回 `(rows, next_cursor)`。

        父亲被判掉的那一瞬间，它下面的回复也一起从读侧消失（`live_comment_clause`），
        所以这里直接按 `parent_id` 取就够：调用方刚把这条顶层评论拿在手里，它是不是
        活的已经由那一步回答了。
        """
        stmt = (
            select(FeedbackComment)
            .where(
                FeedbackComment.parent_id == parent_id,
                FeedbackComment.deleted_at.is_(None),
            )
            .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
            .limit(limit + 1)
        )
        if after:
            stmt = after_cursor(stmt, after)
        rows = list((await self._session.execute(stmt)).scalars().all())
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, (cursor_of(rows[-1]) if more and rows else None)

    async def _replies_of(
        self, top_ids: Sequence[uuid.UUID], *, limit: int
    ) -> list[FeedbackComment]:
        """这些楼各自的前 `limit` 条回复 —— 一次查询，每栋楼各数各的。

        `row_number() OVER (PARTITION BY parent_id ...)` 是这件事的正解：按楼分窗、
        窗内按 `(created_at, id)` 排序、每窗取前 N。写成「每栋楼一个 LIMIT」是一页
        50 栋楼 50 次查询；写成「先全取回来再在 Python 里截」等于这条上限没生效，
        而这正是要防的那件事。
        """
        ranked = (
            select(
                FeedbackComment.id.label("id"),
                func.row_number()
                .over(
                    partition_by=FeedbackComment.parent_id,
                    order_by=(
                        FeedbackComment.created_at.asc(),
                        FeedbackComment.id.asc(),
                    ),
                )
                .label("rank"),
            )
            .where(
                FeedbackComment.parent_id.in_(list(top_ids)),
                FeedbackComment.deleted_at.is_(None),
            )
            .subquery()
        )
        stmt = (
            select(FeedbackComment)
            .where(
                FeedbackComment.id.in_(
                    select(ranked.c.id).where(ranked.c.rank <= limit)
                )
            )
            .order_by(FeedbackComment.created_at.asc(), FeedbackComment.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def _reply_counts(self, top_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """每栋楼的回复**总数**（不是页里带了几条），一次 `GROUP BY`。"""
        stmt = (
            select(FeedbackComment.parent_id, func.count(FeedbackComment.id))
            .where(
                FeedbackComment.parent_id.in_(list(top_ids)),
                FeedbackComment.deleted_at.is_(None),
            )
            .group_by(FeedbackComment.parent_id)
        )
        return {
            row[0]: int(row[1]) for row in (await self._session.execute(stmt)).all()
        }

    async def get_comment(self, comment_id: uuid.UUID) -> FeedbackComment | None:
        stmt = select(FeedbackComment).where(
            FeedbackComment.id == comment_id, FeedbackComment.deleted_at.is_(None)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_comment(
        self,
        *,
        feedback_id: uuid.UUID,
        author_handle: str,
        author_user_id: int | None,
        author_is_agent: bool,
        body: str,
        parent_id: uuid.UUID | None,
        reply_to_handle: str | None,
    ) -> FeedbackComment:
        row = FeedbackComment(
            feedback_id=feedback_id,
            parent_id=parent_id,
            author_handle=author_handle,
            author_user_id=author_user_id,
            author_is_agent=author_is_agent,
            body=body,
            reply_to_handle=reply_to_handle,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def soft_delete_comment(self, row: FeedbackComment) -> None:
        """Soft-delete `row`, and the replies that hang under it.

        **The children have to go too, and that is not tidiness.** The client
        renders a thread by taking the comments with no `parent_id` and then
        asking each of those for its replies, while `list_comments` filters
        deleted rows out — so a deleted parent does not merely hide itself:
        every reply under it leaves the screen with it, with no tombstone and
        nothing left that would ever query them again. `FeedbackComment.parent_id`
        already states the intended behaviour («deleting a top-level comment
        takes its replies with it»); this is where that is kept. B站 and 小红书
        answer the same way, and no other reading survives contact with a
        reader: a reply that says 「回复 X」 while X is nowhere on the page is
        worse than either outcome.

        One `UPDATE` for the children rather than a load-and-touch per reply —
        a top-level comment can carry a page of them, and someone is waiting on
        this request. A reply being deleted matches no rows here, which is
        correct rather than lucky: `parent_id` only ever points at a TOP-LEVEL
        comment (the service folds replies onto their grandparent), so a reply
        has no children to begin with.
        """
        deleted_at = datetime.now(UTC)
        row.deleted_at = deleted_at
        await self._session.execute(
            update(FeedbackComment)
            .where(
                FeedbackComment.parent_id == row.id,
                FeedbackComment.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at)
        )
        await self._session.flush()

    async def soft_delete_feedback(self, row: Feedback) -> None:
        """软删一条反馈，**连同它下面所有还在的评论**。

        和 `soft_delete_comment` 同一个形状、同一个理由：读侧过滤 `deleted_at`
        （`_public_where` / `_admin_where` 与 `live_comment_clause`），所以打了时间戳
        的行从列表、详情、计数和搜索里一起消失。

        **评论必须跟着走**：留下的话，它们挂在一条谁也读不到的反馈下面 —— 楼还在、
        帖子没了，而「这栋楼在回哪条反馈」是永远查不出来的那一半。一条 `UPDATE` 全
        带走，不做逐条，理由同评论那一处（有人正在等这个请求）。

        **不硬删**。这一动作有两个调用者（作者删自己的、管理员删别人的），而管理员
        删的是别人写的东西 —— 那是需要留痕的一类动作，`deleted_at` 就是那条痕。
        """
        deleted_at = datetime.now(UTC)
        row.deleted_at = deleted_at
        await self._session.execute(
            update(FeedbackComment)
            .where(
                FeedbackComment.feedback_id == row.id,
                FeedbackComment.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at)
        )
        await self._session.flush()

    # --- 评论点赞 -------------------------------------------------------------
    #
    # Read side is batched for the whole thread, for the same reason the report
    # counts are (`supports_counts`): a thread is a page of rows and the
    # alternative is two queries per reply.

    async def comment_like_counts(
        self, comment_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        counts: dict[uuid.UUID, int] = {}
        for batch in batched(comment_ids):
            stmt = (
                select(
                    FeedbackCommentLike.comment_id,
                    func.count(FeedbackCommentLike.id),
                )
                .where(FeedbackCommentLike.comment_id.in_(list(batch)))
                .group_by(FeedbackCommentLike.comment_id)
            )
            counts.update(
                {
                    row[0]: int(row[1])
                    for row in (await self._session.execute(stmt)).all()
                }
            )
        return counts

    async def comment_like_count(self, comment_id: uuid.UUID) -> int:
        stmt = select(func.count(FeedbackCommentLike.id)).where(
            FeedbackCommentLike.comment_id == comment_id
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def comment_liked_by(
        self, comment_ids: Sequence[uuid.UUID], handle: str
    ) -> set[uuid.UUID]:
        """Which of these the viewer already liked — the button's filled state."""
        liked: set[uuid.UUID] = set()
        for batch in batched(comment_ids):
            stmt = select(FeedbackCommentLike.comment_id).where(
                FeedbackCommentLike.comment_id.in_(list(batch)),
                FeedbackCommentLike.author_handle == handle,
            )
            liked.update((await self._session.execute(stmt)).scalars().all())
        return liked

    async def add_comment_like(self, comment_id: uuid.UUID, handle: str) -> bool:
        """`add_support`, one table down — including why it is one statement."""
        stmt = (
            pg_insert(FeedbackCommentLike)
            .values(comment_id=comment_id, author_handle=handle)
            .on_conflict_do_nothing(index_elements=["comment_id", "author_handle"])
            .returning(FeedbackCommentLike.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def remove_comment_like(self, comment_id: uuid.UUID, handle: str) -> bool:
        """Also idempotent — unliking something not liked answers 200."""
        stmt = select(FeedbackCommentLike).where(
            FeedbackCommentLike.comment_id == comment_id,
            FeedbackCommentLike.author_handle == handle,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    # --- 时间线 ---------------------------------------------------------------

    async def list_timeline(self, feedback_id: uuid.UUID) -> list[FeedbackTimeline]:
        stmt = (
            select(FeedbackTimeline)
            .where(FeedbackTimeline.feedback_id == feedback_id)
            .order_by(FeedbackTimeline.at.asc(), FeedbackTimeline.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def append_timeline(
        self, feedback_id: uuid.UUID, status: FeedbackStatus, by_handle: str | None
    ) -> FeedbackTimeline:
        row = FeedbackTimeline(
            feedback_id=feedback_id, status=status, by_handle=by_handle
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # --- 备注 -----------------------------------------------------------------

    async def list_notes(self, feedback_id: uuid.UUID) -> list[FeedbackNote]:
        stmt = (
            select(FeedbackNote)
            .where(FeedbackNote.feedback_id == feedback_id)
            .order_by(FeedbackNote.created_at.asc(), FeedbackNote.id.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add_note(
        self, feedback_id: uuid.UUID, author_handle: str, body: str
    ) -> FeedbackNote:
        row = FeedbackNote(
            feedback_id=feedback_id, author_handle=author_handle, body=body
        )
        self._session.add(row)
        await self._session.flush()
        return row

    # --- 未读 cursor ----------------------------------------------------------

    async def get_read_state(self, handle: str) -> FeedbackReadState | None:
        stmt = select(FeedbackReadState).where(FeedbackReadState.user_handle == handle)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def bump_read_state(self, handle: str, at: datetime) -> FeedbackReadState:
        row = await self.get_read_state(handle)
        if row is None:
            row = FeedbackReadState(user_handle=handle, last_read_at=at)
            self._session.add(row)
        else:
            row.last_read_at = at
        await self._session.flush()
        return row

    async def count_activity_since(
        self,
        handles: Sequence[str],
        since: datetime | None,
        *,
        is_admin: bool = False,
    ) -> int:
        """New comments + status events on the items these handles are party to.

        Two event tables, counted separately then summed — a UNION would have to
        dedupe rows that carry no shared id, and the sum of two indexed counts is
        cheaper than the sort that costs.

        The 指派给我的 arm carries `visible_to`, exactly as `list_related_to`
        does and for the same reason: being handed a report is work, not access.
        Without it a non-admin assignee of a private row got an unread number for
        a report that the list filters out and the detail endpoint answers with a
        404 — a count saying "something happened" about a row they can never
        open, and that cannot be cleared because there is nothing to read.
        """
        if not handles:
            return 0
        who = list(handles)
        mine = or_(
            Feedback.author_handle.in_(who),
            Feedback.submitted_by_handle.in_(who),
            *[
                and_(
                    Feedback.assignee_handle == handle,
                    visible_to(handle, is_admin=is_admin),
                )
                for handle in who
            ],
        )
        comments_stmt = (
            select(func.count(FeedbackComment.id))
            .join(Feedback, Feedback.id == FeedbackComment.feedback_id)
            .where(
                mine,
                Feedback.deleted_at.is_(None),
                FeedbackComment.deleted_at.is_(None),
                # My own words are not news to me.
                FeedbackComment.author_handle.notin_(who),
            )
        )
        timeline_stmt = (
            select(func.count(FeedbackTimeline.id))
            .join(Feedback, Feedback.id == FeedbackTimeline.feedback_id)
            .where(
                mine,
                Feedback.deleted_at.is_(None),
                or_(
                    FeedbackTimeline.by_handle.is_(None),
                    FeedbackTimeline.by_handle.notin_(who),
                ),
            )
        )
        if since is not None:
            comments_stmt = comments_stmt.where(FeedbackComment.created_at > since)
            timeline_stmt = timeline_stmt.where(FeedbackTimeline.at > since)
        comments = int((await self._session.execute(comments_stmt)).scalar_one() or 0)
        events = int((await self._session.execute(timeline_stmt)).scalar_one() or 0)
        return comments + events

    async def latest_activity_of(
        self, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """Newest comment-or-status time per item, for 「最近活动」 sorting/display."""
        if not ids:
            return {}
        out: dict[uuid.UUID, datetime] = {}
        comment_stmt = (
            select(FeedbackComment.feedback_id, func.max(FeedbackComment.created_at))
            .where(
                FeedbackComment.feedback_id.in_(list(ids)),
                FeedbackComment.deleted_at.is_(None),
            )
            .group_by(FeedbackComment.feedback_id)
        )
        for feedback_id, at in (await self._session.execute(comment_stmt)).all():
            if at is not None:
                out[feedback_id] = at
        event_stmt = (
            select(FeedbackTimeline.feedback_id, func.max(FeedbackTimeline.at))
            .where(FeedbackTimeline.feedback_id.in_(list(ids)))
            .group_by(FeedbackTimeline.feedback_id)
        )
        for feedback_id, at in (await self._session.execute(event_stmt)).all():
            if at is not None and (feedback_id not in out or at > out[feedback_id]):
                out[feedback_id] = at
        return out

    async def unassigned_count(self) -> int:
        """The admin's 「还没人管」 number, asked once per admin list render.

        Deliberately not narrowed by `PUBLIC_ONLY`: "nobody has picked this up"
        spans the private and security queues too, and that is the number the
        分诊台 acts on. It is therefore an **admin-only** read — `FeedbackService.
        counts` is the only caller and it only asks when the caller is an admin,
        because this count, unlike the four tabs, describes rows the caller may
        not be allowed to open.
        """
        stmt = select(func.count(Feedback.id)).where(
            Feedback.deleted_at.is_(None),
            Feedback.assignee_handle.is_(None),
            Feedback.status.not_in(list(CLOSED_STATUSES)),
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def comment_counts(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Live comment counts for a page — one query, same reason as supports."""
        if not ids:
            return {}
        stmt = (
            select(FeedbackComment.feedback_id, func.count(FeedbackComment.id))
            .where(
                FeedbackComment.feedback_id.in_(list(ids)),
                live_comment_clause(),
            )
            .group_by(FeedbackComment.feedback_id)
        )
        return {
            row[0]: int(row[1]) for row in (await self._session.execute(stmt)).all()
        }

    # --- 平台管理员的第二份名单（页面上加的那些） -----------------------------
