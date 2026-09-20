"""守卫：产生事件的调用点必须从 `landing()` 取落点，不许就地挑一个 `topic_id`。

和 ``test_author_type_two_values.py`` / ``test_no_adhoc_auth_helpers.py`` 一样，
这是一条**静态**测试 —— 被测的东西本身就是源码树的一个性质，属于 CLAUDE.md 那条
「测行为、不读源码」的例外。

它守的是这次改动唯一守不住自己的一半。封闭表（``block/about.py``）只在被调用时才
起作用：下一个人写事件时照旧写 ``topic_id=topic.id``，代码跑得好好的，事件也确实
写进了某个房间 —— 只是落错地方的事件从来不报错，只是在该读到它的那张卡上再也读不
到。一次都不会响，而这正是这张表本来要消灭的那种自由。

判据：任何一处 ``…add(kind=BlockKind.event, …)``，它的 ``project_id`` /
``topic_id`` / ``task_id`` 三个实参都必须是同一个 ``landing()`` 返回值上的三个字段
（``landed.project_id`` 这样的形状），一个都不能少。

``task_id`` 少不得：默认值是 ``None``，省掉它就等于悄悄把一条卡的事件说成房间的
事件 —— 正是这张表要回答的那个问题，被一个默认值替调用点答了。

**扫的不止 ``backend/app``。** 写进同一张 ``blocks`` 表的还有种子脚本、一次性回填
脚本、evals 和 probe；它们今天一条事件也不写，而这条守卫存在的意义就是它们明天开始
写的时候会响。``backend/tests`` 不扫：fixture 手写的存量行就是要绕开这张表。
``backend/alembic`` 不扫：迁移是已经发生过的历史。
"""

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 连着同一张 blocks 表的、活的代码。
SCANNED = ("backend/app", "backend/scripts", "scripts", "evals")

LANDING_FIELDS = ("project_id", "topic_id", "task_id")


def _sources() -> list[tuple[pathlib.Path, str]]:
    found: list[tuple[pathlib.Path, str]] = []
    for root in SCANNED:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            found.append((path, path.relative_to(REPO_ROOT).as_posix()))
    return found


def _landing_names(tree: ast.AST) -> set[str]:
    """本模块里哪些名字绑的是 ``landing()`` 的返回值。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        called = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if called != "landing":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _writes_an_event(call: ast.Call) -> bool:
    """这个调用写的是一条 ``kind=BlockKind.event`` 的块吗。

    ``kind`` 可以是一个条件表达式（``BlockKind.event if … else …``），
    所以看的是整棵子树里有没有 ``BlockKind.event``。
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "add":
        return False
    for kw in call.keywords:
        if kw.arg != "kind":
            continue
        return any(
            isinstance(n, ast.Attribute)
            and n.attr == "event"
            and isinstance(n.value, ast.Name)
            and n.value.id == "BlockKind"
            for n in ast.walk(kw.value)
        )
    return False


def _picked_in_place(call: ast.Call, landing_names: set[str]) -> list[str]:
    """这个调用点自己挑落点的那几个实参（空列表 = 落点全部来自 `landing()`）。"""
    given = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    bad: list[str] = []
    for field in LANDING_FIELDS:
        value = given.get(field)
        if value is None:
            bad.append(f"{field}（没给，默认值替调用点答了）")
            continue
        if not (
            isinstance(value, ast.Attribute)
            and value.attr == field
            and isinstance(value.value, ast.Name)
            and value.value.id in landing_names
        ):
            bad.append(field)
    return bad


def test_every_event_takes_its_landing_from_the_closed_table():
    offenders: list[str] = []
    for path, rel in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        landing_names = _landing_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _writes_an_event(node):
                continue
            bad = _picked_in_place(node, landing_names)
            if bad:
                offenders.append(f"{rel}:{node.lineno} — {', '.join(bad)}")
    assert not offenders, (
        "这些事件的落点是调用点自己挑的，不是从 block/about.py 的 landing() 取的：\n"
        + "\n".join(offenders)
    )


def test_the_guard_can_see_a_call_site_picking_its_own_landing():
    """守卫自己得能抓到东西 —— 一条恒真的断言守不住任何东西。"""
    tree = ast.parse(
        "blocks.add(project_id=p, topic_id=t, task_id=None, kind=BlockKind.event)"
    )
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    assert _writes_an_event(call)
    assert _picked_in_place(call, set()) == list(LANDING_FIELDS)
