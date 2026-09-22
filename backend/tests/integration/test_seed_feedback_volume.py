"""造数脚本自己也得是对的 —— `scripts/seed_feedback_volume.py` 的契约。

它是个测量工具，所以「它造出来的东西长什么样」本身就是被测对象：一批形状不对的
数据会让一次性能测量得出错误结论，而错误结论比没有结论更糟。这里钉四件事：

* **默认 dry-run 一行都不写。** 这是这个仓库脚本的既有约定，也是这个脚本敢被人在
  开发库上随手跑一遍的前提。
* **`reply_to_handle` 的两条规则成立**：顶层恒 NULL，有值当且仅当被回复的那条本身
  是回复；而 `parent_id` 永远指顶层评论。这是 `FeedbackComment` 类说明里那条「两层、
  折回同一栋」的规则，造错的形状在那个模型里根本不存在，测它等于测了个幻觉。
* **`(feedback, author)` 和 `(comment, author)` 不重复**，第二次 `--apply` 也不炸。
* **`--purge` 按前缀清干净，且只清前缀。** 判据是前缀，不是「最近创建的」。

脚本走的是 `async_session_factory`（真提交），测试夹具走的是回滚事务，两者不是同一个
连接 —— 所以这个文件里的行是**真的写进库**的，`finally` 里必须自己清掉。清不掉的
话，前缀行会漏进同一个 worker 后面每一个用例。
"""

from anyio.from_thread import BlockingPortal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.feedback.models import (
    Feedback,
    FeedbackComment,
    FeedbackCommentLike,
    FeedbackKind,
    FeedbackSupport,
    FeedbackTimeline,
    FeedbackVisibility,
)
from app.domain.user.models import User, UserProfile
from scripts import seed_feedback_volume as seed

PREFIX = "volseed"
#: 只在这个文件里出现的前缀，好把「不带前缀的行 purge 不能碰」钉住。
OUTSIDER = "fb-outsider"

#: 小规模但形状齐全的一套参数。`--seed` 固定，断言才不靠运气。
SMALL: tuple[str, ...] = (
    "--feedback",
    "20",
    "--authors",
    "6",
    "--days",
    "120",
    "--long-thread",
    "3",
    "--seed",
    "7",
    "--prefix",
    PREFIX,
)
#: dry-run 那一次走同一套参数，只是不加 `--apply`。
DRY: tuple[str, ...] = (
    "--feedback",
    "5",
    "--authors",
    "6",
    "--days",
    "120",
    "--long-thread",
    "3",
    "--seed",
    "7",
    "--prefix",
    PREFIX,
)


def _run(portal: BlockingPortal, *argv: str) -> int:
    """跑一次脚本（它自己 `asyncio.run` 那条路之外的那个入口）。

    走 portal 而不是自己起一个循环：脚本用的引擎在测试里是 NullPool，但 `db_session`
    的连接活在这个 portal 的循环上，两个循环不能互相碰。
    """
    return portal.call(seed.main, list(argv))


async def _count(session: AsyncSession, model, *conditions) -> int:
    total = await session.scalar(
        select(func.count()).select_from(model).where(*conditions)
    )
    return int(total or 0)


