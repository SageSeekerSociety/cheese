"""门面守卫：``room/sessions.py`` 拆成包之后，公开面和「谁的身子在哪」都不能变。

``app/domain/agent/room/sessions.py``（1453 行）本来就对着 ``backend/app`` 那份 1500
行的上限，这次把它变成同名的包 ``app/domain/agent/room/sessions/``，身子按职责搬进
子模块：

- ``records.py`` —— ``Clock`` / ``Live`` 两个记录，和 ``_home``；
- ``books.py`` —— 房间的账：回执、完成、终止，记忆、``pulse``、``verdict``；
- ``reading.py`` —— 一个座位的读：接上、听、``_take``，以及 ``_died``；
- ``starting.py`` —— ``ensure``；
- ``speaking.py`` —— ``send`` / ``steer`` / ``reading``；
- ``lifecycle.py`` —— 松手、关、``recover`` / ``replay``。

子模块是同一个包的，不是六个同级模块：同级模块互相 import 会在 C3（``app.domain``
下的同级节点不许成环）上多出环，子模块 import 的是父包这个节点，契约不算它。

这道测试不连数据库，钉住四件事：

1. 旧路径 ``app.domain.agent.room.sessions`` 仍答得出原来那一批名字，而且是**同一批
   对象**，不是各留一份；
2. 身子里的每个函数在类上恰好一句转发：查得到模型、调得到实现，中间不做判断；
3. **一块身子伸手要的每一样东西，门面上都答得出来**——``svc.<名字>`` 那份。这一条是
   主盯的：漏一处转发，报出来的是运行时 ``AttributeError``，还常落在没几步就没人跑
   的路径上（这次拆漏掉的 ``_died`` 就是四个存活用例逮住的）；
4. 一个名字只有一块身子，没有第二份实现。
"""

from __future__ import annotations

import ast
import pathlib

from app.domain.agent.room import sessions as facade
from app.domain.agent.room.sessions import (
    CLOCK_READ_S,
    RUNNER_GONE_S,
    TALKING_S,
    Clock,
    Live,
    RoomSessions,
)
from app.domain.agent.room.sessions import records as _records

PACKAGE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app"
    / "domain"
    / "agent"
    / "room"
    / "sessions"
)

#: 门面自己当然要从子模块里取名字，它不在这条规矩的对象里。
FACADE = "__init__.py"

#: 装身子的那些文件，按名字排，好让报错稳定。
PIECES = sorted(p.name for p in PACKAGE.glob("*.py") if p.name != FACADE)

_PREFIX = "app.domain.agent.room.sessions"


def _tree(name: str) -> ast.Module:
    return ast.parse((PACKAGE / name).read_text())


def _piece_names() -> dict[str, list[str]]:
    """身子里的每个顶层名字（函数、类）→ 写着它的文件（正常只有一个）。

    类也要收：``Clock``/``Live`` 由门面从 ``records.py`` 端出来，别处再写一份
    同名类，名字就有了两块身子，而函数那一遍看不见。
    """
    homes: dict[str, list[str]] = {}
    for piece in PIECES:
        for node in _tree(piece).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                homes.setdefault(node.name, []).append(piece)
    return homes


def _re_exported() -> set[str]:
    """门面照原样端出来的名字：直接 ``from`` 某个身子取过来，不套转发。"""
    names: set[str] = set()
    for node in ast.walk(_tree(FACADE)):
        if not isinstance(node, ast.ImportFrom):
            continue
        home = node.module or ""
        if home.split(".")[-1] not in {p[:-3] for p in PIECES}:
            continue
        if not home.startswith(_PREFIX):
            continue
        names.update(alias.asname or alias.name for alias in node.names)
    return names


