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

这道测试钉五件事：

1. 旧路径仍答得出原来那一组名字，而且是**同一批对象**，不是各留一份；
2. 公开面就是拆之前那一组（`PUBLIC`，照旧文件抄下来的一份名单），一个不多、一个
   不少，而且上面那张落点表正好覆盖它；
3. 每个名字现在住在哪个子模块里 —— 这是「谁的职责归谁」的可执行版。它同时在给下一
   次搬迁当路标：**动布局就要动这张表**，这是故意的，不是意外；
4. 没有哪个类或函数在包里写了两遍 —— 扫四个子模块的每一个顶层定义，不是只看表里
   那几行；门面只做再导出，自己不许定义任何东西；
5. 全仓还有谁从这个路径取名字也一起看着：删掉一个还在被导入的名字就红。
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

#: 包自己那一摞文件的路径前缀（相对仓库根）。只有这些位置在册时，说明真实调用方
#: 一个都没扫到。
_PACKAGE_DIR = "backend/app/domain/memory/migration_plan/"

#: 每个公开名字现在住在哪个子模块。搬错地方就红。
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

#: 拆之前那个模块的公开名字，照它抄下来的，一个不差。**不能写成从 `HOME` 里算出来
#: 的**：那样表里漏一个名字，名单跟着一起漏，两条测试都不出声。
PUBLIC = [
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
    "build_plan",
    "decode_answer",
    "render_report",
]

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


def _module_of(path: Path) -> str | None:
    """这份文件自己的点分模块名；不在 `backend/` 里就算不出来。"""
    try:
        relative = path.relative_to(BACKEND)
    except ValueError:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _lands_on_the_package(node: ast.ImportFrom, owner: str | None) -> bool:
    """`from … import …` 取的是这个包，不论写的是绝对还是相对拼法。"""
    if node.level == 0:
        return node.module == MODULE
    if owner is None:
        return False
    base = owner.split(".")[:-1]  # 文件自己所在的包
    for _ in range(node.level - 1):  # 相对导入先往上退 level-1 层
        if not base:
            return False
        base.pop()
    target = ".".join([*base, node.module]) if node.module else ".".join(base)
    return target == MODULE


def _dotted_name(node: ast.AST) -> str | None:
    """`mp.render_report` / `app.domain.memory.migration_plan.render_report`。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _names_imported_from_the_package() -> dict[str, list[str]]:
    """全仓从这条路径取名字的地方：名字 → 位置。

    两种取法都要认：`from … import 名字`（绝对和相对都算），以及
    `import … [as 别名]` 之后拿 `别名.名字` 取值。只认前一种的绝对拼法，等于把换了
    写法的那类调用方当不存在。
    """
    found: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里几千个文件，只有提到这个模块的那几个值得解析。
        # 筛的是短名而不是全路径：相对导入的源码里不出现全路径，用全路径筛会把
        # `from .migration_plan import …` 这类调用方在解析之前就丢掉。
        if "migration_plan" not in source:
            continue
        tree = ast.parse(source, str(path))
        owner = _module_of(path)
        where = f"{path.relative_to(REPO)}"
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == MODULE:
                        # `import … as mp` 拿别名取名字，`import a.b.c` 拿全路径取；
                        # 两种情况都记成前缀，下面按属性链把名字摘出来。
                        aliases.add(alias.asname or MODULE)
            elif isinstance(node, ast.ImportFrom):
                if not _lands_on_the_package(node, owner):
                    continue
                for alias in node.names:
                    found.setdefault(alias.name, []).append(f"{where}:{node.lineno}")
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            dotted = _dotted_name(node)
            if dotted is None:
                continue
            for prefix in aliases:
                if dotted.startswith(f"{prefix}."):
                    name = dotted[len(prefix) + 1 :].split(".")[0]
                    found.setdefault(name, []).append(f"{where}:{node.lineno}")
    return found


def _defined_at_the_top_level(path: Path) -> set[str]:
    """一个文件顶层定义了哪些类、函数和常量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_the_old_path_answers_the_same_objects():
    package = importlib.import_module(MODULE)

    for module, names in HOME.items():
        for name in names:
            assert getattr(package, name) is getattr(module, name), (
                f"`{MODULE}` 上的 {name} 和它应该住的地方（{module.__name__}）不是"
                "同一个对象：门面各留了一份，改一处另一处不会跟着变。"
            )


def test_the_public_face_is_exactly_what_it_was():
    package = importlib.import_module(MODULE)

    # 公开面就是拆之前那一组：名单是照旧文件抄的，不是从落点表里算出来的。
    assert sorted(package.__all__) == sorted(PUBLIC)

    # 落点表正好覆盖公开面，一个不漏、一个不多；每个名字只有一个家。
    mapped = [name for names in HOME.values() for name in names]
    assert sorted(mapped) == sorted(PUBLIC), (
        f"落点表和公开面对不上：表里多 {sorted(set(mapped) - set(PUBLIC))}，"
        f"少 {sorted(set(PUBLIC) - set(mapped))}"
    )
    for name in PUBLIC:
        homes = [module.__name__ for module, names in HOME.items() if name in names]
        assert len(homes) == 1, f"{name} 被派给了多个子模块：{homes}"


def test_no_class_or_function_is_written_twice():
    """包里每个顶层定义只许出现在一个文件里。

    扫的是四个子模块里**所有**的顶层定义，不是上面那张表里的那几行：表只列公开名
    字，一个私有辅助函数、一个常量在别处又写一遍，只看表是看不见的。
    """
    where: dict[str, list[str]] = {}
    for module in HOME:
        for name in _defined_at_the_top_level(Path(module.__file__)):
            where.setdefault(name, []).append(module.__name__)

    twice = {name: homes for name, homes in where.items() if len(homes) > 1}
    assert not twice, f"这些东西在包里写了不止一份：{twice}"

    # 门面只做再导出，自己不许定义任何东西 —— 在这儿写一份就是第二份。
    facade = _defined_at_the_top_level(Path(importlib.import_module(MODULE).__file__))
    assert not (facade & set(where)), sorted(facade & set(where))


def test_every_name_the_tree_imports_from_the_package_is_still_there():
    package = importlib.import_module(MODULE)
    imported = _names_imported_from_the_package()

    # 扫描范围本身要看着：只有包自己那一条（`plan.py` 里那句延迟导入）时也要红。
    # 真实调用方全换成别的拼法、这条一条都没扫到时，它不能照样绿。
    sites = [site for places in imported.values() for site in places]
    assert any(not site.startswith(_PACKAGE_DIR) for site in sites), sites
    assert {"build_plan", "Destination", "render_report"} <= set(imported)

    missing = {
        name: where for name, where in imported.items() if not hasattr(package, name)
    }

    assert not missing, (
        f"这些名字还有人从 `{MODULE}` 导入，包里却已经没有了：{missing}。"
        "搬走的身子要把名字留在包的入口：调用方的导入在模块加载时就断，比调用时"
        "才炸更早一步。"
    )
