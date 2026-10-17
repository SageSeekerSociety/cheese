"""守卫：两处零引用的模块级函数已经删掉，不许再长回来。

``app.domain.textfile.looks_binary`` 只是 ``decode_text(...) is None`` 的包装，
``app.domain.agent.market.subscription_model_default`` 只是从 ``_SUB_MODELS`` 里
挑 ``default`` 为真的那一项。两者全仓零调用、零测试、零导出，留着只会让读者以为
它们是活接口（#1681 第九批 X6）。

读源码的 AST，不走 ``hasattr``：名字在文件里重新出现就算回流，不管是重新定义、
从别处导入，还是给它一个别名赋值。文件末尾两条自测证明这个检查本身会红——模块级
定义算数，函数或类体里的同名局部定义不算。
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


def _source_path(module: str) -> Path:
    return APP_ROOT.joinpath(*module.split(".")[1:]).with_suffix(".py")


def _module_level_names(source: str) -> set[str]:
    """模块顶层绑定的名字：``from module import X`` 能拿到的那些。"""
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


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
        "looks_binary = decode_text\n",
    ):
        assert "looks_binary" in _module_level_names(source), source


def test_the_check_ignores_a_nested_definition():
    nested = (
        "class Panel:\n"
        "    def looks_binary(self):\n"
        "        return True\n"
        "\n"
        "def render():\n"
        "    def looks_binary(data):\n"
        "        return True\n"
        "    return looks_binary\n"
    )
    assert _module_level_names(nested) == {"Panel", "render"}
