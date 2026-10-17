"""包里的子模块运行时只许往下引／横引，不许回头引门面。

包的方向是门面 → 子模块：``gateway_models/__init__.py`` 从 ``answers``／``models``／
``projects``／``sources``／``_shared``／``cache`` 取身子，子模块之间只横着互引兄弟
（``answers`` 用 ``cache`` 的缓存）。运行时反着引一次就是环，而环在这层的后果看导入
谁先：``import app.domain.agent.gateway_models.models`` 先到时，门面正引到一半，那里
的 ``GatewayModelsService`` 还不存在——报出来的是「partially initialized module」。

**类型标注不算。** 子模块给 ``service`` 标 ``pkg.GatewayModelsService`` 时在
``if TYPE_CHECKING:`` 里引门面，运行时那一句不执行，导入图上没有这条边——这是这套
拆法本来就用的写法，下面的判据跟着它走。别名（``as TC``）也认。只认这一种写法：
``TYPE_CHECKING`` 跟别的条件拼在一起（``if TYPE_CHECKING and _X``、``while
TYPE_CHECKING``）按运行时报，宁可让人把那句挪进 ``if TYPE_CHECKING:``，也不放过一
条真的边。

**怎么写不决定判不判。** 判据先把一句解析成它落在哪个模块上，再判那个模块是不是门
面：``from . import GatewayModelsService`` 和 ``from app.domain.agent.gateway_models
import GatewayModelsService`` 落在门面上；而 ``from .cache import _cached``、``import
app.domain.agent.gateway_models.cache`` 落在兄弟上，``from . import cache`` 取的名字
本身就是兄弟，都不算。

**按名字导的也算。** ``__import__("app.domain.agent.gateway_models", …)`` 和
``importlib.import_module("app.domain.agent.gateway_models")`` 运行时一样建得出那条边，
环一样炸，而 `ast` 里它们只是普通调用。字面量正好是门面的模块名、调用的又是这两个名
字中的一个，就报；点到兄弟模块（``…gateway_models.cache``）的不算，那和静态写法一
样是横引。

**这段 C3 看不见。** ``acyclic_siblings`` 数的是 ``app.domain`` 的孩子和它们的孩子
之间的环；包自己是一个节点，它内部这些子模块不在它的账上。所以在这里点名扫包内，而
不是指望契约。
"""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

from tests.unit._import_anchor import absolute_module, package_at, package_of

MODULE = "app.domain.agent.gateway_models"
PARENT = "app.domain.agent"
PACKAGE = (
    Path(__file__).resolve().parents[2] / "app" / "domain" / "agent" / "gateway_models"
)

#: 门面自己当然要从子模块里取名字，它不在这条规矩的对象里。
FACADE = "__init__.py"

#: 同级兄弟。`from . import X` 取到的是它们，那就是横引，不算回头。
SIBLINGS = {path.stem for path in PACKAGE.glob("*.py") if path.name != FACADE} | {
    path.name
    for path in PACKAGE.iterdir()
    if path.is_dir() and path.name != "__pycache__"
}


def _piece_files() -> list[Path]:
    """包内全部子模块，递归。以后多一层子包也不漏在扫描外。"""
    return sorted(
        path
        for path in PACKAGE.rglob("*.py")
        if path.relative_to(PACKAGE).as_posix() != FACADE
        and "__pycache__" not in path.parts
    )


def _type_checking_names(tree: ast.Module) -> set[str]:
    """这份文件里能当 ``TYPE_CHECKING`` 用的名字，别名一起收。"""
    names = {"TYPE_CHECKING"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == (
            "typing"
        ):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "TYPE_CHECKING"
            )
    return names


def _is_type_checking(test: ast.expr, names: set[str]) -> bool:
    """``if`` 的判断是不是 ``TYPE_CHECKING``（``typing.TYPE_CHECKING`` 也算）。"""
    called = (
        getattr(test, "id", None)
        if isinstance(test, ast.Name)
        else getattr(test, "attr", None)
    )
    return called in names


