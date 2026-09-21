"""守卫：平台只投递，从来不自己点起一轮（结论 13，不变量 I12、I13）。

这几条查的是**全仓有没有那条路**，不是某一次调用的结果 —— 一处调用点重新写上
`summon=True`，或者谁又加回一个「没有人写过它」的作者值，下面就有一条红。

为什么是源码扫描而不是跑一遍：这里要证的是「不存在这样一条路」。跑用例只能证被
跑到的那几条路没走它，而漏掉的恰恰是没人想起来去跑的那一处 —— 平台自召唤当初就
是这么一处一处长出来的（两份清单都说 5 处，实际 9 处）。
"""

import inspect
import pathlib

from app.api.routes import chat as chat_route
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
FRONTEND = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src"


def _sources(root: pathlib.Path, suffixes: tuple[str, ...]):
    for path in sorted(root.rglob("*")):
        if path.suffix in suffixes and path.is_file():
            yield path, path.read_text()


def test_no_call_site_can_ask_the_platform_to_start_a_turn():
    """没有任何一处写得出「跑一轮」——`submit` 收的是寻址结果，不是一个布尔。"""
    offenders = [
        f"{path}:{i}"
        for path, text in _sources(APP, (".py",))
        for i, line in enumerate(text.split("\n"), 1)
        if "summon=True" in line or "summon = True" in line
    ]
    assert offenders == [], f"平台自召唤又回来了：{offenders}"


def test_starting_a_turn_takes_an_addressing_result_not_a_boolean():
    for method in (InProcessBroker.receive_message, AgentWorkRunner.submit):
        params = inspect.signature(method).parameters
        assert "summon" not in params, f"{method.__qualname__} 又收了一个 summon 布尔"
    assert "addressed" in inspect.signature(AgentWorkRunner.submit).parameters


def test_there_is_no_author_value_nobody_wrote():
    """`SELF_STARTED_AUTHOR = "session"` 那种轮次退场：一条便条有发件人。"""
    offenders = [
        str(path)
        for path, text in _sources(APP, (".py",))
        if "SELF_STARTED_AUTHOR" in text or "open_self_started_turn" in text
    ]
    assert offenders == []


def test_the_platform_has_no_kickoff_left():
    """分身开局那一整条路（`submit_kickoff` + `KICKOFF_PROMPT`）零命中。"""
    offenders = [
        str(path)
        for path, text in _sources(APP, (".py",))
        if "KICKOFF_PROMPT" in text or "submit_kickoff" in text
    ]
    assert offenders == []


def test_summon_is_not_an_http_or_ws_input():
    """浏览器算出来的那一位不存在了：点名由服务端从正文解析（I13）。

    只查**发到线上的那个帧**：前端内部叫什么随它（组合框自己要知道按钮亮不亮），
    进了 `WsClientChatMessage` 就是入参。
    """
    ws = inspect.getsource(chat_route)
    assert 'payload.get("summon"' not in ws

    types = (FRONTEND / "cx_types.ts").read_text()
    start = types.index("export interface WsClientChatMessage {")
    frame = types[start : types.index("}", start)]
    assert "summon" not in frame, f"帧的契约里又有 summon：{frame}"

    sends = (FRONTEND / "components" / "ChatPanel.vue").read_text()
    assert "summon: item.summon" not in sends, "发送时又往帧上放了 summon"
