"""守卫：重派路径在发出任何东西之前，先读平台侧的执行记录（结论 57，6.5）。

和 ``test_event_landing_guard.py`` / ``test_author_type_two_values.py`` 一样，这是
一条**静态**测试 —— 被测的东西本身就是源码的一个性质，属于 CLAUDE.md 那条「测行为、
不读源码」的例外。

它守的是这次改动唯一守不住自己的那一半。``unsettled()`` 只在被调用时才起作用：下一
个人往 ``_settle_restart_orphans`` 里加一段新的补救，加在这个读的前面，代码跑得好好
的，房间里也照样有东西发生 —— 只是那段补救是在还不知道上一次做到哪的情况下发的。
那种错一次都不会响，正好是这条记录本来要消灭的。

判据取「函数体里第一件会挂起的事就是它」，比「第一次外部调用之前」严一点，也是它唯
一可判的写法：这个函数里哪一次 await 会走出这台机器，从 AST 上看不出来（``_post_
orphan_event`` 会写房间、``turns_that_produced_something`` 只查库，两者长得一模一
样）。严在这里没有代价 —— 先读一行记录，本来就不该排在任何事情后面。

「会挂起的事」不只有 ``ast.Await``：``async with`` 和 ``async for`` 各自也 await 一
次（``__aenter__``、``__anext__``），而这个读本身就坐在一个 ``async with`` 里面 ——
只数 ``ast.Await`` 的话，在它前面并排插一句 ``async with 某个客户端(...)``，守卫照
绿。所以三种都数，排在那次读前面的只能是**把它包起来的那几层**，而且只能是打开它要
读的那个 session 的那一层。
"""

import ast
import pathlib

RUNTIME = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app"
    / "domain"
    / "agent"
    / "runtime.py"
)

#: 重派路径：孤儿轮次扫底里决定「要不要把这条活再派一次」的那个函数。
RETRY_PATH = "_settle_restart_orphans"


def _retry_path() -> ast.AsyncFunctionDef:
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == RETRY_PATH
    ]
    assert found, (
        f"{RETRY_PATH} 不在 runtime.py 里了。重派路径换了名字或者搬了家，"
        "这条守卫要跟着搬 —— 删掉它等于把「重派前先读执行记录」这条规则一起删掉。"
    )
    return found[0]


def _call_name(node: ast.AST) -> str:
    """``a.b.c(...)`` → ``"b.c"``，``f(...)`` → ``"f"``。够认出是不是那一个调用。"""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        prefix = owner.id + "." if isinstance(owner, ast.Name) else ""
        return prefix + func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


#: 一步「会挂起的事」。见模块开头：只数 ``ast.Await`` 漏得掉 ``async with``。
SUSPENDS = (ast.Await, ast.AsyncWith, ast.AsyncFor)


def _opens_the_ledger(node: ast.AST) -> bool:
    """这一层是不是「打开那个 session」——唯一允许包在那次读外面的东西。"""
    return isinstance(node, ast.AsyncWith) and all(
        _call_name(item.context_expr).endswith("session_factory") for item in node.items
    )


def test_the_retry_path_reads_the_dispatch_record_before_anything_else():
    retry_path = _retry_path()
    # 按源码位置排，不按 `ast.walk` 的遍历顺序 —— 后者是广度优先，嵌套里的 await
    # 会排到外层前面去，而这条守卫问的恰好是「谁在前」。
    steps = sorted(
        (node for node in ast.walk(retry_path) if isinstance(node, SUSPENDS)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    assert steps, f"{RETRY_PATH} 里一步会挂起的事都没有 —— 它不再是那条重派路径了"
    read = next(
        (
            index
            for index, node in enumerate(steps)
            if isinstance(node, ast.Await)
            and _call_name(node.value) == "dispatch_log.unsettled"
        ),
        None,
    )
    assert read is not None, (
        f"{RETRY_PATH} 里没有 `dispatch_log.unsettled`。重派之前先读平台侧的执行"
        "记录（结论 57）：不读就重发，等于把一次可能已经落地的写操作再做一遍。"
    )
    ahead = [
        node
        for node in steps[:read]
        # 包着它的那几层不算「在它前面」——读就发生在它们里面。而包着它的也只能是
        # 打开那个 session 的 `async with`。
        if not (
            _opens_the_ledger(node)
            and any(inner is steps[read] for inner in ast.walk(node))
        )
    ]
    assert not ahead, (
        f"{RETRY_PATH} 在读执行记录之前还做了 "
        + "、".join(f"第 {node.lineno} 行的 `{type(node).__name__}`" for node in ahead)
        + "。重派之前先读平台侧的执行记录（结论 57），在这个函数做任何别的事情之前："
        "不读就重发，等于把一次可能已经落地的写操作再做一遍。"
    )
