"""守卫：两处零引用的模块级函数已经删掉，不许再长回来。

``app.domain.textfile.looks_binary`` 只是 ``decode_text(...) is None`` 的包装，
``app.domain.agent.market.subscription_model_default`` 只是从 ``_SUB_MODELS`` 里
挑 ``default`` 为真的那一项。两者全仓零调用、零测试、零导出，留着只会让读者以为
它们是活接口（#1681 第九批 X6）。

读源码的 AST，不走 ``hasattr``：名字在文件里重新出现就算回流——重新定义、从别处
导入、别名赋值都算；``if``/``try``/``with``/``for``/``match`` 这些顶层语句不产生
新作用域，它们分支里绑定的名字一样能从模块上取到，所以也照算。只有 ``def``、
``class``、``lambda`` 体里的同名局部定义不算。文件末尾两条自测把这两个方向都钉住。
"""

import ast
from pathlib import Path

import app

APP_ROOT = Path(app.__file__).resolve().parent

#: 删掉的两个函数：模块 → 名字。
_GONE = {
    "app.domain.textfile": "looks_binary",
    "app.domain.agent.market": "subscription_model_default",
}

#: 每个模块里必须还在的一个邻居。文件读错、解析范围缩小时，上面那条断言会空着过，
#: 所以拿一个还在的名字当底线。
_STILL_THERE = {
    "app.domain.textfile": "decode_text",
    "app.domain.agent.market": "subscription_model_listings",
}


class _ModuleBindings(ast.NodeVisitor):
    """模块顶层绑定的名字，也就是 ``from module import X`` 能拿到的那些。

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

    def _add_target(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._add_target(element)
        elif isinstance(target, ast.Starred):
            self._add_target(target.value)


def _source_path(module: str) -> Path:
    return APP_ROOT.joinpath(*module.split(".")[1:]).with_suffix(".py")


def _module_level_names(source: str) -> set[str]:
    visitor = _ModuleBindings()
    visitor.visit(ast.parse(source))
    return visitor.names


def test_the_deleted_functions_are_still_gone():
    for module, gone in _GONE.items():
        path = _source_path(module)
        assert path.is_file(), path
        names = _module_level_names(path.read_text(encoding="utf-8"))
        # 底线：确认读到的确实是这个模块，而不是空文件或拼错的路径。
        assert _STILL_THERE[module] in names, path
        assert gone not in names, f"{module}.{gone} 又回来了"


def test_the_check_sees_every_way_the_name_could_come_back():
    for source in (
        "def looks_binary(data):\n    return True\n",
        "async def looks_binary(data):\n    return True\n",
        "from app.domain.textfile import looks_binary\n",
        "from app.domain import textfile as looks_binary\n",
        "looks_binary = decode_text\n",
        "looks_binary: object = decode_text\n",
        "looks_binary, other = decode_text, 1\n",
        "(looks_binary := decode_text)\n",
        "if True:\n    from app.domain.textfile import looks_binary\n",
        "try:\n    def looks_binary(data):\n        return True\n"
        "except ImportError:\n    looks_binary = None\n",
        "with open('x') as handle:\n    looks_binary = handle\n",
    ):
        assert "looks_binary" in _module_level_names(source), source


def test_the_check_ignores_a_nested_definition():
    nested = (
        "class Panel:\n"
        "    looks_binary = None\n"
        "    def looks_binary(self):\n"
        "        return True\n"
        "\n"
        "def render():\n"
        "    def looks_binary(data):\n"
        "        return True\n"
        "    return looks_binary\n"
        "\n"
        "renderer = lambda looks_binary=None: looks_binary\n"
    )
    assert _module_level_names(nested) == {"Panel", "render", "renderer"}
