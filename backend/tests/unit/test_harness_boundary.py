"""架构守卫：Claude Code 适配器的内部，外面只能从包门口拿。

包里的东西没有一样是「跑一个 agent」的事实——它们全是 Claude Code 这一个 harness
碰巧长成的样子：一个用 stream-json 管道握住 headless ``claude -p`` 的 runner、把它
的记录翻成房间事件的那一层、钉死版本并隔离配置的启动器、把它的工具转去房间执行机
的 remote-execution 插件。第二个 harness 一样都不用付。

所以边界就是全部的意义。**外面 import 包本身，只拿它 ``__all__`` 里的名字**，
``claude_code/__init__.py`` 的导出表就是「还有什么在跨界」的那一份声明。越过门口
没有例外：直接 import 子模块（``from … import`` 和 ``import …`` 两种写法都算），
或者从门口拿一个不在 ``__all__`` 里的名字（比如把子模块本身当名字拿走），一律红。
"""

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
PKG = "app.domain.agent.harness.claude_code"


def _module_name(path: Path) -> str:
    return "app." + ".".join(path.relative_to(APP).with_suffix("").parts)


def _exported(package: str) -> set[str]:
    """The names the package's door lists in ``__all__``, read from source."""
    door = APP.parent.joinpath(*package.split("."), "__init__.py")
    for node in ast.parse(door.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"{door} has no __all__")


def _reached_past(package: str = PKG) -> list[str]:
    """Every import outside the package that does not go through its door."""
    exported = _exported(package)
    found: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        module = _module_name(path)
        if module == package or module.startswith(package + "."):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [
                    f"{module} → {alias.name}"
                    for alias in node.names
                    if alias.name.startswith(package + ".")
                ]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(package + "."):
                    found.append(f"{module} → {node.module}")
                elif node.module == package:
                    found += [
                        f"{module} → {package}.{alias.name} (not in __all__)"
                        for alias in node.names
                        if alias.name not in exported
                    ]
    return found


@pytest.mark.parametrize("package", [PKG, "app.domain.agent.harness.codex"])
def test_nothing_outside_reaches_past_the_package_door(package):
    """一个 harness 的内部结构不该是别人能依赖的东西。从包门口拿、只拿导出表里的
    名字，门口那张表才是跨界的全部；直接摸子模块，那张表就不知道有这回事。"""
    reached_past = _reached_past(package)
    assert not reached_past, (
        f"这些地方越过了 {package} 的包门口。"
        "要么从包本身 import 导出表里的名字，要么把名字加进 __init__ 的 __all__：\n  "
        + "\n  ".join(sorted(reached_past))
    )


# --- 骨架的名字只写在注册表里 -----------------------------------------------
#
# 结论 28：harness 不是产品概念，是部署/项目级的开发者选项。不变量 I5：领域层不
# 出现实现的名字，而且**按字面量抓，不按 import 抓**——从注册表 import 一个常量
# 是对的写法，源码里再打一遍那个名字不是。
#
# 为什么按字面量：一处字面量就是「跑的是哪个骨架」的第二个答法，而它永远不会跟着
# 设置改。原样长在这儿的三处——``central_provider`` 的分岔、``agent_session`` 那
# 一列的两个默认值、事件翻译给事件盖的戳——每一处都让一套配成别的骨架的
# 部署当场答错，而没有一条功能测试会因此变红。
#
# 名字只许出现在一个文件里：``harness/__init__.py``。那里是注册表，也是
# ``deployment_harnesses()`` 在部署没配的时候取值的地方。适配器自己那个目录也不
# 例外——``claude_code/runtime.py`` 是 ``harness = CLAUDE_CODE``，import 得到的
# 东西就不该再拼一遍。
#
# 三个名字一起守。只守 claude-code 的那一版，是拿一条守着三分之一的守卫当验收：
# 下一个人往领域层写 ``harness != "codex"``——和这次从 ``central_provider`` 删掉的
# 那处分岔一模一样的东西——照样全绿。codex 和 pi 两个适配器目录里今天还各有几处在
# 打自己的名字，一条一条记在下面的 ``_SPELLED_OUT`` 里，清单外的一律红。

_REGISTRY = "app/domain/agent/harness/__init__.py"

#: 注册表里写着的名字，三个都在。这里按源码认，不 import ``HARNESSES`` ——守卫要
#: 抓的正是「名字被写成了字面量」，拿被守的东西当判据等于放弃判据。
_HARNESS_NAMES = ("claude-code", "codex", "pi")

#: 今天还把自己名字原样打出来的地方：模块 → 它打出来的名字（排序后）。这是一道
#: 棘轮——多一条会红（新长出来的字面量，得过一次 review），少一条也会红（还完了
#: 忘删这一行）。
#:
#: 清单上一条都不在领域层：全是 codex 和 pi 两个适配器自己目录里的，说的是「我是
#: 谁」而不是「这一轮跑谁」——它们的门口还没有像 claude_code 那样从注册表 import
#: 自己那个常量。清单外的任何一处新字面量都红，这才是 I5 要守的那条线。
_SPELLED_OUT: dict[str, tuple[str, ...]] = {
    "app/domain/agent/harness/codex/host.py": ("codex",),
    "app/domain/agent/harness/codex/runner.py": ("codex",),
    "app/domain/agent/harness/pi/runner.py": ("pi",),
}


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
    spelled: dict[str, tuple[str, ...]] = {}
    for path in sorted(APP.rglob("*.py")):
        relative = str(path.relative_to(APP.parent))
        if relative == _REGISTRY:
            continue
        hits = _name_literals(path.read_text(encoding="utf-8"))
        if hits:
            spelled[relative] = tuple(sorted({value for _, value in hits}))
    added = sorted(set(spelled) - set(_SPELLED_OUT))
    paid_off = sorted(set(_SPELLED_OUT) - set(spelled))
    assert not added, (
        f"这些地方把骨架的名字原样写了出来：{ {m: spelled[m] for m in added} }。"
        f"跑的是哪个骨架由部署设置加项目设置答（``harness_for()`` / "
        f"``harness_on()``），名字本身只写在 {_REGISTRY} 里。真的躲不掉就加进 "
        f"_SPELLED_OUT，顺便在 review 里说清为什么。"
    )
    assert not paid_off, (
        f"这些模块已经不打自己的名字了，把它们从 _SPELLED_OUT 里删掉：{paid_off}"
    )
    for module in sorted(spelled):
        assert spelled[module] == _SPELLED_OUT[module], (
            f"{module} 打出来的名字变了。清单写着 {_SPELLED_OUT[module]}，"
            f"实际是 {spelled[module]}"
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
    # 另外两个骨架的名字同样是名字：领域层写 ``harness != "codex"`` 和写
    # ``!= "claude-code"`` 是同一处分岔，守卫也得同样红。
    "a-branch-on-another-harness": 'if harness != "codex":\n    pass\n',
    "a-second-declaration": 'PI = "pi"\n',
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
