"""门面守卫：``app.domain.feedback.repositories`` 拆文件之后，行为和公开面一个都不能变。

2026-10-15 那次拆分把 ``FeedbackRepository`` 的 50 个方法体搬进了
``app/domain/feedback/repositories/`` 这个包里的 ``reads`` / ``comments`` /
``writes`` / ``stats``，类上只留同名转发。调用方完全没改：``services/`` 读的仍是
``from app.domain.feedback import repositories as repo``（``repo.CLOSED_STATUSES``、
``repo.FeedbackRepository``），集成测试按模块对象压的 ``_IN_BATCH`` 还是同一个常量，
``feedback_repo.THREAD_PAGE`` 也还在。

这道测试不连数据库，钉住五件事：

1. 旧路径仍然答得出原来那一组名字，而且都是同一个对象（不是复制出来的一份）。
2. ``FeedbackRepository`` 的 51 个方法（含 ``__init__``）一个不少，而且是**真方法**
   （类自己的 ``__dict__`` 里就有），不是 ``__getattr__`` 之类动态转发的。
3. ``__init__`` 没动：``_session`` 仍从这里赋值。
4. **拆的是文件，不是行为**：每个方法体恰好是一句转发，转给四个子模块里同名、首参为
   ``repository`` 的那一个函数。谁把方法体抄回类里一份，这条就红。
5. ``batched`` / ``_IN_BATCH`` 仍**定义在包里**：集成测试是按这个模块对象把批大小压到
   2 的，搬进子模块之后那条测试会静默地不再验分块 —— 它照样绿。
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.domain.feedback import queries
from app.domain.feedback import repositories as feedback_repo
from app.domain.feedback.paging import REPLIES_PAGE, THREAD_PAGE
from app.domain.feedback.repositories import (
    comments,
    reads,
    stats,
    writes,
)

# 51 个方法，照搬运之前那份 repositories.py 里 FeedbackRepository 的类体抄的。
# 顺序按定义先后；__init__ 也在内。
FEEDBACK_REPOSITORY_METHODS = [
    "__init__",
    "lock_author",
    "count_author_since",
    "add",
    "get",
    "filed_in_a_room_of_mine",
    "_count",
    "list_public",
    "public_counts",
    "list_admin",
    "_reached_since",
    "created_series",
    "reached_series",
    "count_created_between",
    "count_reached_resolved_between",
    "list_related_to",
    "set_status",
    "set_priority",
    "set_assignee",
    "set_security",
    "open_count_by",
    "supports_count",
    "supports_counts",
    "supported_by",
    "has_support",
    "add_support",
    "remove_support",
    "page_comments",
    "page_replies",
    "_replies_of",
    "_reply_counts",
    "get_comment",
    "add_comment",
    "soft_delete_comment",
    "soft_delete_feedback",
    "comment_like_counts",
    "comment_like_count",
    "comment_liked_by",
    "add_comment_like",
    "remove_comment_like",
    "list_timeline",
    "append_timeline",
    "list_notes",
    "add_note",
    "get_read_state",
    "bump_read_state",
    "count_activity_since",
    "latest_activity_of",
    "unassigned_count",
    "admin_counts",
    "comment_counts",
]

#: 方法体搬去的那四个模块，按它们在类里被引用的名字。
SIBLINGS = {
    "reads": reads,
    "comments": comments,
    "writes": writes,
    "stats": stats,
}

#: 不碰 session 的那一个：类上仍是 ``@staticmethod``，子模块里的函数也就没有实例参数。
STATIC_METHODS = {"_reached_since"}


def test_the_old_import_path_still_answers_its_names() -> None:
    """调用点读的就是这几个名字；少一个，那边是 ImportError 或 AttributeError。"""
    assert feedback_repo.FeedbackRepository is not None
    # 这些名字定义在 `queries.py`，在原模块只再导出一次——两个路径必须同一个对象。
    assert feedback_repo.CLOSED_STATUSES is queries.CLOSED_STATUSES
    assert feedback_repo.PUBLIC_ONLY is queries.PUBLIC_ONLY
    assert feedback_repo.CommentPage is queries.CommentPage
    assert feedback_repo.visible_to is queries.visible_to
    assert feedback_repo.matching is queries.matching
    assert feedback_repo.hot_score is queries.hot_score
    assert isinstance(feedback_repo.HOT_SCORE, (int, float))
    # `services.writes` 读 `repo.CLOSED_STATUSES`，路由读 `repo.THREAD_PAGE`——
    # 两个数住在 `paging.py`，这里再导出一遍。
    assert feedback_repo.THREAD_PAGE is THREAD_PAGE
    assert feedback_repo.REPLIES_PAGE is REPLIES_PAGE


def test_the_batching_helpers_are_defined_here_not_re_exported() -> None:
    """``batched`` / ``_IN_BATCH`` 必须**定义在包上**，这是集成测试的 patch 点。

    `tests/integration/test_feedback.py` 按 ``app.domain.feedback.repositories``
    这个模块对象把 ``_IN_BATCH`` 压到 2 来验分块。搬进子模块、或者让子模块从别处
    拿一份副本，那条测试就再也压不动它 —— 而它照样是绿的，等于不再验分块。
    """
    assert "_IN_BATCH" in vars(feedback_repo), "常量被搬走了，集成测试压不动它"
    assert feedback_repo.batched.__module__ == "app.domain.feedback.repositories"
    assert feedback_repo._IN_BATCH == 500


def test_the_chunk_size_is_read_from_this_package_at_call_time(monkeypatch) -> None:
    """批大小是**调用时**读的：压小之后真按小批切，分块那条测试才立得住。"""
    monkeypatch.setattr(feedback_repo, "_IN_BATCH", 2)
    assert [list(chunk) for chunk in feedback_repo.batched(range(5))] == [
        [0, 1],
        [2, 3],
        [4],
    ]


def test_the_comment_readers_batch_through_the_package(monkeypatch) -> None:
    """评论那两个读批次的入口，用的是**包上**的那个 ``batched``。

    它们在 ``comments.py`` 里，而常量按 patch 点留在包上，所以这里走的是
    ``repo.batched``。这条钉住那条捷径不会被改成从别处 import 一份。
    """
    assert comments.repo is feedback_repo
    monkeypatch.setattr(feedback_repo, "_IN_BATCH", 2)
    assert [list(chunk) for chunk in comments.repo.batched(range(5))] == [
        [0, 1],
        [2, 3],
        [4],
    ]


def test_the_repository_keeps_all_51_methods() -> None:
    """51 个方法一个不少，而且都是类自己定义的。"""
    assert len(FEEDBACK_REPOSITORY_METHODS) == 51
    found = {name for name, _ in inspect.getmembers(feedback_repo.FeedbackRepository)}
    assert [n for n in FEEDBACK_REPOSITORY_METHODS if n not in found] == []

    # 真方法：类自己的 __dict__ 里就有，不是动态转发来的。
    own = set(vars(feedback_repo.FeedbackRepository))
    assert [n for n in FEEDBACK_REPOSITORY_METHODS if n not in own] == []
    assert "__getattr__" not in own


def test_the_query_helper_still_comes_through_as_the_base_class() -> None:
    """`queries.ListingQueries` 仍是基类：`_list_stmt` / `_tab_where` 还在它上面。"""
    assert issubclass(feedback_repo.FeedbackRepository, queries.ListingQueries)
    for helper in ("_list_stmt", "_hot_ids", "_tab_where"):
        assert hasattr(feedback_repo.FeedbackRepository, helper), helper


def test_init_still_builds_the_session() -> None:
    """拆分没动 __init__：仍**在这里**把 session 赋给 `_session`。

    看的是赋值本身，不是「源码里出现过这个词」——把赋值改成一句提到 `_session` 的
    注释不该让这条过。
    """
    init = ast.parse(
        textwrap.dedent(inspect.getsource(feedback_repo.FeedbackRepository.__init__))
    ).body[0]
    assert [a.arg for a in init.args.args] == ["self", "session"]

    assigned: dict[str, str] = {}
    for node in ast.walk(init):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                assigned[target.attr] = ast.unparse(node.value)
    assert assigned == {"_session": "session"}


@pytest.mark.parametrize(
    "name", [n for n in FEEDBACK_REPOSITORY_METHODS if n != "__init__"]
)
def test_every_method_is_one_forward_to_its_sibling_module(name: str) -> None:
    """方法体恰好一句转发，转给子模块里的同名函数——实现只有一份。

    转发本身也是行为：这条要求**每个参数都按同一个名字、同一个顺序原样转出去**，
    一个不漏、一个不多，默认值也一样。少转一个关键字（`handle` 丢了、窗口参数丢了）、
    换个顺序（位置调用方错位）、或者改掉一个默认值，都是静默改行为，这条红。
    """
    method = getattr(feedback_repo.FeedbackRepository, name)
    declaration = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
    assert isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
    body = declaration.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        body = body[1:]  # 方法自己的 docstring 跟着方法走了
    assert len(body) == 1, f"{name} 的方法体不止一句，实现被抄回来了"

    statement = body[0]
    assert isinstance(statement, ast.Return), f"{name} 不是转发"
    call = statement.value
    if isinstance(call, ast.Await):
        call = call.value
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    target_module = call.func.value
    assert isinstance(target_module, ast.Name) and target_module.id in SIBLINGS
    assert call.func.attr == name, f"{name} 转发去了同名的另一个名字 {call.func.attr}"

    # 转发把仓储原样交出去：子模块函数的首参就是它（不碰 session 的那一个不带）。
    if name in STATIC_METHODS:
        assert [d.id for d in declaration.decorator_list] == ["staticmethod"]
        first_parameter = "status"
    else:
        assert isinstance(call.args[0], ast.Name) and call.args[0].id == "self"
        first_parameter = "repository"
    assert call.args[1:] == [], f"{name} 除了仓储还多传了位置参数"

    forwarded = getattr(SIBLINGS[target_module.id], name)
    assert callable(forwarded)
    parameters = inspect.signature(forwarded).parameters
    assert next(iter(parameters)) == first_parameter
    assert not hasattr(forwarded, "__wrapped__"), f"{name} 被子模块包了一层"

    # 一对一、且顺序一致：每个形参一个同名的关键字实参，两边一个不差。
    passed = {keyword.arg: keyword.value for keyword in call.keywords}
    assert None not in passed, f"{name} 用了 **kwargs 摊开，转发不再是一对一"
    assert [n for n in passed if not isinstance(passed[n], ast.Name)] == []
    assert {n: passed[n].id for n in passed} == {n: n for n in passed}, (
        f"{name} 转发时换了名字：{ {n: passed[n].id for n in passed} }"
    )
    expected = list(parameters) if name in STATIC_METHODS else list(parameters)[1:]
    missing = set(expected) - set(passed)
    extra = set(passed) - set(expected)
    assert list(passed) == expected, (
        f"{name} 的转发表和 {target_module.id}.{name} 的形参对不上："
        f"漏了 {missing}，多了 {extra}，顺序 {list(passed)} vs {expected}"
    )
    own = [p for p in method_signature(method) if p != "self"]
    assert own == expected == list(passed), (
        f"{name} 自己的形参、转发表和 {target_module.id}.{name} 的形参不是同一张表："
        f"{own} vs {list(passed)} vs {expected}"
    )
    # 默认值也是行为：把它改掉不影响转发表，但不传它的调用方会换一批结果。
    own_parameters = inspect.signature(method).parameters
    drifted = [
        p for p in expected if own_parameters[p].default != parameters[p].default
    ]
    assert drifted == [], (
        f"{name} 的默认值和 {target_module.id}.{name} 的不一致：{drifted}"
    )


def method_signature(method: object) -> list[str]:
    """方法自己的形参名（含 `self`），用来和转发表对照。"""
    declaration = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
    assert isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
    args = declaration.args
    return [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
