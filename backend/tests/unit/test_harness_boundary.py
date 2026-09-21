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
  不懂的 ``LaunchSpec``。device 那段 shell 也还清了：平台那一半是
  ``machine_launcher``，harness 那一半是 ``LaunchPlan.on`` 答的，channel 只说
  「在哪」。剩下 ``SESSION_TOKEN_TTL_S``、``DEVICE_*_PROBE`` 还没还：Claude Code
  的知识，今天还长在传输层里。「起来了」的判据今天是 rendezvous socket 开始接受
  连接，那是连接器在
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
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
PKG = "app.domain.agent.harness.claude_code"

# 模块 → 它从适配器拿走的名字（排序后的元组）。见上面「账本是棘轮」。
_LEDGER: dict[str, tuple[str, ...]] = {
    "app.domain.agent.private_chat": ("private_execution_target",),
    "app.api.routes.remote_control": ("REMOTE_CONTROLS",),
    # --- 边缘：适配器对外的那条边 ---
    "app.api.routes.sandbox": ("hook_router",),
    # 行为声明的汇总侧：三个骨架的门口各取一份 declaration()，拼成功能矩阵。
    # 它只拿这一个名字，而且拿的是「这个 harness 自己说自己是什么」——不是
    # 适配器的内部零件。
    "app.domain.agent.capability.matrix": ("declaration",),
    # Enrollment prepares the native cache and idle process before advertising capacity.
    "app.domain.machine.enrollment": (
        "CLAUDE_MIN_VERSION",
        "CLAUDE_PINNED_VERSION",
        "build_startup_cache_prepare",
        "build_warm_session_prepare",
    ),
    # --- 装配：池子在这里把 runtime 和 channel 拼起来，也只在这里 ---
    "app.domain.agent.compute": ("ClaudeCodeRuntime", "executor_launch"),
    "app.domain.agent.device_hub": (
        "drop_device_subscriptions",
        "drop_screen_subscriptions",
    ),
    # --- channels：接缝本身，加上还没搬过缝的 Claude Code 知识 ---
    "app.domain.agent.device_provider": (
        "DEVICE_ALIVE_PROBE",
        "DEVICE_TUNNEL_PROBE",
        "SESSION_TOKEN_TTL_S",
        "resident_release",
    ),
}


def _module_name(path: Path) -> str:
    return "app." + ".".join(path.relative_to(APP).with_suffix("").parts)


def _crossings(package: str = PKG) -> tuple[dict[str, set[str]], list[str]]:
    """Who imports the adapter, what they take, and who reached past the door."""
    taken: dict[str, set[str]] = {}
    through_a_submodule: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        module = _module_name(path)
        if module.startswith(package):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == package:
                taken.setdefault(module, set()).update(a.name for a in node.names)
            elif node.module.startswith(package + "."):
                through_a_submodule.append(f"{module} → {node.module}")
    return taken, through_a_submodule


@pytest.mark.parametrize("package", [PKG, "app.domain.agent.harness.codex"])
def test_nothing_outside_reaches_past_the_package_door(package):
    """一个 harness 的内部结构不该是别人能依赖的东西。从包门口拿，门口那张表才
    能当账本用；直接摸子模块，账本就不知道有这回事。"""
    _, reached_past = _crossings(package)
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


# --- 骨架的名字只写在注册表里 -----------------------------------------------
#
# 结论 28：harness 不是产品概念，是部署/项目级的开发者选项。不变量 I5：领域层不
# 出现实现的名字，而且**按字面量抓，不按 import 抓**——从注册表 import 一个常量
# 是对的写法，源码里再打一遍那个名字不是。
#
# 为什么按字面量：一处字面量就是「跑的是哪个骨架」的第二个答法，而它永远不会跟着
# 设置改。原样长在这儿的三处——``central_provider`` 的分岔、``agent_session`` 那
# 一列的两个默认值、``hook_events`` 给事件盖的戳——每一处都让一套配成别的骨架的
# 部署当场答错，而没有一条功能测试会因此变红。
#
# 名字只许出现在一个文件里：``harness/__init__.py``。那里是注册表，也是
# ``deployment_harness()`` 在部署没配的时候取值的地方。适配器自己那个目录也不
# 例外——``hooks_substrate`` 早就是 ``harness = CLAUDE_CODE``，import 得到的东西
# 就不该再拼一遍。