def _facade_methods() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in _tree(FACADE).body:
        if isinstance(node, ast.ClassDef) and node.name == "RoomSessions":
            return {
                item.name: item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError("RoomSessions is not in the facade")


def _facade_names() -> set[str]:
    """一个实例答得出的名字：类上有的，加上 ``__init__`` 里立起的字段。

    取 ``dir(RoomSessions)``，不取 ``vars(facade)``：模块全局名里有些是身子从
    ``pkg.`` 上读的常量、记录（``RUNNER_GONE_S``、``Clock`` 之类），它们**不是**
    实例上的属性，``svc.<它们>`` 在运行时是 ``AttributeError``，拿全局名当答案就
    正是漏掉这一类。
    """
    names = set(dir(RoomSessions))
    for name, node in _facade_methods().items():
        names.add(name)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                if inner.value.id == "self" and isinstance(inner.ctx, ast.Store):
                    names.add(inner.attr)
    return names


def test_the_old_path_still_answers_with_the_same_objects():
    """调用方、类型标注和 monkeypatch 目标都还落在原来那个名字上。"""
    # 类自己的路径没变，报错栈和日志里认的还是这个名字。
    assert RoomSessions.__module__ == "app.domain.agent.room.sessions"
    # 门面端出来的就是子模块里那一个，不是又抄了一份。
    assert facade.Clock is _records.Clock is Clock
    assert facade.Live is _records.Live is Live
    for constant in (RUNNER_GONE_S, CLOCK_READ_S, TALKING_S):
        assert isinstance(constant, float)
    # 计时日志按这个 logger 名过滤（test_room_send_timing 就靠它）。
    assert facade.logger.name == "app.domain.agent.room.sessions"


def test_every_method_a_piece_reaches_for_is_answered_by_the_facade():
    """身子里的 ``svc.<名字>`` 全落在门面上——漏一处就是运行时才炸的 AttributeError。"""
    answered = _facade_names()
    missing = {
        f"{piece}: svc.{node.attr}"
        for piece in PIECES
        for node in ast.walk(_tree(piece))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "svc"
        and node.attr not in answered
    }
    assert missing == set()


def test_no_piece_calls_a_forwarded_method_by_its_bare_function_name():
    """身子之间只走对象：在同一个文件里裸调那个函数名，替换和子类就拦不住了。

    上面那条扫的是 ``svc.<名字>`` 这种属性访问，看不见裸调到同文件那份实现。
    ``_room`` 就这么漏过一次（books 里五处裸调），而门面上那个转发因此没人走。
    """
    methods = set(_facade_methods())
    bare = {
        f"{piece}: {node.func.id}()"
        for piece in PIECES
        for node in ast.walk(_tree(piece))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in methods
    }
    assert bare == set()


def test_every_piece_function_is_one_line_of_forwarding_on_the_class():
    """搬走的是身子，不是判断：类上那一句只许把调用交给它住的那个文件。"""
    methods = _facade_methods()
    given = _re_exported()
    for name, homes in sorted(_piece_names().items()):
        if name in given:
            # 门面照原样端出来的（``_home``），它本来就不是方法。
            continue
        assert name in methods, f"{name} lives in {homes[0]} and has no method"
        node = methods[name]
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # 一句文档不算身子
        assert len(body) == 1, f"{name} is not one statement of forwarding"
        calls = {
            (inner.func.value.id, inner.func.attr)
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and isinstance(inner.func.value, ast.Name)
        }
        assert (f"_{homes[0][:-3]}", name) in calls, (
            f"{name} does not hand over to {homes[0]}"
        )
        deciding = [
            inner
            for inner in ast.walk(node)
            if isinstance(inner, (ast.If, ast.For, ast.While, ast.Try, ast.Match))
        ]
        assert deciding == [], f"{name} decides something instead of forwarding"


def test_no_piece_keeps_a_second_copy_of_a_name():
    """一个名字只有一份身子：两份就是谁归谁没说清。"""
    twice = {name: who for name, who in _piece_names().items() if len(who) > 1}
    assert twice == {}
