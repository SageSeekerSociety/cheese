"""``app/api/auth`` 拆成包之后，三个方面不许松掉。

**导出面。** 树里从 `app.api.auth` 取过的名字，门面上还必须在。拆分最容易弄丢的就是
这个：搬走身子的人只看自己那一片，没人看全仓库谁从这个模块取过什么名字，而缺一个名
的代价是导入期就炸——调用方连启动都到不了。所以这里不列名单，而是把树里真实的取用点
读出来，谁删掉一个还有人取的名字，谁就红。

**替换面。** `resolver.py` 经 ``pkg.`` 在调用时读的名字，门面上也得在。这条和上一条
方向相反：上一条问「外面取的还在不在」，这条问「里面读的还在不在」。少一个，
`monkeypatch.setattr(auth_mod, "UserRepository", …)` 就从「换掉了」变成
「换了个寂寞」，而 AttributeError 要到真正授权的那一次请求才出现。

**不自己拿一份。** `resolver.py` 不许把这两个仓储导进自己的命名空间。导入一份副本，
被换掉的就不是它用的那一份了，替换点从此静默失效——正是这条拆法最容易悄悄踩坏的地
方（`app/domain/review/services` 里各子模块经 ``pkg.`` 回读，是同一个理由）。
"""

import ast
import importlib
import os
from pathlib import Path

import app

MODULE = "app.api.auth"
PARENT = "app.api"
PACKAGE = Path(__file__).resolve().parents[2] / "app" / "api" / "auth"
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent
FACADE = "__init__.py"
PIECE = "resolver.py"

#: 测试替换掉的那两个仓储。它们只能住在门面上：`tests/unit/
#: test_actor_numeric_handle.py`、`test_token_scope_violation.py`、
#: `test_fetch_service.py` 换的都是门面属性。
REPLACED = ("UserRepository", "TopicMemberService")

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
    """这份文件的 ``__package__``——相对导入按它折算；不在后端包里就返回 None。"""
    try:
        rel = path.resolve().relative_to(BACKEND)
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    return ".".join(parts[:-1]) if len(parts) > 1 else None


