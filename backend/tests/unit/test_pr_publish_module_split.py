"""门面拆开之后，`from app.domain.review.pr_publish import X` 不许断。

拆分最容易弄丢的就是这个：搬走身子的人只看自己那一片，没人看全仓库谁从这个模块
导过什么名字，而缺一个名的代价是导入时就炸——调用方连启动都到不了。这一条和
`test_github_pr_module_split.py` 是同一个写法，扫的也是整仓，不止 `backend/`。

**这一条多守一件：谁读被替换的名字。** 这个模块上有六个名字是测试按属性换掉的——
`enabled`、`dispatch`、`branch_head`、`proposal_client`、`_branch_ahead`、
`sweep_draft_prs`。替换要看得见，读它们的每一具身子就得留在门面的命名空间里：
搬到 `pr_publish_payload.py` 上的身子读的是自己那份副本，替换落空，而 ruff、pyright、
边界闸门和其余单测都不会因此变红（`naming.py` 2026-10 那次就是这么静默失效的）。
"""

import ast
import importlib
import os
from pathlib import Path

import app

MODULE = "app.domain.review.pr_publish"
SIBLING = "app.domain.review.pr_publish_payload"
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent

#: 测试在 `app.domain.review.pr_publish` 这个模块对象上换掉的名字。
REPLACED_BY_TESTS = (
    "enabled",
    "dispatch",
    "branch_head",
    "proposal_client",
    "_branch_ahead",
    "sweep_draft_prs",
)

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