_REGISTRY = "app/domain/agent/harness/__init__.py"

#: 注册表里写着的名字。这里按源码认，不 import ``HARNESSES`` ——守卫要抓的正是
#: 「名字被写成了字面量」，拿被守的东西当判据等于放弃判据。
_HARNESS_NAMES = ("claude-code",)


def _docstring_constants(tree: ast.AST) -> set[int]:
    """文档字符串那些节点。写清楚一个骨架长什么样是说明，不是选择。"""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }


def _name_literals(source: str) -> list[tuple[int, str]]:
    """源码里把骨架名字原样打出来的地方。

    只认**整个**字符串就是那个名字的：``"https://…/claude-code-releases"`` 是
    Anthropic 的下载地址，不是「这一轮跑哪个骨架」的答案，把它也算进来只会把下一
    个人导去改一处本来对的代码。
    """
    tree = ast.parse(source)
    skip = _docstring_constants(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in _HARNESS_NAMES
        and id(node) not in skip
    ]


def test_the_harness_name_is_written_down_once_in_the_backend() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(APP.rglob("*.py")):
        relative = str(path.relative_to(APP.parent))
        if relative == _REGISTRY:
            continue
        hits = _name_literals(path.read_text(encoding="utf-8"))
        if hits:
            offenders[relative] = hits
    assert not offenders, (
        f"这些地方把骨架的名字原样写了出来：{offenders}。跑的是哪个骨架由部署设置"
        f"加项目设置答（``deployment_harness()`` / ``harness_for()``），名字本身"
        f"只写在 {_REGISTRY} 里。"
    )


_FRONTEND_SRC = APP.parent.parent / "frontend/src"
_IN_THE_INTERFACE = re.compile(
    "|".join(f"""['"`]{re.escape(name)}['"`]""" for name in _HARNESS_NAMES)
)


def test_the_interface_does_not_know_any_harness_by_name() -> None:
    """界面上一个骨架名字都没有：普通用户看不到骨架这回事（结论 28）。"""
    assert _FRONTEND_SRC.is_dir(), (
        f"{_FRONTEND_SRC} 不在——这条守卫会扫到零个文件然后绿。"
    )
    offenders: dict[str, list[str]] = {}
    for path in sorted(_FRONTEND_SRC.rglob("*")):
        if path.suffix not in (".ts", ".vue", ".js"):
            continue
        hits = [
            f"line {number}: {line.strip()}"
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            )
            if _IN_THE_INTERFACE.search(line)
        ]
        if hits:
            offenders[str(path.relative_to(_FRONTEND_SRC))] = hits
    assert not offenders, (
        f"界面上出现了骨架的名字：{offenders}。骨架是开发者选项，界面上没有它。"
    )


# 守卫自己得能抓到东西：名字都收走之后，「扫出来是空的」既是它守住了的样子，也是
# 它什么都没在看的样子。该红的喂进去要命中，不该红的喂进去要放过。
_MUST_CATCH = {
    "a-branch": 'if harness != "claude-code":\n    pass\n',
    "a-column-default": 'harness = mapped_column(String(64), default="claude-code")\n',
    "a-stamp": 'event = AgentSessionInfo(sid, harness="claude-code")\n',
    "a-dict-value": 'env = {"CHEESE_HARNESS": "claude-code"}\n',
}

_MUST_PASS = {
    # 从注册表拿常量是对的写法——I5 明说了按字面量抓，不按 import 抓。
    "the-constant": "harness = CLAUDE_CODE\n",
    # Anthropic 的下载地址，名字在里面但它答的不是「跑哪个骨架」。
    "a-url": 'BASE = "https://downloads.claude.ai/claude-code-releases"\n',
    # 说明不是选择。
    "a-docstring": '"""一间房里的 claude-code 队友换成了 pi。"""\n',
}


@pytest.mark.parametrize("source", _MUST_CATCH.values(), ids=list(_MUST_CATCH))
def test_self_test_the_literal_guard_goes_red_on(source: str) -> None:
    assert _name_literals(source), f"没抓到：{source!r}"


@pytest.mark.parametrize("source", _MUST_PASS.values(), ids=list(_MUST_PASS))
def test_self_test_the_literal_guard_lets_through(source: str) -> None:
    assert _name_literals(source) == [], f"误红：{source!r}"
