"""`repositories.py` 拆出 `queries.py` 之后，它那一面必须和拆之前一样。

拆的是文件，不是行为。三件事要守住：

**旧路径照旧回答。** 仓库内外一直按 `from app.domain.questions.repositories
import ...` 取这几个名字，按名字打 monkeypatch 也指着这个路径。规矩是「读那一半」
搬去 `queries.py`、`repositories.py` 负责再导出，调用点一行都不改。

**再导出不是再定义。** 名字在两边必须是同一个对象；读那一半的方法也不许在这个模块
里再出现一份 —— 两份拷贝就是同一句 SQL 有两个版本，改一边不报错，只是行为慢慢分叉
（`_use_fts` 与 `_keyword_filter` 的距离就是这么拉开的：仓库里四个域各有一份）。

**读那一半仍然是方法。** 它们拿 `self._session` 执行语句，而 `list_followed`、
`search` 这些名字是按方法被调用的（services、路由、单测都这么调）。有人「顺手」把它们
改成收 session 的模块函数，调用点就全线 AttributeError。
"""

import ast
import pathlib
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.questions import queries, repositories
from app.domain.questions.repositories import QuestionRepository

#: 拆分前住在 `repositories.py` 顶层、由这个路径回答的名字。
MOVED = ("_HAS_WORD_CHAR_RE", "_use_fts")

#: 「读那一半」：拆分前是 `QuestionRepository` 的方法，现在定义在 `queries.py`。
#: 判断标准只有一条 —— 只 SELECT，不改行。
READ_METHODS = (
    "_get_follow_relation",
    "_get_vote",
    "_keyword_filter",
    "count_comments",
    "count_followers",
    "count_views",
    "count_votes",
    "get_by_id",
    "get_popular_search_terms",
    "get_stats",
    "get_trending_questions",
    "get_user_vote",
    "is_following",
    "list_by_user",
    "list_followed",
    "list_followers",
    "search",
)


@pytest.mark.parametrize("name", MOVED)
def test_the_repositories_path_still_answers_every_name_it_used_to(name: str) -> None:
    assert hasattr(repositories, name), (
        f"`app.domain.questions.repositories.{name}` 不见了："
        "`tests/unit/test_questions_repository.py` 按这个路径取它，"
        "搬去 `queries.py` 之后必须在这里再导出一次。"
    )


@pytest.mark.parametrize("name", MOVED)
def test_the_move_did_not_become_a_second_definition(name: str) -> None:
    assert getattr(repositories, name) is getattr(queries, name), (
        f"`{name}` 在 `repositories.py` 和 `queries.py` 里是两个对象 —— "
        "这是把名字又定义了一遍，不是再导出。"
    )


def test_none_of_the_read_half_is_defined_here() -> None:
    """读那一半不许在 `repositories.py` 里再长出一份。

    `is` 挡不住「又写了一份」—— 方法名是字符串，抄一遍两份都在、互不相等，
    只查对象的话还能通过。所以这里读源码：模块级定义、以及 `QuestionRepository`
    这个类里定义的方法名，都不许和 `MOVED` / `READ_METHODS` 撞上。

    只看这一个类：`QuestionInvitationRepository` 有它自己的 `get_by_id`（另一个
    聚合的那一行），那是它本来就该有的，不是这份拷贝。
    """
    tree = ast.parse(pathlib.Path(repositories.__file__).read_text())
    defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.ClassDef) and node.name == "QuestionRepository":
            defined.update(
                member.name
                for member in node.body
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    clash = defined & (set(MOVED) | set(READ_METHODS))
    assert not clash, (
        "这些名字在 `repositories.py` 里被又定义了一份，"
        f"而不是继承/再导出：{sorted(clash)}"
    )


@pytest.mark.parametrize("name", READ_METHODS)
def test_the_read_half_is_still_a_method_of_the_repository(name: str) -> None:
    assert name in vars(queries.QuestionQueries), (
        f"`{name}` 不再定义在 `QuestionQueries` 上 —— 它属于读那一半。"
    )
    repo = QuestionRepository(cast(AsyncSession, None))
    assert callable(getattr(repo, name)), (
        f"`QuestionRepository({name})` 取不到：调用点一直是按实例调它的。"
    )


def test_the_public_classes_are_all_reachable_from_the_old_path() -> None:
    """三个仓储都还在原来的模块里；`QuestionRepository` 自己就是读那一半的子类。"""
    for name in (
        "QuestionRepository",
        "QuestionTopicRepository",
        "QuestionInvitationRepository",
    ):
        assert hasattr(repositories, name), name
    assert issubclass(QuestionRepository, queries.QuestionQueries)
