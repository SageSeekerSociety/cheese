"""``is_private`` 只在 ``app/domain/topic/models.py`` 里读（ARCH §9.1 判据②）。

私聊是项目内名册两席的房间（结论 19）。凡是「私聊要不一样」的地方，各自再问一遍这个
布尔，就是同一件事有 N 份声明，而 N 份声明各自漂移是必然的，不是可能的。所以这一列
在 ``models.py`` 里声明，也只在那里被读：``is_private_chat``（一间已经取到的房）、
``private_chat`` / ``not_a_private_chat``（SQL 里的一行）、``new_private_chat``（建
一间）。别处要区别私聊，就调这几个；直接碰这个属性，在任何一个文件里都红，没有登记表。

数的是 AST 里叫 ``is_private`` 的节点（属性、名字、实参、形参），不是文本出现次
数：注释和 docstring 里说到它不算一次声明，说清楚反而是该做的事。
"""

import ast
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: 唯一读这一列的文件，和其中允许读它的地方：列的声明（在类体里，不在任何函数
#: 里），以及四个答「是不是私聊」的函数。
_HOME = "app/domain/topic/models.py"
_DECISION_POINTS = {
    "is_private_chat",
    "private_chat",
    "not_a_private_chat",
    "new_private_chat",
}


def _naming_it(tree: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "is_private":
            found.append(node)
        elif isinstance(node, ast.Name) and node.id == "is_private":
            found.append(node)
        elif isinstance(node, ast.keyword) and node.arg == "is_private":
            found.append(node)
        elif isinstance(node, ast.arg) and node.arg == "is_private":
            found.append(node)
    return found


def _calls_of(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _enclosing_function(tree: ast.AST, node: ast.AST) -> str | None:
    """包着 ``node`` 的最近一层函数叫什么名字。"""
    parent: dict[ast.AST, ast.AST] = {}
    for holder in ast.walk(tree):
        for child in ast.iter_child_nodes(holder):
            parent[child] = holder
    current = node
    while current in parent:
        current = parent[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return None


def _reads() -> dict[str, list[tuple[int, str | None]]]:
    """Every file that names ``is_private``: (line, enclosing function) per read."""
    found: dict[str, list[tuple[int, str | None]]] = {}
    for path in sorted(_APP.rglob("*.py")):
        source = path.read_text()
        if "is_private" not in source:
            continue
        tree = ast.parse(source)
        naming = _naming_it(tree)
        if naming:
            found[str(path.relative_to(_APP.parent))] = [
                (getattr(node, "lineno", 0), _enclosing_function(tree, node))
                for node in naming
            ]
    return found


def test_is_private_is_asked_no_more_often_than_the_baseline():
    """读点只有 ``models.py`` 里那几个：别的文件一处都没有，``models.py`` 里也只在列
    的声明和四个判断函数里。"""
    found = _reads()
    elsewhere = {
        f: [line for line, _ in reads] for f, reads in found.items() if f != _HOME
    }
    assert not elsewhere, (
        f"这些地方直接读了 is_private：{elsewhere}。"
        f"私聊要不一样的地方，调 {_HOME} 里的 is_private_chat / private_chat / "
        "not_a_private_chat / new_private_chat（结论 19，ARCH §9.1 判据②），"
        "不要在这里再问一遍这个布尔。"
    )
    home = found.get(_HOME, [])
    declared = [line for line, function in home if function is None]
    stray = [
        (line, fn) for line, fn in home if fn is not None and fn not in _DECISION_POINTS
    ]
    assert len(declared) == 1, (
        f"{_HOME} 里在函数之外碰 is_private 的不止列的声明：{declared}"
    )
    assert not stray, f"{_HOME} 里这些函数直接读了 is_private：{stray}"
    assert {fn for _, fn in home if fn} == _DECISION_POINTS, (
        f"{_HOME} 里的判断函数和这里写的不一致：{sorted({fn for _, fn in home if fn})}"
    )


def test_a_turn_asks_it_once_and_derives_the_rest():
    """轮次组装整个文件只问一次「是不是私聊」，而且是在 ``_is_dm`` 里问的。"""
    source = (_APP / "domain" / "agent" / "turn" / "intake" / "rooms.py").read_text()
    tree = ast.parse(source)
    asked = _calls_of(tree, "is_private_chat")
    assert len(asked) == 1
    # 光数个数拦不住「把这一处搬到别的函数里、总数还是 1」的改法：那样一来判据又
    # 散回轮次组装里去了，而两类读点（名册两席、不租地点）本该都从这一个函数推出
    # 来。所以问的是「包着它的那个函数叫什么」。
    assert _enclosing_function(tree, asked[0]) == "_is_dm", (
        "``is_private_chat`` 在 turn/intake/rooms.py 只能在 ``_is_dm`` 里问一次"
        "（结论 19，ARCH §9.1 判据②）：名册两席和不租地点两类答案都从它推出来。"
    )
    intake = (_APP / "domain" / "agent" / "turn" / "intake" / "human.py").read_text()
    assert "private_seats" in intake, "名册两席那一类走 TopicMemberService"
