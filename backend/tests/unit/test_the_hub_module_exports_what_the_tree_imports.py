"""门面拆开之后，`from app.domain.agent.device_hub import X` 不许断。

拆分最容易弄丢的就是这个：搬走函数的人只看自己那一片，没人看全仓库谁从这个模块
导过什么名字，而缺一个名的代价是导入时就炸——调用方连启动都到不了。

所以这里不列名单，而是把树里真实的导入语句读出来：不用维护一张会过期的表，谁删掉
一个还在被导入的名字，谁就红。

**扫的是整仓，不止 `backend/`。** 这是 `backend/AGENTS.md` 对搬走一个公开名字的要求：
顶层 `scripts/` 里就有人从这个模块取名（`scripts/remote_execution/resident_release.py`
的 `HubScreen`、`backend/scripts/device_connection_lifecycle_acceptance.py` 的
`DeviceCallError`），只扫 `app/` 和 `tests/` 的话，删掉名字时这两处红不了。
"""

import ast
import importlib
import os
from pathlib import Path

import app

MODULE = "app.domain.agent.device_hub"
BACKEND = Path(app.__file__).resolve().parent.parent
REPO = BACKEND.parent

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


def _names_imported_from_the_hub() -> dict[str, list[str]]:
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


def test_every_name_the_tree_imports_from_the_hub_is_still_there():
    hub = importlib.import_module(MODULE)
    imported = _names_imported_from_the_hub()
    # 扫不出东西就是扫错了（比如路径写歪），别让这条测试空着过。`HubScreen` 只有
    # 顶层 `scripts/` 里那处在导，它顺带看着整仓这一半还在扫。
    assert {"device_hub", "DeviceHub", "DeviceOffline", "HubScreen"} <= set(imported)

    missing = {
        name: where for name, where in imported.items() if not hasattr(hub, name)
    }

    assert not missing, (
        f"这些名字还有人从 {MODULE} 导入，模块里却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（`X as X` 或 `# noqa: F401`）：调用方的导入"
        "在模块加载时就断，比调用时才炸更早一步。"
    )