def _imported_module(node: ast.AST, package: str | None) -> str | None:
    """这一句导的是哪个模块（多点一级 `a.b.c` 就落到 `a.b.c` 上）。"""
    if isinstance(node, ast.Import):
        # `import a.b.c` 导的是 `a.b.c` 这个模块本身；一句点几个名字时，任一个点中
        # 门面就算——所以门面优先，别被同一句里靠前的 `app.domain.agent` 抢先。
        names = {alias.name for alias in node.names}
        if MODULE in names:
            return MODULE
        return PARENT if PARENT in names else None
    if not isinstance(node, ast.ImportFrom):
        return None
    return absolute_module(node, package)


def _is_the_facade(node: ast.AST, package: str | None) -> bool:
    """这一句导的是门面吗。先解析它落在哪个模块上，再看那个模块是不是门面。"""
    target = _imported_module(node, package)
    if target is None:
        return False
    if target == MODULE:
        # 从门面里取名字。取到的是兄弟模块（`from . import cache`）才不算。
        return not isinstance(node, ast.ImportFrom) or any(
            alias.name not in SIBLINGS for alias in node.names
        )
    if target == PARENT:
        # `from .. import gateway_models`，或绝对写法的同一条。
        return any(alias.name == "gateway_models" for alias in node.names)
    # 落到 `...gateway_models.cache` 这类兄弟模块上的，是横引。
    return False


def _dynamic_imports(source: str, where: str) -> list[ast.Call]:
    """运行时按名字导门面的调用——`from` 看得见，这两种看不见。

    `__import__("app.domain.agent.gateway_models")` 和
    `importlib.import_module("app.domain.agent.gateway_models")` 都会在运行时建出那条
    边来，环该炸还炸；`ast` 里它们只是普通调用，得单独认。
    """
    tree = ast.parse(source, where)
    # `import importlib`／`import importlib as il`／`from importlib import ...`
    # 都收成「哪个名字上的 import_module 算 importlib 的」。
    heads: set[str] = set()
    if_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "importlib":
                    heads.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == (
            "importlib"
        ):
            if_modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        # 只认门面自己。点到 `…gateway_models.cache` 是横引兄弟，和静态写法的
        # `import …gateway_models.cache` 同一件事——`__init__` 早就在 `sys.modules`
        # 里，不会重跑一遍。
        if first.value != MODULE:
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        # `importlib.import_module` 和 `importlib.util.…` 都落在 importlib 上。
        receiver = getattr(func, "value", None)
        while isinstance(receiver, ast.Attribute):
            receiver = receiver.value
        on_importlib = isinstance(receiver, ast.Name) and receiver.id in heads
        if (
            called == "__import__"
            or called in if_modules
            or (called == "import_module" and on_importlib)
        ):
            found.append(node)
    return found


def _runtime_imports(source: str, where: str) -> list[ast.Import | ast.ImportFrom]:
    """这段源码里运行时真会执行的导入——``if TYPE_CHECKING:`` 底下的那些不算。

    只走 ``if`` 的 ``body``，不走 ``orelse``：``else:`` 那一支是运行时的。
    """
    tree = ast.parse(source, where)
    names = _type_checking_names(tree)
    type_only: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test, names):
            type_only.update(
                inner for statement in node.body for inner in ast.walk(statement)
            )
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in type_only
    ]


def _reaching_back(source: str, where: str, package: str | None = MODULE) -> list[str]:
    """这段源码里运行时会建到门面上的那些写法。

    ``package`` 是被扫那份源码的 ``__package__``，合成源码默认按包根算。
    """
    return [
        ast.unparse(node)
        for node in _runtime_imports(source, where)
        if _is_the_facade(node, package)
    ] + [ast.unparse(node) for node in _dynamic_imports(source, where)]


