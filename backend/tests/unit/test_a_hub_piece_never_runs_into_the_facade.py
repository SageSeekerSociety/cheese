"""包里的子模块运行时只许往下引／横引，不许回头引门面。

包的方向是门面 → 子模块：``device_hub/__init__.py`` 从 ``link``／``sessions``／
``calls``／``local_fs``／``viewers``／``inbound`` 取身子，子模块之间只横着互引兄弟
（``viewers`` 问 ``sessions`` 要一块屏幕）。运行时反着引一次就是环，而环在这层的
后果看导入谁先：``import app.domain.agent.device_hub.viewers`` 先到时，门面正引到
一半，那里的 ``DeviceHub`` 还不存在。

**类型标注不算。** 子模块给 ``hub`` 标 ``pkg.DeviceHub`` 时在 ``TYPE_CHECKING`` 里
引门面，运行时那一句不执行，导入图上没有这条边——这是这套拆法本来就用的写法，下面
的判据跟着它走。

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


def _is_the_facade(node: ast.ImportFrom | ast.Import) -> bool:
    """这一句导的是门面吗。两种写法都认：``...device_hub`` 和 ``agent`` 里那个。"""
    if isinstance(node, ast.Import):
        return any(
            alias.name == MODULE or alias.name.startswith(MODULE + ".")
            for alias in node.names
        )
    if node.module == MODULE:
        # `from app.domain.agent.device_hub import X` —— 兄弟是 `...device_hub.link`。
        return True
    # `from app.domain.agent import device_hub as pkg`
    return node.module == PARENT and any(
        alias.name == "device_hub" for alias in node.names
    )


def _imports_run_at_runtime(path: Path) -> list[tuple[int, str]]:
    """这份文件里运行时真的会执行的（也就是不在 ``if TYPE_CHECKING:`` 底下的）导入。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    inside_type_checking: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            guarded = node.test
            named = (
                getattr(guarded, "id", None)
                if isinstance(guarded, ast.Name)
                else getattr(guarded, "attr", None)
            )
            if named == "TYPE_CHECKING":
                inside_type_checking.update(ast.walk(node))

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if node in inside_type_checking:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)) and _is_the_facade(node):
            found.append((node.lineno, ast.unparse(node)))
    return found


def test_no_hub_submodule_imports_the_facade_at_runtime():
    pieces = sorted(p for p in PACKAGE.glob("*.py") if p.name != FACADE)
    # 扫不出文件就是路径写歪了，别让这条测试空着过。
    assert {"link.py", "sessions.py", "calls.py"} <= {p.name for p in pieces}

    reaching_back = [
        (path.name, lineno, source)
        for path in pieces
        for lineno, source in _imports_run_at_runtime(path)
    ]

    assert not reaching_back, (
        f"这些子模块运行时引了门面：{reaching_back}。"
        "门面引子模块、子模块再引门面就是环，导入谁先决定它炸不炸；"
        "要 hub 的类型就放进 `if TYPE_CHECKING:`，或在门面里把名字转出去。"
    )
