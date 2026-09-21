"""守卫：平台只投递，从来不自己点起一轮（结论 13，不变量 I12、I13）。

这几条查的是**全仓有没有那条路**，不是某一次调用的结果 —— 一处调用点重新写上
`summon=True`，或者谁又加回一个「没有人写过它」的作者值，下面就有一条红。

为什么是源码扫描而不是跑一遍：这里要证的是「不存在这样一条路」。跑用例只能证被
跑到的那几条路没走它，而漏掉的恰恰是没人想起来去跑的那一处 —— 平台自召唤当初就
是这么一处一处长出来的（两份清单都说 5 处，实际 9 处）。
"""

import inspect
import pathlib
import re

from app.api.routes import chat as chat_route
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
FRONTEND = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src"


def _sources(root: pathlib.Path, suffixes: tuple[str, ...]):
    for path in sorted(root.rglob("*")):
        if path.suffix in suffixes and path.is_file():
            yield path, path.read_text()


#: `summon` 后面跟着 `True` 的**所有写法**：`summon=True`、`summon = True`，以及带
#: 类型标注的那一种 `summon: bool = True`。最后一种是个默认参数，也就是「不写就跑
#: 一轮」——这条路和显式写 `summon=True` 是同一条，只是不用在调用点写出来，所以以
#: 前逐字扫两个字面量的时候它整条漏在覆盖之外。
_SUMMON_IS_TRUE = re.compile(r"\bsummon\b\s*(?::[^=\n]+)?=\s*True")

#: 开那一行的调用点，连同它收到的实参。
_OPENS_THAT_ROW = re.compile(r"open_turn_the_session_started\((?P<args>[^)]*)\)", re.S)
#: 发件人是引号里的一个词 —— 也就是谁也没写过的那种作者值又回来了。
_AUTHOR_IS_A_LITERAL = re.compile(r"""author\s*=\s*["']""")


def test_no_call_site_can_ask_the_platform_to_start_a_turn():
    """没有任何一处写得出「跑一轮」——`submit` 收的是寻址结果，不是一个布尔。"""
    offenders = [
        f"{path}:{i}"
        for path, text in _sources(APP, (".py",))
        for i, line in enumerate(text.split("\n"), 1)
        if _SUMMON_IS_TRUE.search(line)
    ]
    assert offenders == [], f"平台自召唤又回来了：{offenders}"


def test_whether_a_turn_runs_has_no_default():
    """`converse` 的 `summon` 是必填的。

    有默认值就等于「不说也跑」——平台又多一条不点名也起得了轮次的路，而它不写在
    任何调用点上，扫调用点扫不出来。"""
    from app.domain.agent.chat import ChatService

    summon = inspect.signature(ChatService.converse).parameters["summon"]
    assert summon.default is inspect.Parameter.empty, "「跑不跑」又有默认值了"


def test_starting_a_turn_takes_an_addressing_result_not_a_boolean():
    for method in (InProcessBroker.receive_message, AgentWorkRunner.submit):
        params = inspect.signature(method).parameters
        assert "summon" not in params, f"{method.__qualname__} 又收了一个 summon 布尔"
    assert "addressed" in inspect.signature(AgentWorkRunner.submit).parameters


def test_there_is_no_author_value_nobody_wrote():
    """会话自己开的那一行，发件人是**读**出来的，没有一处写得出一个字面量。

    退的是那个作者值（字面量就是「会话」两个字），不是那条记录——会话被自己的
    worker 唤醒照样跑一整轮，它的行还得开，否则收尸看不见、算力不入账、房间一直
    显示「正在思考」（#604）。所以这里不查某一个已经退役的名字：那种查法只要改个
    名就按构造必过，证不了任何事。查的是这条路今天还能不能写出那样一个值——发件人
    必填，且每一处调用点给的都是读来的表达式，不是引号里的一个词。

    「它确实是名册上那个席位」由 integration 断：
    `test_the_session_starts_its_own_turn.py` 比对 `room_agent_seat(...)`。
    """
    author = inspect.signature(
        AgentWorkRunner.open_turn_the_session_started
    ).parameters["author"]
    assert author.default is inspect.Parameter.empty, "这一行又能开成没有发件人的了"

    call_sites = []
    for path, text in _sources(APP, (".py",)):
        for match in _OPENS_THAT_ROW.finditer(text):
            if text[: match.start()].rstrip().endswith("def"):
                continue  # 定义本身，不是调用点
            call_sites.append((path, match.group("args")))
    assert call_sites, "这条路改名或没了，守卫得跟着改，而不是就此空过"
    written = [
        f"{path}: {args.strip()}"
        for path, args in call_sites
        if _AUTHOR_IS_A_LITERAL.search(args)
    ]
    assert written == [], f"又有人往这一行上写死了一个作者值：{written}"


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

    # 查的是**发出去的那个对象有哪些键**，不是某一行怎么拼写的：`summon: true`、
    # 换个变量名、对象展开，都得一样红。发出去的 payload 里没有这一位，是由
    # ChatPanelComposer.test.ts 真发一条消息断的。
    sends = (FRONTEND / "components" / "ChatPanel.vue").read_text()
    start = sends.index("const msg: WsClientChatMessage = {")
    literal = sends[start : sends.index("}", start)]
    assert "summon" not in literal, f"发送时又往帧上放了 summon：{literal}"
