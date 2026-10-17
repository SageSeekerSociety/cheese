"""包里的子模块运行时只许往下引／横引，不许回头引门面。

包的方向是门面 → 子模块：``device_hub/__init__.py`` 从 ``link``／``sessions``／
``calls``／``local_fs``／``viewers``／``inbound`` 取身子，子模块之间只横着互引兄弟
（``viewers`` 问 ``sessions`` 要一块屏幕）。运行时反着引一次就是环，而环在这层的
后果看导入谁先：``import app.domain.agent.device_hub.viewers`` 先到时，门面正引到
一半，那里的 ``DeviceHub`` 还不存在——报出来的是「partially initialized module」。

**类型标注不算。** 子模块给 ``hub`` 标 ``pkg.DeviceHub`` 时在 ``if TYPE_CHECKING:``
里引门面，运行时那一句不执行，导入图上没有这条边——这是这套拆法本来就用的写法，
下面的判据跟着它走。别名（``as TC``）也认。只认这一种写法：``TYPE_CHECKING`` 跟
别的条件拼在一起（``if TYPE_CHECKING and _X``、``while TYPE_CHECKING``）按运行时
报，宁可让人把那句挪进 ``if TYPE_CHECKING:``，也不放过一条真的边。

**怎么写不决定判不判。** 判据先把一句解析成它落在哪个模块上，再判那个模块是不是门
面：``from . import DeviceHub`` 和 ``from app.domain.agent.device_hub import
DeviceHub`` 落在门面上，``from ..device_hub import HubScreen`` 也落在门面上（``..``
是 ``agent``，再点门面的名）；而 ``from .link import X``、``import
app.domain.agent.device_hub.link`` 落在兄弟上，``from . import sessions`` 取的名字本
身就是兄弟，都不算。仓库里相对导入不是空想（``review/services`` 就在用），两种写法
都得读。

**这段 C3 看不见。** ``acyclic_siblings`` 数的是 ``app.domain`` 的孩子和它们的孩子
之间的环；包自己是一个节点，它内部这些子模块不在它的账上。所以在这里点名扫包内，而
不是指望契约。
"""

import ast
from pathlib import Path

MODULE = "app.domain.agent.device_hub"
PARENT = "app.domain.agent"
PACKAGE = (
    Path(__file__).resolve().parents[2] / "app" / "domain" / "agent" / "device_hub"
)

#: 门面自己当然要从子模块里取名字，它不在这条规矩的对象里。
FACADE = "__init__.py"

#: 同级兄弟。`from . import X` 取到的是它们，那就是横引，不算回头。
SIBLINGS = {path.stem for path in PACKAGE.glob("*.py") if path.name != FACADE}


def _type_checking_names(tree: ast.Module) -> set[str]:
    """这份文件里能当 ``TYPE_CHECKING`` 用的名字，别名一起收。"""
    names = {"TYPE_CHECKING"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == (
            "typing"
        ):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "TYPE_CHECKING"
            )
    return names


def _is_type_checking(test: ast.expr, names: set[str]) -> bool:
    """``if`` 的判断是不是 ``TYPE_CHECKING``（``typing.TYPE_CHECKING`` 也算）。"""
    called = (
        getattr(test, "id", None)
        if isinstance(test, ast.Name)
        else getattr(test, "attr", None)
    )
    return called in names


def _package_at(level: int) -> str | None:
    """相对导入第 ``level`` 级落在哪个包上。本文件是包的直接子模块。"""
    parts = MODULE.split(".")
    if level - 1 >= len(parts):
        return None
    return ".".join(parts[: len(parts) - (level - 1)])


def _is_the_facade(node: ast.AST) -> bool:
    """这一句导的是门面吗。先解析它落在哪个模块上，再看那个模块是不是门面。"""
    if isinstance(node, ast.Import):
        # `import a.b.c` 导的是 a.b.c 这个模块本身；带上兄弟的名字就不是门面。
        return any(alias.name == MODULE for alias in node.names)
    if not isinstance(node, ast.ImportFrom):
        return False
    target = node.module
    if node.level:
        package = _package_at(node.level)
        if package is None:
            return False
        target = f"{package}.{node.module}" if node.module else package
    if target == MODULE:
        # 从门面里取名字。取到的是兄弟模块（`from . import sessions`）才不算。
        return any(alias.name not in SIBLINGS for alias in node.names)
    if target == PARENT:
        # `from .. import device_hub` / `from app.domain.agent import device_hub`
        return any(alias.name == "device_hub" for alias in node.names)
    # 落到 `...device_hub.link` 这类兄弟模块上的，是横引。
    return False


