"""守卫：`agent_instance` 不再 import `agent` —— C3 这一次还掉的那道环。

一个项目能用哪些模型，现在由 `app.domain.agent.model_catalog` 回答（它读网关的
报价、订阅名单、供给策略和骨架，都在 `agent` 里）；`agent_instance` 只留下一个队友
保存的配置**形状**（`AgentConfiguration`）。两边曾经互相 import：配置那半边读
`agent.market` / `agent.supply` / `agent.harness` / `agent.gateway_catalog`，而
`agent.queries` 等又反过来读它。C3 把这四条边提名为最便宜的一刀；拆掉之后
`app.domain` 的环从 27 条降到 14 条（`scripts/check_boundaries.py --measure`）。

**为什么还要这道守卫。** C3 的基线是从 merge base 树上量的：主干的树里那四条边还在，
所以谁把它们加回来，检查只会回到「和基线一样」，不会红。已经还掉的债，只能在这里
钉住。

判据只有一条：`app/domain/agent_instance/` 下的任何一个文件都不许 import
`app.domain.agent`（或它的子模块），相对写法也算。真需要那边的东西时，按这次的做法
由调用方递进来，不要在包内把边加回去。
"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = BACKEND_ROOT / "app/domain/agent_instance"
FORBIDDEN = "app.domain.agent"


def _agent_imports(source: str, module: str) -> set[str]:
    """这个源文件 import 的、落在 `app.domain.agent` 里的模块。

    ``module`` 是文件自己的模块名，相对 import 靠它折回绝对路径：``from ..agent.x``
    写在 ``app.domain.agent_instance.services`` 里，折出来是 ``app.domain.agent.x``。
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")[: -node.level]
                head = ".".join(base + (node.module.split(".") if node.module else []))
            else:
                head = node.module or ""
            targets = [head, *(f"{head}.{alias.name}" for alias in node.names)]
        # 一条 import 只记一个答案：先看它写出来的模块，再看 `from x import y`
        # 从同一个模块里取的名字（`from app.domain import agent` 只有后者抓得住）。
        for target in targets:
            if target == FORBIDDEN or target.startswith(f"{FORBIDDEN}."):
                found.add(target)
                break
    return found


def test_the_guard_scans_the_package_it_thinks_it_does() -> None:
    """先钉住扫描范围：目录改名或路径写错时，上面那条会「扫了 0 个文件」全绿。

    `offenders` 为空既可能是真干净，也可能是 glob 一个都没匹配上——这两种情况在
    断言里长得一样。这里要求至少扫到本包的入口文件。
    """
    scanned = {py.name for py in PACKAGE.rglob("*.py")}
    entry = {"__init__.py", "services.py", "configuration.py"}
    assert entry <= scanned, sorted(scanned)


def test_no_module_in_agent_instance_imports_the_agent_package() -> None:
    """这个包里一个 import 都不许有：有了一条，那条环就回来了。"""
    offenders = {}
    for py in sorted(PACKAGE.rglob("*.py")):
        module = ".".join(py.relative_to(BACKEND_ROOT).with_suffix("").parts)
        found = _agent_imports(py.read_text(encoding="utf-8"), module)
        if found:
            offenders[module] = sorted(found)
    assert not offenders, (
        f"agent_instance 又 import 了 agent：{offenders}。"
        "模型目录由调用方递进来（AgentInstanceService(models=...)），不要在包内取。"
    )


def test_the_guard_would_notice_the_import_put_back() -> None:
    """证明上一条不是空转：两种写法、以及包本身，都能认出来。"""
    module = "app.domain.agent_instance.services"
    market = "from app.domain.agent.market import TIER_INCLUDED\n"
    assert _agent_imports(market, module) == {"app.domain.agent.market"}
    assert _agent_imports("from ..agent.supply import GATEWAY\n", module) == {
        "app.domain.agent.supply"
    }
    assert _agent_imports("from app.domain.agent import model_catalog\n", module) == {
        "app.domain.agent"
    }
    assert _agent_imports("from app.domain import agent\n", module) == {
        "app.domain.agent"
    }
    # 同一层级里名字相像的包不是它：`agent_instance`、`agent_type`、`agent_session`
    # 都以 ``agent`` 开头，前缀比对要停在一个点上。
    assert not _agent_imports(
        "from app.domain.agent_instance.models import AgentInstance\n"
        "from app.domain.agent_type.library import preset_types\n"
        "from app.domain.agent_session.repositories import SessionRepository\n",
        module,
    )
