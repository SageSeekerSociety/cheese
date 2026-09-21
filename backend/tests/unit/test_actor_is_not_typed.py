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

第三条判据换了个问题：不是「谁被贴了类型」，而是「在信任边界上，还有没有人靠 handle 的
**形状**回答关于调用者的问题」。``looks_like_agent_handle`` / ``names_a_person`` 是一条
**显示用的**门规（前者的 docstring 自己写着「Authorization must NEVER use it」），判据是
``cheese`` 前缀而不是绑定。它们在 ``backend/app/api`` 下零命中 —— 那一层正是 actor
被解析出来、授权 / 收件人 / 署名三类决定落地的地方。

这条判据**只管 ``backend/app/api``**，域里那些命中不在它的范围内，也不该在：P8（#1309）
把「这条是不是芝士说的」从事件行的档位改成了**按署名 handle 解析**，``agent/chat.py`` 与
``block/repositories.py`` 里的那几处就是它落下来的形状。那半边在 P11 才动 ——
``looks_like_agent_handle`` 的第二个分句是 ``TOPIC_AGENT_PREFIX``，而 P11 删的就是它。
"""

import ast
import dataclasses
import pathlib

from app.domain.identity.actor import Actor

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 信任边界解析出来的身份会流到哪里 —— 平台自己的活代码，全扫。
SCANNED = ("backend/app",)

# 信任边界本身：actor 在这里被解析出来，授权 / 收件人 / 署名三类决定也在这里落地。
BOUNDARY = ("backend/app/api",)

# 按 handle 的**形状**回答问题的那几个谓词。``agent_handle_column`` 一并列上，它是
# ``looks_like_agent_handle`` 的 SQL 孪生、判据逐字相同（``identity/handles.py``），
# 漏掉它等于允许同一个问题改用一条查询来问。
SHAPE_PREDICATES = frozenset(
    {"looks_like_agent_handle", "names_a_person", "agent_handle_column"}
)


def _sources(roots: tuple[str, ...] = SCANNED) -> list[tuple[pathlib.Path, str]]:
    found: list[tuple[pathlib.Path, str]] = []
    for root in roots:
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


def _shape_predicate_lines(tree: ast.AST) -> list[int]:
    """按 handle 形状判「这是不是 agent / 是不是人」的谓词，出现在哪几行。

    名字被怎么写出来都算：直接调用、``from ... import`` 进来、经模块属性访问。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in SHAPE_PREDICATES:
            lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in SHAPE_PREDICATES:
            lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in SHAPE_PREDICATES:
                    lines.add(node.lineno)
    return sorted(lines)


def test_the_boundary_does_not_read_a_handle_for_its_shape() -> None:
    offenders: list[str] = []
    for path, rel in _sources(BOUNDARY):
        for lineno in _shape_predicate_lines(ast.parse(path.read_text())):
            offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "信任边界上这些地方按 handle 的**形状**回答关于调用者的问题：\n  "
        + "\n  ".join(offenders)
        + "\n\n形状是一条显示用的门规（cheese 前缀），不是绑定，也不是席位。"
        "授权与「这一轮该发给谁」问席位（TopicMemberService.holds_an_agent_seat）；"
        "「这个 handle 带不带 agent 绑定」问 IdentityService(session).is_agent。"
    )


def test_every_scanned_root_still_exists() -> None:
    """扫描根改了名，守卫就一个文件都不扫了，而且照样是绿的。"""
    missing = [root for root in SCANNED + BOUNDARY if not (REPO_ROOT / root).is_dir()]
    assert not missing, f"扫描根已经不存在了：{missing}"
