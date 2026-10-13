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

import pytest

from app.api.routes.topics_messages import ChatMessageIn
from app.domain.agent.runtime import AgentWorkRunner

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

#: 这几条守卫指向同一个规矩，修法写一次。规则：平台只投递，从来不自己点起一轮
#: （结论 13，不变量 I12、I13）；点名由服务端从正文解析。
_REPAIR_TURN = (
    "规则：平台只投递，从来不自己点起一轮（结论 13，不变量 I12、I13）；点名由服务端"
    "从正文解析。修法：调用点收寻址结果 addressed，不收一个「跑不跑」的布尔；"
    "summon 在 app/domain/agent/chat.py 的 ChatService.converse 上必填、没有默认值；"
    "会话自己开的那一行，发件人 author 传读出来的变量或表达式（如 author=author），"
    "不是引号里的字面量；KICKOFF_PROMPT / submit_kickoff 这条路已退役，不要再写。"
    "文档：docs/manual/dev/turn.md「一条消息怎么变成芝士的一轮」（#turn）。"
    "复查：cd backend && uv run pytest "
    "tests/unit/test_the_platform_never_starts_a_turn.py -q"
)


def test_no_call_site_can_ask_the_platform_to_start_a_turn():
    """没有任何一处写得出「跑一轮」——`submit` 收的是寻址结果，不是一个布尔。"""
    offenders = [
        f"{path}:{i}"
        for path, text in _sources(APP, (".py",))
        for i, line in enumerate(text.split("\n"), 1)
        if _SUMMON_IS_TRUE.search(line)
    ]
    assert offenders == [], (
        "又有一处写得出「跑一轮」——submit 收的是寻址结果，不是一个布尔：\n"
        + "\n".join(offenders)
        + "\n"
        + _REPAIR_TURN
    )


def test_whether_a_turn_runs_has_no_default():
    """`converse` 的 `summon` 是必填的。

    有默认值就等于「不说也跑」——平台又多一条不点名也起得了轮次的路，而它不写在
    任何调用点上，扫调用点扫不出来。"""
    from app.domain.agent.chat import ChatService

    summon = inspect.signature(ChatService.converse).parameters["summon"]
    assert summon.default is inspect.Parameter.empty, (
        "「跑不跑」又有默认值了——app/domain/agent/chat.py 的 ChatService.converse "
        "上 summon 必须必填：\n" + _REPAIR_TURN
    )


def test_starting_a_turn_takes_an_addressing_result_not_a_boolean():
    for method in (AgentWorkRunner.receive_message, AgentWorkRunner.submit):
        params = inspect.signature(method).parameters
        assert "summon" not in params, (
            f"{method.__qualname__} 又收了一个 summon 布尔：\n" + _REPAIR_TURN
        )
    assert "addressed" in inspect.signature(AgentWorkRunner.submit).parameters, (
        "AgentWorkRunner.submit 不再收寻址结果 addressed——它是「跑一轮」的唯一入参：\n"
        + _REPAIR_TURN
    )


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
    assert author.default is inspect.Parameter.empty, (
        "这一行又能开成没有发件人的了——author 在 "
        "app/domain/agent/runtime.py 的 open_turn_the_session_started 上必填：\n"
        + _REPAIR_TURN
    )

    call_sites = []
    for path, text in _sources(APP, (".py",)):
        for match in _OPENS_THAT_ROW.finditer(text):
            if text[: match.start()].rstrip().endswith("def"):
                continue  # 定义本身，不是调用点
            call_sites.append((path, match.group("args")))
    assert call_sites, (
        "这条路改名或没了，守卫得跟着改，而不是就此空过：\n"
        "open_turn_the_session_started 的调用点一处都没扫到"
    )
    written = [
        f"{path}: {args.strip()}"
        for path, args in call_sites
        if _AUTHOR_IS_A_LITERAL.search(args)
    ]
    assert written == [], (
        "又有人往这一行上写死了一个作者值（author= 后面是引号里的一个词）：\n"
        + "\n".join(written)
        + "\n"
        + _REPAIR_TURN
    )


