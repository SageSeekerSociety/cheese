"""门面拆开之后，`from app.domain.agent.turn.intake.preparation import X` 不许断。

拆分最容易弄丢的就是这个：搬走身子的人只看自己那一片，没人看全仓库谁从这个模块
导过什么名字，而缺一个名的代价是导入时就炸——调用方连启动都到不了。

所以这里不列名单，而是把树里真实的导入语句读出来：不用维护一张会过期的表，谁删掉
一个还在被导入的名字，谁就红。

**扫的是整仓，不止 `backend/`。** 这是 `backend/AGENTS.md` 对搬走一个公开名字的要求，
`device_hub` 拆成包时留下的那条测试就是这么扫的。

**按模块对象替换的名字扫不到。** `held_blocks` 没有人按名字导入它，
`backend/tests/integration/native_deferred_message.py` 是拿到模块对象再替换的
（`monkeypatch.setattr(turn_module, "held_blocks", …)`）。替换要看得见，它就得留在
这个模块的命名空间里，所以下面单独守一条。
"""

import ast
import importlib
import os
from pathlib import Path

import app

MODULE = "app.domain.agent.turn.intake.preparation"
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent

#: 这些目录不是仓库自己读的 Python 源码，进去只会白读几千个文件。
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
}


def _scanned_files() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        found.extend(
            Path(dirpath) / name for name in sorted(filenames) if name.endswith(".py")
        )
    return found


def _names_imported_from_preparation() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里两千个文件，只有导入过这个模块的那几个才值得解析。
        if MODULE not in source:
            continue
        for node in ast.walk(ast.parse(source, str(path))):
            if isinstance(node, ast.ImportFrom) and node.module == MODULE:
                for alias in node.names:
                    found.setdefault(alias.name, []).append(
                        f"{path.relative_to(REPO)}:{node.lineno}"
                    )
    return found


def test_every_name_the_tree_imports_from_preparation_is_still_there():
    preparation = importlib.import_module(MODULE)
    imported = _names_imported_from_preparation()

    # 扫描范围本身要看着。名字的地板断言看不出范围缩回去了，所以直接数范围：
    # 调用方在 `app` 里，测试在 `tests` 里，两边都在册才算真的在扫整仓。
    sites = [site for place in imported.values() for site in place]
    assert any(site.startswith("backend/app/") for site in sites), sites
    assert any(site.startswith("backend/tests/") for site in sites), sites
    # 一条都扫不出来就是扫错了（路径拼歪、名字改坏），别让这条测试空着过。
    assert {"TurnPreparation", "living_doc_text"} <= set(imported)

    missing = {
        name: where
        for name, where in imported.items()
        if not hasattr(preparation, name)
    }

    assert not missing, (
        f"这些名字还有人从 {MODULE} 导入，模块里却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（`X as X` 或 `# noqa: F401`）：调用方的导入"
        "在模块加载时就断，比调用时才炸更早一步。"
    )


def test_the_name_a_test_replaces_on_this_module_is_still_here():
    """`held_blocks` 是这门面上唯一一个按模块对象被替换的名字。

    替换之后调用方要看到替换体，它就必须还挂在这个模块的命名空间里——搬到某个子
    模块上，`monkeypatch.setattr(turn_module, "held_blocks", …)` 就替换不到任何
    还会被读到的东西。
    """
    preparation = importlib.import_module(MODULE)

    assert hasattr(preparation, "held_blocks")
