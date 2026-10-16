"""拆开之后，`app.domain.agent.gateway_models` 有三个方面不许松掉。

**导出面。** 树里从门面导过的名字，门面上还必须在。拆分最容易弄丢的就是这个：搬走
身子的人只看自己那一片，没人看全仓库谁从这个模块取过什么名字，而缺一个名的代价是
导入期就炸——调用方连启动都到不了。所以这里不列名单，而是把树里真实的取用点读出来，
谁删掉一个还有人取的名字，谁就红。

**走对象的名字。** 这套拆法把每个原先的 `self.X(...)` 换成了 `service.X(...)`，也就
是子模块经服务对象调自己的方法。那么凡是子模块这么叫过的名字，类上就得有——少一个
转发器，是运行时 AttributeError，导出面那条管不着（它不是从外面导进来的）。

**包的大小。** 按职责拆开是为了每一片都读得动。这一片再长回去，拆了也白拆。
"""

import ast
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import app
from app.domain.agent.gateway_models import GatewayModelsService

MODULE = "app.domain.agent.gateway_models"
PARENT = "app.domain.agent"
PACKAGE = (
    Path(__file__).resolve().parents[2] / "app" / "domain" / "agent" / "gateway_models"
)
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent
FACADE = "__init__.py"

#: 一套拆法只该拆到「每一片都读得动」为止。这个数不是仓库闸门（`backend/app` 是
#: 1500 行），是这次拆分自己给自己定的：哪一片越过它，多半是又攒回了不属于一个职责
#: 的东西，该再分而不是接着堆。
SPLIT_CEILING = 650

# 这些目录不是仓库自己读的 Python 源码，进去只会白读几千个文件。
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


def _names_reached_on_the_package() -> dict[str, list[str]]:
    """树里从门面取的名字，连同取的地方。

    两种取法都算：`from …gateway_models import X` 直接取 `X`；`from …agent import
    gateway_models` 先把模块绑成一个本地名，之后 `gateway_models.X` 也是从门面取。
    仓库里两种都有（路由和两个单测用前者，集成测试用后者）。
    """
    found: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里两千个文件，只有提到这个模块的那几个才值得解析。
        if "gateway_models" not in source:
            continue
        tree = ast.parse(source, str(path))
        where = path.relative_to(REPO)
        bound: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == MODULE:
                for alias in node.names:
                    if alias.name != "*":
                        found.setdefault(alias.name, []).append(
                            f"{where}:{node.lineno}"
                        )
            elif node.module == PARENT:
                bound.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "gateway_models"
                )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in bound
            ):
                found.setdefault(node.attr, []).append(f"{where}:{node.lineno}")
    return found


def _names_reached_on_the_service() -> dict[str, list[str]]:
    """子模块里 `service.X` 取过的名字，连同取的地方。"""
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == FACADE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "service"
            ):
                found.setdefault(node.attr, []).append(f"{path.name}:{node.lineno}")
    return found


def test_every_name_the_tree_reaches_on_the_package_is_still_there():
    package = importlib.import_module(MODULE)
    reached = _names_reached_on_the_package()

    # 一条都扫不出来就是扫错了（路径拼歪、解析改坏），别让这条测试空着过。
    assert {"GatewayModelsService", "reset_cache"} <= set(reached)
    sites = [site for place in reached.values() for site in place]
    # 扫描范围本身要看着。取用点九成在 `backend/app` 里，只扫 `app/` 也照样绿，所以
    # 直接数范围：`backend/tests/` 里那几处在册，才算真的从仓库根扫起。
    assert any(site.startswith("backend/tests/") for site in sites), sites

    missing = {
        name: where for name, where in reached.items() if not hasattr(package, name)
    }

    assert not missing, (
        f"这些名字还有人从 {MODULE} 取，门面上却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（重导出加 `# noqa: F401`）：取用方的导入在"
        "模块加载时就断，比调用时才炸更早一步。"
    )


def test_the_scan_starts_at_the_repository_root():
    """只扫 `backend/` 就能扫出上面那些取用点，所以范围得单独钉一条。"""
    scanned = {str(path.relative_to(REPO)) for path in _scanned_files()}
    assert any(name.startswith("backend/") for name in scanned), scanned
    assert any(name.startswith("scripts/") for name in scanned), scanned


def test_every_name_a_piece_reaches_on_the_service_is_on_the_service():
    """子模块经对象叫的方法，类上得有。

    少一个转发器，是运行时 AttributeError，而且只在走到那条路时才炸——这条把它提前
    到测试里。
    """
    reached = _names_reached_on_the_service()
    assert {"_item", "_models_raw", "_readiness", "_window"} <= set(reached)

    service = GatewayModelsService(SimpleNamespace(), None)
    missing = {
        name: where for name, where in reached.items() if not hasattr(service, name)
    }

    assert not missing, (
        f"子模块这样调过服务：{missing}，但类上没有这些名字。"
        "门面要给每个子模块函数留一行转发，`service.X()` 才落得到对象上。"
    )


def test_no_piece_of_the_split_grows_past_the_ceiling():
    sized = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(PACKAGE.glob("*.py"))
    }
    assert {"answers.py", "models.py", "sources.py"} <= set(sized)

    oversized = {name: lines for name, lines in sized.items() if lines > SPLIT_CEILING}

    assert not oversized, (
        f"这几片超过了 {SPLIT_CEILING} 行：{oversized}。"
        "拆开是为了每一片都读得动；又要堆回一个职责以外的东西，就再分一片。"
    )
