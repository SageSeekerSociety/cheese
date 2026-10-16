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


def _piece_functions() -> dict[str, list[str]]:
    """身子里的每个函数名 → 写着它的文件（正常只有一个）。"""
    homes: dict[str, list[str]] = {}
    for piece in PIECES:
        for node in _tree(piece).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
    """门面答得出的名字：模块级的，加上类自己的、和它 ``__init__`` 里立起的字段。"""
    names = set(vars(facade))
    for name, node in _facade_methods().items():
        names.add(name)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                if inner.value.id == "self" and isinstance(inner.ctx, ast.Store):
                    names.add(inner.attr)
    return names


def test_the_old_path_still_answers_with_the_same_objects():
    """调用方、类型标注和 monkeypatch 目标都还落在原来那个名字上。"""
    assert facade.RoomSessions is RoomSessions
    assert facade.Clock is Clock
    assert facade.Live is Live
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


def test_every_piece_function_is_one_line_of_forwarding_on_the_class():
    """搬走的是身子，不是判断：类上那一句只许把调用交给它住的那个文件。"""
    methods = _facade_methods()
    given = _re_exported()
    for name, homes in sorted(_piece_functions().items()):
        if name in given:
            # 门面照原样端出来的（``_home``），它本来就不是方法。
            continue
        assert name in methods, f"{name} lives in {homes[0]} and has no method"
        node = methods[name]
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
    twice = {name: who for name, who in _piece_functions().items() if len(who) > 1}
    assert twice == {}
