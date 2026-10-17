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


def _dotted(node: ast.expr) -> str | None:
    """`a.b.c` 这种属性链的点分写法；不是一条链就是 None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


def _package_of(path: Path) -> str | None:
    """这份文件的 ``__package__``——相对导入按它折算；不在后端包里就返回 None。

    ``app/x/y.py`` 和 ``app/x/__init__.py`` 的锚点都是 ``app.x``：``__init__.py``
    的 ``__name__`` 和 ``__package__`` 本来就是同一个。
    """
    try:
        rel = path.resolve().relative_to(BACKEND)
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def _absolute_module(node: ast.ImportFrom, package: str | None) -> str | None:
    """一句 ``from … import`` 落在哪个模块上；相对的按 ``package`` 折算。

    相对写法不折算就认不出来：``from .. import gateway_models as pkg`` 和绝对写法
    ``from app.domain.agent import gateway_models`` 是同一句话，后面 ``pkg.X`` 取
    的也是门面上的名字。
    """
    if not node.level:
        return node.module
    if package is None:
        return None
    parts = package.split(".")
    if node.level - 1 >= len(parts):
        return None
    base = ".".join(parts[: len(parts) - (node.level - 1)])
    return f"{base}.{node.module}" if node.module else base


def _names_reached_in(
    source: str, where: str, package: str | None = None
) -> dict[str, list[str]]:
    """一处源码里从门面取的名字，连同取的地方。

    三种取法都算：`from …gateway_models import X` 直接取 `X`；`from …agent import
    gateway_models`（或 `import …gateway_models as gm`）先把模块绑成一个本地名，之后
    那个名字上的属性就是从门面取；`import app.domain.agent.gateway_models` 不带别名
    时绑的是顶层包，后面整条属性链写全，也算。相对的写法按 ``package`` 折算成同一句。
    """
    found: dict[str, list[str]] = {}
    tree = ast.parse(source, where)
    bound: set[str] = set()
    # `import app.domain.agent.gateway_models`（不带 `as`）绑的是 `app`，只能靠整条
    # 属性链认回来。
    followed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name != MODULE:
                    continue
                (bound if alias.asname else followed).add(alias.asname or alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        target = _absolute_module(node, package)
        if target == MODULE:
            for alias in node.names:
                if alias.name != "*":
                    found.setdefault(alias.name, []).append(f"{where}:{node.lineno}")
        elif target == PARENT:
            bound.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "gateway_models"
            )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        head = node.value
        if (isinstance(head, ast.Name) and head.id in bound) or (
            _dotted(head) in followed
        ):
            found.setdefault(node.attr, []).append(f"{where}:{node.lineno}")
    return found


def _names_reached_on_the_package() -> dict[str, list[str]]:
    """树里从门面取的名字，连同取的地方。"""
    found: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里两千个文件，只有提到这个模块的那几个才值得解析。
        # 包自己是个例外：里面写 `from . import GatewayModelsService` 一个「gateway_
        # models」都不带，一样是取门面上的名字。
        if "gateway_models" not in source and PACKAGE not in path.parents:
            continue
        where = str(path.relative_to(REPO))
        for name, sites in _names_reached_in(source, where, _package_of(path)).items():
            found.setdefault(name, []).extend(sites)
    return found


def _service_receivers(source: str, where: str) -> set[str]:
    """文件里绑到 `service` 上的本地名——`svc = service` 之后 `svc.X` 也是走对象。

    只跟一条：`X = service` / `X: T = service`。别名再套别名也认（走到不动为止）。
    """
    tree = ast.parse(source, where)
    names = {"service"}
    while True:
        grown = set(names)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value, targets = node.value, node.targets
            elif isinstance(node, ast.AnnAssign):
                value, targets = node.value, [node.target]
            else:
                continue
            if isinstance(value, ast.Name) and value.id in names:
                grown.update(t.id for t in targets if isinstance(t, ast.Name))
        if grown == names:
            return names
        names = grown


def _pieces() -> list[Path]:
    """包内全部子模块。递归，免得以后多一层子包就漏在扫描外。"""
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if path.relative_to(PACKAGE).as_posix() != FACADE
        and "__pycache__" not in path.parts
    )


def _names_reached_on_the_service_in(source: str, where: str) -> dict[str, list[str]]:
    """一处源码里经 `service`（含别名接收者）取过的名字，连同行号。

    单独一份，好对着合成源码试判据本身读不读得懂某种写法。
    """
    receivers = _service_receivers(source, where)
    found: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source, where)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in receivers
        ):
            found.setdefault(node.attr, []).append(f"{where}:{node.lineno}")
    return found


def _names_reached_on_the_service() -> dict[str, list[str]]:
    """子模块里 `service.X`（含别名接收者）取过的名字，连同取的地方。"""
    found: dict[str, list[str]] = {}
    for path in _pieces():
        where = path.relative_to(PACKAGE).as_posix()
        for name, sites in _names_reached_on_the_service_in(
            path.read_text(encoding="utf-8"), where
        ).items():
            found.setdefault(name, []).extend(sites)
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


def test_the_relative_anchor_is_the_package_not_the_module():
    """相对导入按 ``__package__`` 折算，包的 ``__init__.py`` 上它不比 ``__name__`` 长。

    拿 ``__name__`` 当锚点：``sub/__init__.py`` 会被多折一级，``from .. import
    gateway_models`` 落到 ``…agent`` 之外、后面 ``gateway_models.X`` 就少抓一个名字。
    """
    assert _package_of(PACKAGE / "models.py") == MODULE
    assert _package_of(PACKAGE / FACADE) == MODULE
    assert _package_of(PACKAGE / "sub" / "leaf.py") == f"{MODULE}.sub"
    assert _package_of(PACKAGE / "sub" / FACADE) == f"{MODULE}.sub"


def test_the_scans_read_the_shapes_they_claim_to_read():
    """两条扫描各自的判据也过一遍。

    上面两条测试绿，只说明树里现在干净；判据读不认得某种写法的话，有人用那种写法取
    一个名字，删掉它还是一片绿。所以对着合成源码逐条问一遍。
    """
    for source in (
        "from app.domain.agent.gateway_models import reset_cache",
        "from app.domain.agent import gateway_models\ngateway_models.reset_cache()",
        "from app.domain.agent import gateway_models as gm\ngm.reset_cache()",
        # 边界：带上 `as` 的一条也认。
        "import app.domain.agent.gateway_models as gm\ngm.reset_cache()",
        # 不带 `as` 时绑的是顶层包，整条链写全才算。
        "import app.domain.agent.gateway_models\n"
        "app.domain.agent.gateway_models.reset_cache()",
    ):
        assert "reset_cache" in _names_reached_in(source, "<sample>"), source

    # 相对写法折算出来是同一句话。子包的 `__init__.py` 锚点和别的模块一样是
    # `…gateway_models.sub`，不能按 `__name__` 多折一级。
    sub = f"{MODULE}.sub"
    for source, package in (
        ("from .. import gateway_models\ngateway_models.reset_cache()", MODULE),
        ("from . import reset_cache", MODULE),
        ("from . import reset_cache as rc\nrc()", MODULE),
        # 子包回门面：`...gateway_models` 折算出来正是门面，直接取名字。
        ("from ...gateway_models import reset_cache", sub),
        # 三级相对向上落到 `app.domain.agent`（门面的上一级），从那里取
        # `gateway_models` 再取属性，和绝对写法同义。
        ("from ... import gateway_models as gm\ngm.reset_cache()", sub),
    ):
        assert "reset_cache" in _names_reached_in(source, "<sample>", package), source

    for source, package in (
        # 相对写法点到兄弟上的，和绝对写法一样不算。
        ("from . import cache\ncache.reset_cache()", MODULE),
        ("from .leaf import thing\nthing.reset_cache()", sub),
        ("from .. import cache\ncache.reset_cache()", sub),
        # 折过头的那种：三级相对再加 `agent` 落到 `app.domain.agent.agent`，不是门面，
        # 认成门面就是多认（别人从那儿取名字反而不会被发现）。
        ("from ...agent import gateway_models as gm\ngm.reset_cache()", sub),
    ):
        assert "reset_cache" not in _names_reached_in(source, "<sample>", package), (
            source
        )

    for source in (
        # 兄弟模块和它的属性不是门面上的名字。
        "from app.domain.agent import gateway_admin\nadmin_models.reset_cache()",
        "from app.domain.agent.gateway_models import cache\ncache.reset_cache()",
        "import app.domain.agent.gateway_models.cache\n"
        "app.domain.agent.gateway_models.cache.reset_cache()",
        # 只提名字，没有取用。
        "# 见 app.domain.agent.gateway_models.reset_cache",
    ):
        assert "reset_cache" not in _names_reached_in(source, "<sample>"), source

    for source in (
        "def f(service):\n    service._window()",
        # 名字换了，接收者还是那个对象。
        "def f(service):\n    svc = service\n    svc._window()",
        "def f(service):\n    one = service\n    two = one\n    two._window()",
        "def f(service):\n    svc: GatewayModelsService = service\n    svc._window()",
    ):
        reached = _names_reached_on_the_service_in(source, "<sample>")
        assert "_window" in reached, source

    # 接收者不是 `service` 的，不算：别人身上的同名属性和这个类没关系。
    for source in (
        "def f(other):\n    other._window()",
        "def f(other):\n    svc = other\n    svc._window()",
    ):
        reached = _names_reached_on_the_service_in(source, "<sample>")
        assert "_window" not in reached, source


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
    # 门面也算一片。`_pieces()` 把它排除在外是因为别处的扫描不扫它，不是因为它不该
    # 受这个上限管——它正是包里最长的那一份。
    sized = {
        path.relative_to(PACKAGE).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    assert {"__init__.py", "answers.py", "models.py", "sources.py"} <= set(sized)

    oversized = {name: lines for name, lines in sized.items() if lines > SPLIT_CEILING}

    assert not oversized, (
        f"这几片超过了 {SPLIT_CEILING} 行：{oversized}。"
        "拆开是为了每一片都读得动；又要堆回一个职责以外的东西，就再分一片。"
    )
