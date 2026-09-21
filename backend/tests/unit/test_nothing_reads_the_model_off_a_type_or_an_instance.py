"""架构守卫：模型、骨架、思考深度都读不出一个类型或一个实例。

结论 3「模型不是 agent 的属性，是**工作的资源绑定**」、结论 28「harness 是部署
级的开发者设置，不在类型上也不在实例上」，ARCH §9.1「参与者」行判据②。

**为什么是一条守卫而不是一条功能测试**：这三个字段没有了读者，也就没有了行为可
以断言——一个字段被悄悄读回去，不会有任何一条测试变红，只会让「用哪个模型」重新
有两个住处。能看见它的只有「全仓零读点」这一条。

字段本身还在 schema 上（P15b 才删），所以这里盯的是**读点和写点**，不是字段。
"""

import ast
import re
from pathlib import Path

import pytest

from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_type.library import AgentTypeDef

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
FRONTEND_SRC = BACKEND.parent / "frontend/src"

RETIRED = ("model", "harness", "effort")


def _reads_a_retired_key(tree: ast.AST) -> list[tuple[int, str]]:
    """一个 ``configuration`` 里的三个键被读出来的地方。

    读法只有两种——``configuration["model"]`` 和 ``configuration.get("model")``
    ——两种都按「取值的那个东西的源码里出现了 configuration」来认。宽一点是故意
    的：这条守卫宁可多问一句，也不要放过一个改了变量名就绕过去的读点。
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        holder = key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            holder, key = node.value, node.slice.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            holder, key = node.func.value, node.args[0].value
        if key not in RETIRED or holder is None:
            continue
        if "configuration" in ast.dump(holder):
            found.append((node.lineno, key))
    return found


def test_no_backend_code_reads_the_three_keys_off_a_saved_configuration() -> None:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(APP.rglob("*.py")):
        hits = _reads_a_retired_key(ast.parse(path.read_text(encoding="utf-8")))
        if hits:
            offenders[str(path.relative_to(APP))] = hits
    assert not offenders, (
        f"这些地方还在从 configuration 里读模型/骨架/思考深度：{offenders}。"
        "模型读这条活的绑定（room_task/binding.py），骨架读部署设置。"
    )


# ``draft.value.harness``、``agent.configuration.model``、``preset?.effort``——
# 前端读它们只有属性访问这一种写法，而持有它们的名字就这几个。
_FRONTEND_READ = re.compile(
    r"\b(configuration|draft|preset|config|agent)(\.value)?\??\.(model|harness|effort)\b"
)


def test_no_frontend_code_reads_the_three_fields_off_a_type_or_an_instance() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in (".ts", ".vue"):
            continue
        hits = [
            f"line {i}: {line.strip()}"
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _FRONTEND_READ.search(line)
        ]
        if hits:
            offenders[str(path.relative_to(FRONTEND_SRC))] = hits
    assert not offenders, (
        f"界面还在读一个类型或一个实例上的模型/骨架/思考深度：{offenders}。"
        "用户接触模型的地方只有卡。"
    )


@pytest.mark.parametrize("field", RETIRED)
def test_a_saved_configuration_never_writes_the_three_keys_back(field: str) -> None:
    """写入端只有 ``model_dump()`` 一条。它不交出这三个键，所以 P15b 那条迁移
    清一次就够——不会有新的行再把它们写回去。
    """
    written = AgentConfiguration(
        body="角色", model="opus", harness="codex", effort="high"
    ).model_dump()
    assert field not in written
    assert set(written) == {"body", "skills", "mcp_servers"}


@pytest.mark.parametrize("field", RETIRED)
def test_a_type_has_no_field_for_how_it_runs(field: str) -> None:
    """类型那一侧连字段都没有了：一个类型说的是角色。"""
    assert field not in AgentTypeDef.__dataclass_fields__
