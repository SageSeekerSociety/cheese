"""退役符号的守卫：这四个名字已经清退，不许再回到代码里。

清退的依据是零引用，不是「看着没人用」：

- `app.domain.repository.service` 的 `SANDBOX_SESSIONS_ROOT`、`sessions_root`、
  `sandbox_session_dir`：一个常量和两个路径助手。全仓在用的同族名字是
  `SANDBOX_TOPICS_ROOT`、`sandbox_topic_workdir` 和 `session_dir`，它们必须留着。
- `app.domain.agent.machine_tunnel._OP_CONT`：同一组 opcode 常量里只有它没有读者，
  `_OP_BIN`、`_OP_CLOSE`、`_OP_PING`、`_OP_PONG` 都还在用。

名字回到原来的模块上、或者换一个模块重新定义一份，都算回流；清退时留在原处的邻居
被顺手删掉，同样红。三条一起钉，下一次整理才不会把「没人用的」和「还在用的」看
成一类。

原候选里还有第五个名字 `app.domain.knowledge.models.KnowledgeSource`。合 main 时
#4150 把整个知识库从产品里去掉，`app.domain.knowledge` 整个域连文件一起删了，这个
枚举跟着知识域一起消失。清理它不再是这条改动的功劳，所以这里不再点它的名：守一个
已经不存在的模块，只在知识域被重新造出来时才红，而那件事该不该发生不是这条改动能
定的。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import app

APP_ROOT = Path(app.__file__).resolve().parent

#: (模块, 已清退的名字)。名字回到模块对象上就红。
RETIRED: dict[str, list[str]] = {
    "app.domain.repository.service": [
        "SANDBOX_SESSIONS_ROOT",
        "sessions_root",
        "sandbox_session_dir",
    ],
    "app.domain.agent.machine_tunnel": ["_OP_CONT"],
}

#: (模块, 必须留下的相邻名字)。清退时把这些一起删掉就红。
KEPT: dict[str, list[str]] = {
    "app.domain.repository.service": [
        "SANDBOX_TOPICS_ROOT",
        "sandbox_topic_workdir",
        "session_dir",
    ],
    "app.domain.agent.machine_tunnel": [
        "_OP_BIN",
        "_OP_CLOSE",
        "_OP_PING",
        "_OP_PONG",
    ],
}

RETIRED_NAMES = {name for names in RETIRED.values() for name in names}


class _ModuleBindings(ast.NodeVisitor):
    """一个模块顶层能 `from module import X` 拿到的名字。

    顶层语句不产生新作用域，所以默认的 ``generic_visit`` 照常往下走；只有进入
    ``def``、``class`` 或 ``lambda`` 时才把作用域计数加上去，里面的绑定不再记。
    """

    def __init__(self) -> None:
        self.names: set[str] = set()
        self._scopes = 0

    def _define(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        if self._scopes == 0:
            self.names.add(node.name)
        self._scopes += 1
        self.generic_visit(node)
        self._scopes -= 1

    visit_FunctionDef = _define
    visit_AsyncFunctionDef = _define
    visit_ClassDef = _define

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._scopes += 1
        self.generic_visit(node)
        self._scopes -= 1

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._scopes == 0:
            for target in node.targets:
                self._add_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._scopes == 0:
            self._add_target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._scopes == 0:
            self._add_target(node.target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if self._scopes == 0:
            self._add_target(node.target)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._scopes == 0:
            for alias in node.names:
                self.names.add(alias.asname or alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._scopes == 0:
            for alias in node.names:
                self.names.add(alias.asname or alias.name)

    def visit_For(self, node: ast.For) -> None:
        if self._scopes == 0:
            self._add_target(node.target)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With) -> None:
        if self._scopes == 0:
            for item in node.items:
                if item.optional_vars is not None:
                    self._add_target(item.optional_vars)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if self._scopes == 0 and node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if self._scopes == 0 and node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if self._scopes == 0 and node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if self._scopes == 0 and node.rest:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_TypeAlias(self, node: ast.AST) -> None:
        if self._scopes == 0:
            self._add_target(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        # 函数里 `global x` 之后给 x 赋值，绑的是模块上的 x。
        self.names.update(node.names)

    def _add_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._add_target(element)
        elif isinstance(target, ast.Starred):
            self._add_target(target.value)


def _module_level_names(source: str) -> set[str]:
    visitor = _ModuleBindings()
    visitor.visit(ast.parse(source))
    return visitor.names


@pytest.mark.parametrize(
    ("module_name", "name"),
    [
        pytest.param(module_name, name, id=f"{module_name}:{name}")
        for module_name, names in RETIRED.items()
        for name in names
    ],
)
def test_a_retired_name_is_gone(module_name: str, name: str) -> None:
    module = importlib.import_module(module_name)
    assert not hasattr(module, name), (
        f"{module_name}.{name} 已按零引用清退。要在原处复活它，先说明谁调用、"
        "为什么现有的同族名字做不了这件事。"
    )


@pytest.mark.parametrize(
    ("module_name", "name"),
    [
        pytest.param(module_name, name, id=f"{module_name}:{name}")
        for module_name, names in KEPT.items()
        for name in names
    ],
)
def test_the_name_that_stayed_is_still_there(module_name: str, name: str) -> None:
    module = importlib.import_module(module_name)
    assert hasattr(module, name), (
        f"{module_name}.{name} 还在被使用，清退时不能连它一起删。"
    )


def test_no_module_in_the_app_binds_a_retired_name() -> None:
    """换一个模块重新定义同名符号，同样是回流。

    `hasattr` 只看得见被点名的那三个模块。有人把 `sessions_root` 搬进
    `repository/paths.py`、让调用方从那里 import，原来的模块干干净净，清退就白做了。
    这里扫整个 `backend/app` 的顶层绑定，重新定义、从别处 import、别名赋值都算。
    """
    found: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        # 字符串里都没有，就不可能绑定它。跳过的只是 AST 解析，判定口径不变。
        if not any(name in source for name in RETIRED_NAMES):
            continue
        hits = _module_level_names(source) & RETIRED_NAMES
        if hits:
            found.append(f"{path.relative_to(APP_ROOT)}: {', '.join(sorted(hits))}")
    assert not found, (
        "已清退的名字又出现在这些模块的顶层：" + "；".join(found) + "。"
        "要重新引入，先说明谁调用、为什么现有的同族名字做不了这件事。"
    )


def test_the_binding_scan_sees_every_way_a_name_could_come_back() -> None:
    """守卫自己的自测：换各种写法把名字放回顶层，扫描都要看得见。"""
    for source in (
        "def sessions_root(project_id):\n    return project_id\n",
        "class SANDBOX_SESSIONS_ROOT:\n    pass\n",
        "from app.domain.repository.service import sessions_root\n",
        "from app.domain.repository import service as _OP_CONT\n",
        "sessions_root = None\n",
        "sessions_root: object = None\n",
        "sessions_root, other = None, None\n",
        "(sessions_root := None)\n",
        "if True:\n    from app.domain.repository.service import sandbox_session_dir\n",
        "try:\n    SANDBOX_SESSIONS_ROOT = '/sessions'\n"
        "except ImportError:\n    SANDBOX_SESSIONS_ROOT = None\n",
        "with open('x') as handle:\n    _OP_CONT = handle\n",
        "for sessions_root in []:\n    pass\n",
        "with open('x') as sessions_root:\n    pass\n",
        "try:\n    pass\nexcept Exception as sessions_root:\n    pass\n",
        "match value:\n    case {'k': sessions_root}:\n        pass\n",
        "def f():\n    global _OP_CONT\n    _OP_CONT = 1\n",
        "sessions_root += 1\n",
    ):
        assert _module_level_names(source) & RETIRED_NAMES, source


def test_the_binding_scan_keeps_a_lambda_and_an_inner_import_out() -> None:
    """这两个负向用例各自钉住一条作用域守卫；守卫被删掉，它们会误报。

    自测和守卫要一起看：只写「嵌套定义不算」时，删掉 `visit_Lambda` 或
    `visit_ImportFrom` 里的作用域判断，套件仍然是绿的。
    """
    for source in (
        "f = lambda: (sessions_root := 1)\n",
        "def f():\n    from app.domain.repository.service import sessions_root\n",
    ):
        assert not _module_level_names(source) & RETIRED_NAMES, source


def test_the_binding_scan_ignores_a_nested_definition() -> None:
    """函数或类里面的同名局部量不是回流，扫描不能误报。"""
    nested = (
        "class Panel:\n"
        "    sessions_root = None\n"
        "    def sandbox_session_dir(self):\n"
        "        return 1\n"
        "\n"
        "def render():\n"
        "    def _OP_CONT(value):\n"
        "        return value\n"
        "    return _OP_CONT\n"
        "\n"
        "renderer = lambda SANDBOX_SESSIONS_ROOT=None: SANDBOX_SESSIONS_ROOT\n"
    )
    assert not _module_level_names(nested) & RETIRED_NAMES
