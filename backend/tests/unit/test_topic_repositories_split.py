"""门面守卫：``app.domain.topic.repositories`` 拆文件之后，行为和公开面一个都不能变。

2026-10-15 那次拆分把 ``TopicRepository`` 的 27 个方法体搬进了
``app/domain/topic/repositories/`` 这个包里的 ``reads`` / ``writes`` / ``replied``
/ ``unread``（公共类型与封闭词表进了 ``vocabulary``，共用判据进了
``statements``，``TopicProgressRepository`` 原样住在 ``progress``），类上只留同名
转发。调用方完全没改：``app/domain/topic/services.py``、``app/api/place.py``、
``app/domain/dashboard/services.py`` 这一圈读的仍是
``app.domain.topic.repositories`` 上的 ``TopicRepository`` / ``TopicProgressRepository``
/ ``seen_by`` / ``ListedRoom`` / ``Unread`` / ``TopicSortField`` / ``SortOrder``。
靠的就是包的 ``__init__.py`` 把同一个名字空间重新摊开。

这道测试不连数据库，钉住四件事：

1. 旧路径仍然答得出原来那一组名字，而且是同一个对象——包括唯一一个下划线名字
   ``_replied_to_me_statement``，它原来就住在这个模块里，外面按老路径取它。
2. ``TopicRepository`` 的 28 个方法（含 ``__init__``）一个不少，而且是**真方法**
   （类自己的 ``__dict__`` 里就有），不是 ``__getattr__`` 之类动态转发的。
3. ``__init__`` 没动：``_session`` 仍从这里赋值。
4. **拆的是文件，不是行为**：每个方法体恰好是一句转发，转给四个子模块里同名、
   首参为 ``repo`` 的那一个函数。谁把方法体抄回类里一份，这条就红。
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.domain.topic import repositories as topic_repositories
from app.domain.topic.repositories import (
    progress,
    reads,
    replied,
    repository,
    statements,
    unread,
    vocabulary,
    writes,
)

# 28 个方法，照搬运之前那份 repositories.py 里 TopicRepository 的类体抄的。
# 顺序按定义先后；__init__ 也在内。
TOPIC_REPOSITORY_METHODS = [
    "__init__",
    "add",
    "get",
    "get_many",
    "lock",
    "lock_all_in_project",
    "list_for_project",
    "ids_for_project",
    "visible_unarchived_ids_for_projects",
    "list_by_ids",
    "listed_by_ids",
    "_project_topics_stmt",
    "names_in_projects",
    "list_for_project_with_activity",
    "last_activity_for_topics",
    "get_or_create_private",
    "list_children",
    "archival_for_project",
    "count_for_project",
    "replied_to_me",
    "unread_counts",
    "_levels",
    "muted_among",
    "private_unread_counts",
    "notify_levels",
    "set_notify_level",
    "mark_read_many",
    "mark_read",
]

#: 方法体搬去的那四个模块，按它们在类里被引用的名字。
SIBLINGS = {
    "reads": reads,
    "writes": writes,
    "replied": replied,
    "unread": unread,
}


def test_the_old_import_path_still_answers_its_names() -> None:
    """调用点读的就是这几个名字；少一个，那边是 ImportError 或 AttributeError。"""
    # 定义搬去了别的模块，名字只在那边有一份——两个路径必须是同一个对象。
    assert topic_repositories.TopicRepository is repository.TopicRepository
    assert (
        topic_repositories.TopicProgressRepository is progress.TopicProgressRepository
    )
    assert topic_repositories.seen_by is statements.seen_by
    assert topic_repositories.ListedRoom is vocabulary.ListedRoom
    assert topic_repositories.Unread is vocabulary.Unread
    assert topic_repositories.TopicSortField is vocabulary.TopicSortField
    assert topic_repositories.SortOrder is vocabulary.SortOrder
    assert list(topic_repositories.TopicSortField.__args__) == [
        "updated_at",
        "title",
        "last_activity_at",
    ]
    assert list(topic_repositories.SortOrder.__args__) == ["asc", "desc"]


def test_the_private_name_the_outside_reads_is_still_here() -> None:
    """`_replied_to_me_statement` 原来定义在这个模块里，外面按老路径取它。

    `.claude/scripts/explain_channel_detail.py` 拿它出来，对着几条候选写法比执行
    计划。搬进 `replied.py` 之后这里不再定义它，所以 `__init__.py` 照样再导出：
    这条钉住的是「有人按老路径 import 一个下划线名字」这件事。
    """
    assert (
        topic_repositories._replied_to_me_statement is replied._replied_to_me_statement
    )


def test_progress_repository_is_not_split() -> None:
    """两个方法一张表，不值得再拆：它仍是这里的一个真类，方法也在。"""
    assert set(vars(topic_repositories.TopicProgressRepository)) >= {"get", "save"}
    assert (
        topic_repositories.TopicProgressRepository.__module__
        == "app.domain.topic.repositories.progress"
    )


def test_the_repository_keeps_all_28_methods() -> None:
    """28 个方法一个不少，而且都是类自己定义的。"""
    assert len(TOPIC_REPOSITORY_METHODS) == 28
    found = {name for name, _ in inspect.getmembers(topic_repositories.TopicRepository)}
    assert [n for n in TOPIC_REPOSITORY_METHODS if n not in found] == []

    # 真方法：类自己的 __dict__ 里就有，不是动态转发来的。
    own = set(vars(topic_repositories.TopicRepository))
    assert [n for n in TOPIC_REPOSITORY_METHODS if n not in own] == []
    assert "__getattr__" not in own


def test_init_still_builds_the_session() -> None:
    """拆分没动 __init__：属性仍**在这里赋值**，赋的什么也没换。

    看的是赋值本身，不是「源码里出现过这个词」——把赋值改成一句提到 `_session`
    的注释不该让这条过。
    """
    init = ast.parse(
        textwrap.dedent(inspect.getsource(topic_repositories.TopicRepository.__init__))
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
    "name", [n for n in TOPIC_REPOSITORY_METHODS if n != "__init__"]
)
def test_every_method_is_one_forward_to_its_sibling_module(name: str) -> None:
    """方法体恰好一句转发，转给子模块里的同名函数——实现只有一份。

    转发本身也是行为：这条要求**每个参数都按同一个名字原样转出去**，一个不漏、
    一个不多。少转一个关键字（`sort` 丢了、`unarchived` 丢了）就是静默改行为，
    这条红。
    """
    method = getattr(topic_repositories.TopicRepository, name)
    body = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0].body
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

    # 转发把仓库原样交出去：子模块函数的首参就是它。
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "self"
    assert call.args[1:] == [], f"{name} 除了仓库还多传了位置参数"
    forwarded = getattr(SIBLINGS[target_module.id], name)
    assert callable(forwarded)
    parameters = inspect.signature(forwarded).parameters
    assert next(iter(parameters)) == "repo"

    # 一对一：每个形参一个同名的关键字实参，两边一个不差。
    passed = {keyword.arg: keyword.value for keyword in call.keywords}
    assert None not in passed, f"{name} 用了 **kwargs 摊开，转发不再是一对一"
    assert [n for n in passed if not isinstance(passed[n], ast.Name)] == []
    assert {n: passed[n].id for n in passed} == {n: n for n in passed}, (
        f"{name} 转发时换了名字：{ {n: passed[n].id for n in passed} }"
    )
    missing = set(parameters) - {"repo"} - set(passed)
    extra = set(passed) - set(parameters)
    assert set(passed) == set(parameters) - {"repo"}, (
        f"{name} 的转发表和 {target_module.id}.{name} 的形参对不上："
        f"漏了 {missing}，多了 {extra}"
    )
    assert set(passed) == {p for p in method_signature(method) if p != "self"}, (
        f"{name} 的转发表和它自己的形参对不上"
    )


def method_signature(method: object) -> list[str]:
    """方法自己的形参名（含 `self`），用来和转发表对照。"""
    declaration = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
    assert isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
    args = declaration.args
    return [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