async def _snapshot(session: AsyncSession, prefix: str) -> dict[str, int]:
    """前缀下现在有什么，以及造出来的形状对不对。一次问完，省几十次往返。"""
    like = f"{prefix}%"
    feedback_ids = select(Feedback.id).where(Feedback.author_handle.like(like))
    comment_ids = select(FeedbackComment.id).where(
        FeedbackComment.author_handle.like(like)
    )
    # 一条回复指着的「父」必须自己就是顶层。
    parent = aliased(FeedbackComment)
    deeper = (
        select(func.count())
        .select_from(FeedbackComment)
        .join(parent, FeedbackComment.parent_id == parent.id)
        .where(
            FeedbackComment.feedback_id.in_(feedback_ids), parent.parent_id.is_not(None)
        )
    )
    # `reply_to_handle` 有值，就必须真有一条同楼的、作者是那个 handle 的回复。
    sibling = aliased(FeedbackComment)
    dangling = (
        select(func.count())
        .select_from(FeedbackComment)
        .where(
            FeedbackComment.feedback_id.in_(feedback_ids),
            FeedbackComment.reply_to_handle.is_not(None),
            ~select(sibling.id)
            .where(
                sibling.parent_id == FeedbackComment.parent_id,
                sibling.author_handle == FeedbackComment.reply_to_handle,
            )
            .exists(),
        )
    )
    long_threads = select(func.count()).select_from(
        select(FeedbackComment.feedback_id)
        .where(FeedbackComment.feedback_id.in_(feedback_ids))
        .group_by(FeedbackComment.feedback_id)
        .having(func.count() >= 80)
        .subquery()
    )
    duplicate_supports = select(func.count()).select_from(
        select(FeedbackSupport.feedback_id)
        .group_by(FeedbackSupport.feedback_id, FeedbackSupport.author_handle)
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_likes = select(func.count()).select_from(
        select(FeedbackCommentLike.comment_id)
        .group_by(FeedbackCommentLike.comment_id, FeedbackCommentLike.author_handle)
        .having(func.count() > 1)
        .subquery()
    )
    return {
        "feedback": await _count(session, Feedback, Feedback.author_handle.like(like)),
        "outsiders": await _count(
            session, Feedback, Feedback.author_handle == OUTSIDER
        ),
        "comments": await _count(
            session, FeedbackComment, FeedbackComment.id.in_(comment_ids)
        ),
        "supports": await _count(
            session, FeedbackSupport, FeedbackSupport.feedback_id.in_(feedback_ids)
        ),
        "likes": await _count(
            session,
            FeedbackCommentLike,
            FeedbackCommentLike.comment_id.in_(comment_ids),
        ),
        "timeline": await _count(
            session, FeedbackTimeline, FeedbackTimeline.feedback_id.in_(feedback_ids)
        ),
        "users": await _count(session, User, User.username.like(like)),
        "profiles": await _count(
            session,
            UserProfile,
            UserProfile.user_id.in_(select(User.id).where(User.username.like(like))),
        ),
        "top_with_reply_to": await _count(
            session,
            FeedbackComment,
            FeedbackComment.parent_id.is_(None),
            FeedbackComment.reply_to_handle.is_not(None),
        ),
        "deep_replies": int(await session.scalar(deeper) or 0),
        "dangling_reply_to": int(await session.scalar(dangling) or 0),
        "reply_with_target": await _count(
            session,
            FeedbackComment,
            FeedbackComment.reply_to_handle.is_not(None),
        ),
        "long_threads": int(await session.scalar(long_threads) or 0),
        "duplicate_supports": int(await session.scalar(duplicate_supports) or 0),
        "duplicate_likes": int(await session.scalar(duplicate_likes) or 0),
    }


async def _insert_outsider(session: AsyncSession) -> None:
    """一条不带前缀的反馈 —— purge 的判据必须是前缀，不能碰它。"""
    session.add(
        Feedback(
            title="别人手写的条目",
            summary="不属于造数脚本",
            # `kind` 在模型上没有默认值（status/priority 才有），必须显式给 ——
            # 这一条也正是「这个脚本不能凭印象写字段」的证据。
            kind=FeedbackKind.bug,
            visibility=FeedbackVisibility.public,
            problem="这条是测试自己插的，不在任何前缀里。",
            author_handle=OUTSIDER,
        )
    )
    await session.flush()


def _snap(portal: BlockingPortal, session: AsyncSession) -> dict[str, int]:
    return portal.call(_snapshot, session, PREFIX)


def test_seed_feedback_volume_shapes_and_purges(
    db_session: AsyncSession, _portal: BlockingPortal
) -> None:
    # 防御性清一次：上一次跑挂在这里、没收尾的行不该让这次也挂。
    assert _run(_portal, "--purge", "--apply", "--prefix", PREFIX) == 0
    _portal.call(_insert_outsider, db_session)
    assert _snap(_portal, db_session)["feedback"] == 0

    try:
        # ① 不带 --apply：一行都不写。
        assert _run(_portal, *DRY) == 0
        dry = _snap(_portal, db_session)
        assert dry["feedback"] == 0, dry
        assert dry["users"] == 0, dry
        assert dry["comments"] == 0, dry

        # ② 小规模 apply 一次。
        assert _run(_portal, "--apply", *SMALL) == 0
        snap = _snap(_portal, db_session)

        assert snap["feedback"] == 20
        assert snap["users"] == 7, "6 个人 + 1 个 agent，每个都该有真的 user 行"
        assert snap["profiles"] == snap["users"], "头像那条 join 要有对象"
        assert snap["comments"] == 769
        assert snap["supports"] > 0 and snap["likes"] > 0
        assert snap["timeline"] >= snap["feedback"], "状态和时间线必须一起写"

        # ③ 楼中楼的两条规则。
        assert snap["top_with_reply_to"] == 0, "顶层评论没有回复对象"
        assert snap["deep_replies"] == 0, "parent_id 只能指顶层评论"
        assert snap["dangling_reply_to"] == 0, "回复的回复必须真有一条被回复的那条"
        assert snap["reply_with_target"] > 0, "否则上面两条是空断言"
        assert snap["long_threads"] >= 3, "长楼那几条要有 80+ 回复"

        # ④ 唯一约束那两张表的形状。
        assert snap["duplicate_supports"] == 0
        assert snap["duplicate_likes"] == 0

        # ⑤ 连跑两次不炸（会多出一批新行，这是造数工具，允许）。
        assert _run(_portal, "--apply", *SMALL) == 0
        twice = _snap(_portal, db_session)
        assert twice["feedback"] == 40, twice
        assert twice["duplicate_supports"] == 0 and twice["duplicate_likes"] == 0
        assert twice["users"] == 7, "第二次应当复用已存在的 username，不重复插 user"
    finally:
        assert _run(_portal, "--purge", "--apply", "--prefix", PREFIX) == 0

    # ⑥ purge 之后带前缀的行全部消失，不带前缀的那条还在。
    end = _snap(_portal, db_session)
    assert end["feedback"] == 0
    assert end["comments"] == 0
    assert end["supports"] == 0
    assert end["likes"] == 0
    assert end["timeline"] == 0
    assert end["users"] == 0 and end["profiles"] == 0
    assert end["outsiders"] == 1, "purge 的判据是前缀，不是「最近创建的」"


def test_the_recent_month_is_the_dense_one() -> None:
    """近 30 天更密 —— 这是形状本身的一部分，不需要真写库就能钉。"""
    dataset = seed.build(seed.Spec(feedback=800, days=120, seed=7))
    now = max(row.created_at for row in dataset.feedback)
    within = sum(1 for row in dataset.feedback if (now - row.created_at).days < 30)
    assert within / len(dataset.feedback) > 0.45, "近 30 天应当明显更密（预期约 60%）"


def test_every_row_carries_the_prefix() -> None:
    """凡是脚本造出来的 handle 都带前缀 —— purge 只有靠这个才清得干净。"""
    dataset = seed.build(seed.Spec(feedback=20, authors=6, seed=7))
    handles = {row.author_handle for row in dataset.feedback}
    handles |= {row.author_handle for row in dataset.comments}
    handles |= {row.author_handle for row in dataset.supports}
    handles |= {row.author_handle for row in dataset.likes}
    handles |= {
        handle
        for row in dataset.feedback
        for handle in (row.submitted_by_handle, row.assignee_handle)
        if handle
    }
    handles |= {row.username for row in dataset.users}
    assert handles, "空集合会让这条断言白过"
    assert all(handle.startswith(PREFIX) for handle in handles), sorted(handles)


def test_purge_preview_writes_nothing(
    db_session: AsyncSession, _portal: BlockingPortal
) -> None:
    """`--purge` 不加 `--apply` 也只打印，不删 —— dry-run 是默认，删除也一样。"""
    assert _run(_portal, "--apply", *SMALL) == 0
    try:
        before = _snap(_portal, db_session)
        assert before["feedback"] == 20
        assert _run(_portal, "--purge", "--prefix", PREFIX) == 0
        after = _snap(_portal, db_session)
        assert after["feedback"] == before["feedback"]
        assert after["users"] == before["users"]
        assert after["comments"] == before["comments"]
    finally:
        assert _run(_portal, "--purge", "--apply", "--prefix", PREFIX) == 0
