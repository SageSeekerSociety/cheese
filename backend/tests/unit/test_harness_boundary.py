"""架构守卫：Claude Code 适配器的内部，外面只能从包门口拿。

包里的东西没有一样是「跑一个 agent」的事实——它们全是 Claude Code 这一个 harness
碰巧长成的样子：一地 hook 文件当事件日志（因为它没有事件 API）、一个消息拼装器
（因为 MessageDisplay 是按 flush 发的不是按消息发的）、一个钉死在某个没有文档的
内部版本上的 rendezvous socket、一段替人先点掉三个首启弹窗的启动脚本。第二个
harness 一样都不用付。

所以边界就是全部的意义。**外面 import 包本身，不 import 它的子模块**，而
``claude_code/__init__.py`` 的导出表就是「还有什么在跨界」的账本。

## 账本是棘轮

下面 ``_LEDGER`` 记的是每个跨界的生产模块和它拿走的名字。多一条会红（新的跨界），
少一条也会红（还完了忘删）。所以：把某个模块从适配器上解开之后，必须回来删掉它那
一行，红了就是提醒你删。

写模块粒度而不是名字粒度是故意的：同一个文件从适配器里多拿一个名字，架构上还是同
一处跨界，不值得再走一次 review；而某个文件**整个**不再需要适配器，才是真的还了债。

## 今天这张表在说什么

三类，性质完全不同：

- **channels**（device / cloud）：它们实现 ``Channel``，拿走这一个接缝和它抛
  的错——这些都是 transport 拿来照做的，不是它自己定的。**「跑什么」不在这张表
  里**：那是 ``harness.launch`` 的 ``LaunchPlan``，通道说自己的坐标、拿回一份它读
  不懂的 ``LaunchSpec``。device 还有：一台远程机器的启动是这边写出来的一段 shell，
  脚本里写着 claude，所以 ``build_screen_launch`` 留在账上。剩下
  ``SESSION_TOKEN_TTL_S``、``DEVICE_*_PROBE`` 同理：Claude Code 的知识，今天还长在
  传输层里。「起来了」的判据今天是 rendezvous socket 开始接受连接，那是连接器在
  ``dialWhenReady`` 里等的；等第二个 harness 的 spike 说清它那儿长什么样，再考虑
  把这个判据搬进 ``LaunchPlan``。
- **平台侧（chat.py）：一行也没有了。** 曾经它拿走 ``MessageAssembler`` 和 spool 的
  四个读写函数——「把 hook 翻译成房间里的东西」有一半住在平台侧，认得的是 Claude Code
  的事件形状。现在它只通过 ``AgentRuntime`` 的 ``backlog`` 拿到已经拼好的
  ``AgentEvent``，落库发帧还是它的活，翻译不是。**这一行别再长回来。**
- **装配**（``compute``）：``build_compute_pool`` 在这里把 runtime 和 channel 拼起来，
  所以它认得两个类名。装配处见得到零件是应该的——但也只有这里见得到。
- **边缘**（``routes/sandbox`` 收 hook、``workspace`` 回收工作区时通知、
  ``machine/enrollment`` 检查版本）：这几条大概率会一直在。适配器有一条对外的边，
  边总得有人站着；第二个 harness 自己带一条，而不是从这条挤进去。
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PKG = "app.domain.agent.harness.claude_code"

# 模块 → 它从适配器拿走的名字（排序后的元组）。见上面「账本是棘轮」。
_LEDGER: dict[str, tuple[str, ...]] = {
    # Session placement uses the harness's executor bootstrap and control adapter.
    "app.domain.agent.central_provider": ("ScreenSetupError", "build_executor_launch"),
    "app.domain.agent.private_chat": ("RemoteClient", "private_execution_target"),
    "app.api.routes.remote_control": ("REMOTE_CONTROLS",),
    # Retirement flushes the same native raw-file collector before deletion.
    "app.domain.topic.retire": ("event_drain",),
    # --- 边缘：适配器对外的那条边 ---
    "app.api.routes.sandbox": ("append_event", "hook_router"),
    # Enrollment prepares the native cache and idle process before advertising capacity.
    "app.domain.machine.enrollment": (
        "CLAUDE_MIN_VERSION",
        "CLAUDE_PINNED_VERSION",
        "build_startup_cache_prepare",
        "build_warm_session_prepare",
    ),
    # --- 装配：池子在这里把 runtime 和 channel 拼起来，也只在这里 ---
    "app.domain.agent.compute": ("Channel", "ClaudeCodeRuntime"),
    "app.domain.agent.device_hub": (
        "drop_device_subscriptions",
        "drop_screen_subscriptions",
    ),
    # --- channels：接缝本身，加上还没搬过缝的 Claude Code 知识 ---
    "app.domain.agent.cloud_provider": ("ScreenSetupError",),
    "app.domain.agent.device_provider": (
        "CHEESE_HOOK_SCRIPT",
        "Channel",
        "DEVICE_ALIVE_PROBE",
        "DEVICE_TUNNEL_PROBE",
        "SESSION_TOKEN_TTL_S",
        "ScreenSetupError",
        "build_screen_launch",
    ),
}


def _module_name(path: Path) -> str:
    return "app." + ".".join(path.relative_to(APP).with_suffix("").parts)


def _crossings() -> tuple[dict[str, set[str]], list[str]]:
    """Who imports the adapter, what they take, and who reached past the door."""
    taken: dict[str, set[str]] = {}
    through_a_submodule: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        module = _module_name(path)
        if module.startswith(PKG):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == PKG:
                taken.setdefault(module, set()).update(a.name for a in node.names)
            elif node.module.startswith(PKG + "."):
                through_a_submodule.append(f"{module} → {node.module}")
    return taken, through_a_submodule


def test_nothing_outside_reaches_past_the_package_door():
    """一个 harness 的内部结构不该是别人能依赖的东西。从包门口拿，门口那张表才
    能当账本用；直接摸子模块，账本就不知道有这回事。"""
    _, reached_past = _crossings()
    assert not reached_past, (
        "这些地方越过了 claude_code 的包门口，直接 import 了它的子模块。"
        "要么从包本身 import，要么把名字加进 __init__ 的导出表：\n  "
        + "\n  ".join(sorted(reached_past))
    )


def test_the_ledger_says_exactly_who_still_depends_on_this_harness():
    """新增一处跨界要写进账本（于是过一次 review），还完一处要删掉那一行。"""
    taken, _ = _crossings()
    actual = {module: tuple(sorted(names)) for module, names in taken.items()}
    added = sorted(set(actual) - set(_LEDGER))
    paid_off = sorted(set(_LEDGER) - set(actual))
    assert not added, (
        "新的模块开始依赖 Claude Code 适配器了。真的需要就加进 _LEDGER，"
        f"顺便在 review 里说清为什么：{added}"
    )
    assert not paid_off, (
        f"这些模块已经不依赖适配器了，把它们从 _LEDGER 里删掉：{paid_off}"
    )
    for module in sorted(actual):
        assert actual[module] == _LEDGER[module], (
            f"{module} 从适配器拿的名字变了。"
            f"账本写着 {_LEDGER[module]}，实际是 {actual[module]}"
        )
