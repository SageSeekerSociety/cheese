"""`cheese_session(action=compact)` 默认关着：部署在会话的环境里写下那句话，它才在清单上、才调得动。

v1 的验收就是这两条：开了开关的座位调一次，会话真的压缩；没开的座位连工具都看不
到。开关只有一处——部署写进会话环境的那一句（``CHEESE_COMPACT_TOOL``），三个骨架都
不各自判断——所以下面读的是 `sandbox/cheese` 自己，和它给每一步的那条路
（``run_platform_tool``）。

表本身始终是完整的，这一条单独钉住：`platform_tool_names`、三个骨架的成员判断、
`proxy.js` 的那份名单都读整张表，缺一行会让一次调用落到「没有这样工具」上。
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_compact_switch", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class Harness:
    """这个会话的骨架，就某一样工具要问它的那件事：`compact`。没有它（None）就是
    骨架回答不了这个问题（pi 的早期版本、一场没有 runner 的会话）。"""

    def __init__(self, answer="requested"):
        self.answer = answer
        self.asked = 0

    def compact(self) -> str:
        self.asked += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Host:
    def __init__(self, *, environ=None, harness=None):
        self.environ = dict(environ or {})
        self.compact = None if harness is None else harness.compact
        self.requests: list[dict] = []

    def request(self, plan):
        self.requests.append(plan)
        raise AssertionError("压缩不该向平台要任何东西")

    def sync_task(self, task_id):
        raise AssertionError("压缩不该碰那台机器")


def _keys(rows) -> set[str]:
    """Each tool by name, and each verb of an object by its key
    (`cheese_session.compact`): the switch is on one verb, not a whole tool."""
    keys = set()
    for row in rows:
        verbs = row["inputSchema"]["properties"].get("action", {}).get("enum")
        keys |= {f"{row['name']}.{verb}" for verb in verbs} if verbs else {row["name"]}
    return keys


def offered(environ=None) -> set[str]:
    return _keys(cheese.offered_tools(environ))


def test_the_table_holds_the_row_whether_or_not_a_deployment_offers_it():
    """名单是挑出来的，表不是：`platform_request` 的代理、三个骨架的成员判断、现场
    的中文动词都读整张表。"""
    assert cheese.COMPACT_TOOL in _keys(cheese.PLATFORM_TOOLS.schemas())
    assert cheese.COMPACT_TOOL not in offered({})


def test_a_deployment_that_has_not_asked_is_offered_everything_else():
    asked = offered({"CHEESE_COMPACT_TOOL": "1"})
    assert cheese.COMPACT_TOOL in asked
    assert asked == _keys(cheese.PLATFORM_TOOLS.schemas())
    assert offered({}) == asked - {cheese.COMPACT_TOOL}


@pytest.mark.parametrize(
    ("value", "on"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        ("False", False),
        ("", False),
        (None, False),
    ],
)
def test_the_switch_reads_the_words_a_deployment_writes(value, on):
    environ = {} if value is None else {"CHEESE_COMPACT_TOOL": value}
    assert cheese.switched_on(environ) is on
    assert (cheese.COMPACT_TOOL in offered(environ)) is on


def test_a_session_without_the_switch_is_refused_and_nothing_is_asked():
    """没开开关的座位看不到它，调它也调不动——两处都由这一句话决定。"""
    host = Host(environ={}, harness=Harness())
    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool("cheese_session", {"action": "compact"}, host)
    assert "主动压缩" in str(refused.value)
    assert host.requests == []


def test_a_session_with_the_switch_asks_its_harness_once_and_says_what_happens():
    harness = Harness()
    host = Host(environ={"CHEESE_COMPACT_TOOL": "1"}, harness=harness)
    said = cheese.run_platform_tool("cheese_session", {"action": "compact"}, host)
    assert harness.asked == 1
    assert "这次回复结束" in said
    assert host.requests == []


def test_a_second_request_in_the_same_turn_says_it_is_already_asked():
    """骨架说「已经请求过了」，工具照原话说给 agent 听，不再请求一次。"""
    host = Host(environ={"CHEESE_COMPACT_TOOL": "1"}, harness=Harness("already"))
    assert "已经请求过" in cheese.run_platform_tool(
        "cheese_session", {"action": "compact"}, host
    )


def test_a_harness_that_cannot_compact_refuses_rather_than_pretending():
    host = Host(environ={"CHEESE_COMPACT_TOOL": "1"})
    with pytest.raises(cheese.PlatformToolError) as refused:
        cheese.run_platform_tool("cheese_session", {"action": "compact"}, host)
    assert "骨架" in str(refused.value)


# --- a runner built before there is a loop -------------------------------------
#
# pi's and Codex's runners reach their own loop from a platform tool call's
# worker thread. Taking the loop in `__init__` broke every caller that builds a
# runner outside one — a sync test, or any code that prepares a runner before
# its loop exists — with `RuntimeError: no running event loop`. The reference is
# taken where it exists and set again in `start`.


def test_a_pi_runner_can_be_built_with_no_loop_running(tmp_path):
    from app.domain.agent.harness.pi.runner import Runner

    runner = Runner(tmp_path / "pi")  # no loop here, and that must be fine
    try:
        assert runner.loop is None
    finally:
        runner.journal.close()


def test_a_codex_runner_can_be_built_with_no_loop_running(tmp_path):
    from app.domain.agent.harness.codex.runner import Runner

    runner = Runner(tmp_path / "codex", lambda *_args: None)  # no loop here
    try:
        assert runner.loop is None
    finally:
        runner.journal.close()


@pytest.mark.anyio
async def test_a_runner_started_on_a_loop_remembers_it(tmp_path):
    """`start` runs on the loop the runner keeps working on, and that is the
    one a tool call's thread has to hop onto."""
    import asyncio as _asyncio

    from app.domain.agent.harness.driven.runner import current_loop

    assert current_loop() is _asyncio.get_running_loop()
