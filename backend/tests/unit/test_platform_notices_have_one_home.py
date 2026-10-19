"""守卫：两句平台提示只从 ``platform_notices`` 里出来。

``tools_recovered_notice`` / ``catching_up_notice`` 是这两句话唯一的出处：
``AgentWorkRunner`` 与 ``RunnerIntake`` 只解包它们的返回值，调用点也只使用解包
出来的句子。以前两处各复制了一份 ``notice(...)``、调用点各写了一遍
``say("toolsRecovered")`` / ``say("catchingUp")``：改一处 severity 或文案，另一处
不会跟着变，而且两边都是绿的。

判据分三层：

- 两个类属性与 helper 的返回值逐字相等；
- helper 自己的 event_type / severity 被钉住 —— 把 helper 的 severity 改掉，这里
  当场红，而不是跟着一起变绿的「相等」；
- 句子在全仓 ``backend/app`` 下只在 ``platform_notices.py`` 里被 ``say`` 出来，
  且两处 ``_META`` 属性必须是 helper 调用的解包，不是又一份内联 ``notice(...)``。

这是静态测试：被测的是源码树的一个性质，属于 AGENTS.md「测行为、不读源码」的
例外，和 ``test_actor_is_not_typed.py`` 同类。
"""

import ast
import pathlib

from app.domain.agent.platform_notices import (
    catching_up_notice,
    tools_recovered_notice,
)
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.turn.intake.messages import RunnerIntake
from app.domain.notice_meta import (
    EVENT_TOOLS_RECOVERED,
    EVENT_TURN_QUEUED,
    SEVERITY_INFO,
    SEVERITY_WARN,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP_ROOT = REPO_ROOT / "backend" / "app"
NOTICES_MODULE = "platform_notices.py"

#: 只允许在 ``platform_notices.py`` 里被拼出来的句子。
ONE_HOME_SENTENCES = frozenset({"toolsRecovered", "catchingUp"})


def test_the_runner_unpacks_the_tools_recovered_notice() -> None:
    text, meta = tools_recovered_notice()
    assert AgentWorkRunner._TOOLS_RECOVERED_TEXT == text
    assert AgentWorkRunner._TOOLS_RECOVERED_META == meta


def test_the_intake_unpacks_the_catching_up_notice() -> None:
    text, meta = catching_up_notice()
    assert RunnerIntake._CATCHING_UP_TEXT == text
    assert RunnerIntake._CATCHING_UP_META == meta


def test_the_helpers_keep_their_known_payloads() -> None:
    """改 helper 的 event_type / severity 时，两个解包点不会替它兜底。"""
    _, tools_meta = tools_recovered_notice()
    assert tools_meta["event_type"] == EVENT_TOOLS_RECOVERED
    assert tools_meta["severity"] == SEVERITY_WARN

    _, catching_meta = catching_up_notice()
    assert catching_meta["event_type"] == EVENT_TURN_QUEUED
    assert catching_meta["severity"] == SEVERITY_INFO


def _said_keys(path: pathlib.Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "say"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in ONE_HOME_SENTENCES
        ):
            found.append((node.lineno, node.args[0].value))
    return found


def test_only_the_notices_module_says_these_two_sentences() -> None:
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name == NOTICES_MODULE:
            continue
        for lineno, key in _said_keys(path):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} say({key!r})")

    assert not offenders, (
        "这两句话只能在 platform_notices.py 里拼：\n  "
        + "\n  ".join(offenders)
        + "\n\n调用点用 helper 返回的句子，别再自己 say 一遍。"
    )


def _assigned_value(path: pathlib.Path, name: str) -> ast.expr | None:
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets[0]
        if isinstance(targets, ast.Tuple):
            names = [item.id for item in targets.elts if isinstance(item, ast.Name)]
        elif isinstance(targets, ast.Name):
            names = [targets.id]
        else:
            continue
        if name in names:
            return node.value
    return None


def _unpacks(path: pathlib.Path, attribute: str, helper: str) -> str | None:
    value = _assigned_value(path, attribute)
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
        return f"{attribute} 不是对 {helper}() 的解包"
    if value.func.id != helper:
        return f"{attribute} 解包的是 {value.func.id}()，不是 {helper}()"
    return None


def test_both_constants_are_unpacked_from_their_helper() -> None:
    """又内联一份 notice(...) 时值可能恰好相等，所以这里看的是形状。"""
    homes = {
        APP_ROOT / "domain/agent/runtime.py": (
            "_TOOLS_RECOVERED_META",
            "tools_recovered_notice",
        ),
        APP_ROOT / "domain/agent/turn/intake/messages.py": (
            "_CATCHING_UP_META",
            "catching_up_notice",
        ),
    }
    problems = [
        f"{path.relative_to(REPO_ROOT)}: {problem}"
        for path, (attribute, helper) in homes.items()
        if (problem := _unpacks(path, attribute, helper))
    ]
    assert not problems, "\n".join(problems)