def test_the_platform_has_no_kickoff_left():
    """分身开局那一整条路（`submit_kickoff` + `KICKOFF_PROMPT`）零命中。"""
    offenders = [
        str(path)
        for path, text in _sources(APP, (".py",))
        if "KICKOFF_PROMPT" in text or "submit_kickoff" in text
    ]
    assert offenders == [], (
        "分身开局那一整条路又回来了——KICKOFF_PROMPT / submit_kickoff 应零命中：\n"
        + "\n".join(offenders)
        + "\n"
        + _REPAIR_TURN
    )


def test_summon_is_not_a_message_input():
    """浏览器算出来的那一位不存在了：点名由服务端从正文解析（I13）。

    只查**发到线上的那个请求体**：前端内部叫什么随它（组合框自己要知道按钮亮不亮），
    进了 `ChatMessageBody` 就是入参。
    """
    assert "summon" not in ChatMessageIn.model_fields, (
        "请求体 ChatMessageIn（app/api/routes/topics_messages.py）里又有 summon 了"
        "——点名由服务端从正文解析，不由前端交给入参：\n" + _REPAIR_TURN
    )

    types = (FRONTEND / "cx_types.ts").read_text()
    start = types.index("export interface ChatMessageBody {")
    body = types[start : types.index("}", start)]
    assert "summon" not in body, f"请求体的契约里又有 summon：{body}"

    # 查的是**发出去的那个对象有哪些键**，不是某一行怎么拼写的：`summon: true`、
    # 换个变量名、对象展开，都得一样红。全仓扫而不是盯住某一个文件：这一段在前端
    # 搬过家，盯文件的写法那次直接抛 ValueError，而它真正要防的东西一次都没被查过。
    builds = [
        (path, text[start : text.index("}", start)])
        for path, text in _sources(FRONTEND, (".ts", ".vue"))
        for start in [text.find(": ChatMessageBody = {")]
        if start >= 0
    ]
    assert builds, "前端没有任何一处拼出这个请求体了——这条守卫已经查不到东西"
    for path, literal in builds:
        assert "summon" not in literal, (
            f"{path} 发送时又往请求体上放了 summon：{literal}\n" + _REPAIR_TURN
        )


def test_a_summon_true_call_site_names_its_path_and_repair(monkeypatch):
    """`summon=True` 的命中要报出 file:line 和修法。用 monkeypatch 喂一个假的源，
    触发守卫真正会红的那个扫描分支。"""
    fake = (APP / "example.py", "run = submit(addressed, summon=True)\n")
    monkeypatch.setattr(
        "tests.unit.test_the_platform_never_starts_a_turn._sources",
        lambda root, suffixes: iter([fake]),
    )
    with pytest.raises(AssertionError) as caught:
        test_no_call_site_can_ask_the_platform_to_start_a_turn()
    message = str(caught.value)
    assert "example.py:1" in message
    assert "summon" in message
    assert "chat.py" in message
    assert "turn.md" in message
    assert "test_the_platform_never_starts_a_turn.py" in message


def test_a_kickoff_leftover_names_its_path_and_repair(monkeypatch):
    """`KICKOFF_PROMPT` / `submit_kickoff` 的命中要报出路径和修法。"""
    fake = (APP / "example.py", "KICKOFF_PROMPT = 'start'\n")
    monkeypatch.setattr(
        "tests.unit.test_the_platform_never_starts_a_turn._sources",
        lambda root, suffixes: iter([fake]),
    )
    with pytest.raises(AssertionError) as caught:
        test_the_platform_has_no_kickoff_left()
    message = str(caught.value)
    assert "example.py" in message
    assert "KICKOFF_PROMPT" in message
    assert "turn.md" in message
