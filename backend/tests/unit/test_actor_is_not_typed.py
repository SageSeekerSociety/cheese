"""守卫：参与者身上没有「它是不是 agent」这一栏。

和 ``test_author_type_two_values.py`` / ``test_no_adhoc_auth_helpers.py`` 一样，这是
一条**静态**测试 —— 被测的东西本身就是源码树的一个性质，属于 CLAUDE.md 那条「测行为、
不读源码」的例外。

删掉 ``Actor.is_agent`` 这一栏之后，再写 ``actor.is_agent`` 会当场 ``AttributeError``，
所以第一条判据不是为了防那个。它防的是**这一栏被重新加回来**：一个 dataclass 加一个
布尔字段不会让任何测试变红，而它一旦在信任边界被解析出来，八条路由之外的人就又能拿它
回答授权、收件人、署名 —— 每一处都在按参与者的**种类**做决定，而不是按它**持有什么**。

第二条判据是第一条抓不到的那一半：字段可以叫别的名字、也可以挂在别的壳上（一个
``SimpleNamespace``、一个请求态对象、一条会话记录）。所以它不追字段名，追**姿势**：
「我手上拿着一个东西，我读它的 ``is_agent``」。真正的那个答案是查出来的 ——
``IdentityService(session).is_agent(handle)`` 问的是这个 handle 带不带 agent 绑定，
在需要答案的地方现问；写成 ``X.is_agent`` 而 ``X`` 不是那一次调用，就说明答案是从
别处带过来的，也就说明有什么东西在路上被贴了类型。

要问「它在这个房间里能做什么」，问席位：``TopicMemberService.holds_an_agent_seat``。
"""

import ast
import dataclasses
import pathlib

from app.domain.identity.actor import Actor

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 信任边界解析出来的身份会流到哪里 —— 平台自己的活代码，全扫。
SCANNED = ("backend/app",)


def _sources() -> list[tuple[pathlib.Path, str]]:
    found: list[tuple[pathlib.Path, str]] = []
    for root in SCANNED:
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            found.append((path, path.relative_to(REPO_ROOT).as_posix()))
    return found


def _carried_type_flag_lines(tree: ast.AST) -> list[int]:
    """读一个**手上拿着的**东西的 ``is_agent``，出现在哪几行。

    ``IdentityService(session).is_agent(handle)`` 不算：它的被访问者是一次调用，
    答案是在这一行查出来的，不是从别处带过来的。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "is_agent"
            and not isinstance(node.value, ast.Call)
        ):
            lines.add(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg == "is_agent":
            lines.add(node.value.lineno)
    return sorted(lines)


def test_the_actor_carries_no_kind() -> None:
    """信任边界答「这张凭证是谁的」，不答「这是哪一种参与者」。"""
    assert {f.name for f in dataclasses.fields(Actor)} == {
        "handle",
        "user_id",
        "via",
    }, (
        "Actor 上多出了一栏。人和 agent 是同一种参与者，区别只在谁拥有它；"
        "「它能做什么」由席位答（TopicMemberService.holds_an_agent_seat），"
        "「这个 handle 带不带 agent 绑定」由 IdentityService.is_agent 现查。"
    )


def test_nobody_carries_a_type_flag_around() -> None:
    offenders: list[str] = []
    for path, rel in _sources():
        for lineno in _carried_type_flag_lines(ast.parse(path.read_text())):
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "这些地方把「是不是 agent」当成一个随身携带的值：\n  "
        + "\n  ".join(offenders)
        + "\n\n在房间里问席位（TopicMemberService.holds_an_agent_seat），"
        "没有房间就现查绑定（IdentityService(session).is_agent(handle)）。"
    )


def test_every_scanned_root_still_exists() -> None:
    """扫描根改了名，守卫就一个文件都不扫了，而且照样是绿的。"""
    missing = [root for root in SCANNED if not (REPO_ROOT / root).is_dir()]
    assert not missing, f"扫描根已经不存在了：{missing}"
