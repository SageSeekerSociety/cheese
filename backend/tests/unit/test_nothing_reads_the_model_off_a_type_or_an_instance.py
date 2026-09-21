"""架构守卫：模型、骨架、思考深度都读不出一个类型或一个实例。

结论 3「模型不是 agent 的属性，是**工作的资源绑定**」、结论 28「harness 是部署
级的开发者设置，不在类型上也不在实例上」，ARCH §9.1「参与者」行判据②。

**为什么是一条守卫而不是一条功能测试**：这三个字段没有了读者，也就没有了行为可
以断言——一个字段被悄悄读回去，不会有任何一条测试变红，只会让「用哪个模型」重新
有两个住处。能看见它的只有「全仓零读点」这一条。

字段本身也已经不在 schema 上了（P15b），所以这里盯两样东西：**读点**——全仓再没
有一处从一份 configuration 上取出这三样；以及**字段集合**——类型和实例各自能带
什么是一张封闭清单，三个字段回不来，换个名字也回不来。
"""

import ast
import re
from pathlib import Path

import pytest

from app.domain.agent_instance.configuration import AgentConfiguration
from app.domain.agent_type.library import AgentTypeDef
from app.domain.agent_type.schemas import AgentTypeOut

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
FRONTEND_SRC = BACKEND.parent / "frontend/src"

RETIRED = ("model", "harness", "effort")


def _holds_a_configuration(expr: ast.expr, aliases: set[str]) -> bool:
    """这个表达式取的是一份 configuration 吗。

    两类都算：源码里带 ``configuration`` 的（``agent.configuration``、
    ``AgentConfiguration.model_validate(...)``——大小写不分，后者的类名里也有它），
    以及先被起了局部名的（``cfg = agent.configuration`` 之后的 ``cfg``）。
    """
    if isinstance(expr, ast.Name) and expr.id in aliases:
        return True
    return "configuration" in ast.dump(expr).lower()


def _configuration_aliases(tree: ast.AST) -> set[str]:
    """``x = <一份 configuration>`` 里的那些 ``x``。

    一路跟到不再长出新名字为止，``a = agent.configuration`` 之后 ``b = a`` 的
    ``b`` 也算——改个变量名就绕过守卫，是这条不变量最容易被悄悄破掉的方式。
    """
    aliases: set[str] = set()
    while True:
        grown = set(aliases)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            elif isinstance(node, ast.NamedExpr):
                targets, value = [node.target], node.value
            else:
                continue
            if not _holds_a_configuration(value, grown):
                continue
            grown |= {t.id for t in targets if isinstance(t, ast.Name)}
        if grown == aliases:
            return aliases
        aliases = grown


def _reads_a_retired_key(tree: ast.AST) -> list[tuple[int, str]]:
    """一份 configuration 里的三样东西被读出来的地方。

    三种读法都认：``configuration["model"]``、``configuration.get("model")``，
    以及走 schema 的 ``AgentConfiguration.model_validate(row).model``——最后这种
    今天会当场 ``AttributeError``，但它写起来最像对的，所以照样是一条要拦的写
    法。持有它的表达式先过 :func:`_holds_a_configuration`，所以起个局部名换个写
    法也绕不过去。
    """
    aliases = _configuration_aliases(tree)
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
        elif isinstance(node, ast.Attribute):
            holder, key = node.value, node.attr
        if key not in RETIRED or holder is None:
            continue
        if _holds_a_configuration(holder, aliases):
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


# ``draft.value.harness``、``agent.configuration.model``、``preset?.effort``、
# ``cfg['model']``——界面读它们是属性访问或者下标，而持有它们的要么是下面这几个
# 名字，要么是一个当场起的局部名（``const cfg = a.configuration``）。
_HOLDERS = ("configuration", "draft", "preset", "config", "agent")
_FRONTEND_ALIAS = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*(?::[^=]+)?=[^=].*configuration", re.I
)


def _frontend_reads(text: str) -> list[str]:
    """界面上把这三样东西读出来的行。

    三种写法都认：属性（``a.configuration.model``）、下标（``cfg['model']``），
    以及解构（``const { model } = agent.configuration``）。第三种是这条守卫最容
    易漏的一种——它读出来的名字就叫 ``model``，之后每一处用它的地方都不再提持有
    它的东西，所以只有解构那一行能看见这是一个读点。
    """
    holders = set(_HOLDERS) | set(_FRONTEND_ALIAS.findall(text))
    named = "|".join(sorted(holders))
    reads = re.compile(
        r"\b(" + named + r")(\.value)?\??"
        r"(\.(model|harness|effort)\b|\[['\"](model|harness|effort)['\"]\])"
    )
    destructured = re.compile(
        r"(?:const|let|var)\s*\{[^}]*\b(?:model|harness|effort)\b[^}]*\}\s*="
        r"[^=]*\b(" + named + r")\b"
    )
    return [
        f"line {i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if reads.search(line) or destructured.search(line)
    ]