def _names_the_tree_reaches() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(按名字导入的, 取模块对象再按属性取的)：名字 -> 出现的地方。"""
    imported: dict[str, list[str]] = {}
    by_attr: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里两千个文件，只有碰过这个模块的那几个才值得解析。
        if "pr_publish" not in source:
            continue
        where = path.relative_to(REPO)
        tree = ast.parse(source, str(path))
        module_names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.domain.review"
            for alias in node.names
            if alias.name == "pr_publish"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == MODULE:
                for alias in node.names:
                    imported.setdefault(alias.name, []).append(f"{where}:{node.lineno}")
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in module_names
            ):
                by_attr.setdefault(node.attr, []).append(f"{where}:{node.lineno}")
    return imported, by_attr


def _globals_a_module_reads(source: str) -> set[str]:
    """模块顶层的函数体里读到、又不是自己绑的局部名——也就是模块全局。"""
    tree = ast.parse(source)
    module_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_level.add(node.name)
        elif isinstance(node, ast.Assign):
            module_level.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_level.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            module_level.update(alias.asname or alias.name for alias in node.names)
    read: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = {a.arg for a in node.args.args + node.args.kwonlyargs}
        local = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }
        read |= (
            {
                child.id
                for child in ast.walk(node)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
            - args
            - local
        )
    return read & module_level


def _replaced_names_taken_from_elsewhere(source: str) -> set[str]:
    """这一片里读到的、被测试替换的名字。

    取用不只「读模块全局」一种。函数体里写一句 `from <别的模块> import
    branch_head`，名字绑在局部，`& module_level` 那一关看不见它，可它读的同样是
    别人那份 —— 测试在门面上换掉的那份照样落空。所以这里换一把尺子：这一片里凡是
    会读到那些名字的写法都算，包括 `as` 改名和 `<谁>.branch_head` 这种属性写法。

    放过的只有「调用时才去门面取」的那两种：写在函数体里的 `from ...pr_publish
    import X`（含 `as` 改名）到调用时才解析，`import ...pr_publish as p` 或
    `from ..review import pr_publish` 之后按 `p.X` 取也是读门面那个属性，两者都跟
    着替换走。**顶层的** `from ...pr_publish import X` 不在其列：它在导入那一刻就
    把值抄了一份，替换改的是门面那份，抄走的这份纹丝不动。顶层那句还会绕出一圈
    循环导入，别的闸门都不管 `app.domain.review` 内部的环。

    自己绑的名字不算读别人那份：函数参数、局部赋值、循环和推导式的目标跟被替换的
    名字重名时，读的是自己那个。
    """
    replaced = set(REPLACED_BY_TESTS)
    tree = ast.parse(source)
    module_level = {id(node) for node in tree.body}
    package, _, facade_name = MODULE.rpartition(".")

    # 绑到门面那个模块对象上的别名，按它们取属性就是读门面的属性。`import
    # ...pr_publish as p`，或者 `from app.domain.review import pr_publish`（后面这句
    # 绑的是模块对象本身，不是它里面的哪个名字）。
    facade_aliases = {facade_name}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            facade_aliases |= {
                alias.asname
                for alias in node.names
                if alias.name == MODULE and alias.asname
            }
        elif isinstance(node, ast.ImportFrom):
            facade_aliases |= {
                alias.asname or facade_name
                for alias in node.names
                if node.module == package and alias.name == facade_name
            }
    from_facade = {
        child.attr
        for child in ast.walk(tree)
        if isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id in facade_aliases
    } & replaced

    # 写在函数体里的那几句到调用时才解析，放行；顶层那句抄的是值，照旧算漏点。
    # `as` 改名时两个名字都算（原名的 alias.name 也在）。
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == MODULE
            and id(node) not in module_level
        ):
            from_facade |= {alias.name for alias in node.names}
            from_facade |= {
                alias.asname for alias in node.names if alias.asname is not None
            }

    mentioned = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    mentioned |= {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mentioned |= {alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            mentioned |= {alias.name.split(".")[-1] for alias in node.names}

    own = {
        arg.arg
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    }
    own |= {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    return (mentioned - from_facade - own) & replaced


def test_every_name_the_tree_reaches_is_still_on_the_facade():
    facade = importlib.import_module(MODULE)
    imported, by_attr = _names_the_tree_reaches()

    # 扫描范围本身要看着。名字的地板断言看不出范围缩回去了，所以直接数范围：
    # 调用方在 `app` 里，测试在 `tests` 里，脚本在 `backend/scripts` 里。
    sites = [
        site
        for group in (imported, by_attr)
        for places in group.values()
        for site in places
    ]
    assert any(site.startswith("backend/app/") for site in sites), sites
    assert any(site.startswith("backend/tests/") for site in sites), sites
    assert any(site.startswith("backend/scripts/") for site in sites), sites
    # 一条都扫不出来就是扫错了（路径拼歪、名字改坏），别让这条测试空着过。
    assert {"_draft_pr_for_one_task", "retarget_completed_dependencies"} <= set(
        imported
    )
    assert {"_run", "sweep_draft_prs", "is_draft_title", "record_pr"} <= set(by_attr)

    missing = {
        name: where
        for name, where in {**imported, **by_attr}.items()
        if not hasattr(facade, name)
    }

    assert not missing, (
        f"这些名字树里还在取，{MODULE} 上却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（`X as X`）：调用方的导入在模块加载时就断、"
        "按属性取的到调用时才炸，两种都比放在门面上更晚一步。"
    )


def test_the_names_tests_replace_are_read_from_this_module():
    """替换落不落空，看的是读它的人在哪一侧。

    测试换掉的是 `app.domain.review.pr_publish` 这个模块对象上的属性。谁把读这个
    名字的身子搬到旁边那一片，谁就让替换悄悄失效：那边读的是 `pr_publish_payload`
    自己绑的那一份，替换改的是这份。这条测试不跑那些场景（它们要库、要假 forge），
    只钉住这条规则——被替换的名字，它的读者必须在这一侧。
    """
    facade = importlib.import_module(MODULE)
    read_here = _globals_a_module_reads(
        Path(facade.__file__).read_text(encoding="utf-8")
    )
    # 被替换的名字里，门面自己的身子读得到的那几个，必须真的还在读：删掉一句
    # `enabled()` 会让 `sweep_draft_prs` 少了「这台机器配了 App 吗」这一问，
    # 那条测试就再也压不动它。
    assert {"enabled", "branch_head", "proposal_client", "_branch_ahead"} <= (
        read_here
    ), "门面自己的身子不再读这些名字了：替换会落空，或者那句判断被删掉了。"

    sibling = importlib.import_module(SIBLING)
    leaked = _replaced_names_taken_from_elsewhere(
        Path(sibling.__file__).read_text(encoding="utf-8")
    )
    assert not leaked, (
        f"{SIBLING} 里的身子读了 {sorted(leaked)}，而这些名字是测试在 "
        f"{MODULE} 上换掉的：换了那边、这边照旧用自己那份，置换成空。"
        "把读它的身子搬回门面。"
    )
    # 上面那句是规矩（谁读被替换的名字），跟着门面名字走。这一句是「按老名字取还
    # 取得到」的**实例**，故意用最绕的一条：这三个函数确实搬去了旁边那一片，翻遍
    # 门面也找不到谁提到它们的名字，只有按属性取才认得出是谁。
    for name in ("_open_pr_of_task", "_pr_text", "sync_pr_text"):
        assert getattr(facade, name).__module__ == SIBLING, (
            f"{name} 不在 {SIBLING} 里，说明这具身子没搬走（或者搬错了地方）："
            "这条实例断言就白钉了，规矩那条也跟着不再覆盖「按属性取」这种写法。"
        )

    # 日志名是子系统的名字，不是哪个文件的名字：搬走的身子发出的记录要还落在
    # 同一条通道上，按模块名过滤日志的地方才不会断（github_pr 拆包时为同一件事
    # 单独钉过一条）。
    assert facade.logger.name == "cheesex.pr_publish"
    assert sibling.logger.name == "cheesex.pr_publish"
