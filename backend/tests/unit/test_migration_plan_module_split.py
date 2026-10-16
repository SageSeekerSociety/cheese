"""门面守卫：`app.domain.memory.migration_plan` 拆成包之后，公开面一个都不能少。

拆之前是 `migration_plan.py` 一个 727 行的模块，一个文件里住着四件事，现在按职责分
在包里的四个子模块：

- `plan.py` —— 计划的形状与常量：`Destination`、`Decision`、`PlannedFile`、
  `PlannedIndex`、`Suggestion`、`MigrationPlan`、`MigrationError`；
- `decode.py` —— 模型的回答 → 校验过的决定 → 一份计划：`decode_answer`、
  `build_plan`；
- `layout.py` —— 决定 → 要写的文件与索引；
- `report.py` —— 报告：`render_report`。

拆成**包**而不是四个同级模块，是为了不欠新债：同级模块互相 import 会在 C3
（`app.domain` 下的同级模块不许成环）上多出环来，而子模块引自己的包不算环。

这道测试钉四件事：

1. 旧路径仍答得出原来那一组名字，而且是**同一批对象**，不是各留一份；
2. 每个名字现在住在哪个子模块里，一个不差 —— 这是「谁的职责归谁」的可执行版；
3. 没有哪个类或函数在两个子模块里各写了一遍；
4. 全仓还有谁从这个路径取名字也一起看着：删掉一个还在被导入的名字就红。
"""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path

import app
from app.domain.memory.migration_plan import decode, layout, plan, report

MODULE = "app.domain.memory.migration_plan"
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent

#: 每个公开名字现在住在哪个子模块。搬错地方、或者两个模块各留一份，就红。
HOME = {
    plan: [
        "Decision",
        "Destination",
        "MigrationError",
        "MigrationPlan",
        "PlannedFile",
        "PlannedIndex",
        "REASON_MAX",
        "REPORT_TITLE",
        "Suggestion",
        "TEAM_INDEX_GOAL_LINES",
    ],
    decode: ["build_plan", "decode_answer"],
    layout: [],
    report: ["render_report"],
}

#: 拆之前那个模块的公开名字，一个不差。`migration.py` 按这些名字再导出给调用方。
PUBLIC = sorted(name for names in HOME.values() for name in names)

#: 不是仓库自己读的 Python 源码，进去只会白读几千个文件。
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


def _names_imported_from_the_package() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里几千个文件，只有导过这个模块的那几个值得解析。
        if MODULE not in source:
            continue
        for node in ast.walk(ast.parse(source, str(path))):
            if isinstance(node, ast.ImportFrom) and node.module == MODULE:
                for alias in node.names:
                    found.setdefault(alias.name, []).append(
                        f"{path.relative_to(REPO)}:{node.lineno}"
                    )
    return found


def test_the_old_path_answers_the_same_objects():
    package = importlib.import_module(MODULE)

    for module, names in HOME.items():
        for name in names:
            assert getattr(package, name) is getattr(module, name), (
                f"`{MODULE}` 上的 {name} 和它应该住的地方（{module.__name__}）不是"
                "同一个对象：门面各留了一份，改一处另一处不会跟着变。"
            )


def test_every_public_name_has_exactly_one_home():
    package = importlib.import_module(MODULE)

    for name in PUBLIC:
        homes = [module.__name__ for module, names in HOME.items() if name in names]
        assert len(homes) == 1, f"{name} 被派给了多个子模块：{homes}"

    # 公开面就是这一组：拆之前那个模块里的公开名字，一个不多、一个不少。
    assert sorted(package.__all__) == PUBLIC


def test_no_class_or_function_is_written_twice():
    # 常量没有 `__module__`，认不出来，所以那几条只靠上面两道看住。
    for module, names in HOME.items():
        for name in names:
            obj = getattr(module, name)
            if not hasattr(obj, "__module__"):
                continue
            assert obj.__module__ == module.__name__, (
                f"{name} 住在 {module.__name__} 里，却是 {obj.__module__} 定义的："
                "同一个东西写了两份。"
            )


def test_every_name_the_tree_imports_from_the_package_is_still_there():
    package = importlib.import_module(MODULE)
    imported = _names_imported_from_the_package()

    # 扫描范围本身要看着：一条都扫不出来就是扫错了（路径拼歪、条件改坏），
    # 别让这条测试空着过。
    sites = [site for places in imported.values() for site in places]
    assert any(site.startswith("backend/app/") for site in sites), sites
    assert {"build_plan", "Destination", "render_report"} <= set(imported)

    missing = {
        name: where for name, where in imported.items() if not hasattr(package, name)
    }

    assert not missing, (
        f"这些名字还有人从 `{MODULE}` 导入，包里却已经没有了：{missing}。"
        "搬走的身子要把名字留在包的入口：调用方的导入在模块加载时就断，比调用时"
        "才炸更早一步。"
    )