def test_no_gateway_models_piece_reaches_the_facade_at_runtime():
    pieces = _piece_files()
    # 扫不出文件就是路径写歪了，别让这条测试空着过。
    assert {"answers.py", "models.py", "sources.py"} <= {path.name for path in pieces}

    reaching_back = [
        (path.relative_to(PACKAGE).as_posix(), node.lineno, ast.unparse(node))
        for path in pieces
        for node in _runtime_imports(path.read_text(encoding="utf-8"), str(path))
        if _is_the_facade(node, package_of(path))
    ] + [
        (path.relative_to(PACKAGE).as_posix(), node.lineno, ast.unparse(node))
        for path in pieces
        for node in _dynamic_imports(path.read_text(encoding="utf-8"), str(path))
    ]

    assert not reaching_back, (
        f"这些子模块运行时引了门面：{reaching_back}。"
        "门面引子模块、子模块再引门面就是环，导入谁先决定它炸不炸；"
        "要服务自己的类型就放进 `if TYPE_CHECKING:`，或者在门面里把名字转出去。"
    )


#: 相对导入折算的对照表：``(被扫的文件, 那句 import, 它的 __package__)``。
#: 期望的落点不写死在这里——由 ``importlib.util.resolve_name`` 现场算。手抄一份正好
#: 会把同一个错法抄成期望值，测试就跟着错的一起绿了。
ANCHOR_CASES = [
    # 包的 ``__init__.py`` 和同目录的普通模块锚点相同。
    (FACADE, "from . import cache", MODULE),
    ("models.py", "from . import cache", MODULE),
    ("models.py", "from .cache import _cached", MODULE),
    ("models.py", "from .. import gateway_models", MODULE),
    ("models.py", "from ...agent import gateway_models", MODULE),
    # 子包里的文件：锚点是子包自己，它的 ``__init__.py`` 也一样。
    ("sub/leaf.py", "from . import sibling", f"{MODULE}.sub"),
    ("sub/leaf.py", "from .. import cache", f"{MODULE}.sub"),
    (f"sub/{FACADE}", "from . import sibling", f"{MODULE}.sub"),
    (f"sub/{FACADE}", "from .. import cache", f"{MODULE}.sub"),
    (f"sub/{FACADE}", "from ... import gateway_models", f"{MODULE}.sub"),
]


def test_the_relative_anchor_is_the_package_not_the_module():
    """相对导入从 ``__package__`` 折算，不从 ``__name__`` 再折一级。

    同一目录里两类文件的锚点相同（``pkg/leaf.py`` 和 ``pkg/__init__.py`` 都是
    ``pkg``）。拿 ``__name__`` 推，``sub/__init__.py`` 会多折一层：``from ..``
    落到祖父包上，``from .`` 落到门面上——本来都是兄弟。
    """
    for relative, source, package in ANCHOR_CASES:
        path = PACKAGE / relative
        assert package_of(path) == package, relative
        node = next(
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        )
        assert absolute_module(node, package_of(path)) == resolve_name(
            "." * node.level + (node.module or ""), package
        ), source


def test_a_relative_import_past_the_top_level_is_not_a_module():
    """折出顶层之外的不是一个模块——导入机器在那里抛 ``ImportError``。"""
    assert package_at("app", 2) is None
    with pytest.raises(ImportError):
        resolve_name("..", "app")