def _runtime_imports(source: str, where: str) -> list[ast.Import | ast.ImportFrom]:
    """这段源码里运行时真会执行的导入——``if TYPE_CHECKING:`` 底下的那些不算。

    只走 ``if`` 的 ``body``，不走 ``orelse``：``else:`` 那一支是运行时的。
    """
    tree = ast.parse(source, where)
    names = _type_checking_names(tree)
    type_only: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test, names):
            type_only.update(
                inner for statement in node.body for inner in ast.walk(statement)
            )
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in type_only
    ]


def _reaching_back(source: str, where: str) -> list[str]:
    return [
        ast.unparse(node)
        for node in _runtime_imports(source, where)
        if _is_the_facade(node)
    ]


def test_no_hub_submodule_imports_the_facade_at_runtime():
    pieces = sorted(path for path in PACKAGE.glob("*.py") if path.name != FACADE)
    # 扫不出文件就是路径写歪了，别让这条测试空着过。
    assert {"link.py", "sessions.py", "calls.py"} <= {path.name for path in pieces}

    reaching_back = [
        (path.name, node.lineno, ast.unparse(node))
        for path in pieces
        for node in _runtime_imports(path.read_text(encoding="utf-8"), str(path))
        if _is_the_facade(node)
    ]

    assert not reaching_back, (
        f"这些子模块运行时引了门面：{reaching_back}。"
        "门面引子模块、子模块再引门面就是环，导入谁先决定它炸不炸；"
        "要 hub 的类型就放进 `if TYPE_CHECKING:`，或者在门面里把名字转出去。"
    )


def test_the_guard_reads_the_shapes_it_claims_to_read():
    """规矩本身也过一遍。

    上面那条测试绿，只说明树里现在干净；它读不认得某种写法的话，有人用那种写法把
    环引回来，一样是绿的。所以判据对着合成的源码逐条问一遍——写法对了它得报，兄弟
    和类型标注得放过。
    """
    for source in (
        "from app.domain.agent.device_hub import DeviceHub",
        "import app.domain.agent.device_hub",
        "import app.domain.agent.device_hub as pkg",
        "from app.domain.agent import device_hub",
        "from app.domain.agent import device_hub as pkg",
        # 一个 `from … import` 可以点好几个名字，门面不一定排在第一个。
        "from app.domain.agent import device_link, device_hub",
        "from app.domain.agent.device_hub import *",
        "from . import *",
        "from . import DeviceHub",
        "from .. import device_hub",
        "def f():\n    from app.domain.agent.device_hub import DeviceHub\n",
        # 相对写法要多走一级才点到门面：`..` 落到 `agent`，再点它的 `device_hub`。
        "from ..device_hub import HubScreen",
        "from ...agent.device_hub import HubScreen",
        # 普通 `if` 底下的是运行时的，只有 `TYPE_CHECKING` 那一支才算类型标注。
        "if _HAS_NAMES:\n    from app.domain.agent.device_hub import DeviceHub\n",
        "from typing import TYPE_CHECKING\n"
        "if not TYPE_CHECKING:\n"
        "    from app.domain.agent.device_hub import DeviceHub\n",
    ):
        assert _reaching_back(source, "<sample>"), source

    for source in (
        "from app.domain.agent.device_hub.sessions import _require_screen",
        "from .link import DeviceTransport",
        "from . import sessions",
        "from app.domain.agent import device_link",
        # 绝对写法的横引兄弟，和 `from . import sessions` 是同一件事。
        "import app.domain.agent.device_hub.link as link_mod",
        "from app.domain.agent.device_hub import sessions",
        "from app.domain.agent.device_hub import sessions, link",
    ):
        assert not _reaching_back(source, "<sample>"), source

    # 类型标注那一支：`TYPE_CHECKING` 底下的不报，`else:` 里的报。别名一样算。
    for source in (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.domain.agent.device_hub import DeviceHub\n",
        "from typing import TYPE_CHECKING as TC\n"
        "if TC:\n"
        "    from app.domain.agent.device_hub import DeviceHub\n",
        "import typing\nif typing.TYPE_CHECKING:\n    from . import DeviceHub\n",
    ):
        assert not _reaching_back(source, "<sample>"), source

    assert _reaching_back(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    pass\n"
        "else:\n"
        "    from app.domain.agent.device_hub import DeviceHub\n",
        "<sample>",
    )
