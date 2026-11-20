"""feedback 的**读词汇表**：条件、游标、排序、热度分，以及一栏的行集怎么拼。

这里全是**表达式**，没有一条语句在这里被执行 —— 拿 session 把它们跑掉的是
`repositories.py`。分开的理由是文件体积（`backend/app/` 单文件 1500 行的上限），
不是分层：这两半本来就是同一件事的写法与执行，原来挤在一个 1681 行的文件里。

为什么这些判据要有名字、要有唯一的一份，写在每一处的 docstring 里（`PUBLIC_ONLY`、
`hot_score()`、`_tab_where` 各说了一条）：**「列表里的行」和「标签上的数字」必须是
同一个问题的两个答案**。两处各拼一遍 SQL，症状是数字和列表对不上，而两边各自看着
都对 —— 那是没人会去追的那一类 bug。

`repositories.py` 会把这里的名字再导出一次：仓库内外一直按
`from app.domain.feedback.repositories import ...` 取它们，拆文件不该让任何一行
调用点跟着改。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Select,
    and_,
    case,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.orm import aliased

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackStatus,
    FeedbackSupport,
    FeedbackVisibility,
)
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

#: 「办完了」: fixed, shipped, or declined. The working tabs (a finished item stops
#: competing for attention), the agent's daily quota and the 分诊台's 「还没人管」
#: count all ask "is this still open?" — written once so a new status lands in all
#: three at once; otherwise a tab's number disagrees with its list.
CLOSED_STATUSES: tuple[FeedbackStatus, ...] = (
    FeedbackStatus.resolved,
    FeedbackStatus.deployed,
    FeedbackStatus.declined,
)


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

    **Each word is its own term, and the terms are ANDed.** One pattern over the
    whole query means 「导出 报表」 only finds a row where those two words are
    adjacent with that exact space — so the reader who names two things they
    remember gets *nothing*, while a reader who happens to remember one of them
    gets their row. That is backwards: the more you remember, the fewer results.
    Terms are ANDed rather than ORed because a second word is a further
    restriction, not an alternative (「导出 报表」 is not 「导出」 or 「报表」).

    Recall, not ranking: the rows come back in the tab's order, not by how well
    each one matches. Relevance ranking is a different and larger thing — it
    needs a text index (`pg_search` is in the image, unused) and a maintenance
    story for a table that also takes status updates.
    """
    columns = (
        Feedback.title,
        Feedback.summary,
        Feedback.problem,
        Feedback.author_handle,
    )
    terms = q.split()
    if not terms:
        # Whitespace is not a search: it must not be read as 「find rows with a
        # space in them」 (what the single-pattern version did) nor as nothing at
        # all (an empty AND would match every row and silently drop the filter
        # the caller thinks it applied).
        return true()
    return and_(
        *[
            or_(*[column.ilike(_like_pattern(term), escape="\\") for column in columns])
            for term in terms
        ]
    )


def _like_pattern(term: str) -> str:
    """One search term, as a `LIKE` pattern with its own syntax escaped."""
    literal = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{literal}%"


def search_rank(q: str) -> Any | None:
    """How well a row answers what was typed, as an integer to sort on.

    **The title is the only weight**, and that is a deliberate floor rather than a
    finished ranking. A term that lands in the title is nearly always the thing
    the reader meant — titles are written to be the one line that says what the
    report is about — while a term in the body is often incidental (a stack
    trace, a word in passing). Counting **how many of the typed terms hit the
    title** is the cheapest honest version of 「哪条最像是我要找的」, and it needs
    nothing that is not already in the query: no new column, no index, no
    extension.

    What it is NOT, and the reason it is not called relevance in the usual sense:
    it ignores term frequency and rarity, so it cannot tell a row that says
    「报表」 five times from one that says it once. Doing that needs a text index
    with statistics behind it (`pg_search` is in the image and unused — see issue
    #1461 for what that would take). Until then, this orders the rows a reader is
    most likely to have meant above the ones whose only mention was somewhere in
    the body, which is the difference they actually notice.

    Returns None for a query with no terms, so the caller keeps its own order —
    whitespace is not a search.
    """
    terms = q.split()
    if not terms:
        return None
    exprs = [
        case((Feedback.title.ilike(_like_pattern(term), escape="\\"), 1), else_=0)
        for term in terms
    ]
    rank = exprs[0]
    for expr in exprs[1:]:
        rank = rank + expr
    return rank