def test_the_guard_reads_the_shapes_it_claims_to_read():
    """规矩本身也过一遍。

    上面那条测试绿，只说明树里现在干净；它读不认得某种写法的话，有人用那种写法把
    环引回来，一样是绿的。所以判据对着合成的源码逐条问一遍——写法对了它得报，兄弟
    和类型标注得放过。
    """
    for source in (
        "from app.domain.agent.gateway_models import GatewayModelsService",
        "import app.domain.agent.gateway_models",
        "import app.domain.agent.gateway_models as pkg",
        "from app.domain.agent import gateway_models",
        "from app.domain.agent import gateway_models as pkg",
        # 一个 `from … import` 可以点好几个名字，门面不一定排在第一个。
        "from app.domain.agent import gateway_admin, gateway_models",
        "from app.domain.agent.gateway_models import *",
        "from . import *",
        "from . import GatewayModelsService",
        "from .. import gateway_models",
        "def f():\n    from app.domain.agent.gateway_models import reset_cache\n",
        # 相对写法要多走一级才点到门面：`..` 落到 `agent`，再点它的 `gateway_models`。
        "from ..gateway_models import GatewayModelsService",
        "from ...agent.gateway_models import GatewayModelsService",
        # 普通 `if` 底下的是运行时的，只有 `TYPE_CHECKING` 那一支才算类型标注。
        "if _HAS_NAMES:\n    from app.domain.agent.gateway_models import reset_cache\n",
        "from typing import TYPE_CHECKING\n"
        "if not TYPE_CHECKING:\n"
        "    from app.domain.agent.gateway_models import reset_cache\n",
        # 按名字导的，`ast` 里只是普通调用，但运行时一样建出那条边。
        '__import__("app.domain.agent.gateway_models")',
        '__import__("app.domain.agent.gateway_models", fromlist=["cache"])',
        'import importlib\nimportlib.import_module("app.domain.agent.gateway_models")',
        'import importlib as il\nil.import_module("app.domain.agent.gateway_models")',
        "from importlib import import_module\n"
        'import_module("app.domain.agent.gateway_models")',
        "import importlib.util\n"
        'importlib.util.import_module("app.domain.agent.gateway_models")',
        # 一句 `import` 点好几个名字，门面不一定排在第一个。
        "import app.domain.agent, app.domain.agent.gateway_models",
    ):
        assert _reaching_back(source, "<sample>"), source

    # 子包里的相对写法：锚点是那份文件自己的 `__package__`。子包的 `__init__.py`
    # 上它和 `__name__` 是同一个，拿 `__name__` 当锚点会多折一级。
    sub = f"{MODULE}.sub"
    for source in (
        # 子包要门面得再往上折一级：`..` 落到的还是 `…gateway_models` 自己。
        "from ... import gateway_models as gm",
        "from ...gateway_models import GatewayModelsService",
    ):
        assert _reaching_back(source, "<sample>", package=sub), source

    for source in (
        # 子包引自己的兄弟——`…gateway_models.sub` 上的名字，不是门面的。
        "from . import leaf",
        "from .leaf import thing",
        # 落到 `…gateway_models` 上，但取的是兄弟模块，横引。
        "from .. import cache",
    ):
        assert not _reaching_back(source, "<sample>", package=sub), source

    for source in (
        "from app.domain.agent.gateway_models.cache import _cached",
        "from .cache import _cached",
        "from . import cache",
        "from app.domain.agent import gateway_admin",
        # 绝对写法的横引兄弟，和 `from . import cache` 是同一件事。
        "import app.domain.agent.gateway_models.cache as cache_mod",
        "from app.domain.agent.gateway_models import cache",
        "from app.domain.agent.gateway_models import cache, _shared",
        # 按名字导兄弟模块——横引，不是回头。
        "import importlib\n"
        'importlib.import_module("app.domain.agent.gateway_models.cache")',
        'importlib.import_module("app.somewhere.else")',
        # 名字对上了，但调的不是导模块的那两个名字。
        'convert("app.domain.agent.gateway_models")',
        'import importlib\nimportlib.reload("app.domain.agent.gateway_models")',
        # 参数不是字面量，判据看不见它导的是哪个模块。
        "import importlib\nimportlib.import_module(SOME_NAME)",
    ):
        assert not _reaching_back(source, "<sample>"), source

    # 类型标注那一支：`TYPE_CHECKING` 底下的不报，`else:` 里的报。别名一样算。
    for source in (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from app.domain.agent.gateway_models import GatewayModelsService\n",
        "from typing import TYPE_CHECKING as TC\n"
        "if TC:\n"
        "    from app.domain.agent.gateway_models import GatewayModelsService\n",
        "import typing\n"
        "if typing.TYPE_CHECKING:\n"
        "    from . import GatewayModelsService\n",
    ):
        assert not _reaching_back(source, "<sample>"), source

    assert _reaching_back(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    pass\n"
        "else:\n"
        "    from app.domain.agent.gateway_models import reset_cache\n",
        "<sample>",
    )
