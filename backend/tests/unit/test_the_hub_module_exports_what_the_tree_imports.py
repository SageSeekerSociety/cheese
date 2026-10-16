"""门面拆开之后，`from app.domain.agent.device_hub import X` 不许断。

拆分最容易弄丢的就是这个：搬走函数的人只看自己那一片，没人看全仓库谁从这个模块
导过什么名字，而缺一个名的代价是导入时就炸——调用方连启动都到不了。

所以这里不列名单，而是把树里真实的导入语句读出来：不用维护一张会过期的表，谁删掉
一个还在被导入的名字，谁就红。
"""

import ast
import importlib
from pathlib import Path

import app

MODULE = "app.domain.agent.device_hub"
BACKEND = Path(app.__file__).resolve().parent.parent


def _scanned_files() -> list[Path]:
    return sorted((BACKEND / "app").rglob("*.py")) + sorted(
        (BACKEND / "tests").rglob("*.py")
    )


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
                        f"{path.relative_to(BACKEND)}:{node.lineno}"
                    )
    return found


def test_every_name_the_tree_imports_from_the_hub_is_still_there():
    hub = importlib.import_module(MODULE)
    imported = _names_imported_from_the_hub()
    # 扫不出东西就是扫错了（比如路径写歪），别让这条测试空着过。
    assert {"device_hub", "DeviceHub", "DeviceOffline"} <= set(imported)

    missing = {
        name: where for name, where in imported.items() if not hasattr(hub, name)
    }

    assert not missing, (
        f"这些名字还有人从 {MODULE} 导入，模块里却已经没有了：{missing}。"
        "搬走的身子要把名字留在门面上（`X as X` 或 `# noqa: F401`）：调用方的导入"
        "在模块加载时就断，比调用时才炸更早一步。"
    )