def test_no_frontend_code_reads_the_three_fields_off_a_type_or_an_instance() -> None:
    assert FRONTEND_SRC.is_dir(), (
        f"{FRONTEND_SRC} 不在——这条守卫会扫到零个文件然后绿。"
        "界面那一半的读点没有被检查过。"
    )
    offenders: dict[str, list[str]] = {}
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in (".ts", ".vue"):
            continue
        hits = _frontend_reads(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(FRONTEND_SRC))] = hits
    assert not offenders, (
        f"界面还在读一个类型或一个实例上的模型/骨架/思考深度：{offenders}。"
        "用户接触模型的地方只有卡。"
    )


# --- 守卫自己得能抓到东西 ---------------------------------------------------
#
# 一条正则守卫坏掉的时候是绿的：没有一处读点了，所以「扫出来是空的」既是它守住了
# 的样子，也是它什么都没在看的样子。下面两组片段把这两种情况分开——该红的喂进去
# 要命中，不该红的喂进去要放过（误红更坏，它把下一个人导去改一处本来对的代码）。

_BACKEND_MUST_CATCH = {
    "subscript": "agent.configuration['model']\n",
    "get": "agent.configuration.get('harness')\n",
    "through-the-schema": (
        "AgentConfiguration.model_validate(agent.configuration).effort\n"
    ),
    "renamed-first": "cfg = agent.configuration\nx = cfg.get('model')\n",
}

_BACKEND_MUST_PASS = {
    # 活的绑定就是模型今天的住处（room_task/binding.py），它不是一个读点。
    "the-work-binding": "model = task.model or project.default_model\n",
    # 部署设置，不是从一份 configuration 上读出来的。
    "the-deployment-harness": "harness = deployment_harness()\n",
    # 同名的键，持有它的不是一份 configuration。
    "somebody-else-s-model": "opening = {'model': binding.model}\n",
}


@pytest.mark.parametrize(
    "source", _BACKEND_MUST_CATCH.values(), ids=list(_BACKEND_MUST_CATCH)
)
def test_self_test_the_backend_guard_goes_red_on(source: str) -> None:
    assert _reads_a_retired_key(ast.parse(source)), f"没抓到：{source!r}"


@pytest.mark.parametrize(
    "source", _BACKEND_MUST_PASS.values(), ids=list(_BACKEND_MUST_PASS)
)
def test_self_test_the_backend_guard_lets_through(source: str) -> None:
    assert _reads_a_retired_key(ast.parse(source)) == [], f"误红：{source!r}"


_FRONTEND_MUST_CATCH = {
    "attribute": "const name = agent.configuration.model\n",
    "subscript": "const name = agent.configuration['harness']\n",
    "destructured": "const { model } = agent.configuration\n",
    "destructured-several": "const { body, effort } = draft.value\n",
    "renamed-first": "const cfg = a.configuration\nconst h = cfg.harness\n",
}

_FRONTEND_MUST_PASS = {
    "the-card-s-binding": "const { model } = card.binding\n",
    "the-work-binding": "const label = task.model ?? '默认'\n",
    "a-field-of-its-own": "const model = await pickModel()\n",
}


@pytest.mark.parametrize(
    "source", _FRONTEND_MUST_CATCH.values(), ids=list(_FRONTEND_MUST_CATCH)
)
def test_self_test_the_frontend_guard_goes_red_on(source: str) -> None:
    assert _frontend_reads(source), f"没抓到：{source!r}"


@pytest.mark.parametrize(
    "source", _FRONTEND_MUST_PASS.values(), ids=list(_FRONTEND_MUST_PASS)
)
def test_self_test_the_frontend_guard_lets_through(source: str) -> None:
    assert _frontend_reads(source) == [], f"误红：{source!r}"


# --- 字段集合是封闭清单 -----------------------------------------------------
#
# 为什么是「等于这张清单」而不是「这三个不在里面」：一个字段可以换个名字回来
# （``runs_on``、``thinking``），而「模型不是参与者的属性」管的是这件事本身，不
# 是这三个拼写。清单写在这里，加一个字段就要在这里改一行并说出它凭什么是角色的
# 一部分——这正是结论 3、28 要人停下来想的那一下。

_A_ROLE = {"body", "skills", "mcp_servers"}

_A_TYPE = {
    "name",
    "title",
    "description",
    "builtin",
    "space_id",
    "created_by",
    "created_at",
} | _A_ROLE


def test_a_saved_configuration_carries_a_role_and_nothing_else() -> None:
    assert set(AgentConfiguration.model_fields) == _A_ROLE, (
        "一个实例存的是角色：人设、技能、外部工具。模型绑在活上（结论 3），"
        "骨架是部署设置（结论 28）——两样都不是这张 schema 上的字段。"
    )


def test_a_type_carries_a_role_and_nothing_else() -> None:
    assert set(AgentTypeOut.model_fields) == _A_TYPE, (
        "一个类型说的是角色加它自己的出身（谁建的、哪个空间、是不是内置）。"
        "「怎么跑」不在里面。"
    )


@pytest.mark.parametrize("field", RETIRED)
def test_a_type_has_no_field_for_how_it_runs(field: str) -> None:
    """类型那一侧连字段都没有了：一个类型说的是角色。"""
    assert field not in AgentTypeDef.__dataclass_fields__
