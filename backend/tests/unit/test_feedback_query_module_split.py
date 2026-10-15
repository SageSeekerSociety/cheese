"""`repositories.py` 拆出 `queries.py` 之后，它那一面必须和拆之前一样。

拆的是文件，不是行为。三件事要守住：

**旧路径照旧回答。** 仓库内外一直按 `from app.domain.feedback.repositories
import ...` 取这些名字，按名字打 monkeypatch 也指着这个路径。它们现在定义在
`queries.py`，`repositories.py` 负责再导出。

**再导出不是再定义。** 两边必须是同一个对象，这个模块里也不该再出现一份定义。拆开
之后最可能的退化是有人照着又写一份 —— 而 `PUBLIC_ONLY`、`hot_score()` 的 docstring
讲的正是「两份拷贝就是数字和列表对不上的来路」。

**批大小的那个开关仍然长在这个模块上。** `tests/integration/test_feedback.py` 是
按**本模块**的对象把 `_IN_BATCH` 压到 2 来验分块的。`batched` 跟着名字搬去
`queries.py` 的话，那条测试再也压不动它，等于不再验分块，而且不会红。
"""

import ast
import pathlib
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback import queries, repositories
from app.domain.feedback.repositories import FeedbackRepository

#: 拆分前住在 `repositories.py` 顶层、由这个路径回答的名字。
MOVED = (
    "CLOSED_STATUSES",
    "HOT_HALF_LIFE_DAYS",
    "HOT_MIN_ITEMS",
    "HOT_SCORE",
    "PUBLIC_ONLY",
    "CommentPage",
    "_group_by_parent",
    "_like_pattern",
    "after_cursor",
    "cursor_of",
    "filed_in_a_room_of",
    "hot_score",
    "live_comment_clause",
    "matching",
    "parse_cursor",
    "search_rank",
    "visible_to",
)


@pytest.mark.parametrize("name", MOVED)
def test_the_repositories_path_still_answers_every_name_it_used_to(name: str) -> None:
    assert hasattr(repositories, name), (
        f"`app.domain.feedback.repositories.{name}` 不见了：调用点按这个路径取它，"
        "搬去 `queries.py` 之后必须在这里再导出一次。"
    )


@pytest.mark.parametrize("name", MOVED)
def test_the_move_did_not_become_a_second_definition(name: str) -> None:
    assert getattr(repositories, name) is getattr(queries, name), (
        f"`{name}` 在 `repositories.py` 和 `queries.py` 里是两个对象 —— "
        "这是把名字又定义了一遍，不是再导出。"
    )


def test_none_of_the_moved_names_is_defined_here() -> None:
    """`is` 一个人挡不住「又写了一份」：`HOT_MIN_ITEMS = 5` 再写一行，取到的还是
    同一个对象 —— 解释器把 -5..256 的整数驻留了，`5 is 5` 为真。也就是说只查对象
    相等的话，再定义一份小整数常量会无声通过，而 `_hot_ids` 用它定 `LIMIT`、路由
    用它报给客户端，两边漂开就是「标签写 5、点开只有 2 条」。

    所以这里读源码：被搬走的名字在这个模块里只许出现在那条再导出的 import 里，
    不许作为定义出现。
    """
    tree = ast.parse(pathlib.Path(repositories.__file__).read_text())
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
    assert not defined & set(MOVED), (
        "这些名字在 `repositories.py` 里被又定义了一份，而不是再导出："
        f"{sorted(defined & set(MOVED))}"
    )


def test_the_chunk_size_is_read_from_this_module_at_call_time(monkeypatch) -> None:
    """`batched` 按**本模块**的 `_IN_BATCH` 分批，压小它就压小了每一批。"""
    monkeypatch.setattr(repositories, "_IN_BATCH", 2)
    assert [list(chunk) for chunk in repositories.batched(range(5))] == [
        [0, 1],
        [2, 3],
        [4],
    ]


def test_the_listing_builders_are_still_methods_of_the_repository() -> None:
    """三个构造器按方法被调用（`test_feedback_sort_ordering.py` 就是这么调的）。"""
    repo = FeedbackRepository(cast(AsyncSession, None))
    for name in ("_list_stmt", "_hot_ids", "_tab_where"):
        assert callable(getattr(repo, name)), name


def _order_tail(sort: str, q: str | None = None) -> str:
    repo = FeedbackRepository(cast(AsyncSession, None))
    stmt = repo._list_stmt(where=[], sort=sort, limit=20, offset=0, q=q)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    return sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0].strip()


def test_the_hot_sort_still_leads_with_the_decayed_score() -> None:
    """「热门」按**热度**排，不按支持数排 —— 头一项是那条带衰减的表达式。"""
    tail = _order_tail("hot")
    assert tail.startswith("count(feedback_supports.id) * power("), tail
    assert "EXTRACT(epoch FROM now()" in tail, tail


def test_a_search_still_outranks_the_tab_order() -> None:
    """搜索问的是「哪条最像我要找的」，排在栏位顺序之前，栏位顺序仍在下面。"""
    tail = _order_tail("new", q="报表")
    assert tail.startswith("CASE WHEN"), tail
    assert tail.endswith("feedback.created_at DESC, feedback.display_no DESC"), tail


def test_a_query_with_no_terms_leaves_the_order_alone() -> None:
    """空白不是搜索：只有空格的 `q` 不该改变任何一栏的顺序。"""
    assert queries.search_rank("   ") is None
    assert _order_tail("new", q="   ") == _order_tail("new")