class ListingQueries:
    """一栏的行集、它的排序、它的数字 —— 三个都从这里出来，所以不会漂开。

    这是 `FeedbackRepository` 的一个基类而不是模块函数，理由只有一个：这三个方法
    一直是**按方法**被调用的（`tests/unit/test_feedback_sort_ordering.py` 拿一个
    仓储实例问 `_list_stmt` 拼出了什么），搬到 `queries.py` 里换个形态就要连测试
    一起改，而这次拆分不许改变任何已有调用点。它们本来就不碰 `self` 的实例状态 ——
    所以放在这个没有 `__init__` 的基类里，与放在模块里是同一件事。
    """

    def _list_stmt(
        self,
        *,
        where: Sequence[Any],
        sort: str,
        limit: int,
        offset: int,
        q: str | None = None,
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
            stmt = stmt.outerjoin(
                FeedbackSupport, FeedbackSupport.feedback_id == Feedback.id
            ).group_by(Feedback.id)
            order: list[Any] = [
                hot_score().desc(),
                Feedback.created_at.desc(),
                Feedback.display_no.desc(),
            ]
        elif sort == "supports":
            # `hot` sorts by support count. `GROUP BY feedback.id` rather than a
            # denormalised counter column: MVP lists 20 rows, and this repo has
            # no precedent for a redundant counter (`BlockReaction` has none,
            # `comments.count_votes` is a live aggregate).
            stmt = stmt.outerjoin(
                FeedbackSupport, FeedbackSupport.feedback_id == Feedback.id
            ).group_by(Feedback.id)
            # `display_no` as the tiebreak, same as the `new` branch below:
            # `created_at` comes from the application clock, so two rows made
            # in the same millisecond compare equal and an OFFSET page can
            # repeat or skip one between two requests. An ordering that two
            # rows can tie on is not an ordering.
            order = [
                func.count(FeedbackSupport.id).desc(),
                Feedback.created_at.desc(),
                Feedback.display_no.desc(),
            ]
        else:
            order = [Feedback.created_at.desc(), Feedback.display_no.desc()]
        # **What the search found, before which tab it is in.** The tab's order
        # answers 「这一栏里先看哪条」, and a reader who just typed something is
        # asking a narrower question: 「哪条最像是我要找的」. Answering the second
        # one with the first one's order is what makes a search feel like it
        # ignored you — the row you meant sits wherever its age puts it.
        #
        # Still the tab's order underneath, so the two are consistent for paging
        # and a search inside 「热门」 stays hot-ordered among equals.
        if q:
            rank = search_rank(q)
            if rank is not None:
                order.insert(0, rank.desc())
        return stmt.order_by(*order).limit(limit).offset(offset)

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
        """The three public tabs, defined once so list and counts cannot drift.

        `all` and `hot` narrow nothing: every public report is in both, whatever
        its kind or its status. A finished **bug** used to sink out of them
        (§8.23 — 「默认不展示已解决的 bug」 read narrowly, as only the bug), and
        that left the tab named 「全部」 as the one place a third of the board was
        missing from: it counted 16 while 「已完成」 counted 68, and nothing on
        screen said where the rest had gone. Reversed 2026-10-28 — a report the
        platform holds is reachable from 「全部」, and the status pills, not the
        tab, are how a reader narrows to one rung. It also makes the tab honest:
        「全部」 is now the widest cut rather than the widest cut minus something.

        `resolved` is the only tab with a rule, and it is the closed set rather
        than the status of the same name: to the person who filed it 修复 and
        上线 are two halves of one answer, and 不修复 is an answer too; listing
        only the first makes the others look like they went missing. The tab is
        labelled 「已完成」 because it is named for the set.

        Consequence worth stating: the tabs are filters, not a partition. A
        finished report is in both `all` and `resolved`, so the tab numbers do
        not sum to a total. That is the decision, not an accounting bug — if it
        ever needs to be a partition, this is the one line to change.

        `public_counts` also reports a `deployed` count, so the dashboard can
        draw 「解决」 and 「上线」 as two lines. It narrows nothing here either.
        """
        if tab == "resolved":
            return [Feedback.status.in_(list(CLOSED_STATUSES))]
        return []
