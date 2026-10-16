"""门面拆开之后，`from app.domain.review.github_pr import X` 不许断。

拆分最容易弄丢的就是这个：搬走身子的人只看自己那一片，没人看全仓库谁从这个模块
导过什么名字，而缺一个名的代价是导入时就炸——调用方连启动都到不了。

所以这里不列名单，而是把树里真实的导入语句读出来：不用维护一张会过期的表，谁删掉
一个还在被导入的名字，谁就红。

**扫的是整仓，不止 `backend/`。** 这是 `backend/AGENTS.md` 对搬走一个公开名字的要求，
`device_hub` 拆成包时留下的那条测试就是这么扫的。

**按属性取的名字也要守。** `github_pr.MergeResult(...)` 这种不出现在导入语句里，
但一样是「导入时不炸、调用时才炸」，而且比导入更常见——`github_pr` 拆成包以后，
`app.domain.review.services` 的几处和集成测试都是取模块对象再按属性拿名字。两个猴补
丁点尤其如此：`backend/tests/integration/test_accept_pr.py` 用
`monkeypatch.setattr(github_pr, "GitHubPRClient", _AppPrOpener)` 和
`github_pr.set_default_client(fake)` 替换这两个名字，替换要看得见，它们就必须还留在
门面的命名空间里——搬到某个子模块上，替换的就成了没人会读到的那一份。
"""

import ast
import importlib
import os
from pathlib import Path

import app

MODULE = "app.domain.review.github_pr"
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


def _names_the_tree_reaches() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(按名字导入的, 取模块对象再按属性取的)：名字 -> 出现的地方。"""
    imported: dict[str, list[str]] = {}
    by_attr: dict[str, list[str]] = {}
    for path in _scanned_files():
        source = path.read_text(encoding="utf-8")
        # 先按字符串过一遍：树里两千个文件，只有碰过这个模块的那几个才值得解析。
        if "github_pr" not in source:
            continue
        where = path.relative_to(REPO)
        tree = ast.parse(source, str(path))
        # 这一处把模块本身绑到了哪些本地名上（`from app.domain.review import github_pr`
        # 与它的改名写法）。
        module_names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.domain.review"
            for alias in node.names
            if alias.name == "github_pr"
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


def test_every_name_the_tree_reaches_is_still_on_the_facade():
    github_pr = importlib.import_module(MODULE)
    imported, by_attr = _names_the_tree_reaches()

    # 扫描范围本身要看着。名字的地板断言看不出范围缩回去了，所以直接数范围：
    # 调用方在 `app` 里，测试在 `tests` 里，两边都在册才算真的在扫整仓。
    sites = [site for place in imported.values() for site in place]
    assert any(site.startswith("backend/app/") for site in sites), sites
    assert any(site.startswith("backend/tests/") for site in sites), sites
    # 一条都扫不出来就是扫错了（路径拼歪、名字改坏），别让这条测试空着过。
    assert {"HttpxGitHubPrClient", "GitHubPRClient", "OpenedPR"} <= set(imported)
    assert {"MergeResult", "GitHubPRError", "PullRequest"} <= set(by_attr)

    missing = {
        name: where
        for name, where in {**imported, **by_attr}.items()
        if not hasattr(github_pr, name)
    }

    assert not missing, (
        f"这些名字树里还在取，{MODULE} 上却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（`X as X`）：调用方的导入在模块加载时就断、"
        "按属性取的到调用时才炸，两种都比放在门面上更晚一步。"
    )


def test_the_name_a_test_replaces_on_this_module_is_still_here():
    """替换名是字符串，上面那条扫描看不见它，单独守一条。

    `backend/tests/integration/test_accept_pr.py` 里
    `monkeypatch.setattr(github_pr, "GitHubPRClient", _AppPrOpener)`：`cheese ready`
    与更正描述走 `_app_pr_client`，它在调用时按这个属性取类，所以这个类必须还挂在
    门面的命名空间里——搬去子模块、门面上只剩一份没人读的副本，替换就落空了。
    """
    github_pr = importlib.import_module(MODULE)

    assert hasattr(github_pr, "GitHubPRClient")


def test_this_is_a_package_and_its_facade_carries_every_body():
    """门面是包，身子在子模块里——这条测试守的是拆分这件事本身没被改回去。"""
    github_pr = importlib.import_module(MODULE)

    assert Path(github_pr.__file__).name == "__init__.py", github_pr.__file__
    for submodule in ("_shared", "protocol", "httpx_client", "failure_logs", "legacy"):
        assert importlib.import_module(f"{MODULE}.{submodule}") is not None
    # 两个客户端形状各在自己那一片上定义，门面只是把名字再导出。
    assert (github_pr.GitHubPRClient.__module__) == f"{MODULE}.legacy"
    assert github_pr.HttpxGitHubPrClient.__module__ == f"{MODULE}.httpx_client"
    assert github_pr.GitHubPrClient.__module__ == f"{MODULE}.protocol"
    # 日志名还是拆之前那一个。记录现在从子模块发出来，`getLogger(__name__)` 会
    # 悄悄换成 `._shared`，按模块名过滤日志的地方就跟着断——`room/sessions/`
    # 和 `session_host` 的守卫都是这么钉的。
    assert github_pr.logger.name == MODULE