def _absolute_module(node: ast.ImportFrom, package: str | None) -> str | None:
    """一句 ``from … import`` 落在哪个模块上；相对的按 ``package`` 折算。"""
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

    三种取法都算：`from app.api.auth import X` 直接取 `X`；`from app.api import
    auth`（或 `import app.api.auth as auth`）先把模块绑成一个本地名，之后那个名字
    上的属性就是从门面取；`import app.api.auth` 不带别名时绑的是顶层包，后面整条
    属性链写全，也算。相对的写法按 ``package`` 折算成同一句。
    """
    found: dict[str, list[str]] = {}
    tree = ast.parse(source, where)
    bound: set[str] = set()
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
                if alias.name == "auth"
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
        # 包自己是个例外：里面写 `from .resolver import ActorResolver` 一个
        # 「api.auth」都不带，一样是取门面上的名字，所以包内文件一律解析。
        if "api.auth" not in source and PACKAGE not in path.parents:
            continue
        where = str(path.relative_to(REPO))
        for name, sites in _names_reached_in(source, where, _package_of(path)).items():
            found.setdefault(name, []).extend(sites)
    return found


def _names_read_through_pkg_in(source: str, where: str) -> dict[str, list[str]]:
    """一处源码里 `pkg.X` 取过的名字，连同行号。

    单独一份，好对着合成源码试判据本身读不读得懂某种写法。
    """
    found: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source, where)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "pkg"
        ):
            found.setdefault(node.attr, []).append(f"{where}:{node.lineno}")
    return found


def _names_read_through_pkg() -> dict[str, list[str]]:
    """`resolver.py` 里 `pkg.X` 取过的名字，连同取的地方。"""
    return _names_read_through_pkg_in(
        (PACKAGE / PIECE).read_text(encoding="utf-8"), PIECE
    )


def _piece_imports_its_own_facade_in(source: str, where: str) -> list[str]:
    """一处源码里把门面或它的名字导进自己命名空间的地方。

    单独一份，好对着合成源码试判据本身读不读得懂某种写法。
    """
    offenders: list[str] = []
    package = MODULE if where == PIECE else _package_of(REPO / where)
    for node in ast.walk(ast.parse(source, where)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in (MODULE, PARENT):
                    offenders.append(f"{where}:{node.lineno} import {alias.name}")
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        target = _absolute_module(node, package)
        if target == MODULE:
            offenders.append(f"{where}:{node.lineno} from {MODULE}")
        elif target == PARENT and any(a.name == "auth" for a in node.names):
            # 回读门面只认 `as pkg` 这一种写法：绑成别的本地名，`pkg.` 那条扫描就
            # 看不见它读过了什么。
            if any(a.name == "auth" and a.asname != "pkg" for a in node.names):
                offenders.append(f"{where}:{node.lineno} from {PARENT} import auth")
        elif any(a.name in REPLACED for a in node.names):
            names = ",".join(sorted(a.name for a in node.names))
            offenders.append(f"{where}:{node.lineno} imports {names}")
    return offenders


def _piece_imports_its_own_facade() -> list[str]:
    """`resolver.py` 里自己拿了一份副本的地方。"""
    return _piece_imports_its_own_facade_in(
        (PACKAGE / PIECE).read_text(encoding="utf-8"), PIECE
    )


def test_every_name_the_tree_reaches_on_the_facade_is_still_there():
    facade = importlib.import_module(MODULE)
    reached = _names_reached_on_the_package()

    # 一条都扫不出来就是扫错了（路径拼歪、解析改坏），别让这条测试空着过。
    assert {"ActorResolver", "ActorResolverDep", "require_seated_agent"} <= set(reached)
    sites = [site for place in reached.values() for site in place]
    # 扫描范围本身要看着。取用点九成在 `backend/app` 里，只扫 `app/` 也照样绿，所以
    # 直接数范围：`backend/tests/` 里那几处在册，才算真的从仓库根扫起。
    assert any(site.startswith("backend/tests/") for site in sites), sites

    missing = {
        name: where for name, where in reached.items() if not hasattr(facade, name)
    }

    assert not missing, (
        f"这些名字还有人从 {MODULE} 取，门面上却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（重导出写 `X as X`）：取用方的导入在模块加载"
        "时就断，比调用时才炸更早一步。"
    )


def test_every_name_the_resolver_reads_through_pkg_is_on_the_facade():
    facade = importlib.import_module(MODULE)
    read = _names_read_through_pkg()

    assert set(REPLACED) <= set(read), (
        f"`{PIECE}` 不再经 `pkg.` 读这两个仓储了：{sorted(read)}。"
        "它们被测试逐个替换，自己 import 一份接不到替换（见本文件开头）。"
    )

    missing = {name: where for name, where in read.items() if not hasattr(facade, name)}

    assert not missing, (
        f"`{PIECE}` 经 `pkg.` 读这些名字，门面上却没有：{missing}。"
        "门面是替换点被看见的唯一地方，少了哪一个，那条路要等到真跑起来才断。"
    )


def test_the_resolver_does_not_help_itself_to_a_second_copy():
    offenders = _piece_imports_its_own_facade()
    assert not offenders, (
        f"`{PIECE}` 自己导入了门面的名字或模块：{offenders}。"
        "经 `pkg.` 在调用时回读（`from app.api import auth as pkg`），"
        "别导入自己的副本——副本会绕开测试的替换点。"
    )


def test_the_module_the_package_replaced_is_gone():
    """同名模块和包并排时模块只是死代码，但读到它就是读到一份没人跑的第二实现。"""
    assert not (PACKAGE.parent / "auth.py").exists(), (
        "`app/api/auth.py` 还在：拆成 `app/api/auth/` 之后它不该再存在。"
    )
    assert (PACKAGE / FACADE).is_file() and (PACKAGE / PIECE).is_file()


def test_the_scan_starts_at_the_repository_root():
    """只扫 `backend/` 就能扫出上面那些取用点，所以范围得单独钉一条。"""
    scanned = {str(path.relative_to(REPO)) for path in _scanned_files()}
    assert any(name.startswith("backend/") for name in scanned), scanned
    assert any(name.startswith("scripts/") for name in scanned), scanned
    assert any(name.startswith(".claude/") for name in scanned), scanned


def test_the_relative_anchor_is_the_package_not_the_module():
    """相对导入按 ``__package__`` 折算；包的门面上它不比 ``__name__`` 长。"""
    assert _package_of(PACKAGE / PIECE) == MODULE
    assert _package_of(PACKAGE / FACADE) == MODULE


def test_the_scans_read_the_shapes_they_claim_to_read():
    """两条扫描各自的判据也过一遍。

    上面几条测试绿，只说明树里现在干净；判据读不认得某种写法的话，有人用那种写法取
    一个名字，删掉它还是一片绿。所以对着合成源码逐条问一遍。
    """
    for source, want in (
        ("from app.api.auth import ActorResolver", {"ActorResolver"}),
        ("from app.api import auth\nauth.ActorResolver", {"ActorResolver"}),
        ("from app.api import auth as am\nam.ActorResolver", {"ActorResolver"}),
        ("import app.api.auth as am\nam.DELEGATED_ROUTES", {"DELEGATED_ROUTES"}),
        ("import app.api.auth\napp.api.auth.ActorResolver", {"ActorResolver"}),
        # 兄弟模块不算门面：`from .resolver import X` 取的是包里的另一个文件。
        ("from .resolver import ActorResolver", set()),
        # 别的模块上的同名属性不算。
        ("from app.domain.review import services as svc\nsvc.ActorResolver", set()),
    ):
        got = set(_names_reached_in(source, "<sample>", MODULE))
        assert got == want, (source, got, want)

    # 相对写法折算出来是同一句话：包内文件写 `from . import X` 也是取门面。
    assert set(_names_reached_in("from . import resolver", "<sample>", MODULE)) == {
        "resolver"
    }
    assert set(
        _names_reached_in("from ..auth import ActorResolver", "<sample>", MODULE)
    ) == {"ActorResolver"}

    # `pkg.` 那条只认 `pkg` 这个接收者，别的名字读属性不算。
    for source, want in (
        ("pkg.UserRepository", {"UserRepository"}),
        ("pkg.TopicMemberService(pkg.session)", {"TopicMemberService", "session"}),
        ("other.UserRepository", set()),
        ("self.UserRepository", set()),
    ):
        assert set(_names_read_through_pkg_in(source, "<sample>")) == want, source

    # 自己拿一份副本的几种写法都要认得出来；`as pkg` 的回读不算，`as U` 的副本算。
    for source, caught in (
        ("from app.api.auth import UserRepository", True),
        ("from app.api import auth", True),
        ("from app.api import auth as other", True),
        ("import app.api.auth", True),
        ("from app.domain.user.repositories import UserRepository", True),
        ("from app.domain.user.repositories import UserRepository as U", True),
        ("from app.domain.topic_membership.services import TopicMemberService", True),
        ("from app.api import auth as pkg", False),
        ("from app.domain.user.repositories import Account", False),
        ("from app.api.auth.resolver import ActorResolver", False),
    ):
        got = bool(_piece_imports_its_own_facade_in(source, PIECE))
        assert got is caught, (source, got, caught)
