"""门面拆开之后，`from app.domain.agent_instance.own import X` 不许断。

`own.py` 拆成 `own/` 包时，最容易弄丢的就是这个：搬走函数的人只看自己那一片，
没人看全仓库谁从这个模块导过什么名字，而缺一个名的代价是导入时就炸 —— 调用方连
启动都到不了。`projects_own_agents` 一次取十四个名字，缺一个那个路由就起不来。

所以这里不列名单，而是把树里真实的导入语句读出来：不用维护一张会过期的表，谁删掉
一个还在被导入的名字，谁就红。手工名单会过期这件事不是猜的 —— `OwnCallers` 定义在
`agent_instance.models`，从门面导出，任何按模块职责想当然去翻子模块都找不到它。

**扫的是整仓，不止 `backend/app/`。** 这是 `backend/AGENTS.md` 对搬走一个公开名字的
要求：`backend/pyproject.toml` 的 pyright `include = ["app"]`，`scripts/` 和 `tests/`
都不查，只扫 `app/` 的话，那些调用点在类型检查里根本不存在。（就 `own` 而言
`grep -rn "app\\.domain\\.agent_instance\\.own\\b" --include=*.py .` 目前只命中
`backend/app/`，所以这里没法像 `the_hub` 那样用「扫到某处之外」来钉住扫描范围；退而
用名字的地板断言，那批名字分散在不同文件里，扫歪了就凑不齐。）
"""

import ast
import importlib
import os
from pathlib import Path

import app

MODULE = "app.domain.agent_instance.own"
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent

# 树里真实被导入的这批名字分散在路由、agent 域和 agent_instance 域三处；扫描范围
# 缩回其中任何一角都凑不齐这一组。
_EXPECTED = {
    "HOSTS_SETTING",  # 只有 api/routes/projects_own_agents 取
    "OwnCallers",  # 同上，而且它根本不在 own/ 里定义
    "allowed_kinds",  # 同上
    "callable_by",  # domain/agent/own_calls + domain/delivery/mention
    "may_work_for",  # api/routes/routines + topics_tasks
    "owned_instance",  # domain/agent 下四处
    "rename_for_connection",  # api/routes/connector + harness/claude_code/owner_login
    "unusable",  # domain/agent/owner_provider
}

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


def _names_imported_from_own() -> dict[str, list[str]]:
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


def test_every_name_the_tree_imports_from_own_is_still_there():
    own = importlib.import_module(MODULE)
    imported = _names_imported_from_own()

    # 扫描范围本身要看着：一条都扫不出来就是扫错了（路径拼歪、字符串预筛改坏），
    # 别让下面那条断言空着过。
    assert imported, "一个导入点都没扫到，扫描本身坏了"
    assert _EXPECTED <= set(imported), sorted(_EXPECTED - set(imported))

    missing = {
        name: where for name, where in imported.items() if not hasattr(own, name)
    }

    assert not missing, (
        f"这些名字还有人从 {MODULE} 导入，门面上却已经没有了：{missing}。"
        "搬走的身子要把名字留在 `__init__.py`（`X as X`）：调用方的导入在模块加载"
        "时就断，比调用时才炸更早一步。"
    )
