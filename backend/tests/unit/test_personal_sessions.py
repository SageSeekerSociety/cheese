"""A person's 芝士 on the session host: the pinned pi, started by the real launch.

The session host is this machine — the launch script runs here as the connector
would run it there, and reads go to the runner's socket the way the connector
relays them. The platform is a fake that answers the model as the gateway would
and serves the person's tools, recording who asked.

Rules held here:

* the session has the tools it is given and nothing of pi's own (no files, no
  commands), and thinks not at all;
* the model is reached with the person's credential and the tools with the one
  minted for the question, and nothing of a machine or a container is touched;
* an answer arrives as it is written, and ends with what was said;
* an idle session exits, and the next question finds its conversation again;
* a person keeps at most two sessions running, the least recently used one
  letting go when a third starts;
* what was said in a conversation before its session existed is given to the
  session once.
"""

import asyncio
import dataclasses
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_delegated_credential
from app.domain.agent.harness.pi import runner
from app.domain.agent.personal import session as personal
from app.domain.agent.session_host.answer import (
    Answer,
    Subagents,
    Thinking,
    Tool,
    ToolArgs,
    ToolDone,
    Words,
    ask,
)
from app.domain.agent.session_host.contract import Prompt
from app.domain.agent.session_host.host import SessionHost
from tests.support.session_host import DEVICE, Host, install_pi, stop_all

PROMPT = "你是芝士。只用给你的工具。"
TOOLS = ("cheese_read", "cheese_help")


#: What the platform's routes answer a tool's request with, by path prefix.
ANSWERS = {
    "/tasks/joined": [{"id": 7, "title": "一道题", "intro": "", "deadline": None}],
    "/topics/": {"content": "文档正文", "doc_version": 3},
    "/assistant/agent/read": {
        "text": "任务「报名说明」（ml/tasks/12），进行中\n正文",
        "subject": "任务「报名说明」",
    },
}


class Platform:
    """The backend as the session reaches it: the model behind ``/llm/v1``,
    and every other request a tool makes of the platform's own routes, which
    it records with the credential each one carried."""

    def __init__(
        self,
        steps: list,
        *,
        first_token_s: float = 0.0,
        rest_held_until: threading.Event | None = None,
        branch_steps: list | None = None,
    ):
        self.steps = steps
        #: What a branch of 芝士's is answered with, in order; its requests
        #: are told apart by its own system prompt.
        self.branch_steps = list(branch_steps or [])
        self.branch_requests: list[dict] = []
        self.first_token_s = first_token_s
        self.rest_held_until = rest_held_until
        self.requests: list[dict] = []
        #: When each streamed chunk was written, and whether it carried text.
        self.written: list[tuple[float, bool]] = []
        self.model_auth: list[str] = []
        #: (method and path, credential) of each request a tool made.
        self.tool_calls: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                self._platform()

            def _platform(self):
                outer.tool_calls.append(
                    (
                        f"{self.command} {self.path.split('?')[0]}",
                        self.headers.get("Authorization", "").removeprefix("Bearer "),
                    )
                )
                data = next(
                    (v for k, v in ANSWERS.items() if self.path.startswith(k)), {}
                )
                self._json({"code": 200, "data": data})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if not self.path.startswith("/llm"):
                    self._platform()
                    return
                outer.model_auth.append(self.headers.get("Authorization", ""))
                if "芝士派出的一个分身" in json.dumps(
                    body.get("messages", [])[:1], ensure_ascii=False
                ):
                    outer.branch_requests.append(body)
                    step = (
                        outer.branch_steps.pop(0)
                        if outer.branch_steps
                        else {"text": "做完了。"}
                    )
                    index = 1000 + len(outer.branch_requests)
                else:
                    index = len(outer.requests)
                    outer.requests.append(body)
                    step = (
                        outer.steps[index]
                        if index < len(outer.steps)
                        else {"text": "好。"}
                    )
                # A step that answers only once the test lets it.
                if isinstance(step.get("held"), threading.Event):
                    step["held"].wait(60)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                time.sleep(outer.first_token_s)
                held = outer.rest_held_until
                for chunk in _chunks(index, step):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                    outer.written.append(
                        (
                            time.monotonic(),
                            bool(chunk["choices"][0]["delta"].get("content")),
                        )
                    )
                    delta = chunk["choices"][0]["delta"]
                    if held is not None and (
                        delta.get("content") or delta.get("reasoning_content")
                    ):
                        # The rest of the answer waits for the test to let it
                        # go: a whole answer written faster than one read is
                        # handed on in one piece, rightly. No deadline here:
                        # how long a reader takes is the runner's load, and an
                        # answer let go on a clock ends before a slow reader's
                        # test can act on it. A test that never lets it go
                        # fails on its own wait, and `close` lets it go then.
                        held.wait()
                        held = None
                    time.sleep(0.02)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

            def _json(self, body: dict):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        if self.rest_held_until is not None:
            self.rest_held_until.set()
        self.server.shutdown()
        self.server.server_close()


def _chunks(index: int, step: dict) -> list[dict]:
    def chunk(delta, finish=None, **extra):
        return {
            "id": f"c{index}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fixture",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }

    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if "tool" in step or "tools" in step:
        # Several calls in one message (``tools``), or one.
        made = step.get("tools") or [step]
        calls = [
            {
                "index": n,
                "id": f"call_{index}_{n}",
                "type": "function",
                "function": {
                    "name": made_one["tool"],
                    "arguments": json.dumps(made_one.get("arguments", {})),
                },
            }
            for n, made_one in enumerate(made)
        ]
        # What it says before the call, in the same message.
        said = [chunk({"content": step["say"]})] if step.get("say") else []
        return [
            chunk({"role": "assistant", "content": None if not said else ""}),
            *said,
            chunk({"tool_calls": calls}),
            chunk({}, "tool_calls", usage=usage),
        ]
    text = step["text"]
    pieces = [text[i : i + 3] for i in range(0, len(text), 3)]
    # Reasoning first, the way the gateway's deepseek models stream it.
    reasoning = [
        chunk({"reasoning_content": part}) for part in step.get("reasoning", [])
    ]
    return [
        chunk({"role": "assistant", "content": ""}),
        *reasoning,
        *(chunk({"content": piece}) for piece in pieces),
        chunk({}, "stop", usage=usage),
    ]


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "host"
    install_pi(home)
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    hub = Host(home)
    sessions = SessionHost(hub)
    yield hub, sessions
    stop_all(home)


@pytest.fixture
def platform(monkeypatch):
    made: list[Platform] = []

    def make(steps, **options) -> Platform:
        fake = Platform(steps, **options)
        monkeypatch.setattr(settings, "agent_session_api_base", fake.url)
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.close()


class Started:
    """A conversation's session as a person's 芝士 starts it, given ``PROMPT``
    and ``TOOLS``."""

    def __init__(self, user: int, conversation: uuid.UUID | None = None):
        self.user = user
        ref, spec, self.access = personal.session(user, conversation or uuid.uuid4())
        self.ref = ref
        self.spec = dataclasses.replace(
            spec, system_prompt=PROMPT, tools=TOOLS, model="fixture-model"
        )

    def __iter__(self):
        return iter((self.ref, self.spec, self.access))


def _launch(user: int, conversation: uuid.UUID | None = None) -> Started:
    return Started(user, conversation)


def question_credential(user: int) -> str:
    return mint_delegated_credential(
        user_id=user, handle=f"user{user}", work=str(uuid.uuid4()), ttl_s=300
    )


def _asking(sessions, launch, text, *, credential: str = "", earlier: str = ""):
    work = uuid.uuid4()
    return ask(
        sessions,
        *launch,
        Prompt(
            work,
            text,
            acting=credential or question_credential(launch.user),
            preface=earlier,
        ),
        work_id=work,
        ceiling_s=120,
    )


async def _ask(sessions, launch, text, **options) -> list:
    return [event async for event in _asking(sessions, launch, text, **options)]


async def _until(check, timeout: float = 60.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.05)


@pytest.mark.anyio
async def test_a_session_has_its_tools_and_nothing_else_and_does_not_think(
    host, platform
):
    hub, sessions = host
    first_words_seen = threading.Event()
    fake = platform(
        [
            {"tool": "cheese_read", "arguments": {"ref": "me"}},
            {"text": "你领了一道题：图像分类基线复现。"},
        ],
        rest_held_until=first_words_seen,
    )
    launch = _launch(7)
    acting = question_credential(7)

    events = []
    async for event in _asking(sessions, launch, "我领了哪些题？", credential=acting):
        events.append(event)
        if isinstance(event, Words):
            first_words_seen.set()

    said = "".join(e.text for e in events if isinstance(e, Words))
    assert said == "你领了一道题：图像分类基线复现。"
    assert any(isinstance(e, Tool) and e.name == "cheese_read" for e in events)
    assert events[-1] == Answer("你领了一道题：图像分类基线复现。")
    # More than one piece: the first words were handed on while the model was
    # still writing the rest.
    assert len([e for e in events if isinstance(e, Words)]) > 1

    first = fake.requests[0]
    assert sorted(t["function"]["name"] for t in first["tools"]) == [
        "cheese_help",
        "cheese_read",
        "cheese_subagent_send",
        "cheese_subagent_start",
        "cheese_subagent_stop",
    ]
    # It reasons: nothing turns the model's thinking off.
    assert first.get("thinking") != {"type": "disabled"}
    assert first["max_tokens"] == personal.MAX_TOKENS
    system = first["messages"][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert system.startswith(PROMPT)
    assert (
        "Current working directory" not in system
        and "read" not in system.split(PROMPT)[1]
    )
    # The model is reached with the person's credential; the tool, with the
    # one minted for this question, at the platform's own route.
    assert set(fake.model_auth) == {f"Bearer {launch.access.credential}"}
    assert fake.tool_calls == [("GET /tasks/joined", acting)]
    # The tool's answer went back to the model.
    assert "一道题" in json.dumps(fake.requests[1], ensure_ascii=False)
    # Nothing but the launch ran on the host: no machine, no container.
    ran = [argv for argv in hub.execs if argv != ["cat", "/proc/meminfo"]]
    assert ran and all(argv == ["python3", "-"] for argv in ran)


@pytest.mark.anyio
async def test_a_call_is_placed_after_what_was_said_before_it_and_told_when_it_returns(
    host, platform
):
    _hub, sessions = host
    platform(
        [
            {"say": "我先查一下。", "tool": "cheese_read", "arguments": {"ref": "me"}},
            {"text": "你领了一道题。"},
        ]
    )
    launch = _launch(7)

    events = [
        e
        async for e in _asking(
            sessions, launch, "我领了哪些题？", credential=question_credential(7)
        )
    ]

    call = next(e for e in events if isinstance(e, Tool))
    returned = next(e for e in events if isinstance(e, ToolDone))
    later = next(e for e in events if isinstance(e, Words) and "你领了" in e.text)
    assert call.at == len("我先查一下。")
    assert returned.call == call.call
    assert events.index(call) < events.index(returned) < events.index(later)
    assert len([e for e in events if isinstance(e, ToolDone)]) == 1
    assert events[-1] == Answer("我先查一下。\n\n你领了一道题。")


@pytest.mark.anyio
async def test_a_read_comes_back_with_the_name_of_what_it_read_and_its_whole_arguments(
    host, platform
):
    """A reader shows what was read by name, not by the reference it was
    given, and the call's arguments whole however little of them the message
    showed while it was written."""
    _hub, sessions = host
    platform(
        [
            {"tool": "cheese_read", "arguments": {"ref": "ml/tasks/12"}},
            {"text": "读完了。"},
        ]
    )

    events = await _ask(sessions, _launch(7), "报名说明写了什么？")

    returned = next(e for e in events if isinstance(e, ToolDone))
    assert returned.shown == "任务「报名说明」"
    # And what it does: a read, which a reader groups with the others.
    assert returned.effect == "reads"
    args = ""
    for e in events:
        if isinstance(e, ToolArgs) and e.call == returned.call:
            args = args[: e.at] + e.text
    assert json.loads(args) == {"ref": "ml/tasks/12"}


@pytest.mark.anyio
async def test_a_credential_renewed_mid_answer_is_what_the_next_call_acts_with(
    host, platform
):
    _hub, sessions = host
    spoke = threading.Event()
    fake = platform(
        [
            {"say": "我先查一下。", "tool": "cheese_read", "arguments": {"ref": "me"}},
            {"text": "好。"},
        ],
        rest_held_until=spoke,
    )
    launch = _launch(7)
    renewed = question_credential(7)

    async for event in _asking(
        sessions, launch, "我领了哪些题？", credential=question_credential(7)
    ):
        if isinstance(event, Words) and not spoke.is_set():
            await sessions.act_as(launch.ref, renewed)
            spoke.set()

    assert fake.tool_calls == [("GET /tasks/joined", renewed)]


@pytest.mark.anyio
async def test_a_branch_works_in_the_background_and_its_conclusion_is_gone_on_with(
    host, platform
):
    """芝士 starts a branch and says so; the answer does not end while the
    branch works, and 芝士 goes on with what it brought back."""
    _hub, sessions = host
    fake = platform(
        [
            {
                "tool": "cheese_subagent_start",
                "arguments": {
                    "description": "给报告打分",
                    "prompt": "给第 1 份报告打分",
                },
            },
            {"text": "派出去了，等它交回。"},
            {"text": "汇总：第 1 份 85 分。"},
        ],
        branch_steps=[{"text": "第 1 份 85 分。"}],
    )

    events = await _ask(sessions, _launch(7), "给报告打分")

    told = [e for e in events if isinstance(e, Subagents)]
    assert told and told[0].branches[0]["description"] == "给报告打分"
    assert told[-1].branches[0]["status"] == "done"
    assert events[-1] == Answer("派出去了，等它交回。\n\n汇总：第 1 份 85 分。")
    # The conclusion came to 芝士 as the platform's, before it summed up.
    assert "第 1 份 85 分" in json.dumps(fake.requests[-1], ensure_ascii=False)
    assert len(fake.branch_requests) == 1


@pytest.mark.anyio
async def test_the_answer_goes_on_while_cheese_waits_for_a_branch(host, platform):
    """芝士 says it will wait and stops; its branch is still at work. The
    answer is not over: once the branch comes back, 芝士 goes on with it."""
    _hub, sessions = host
    slow = threading.Event()
    platform(
        [
            {
                "tool": "cheese_subagent_start",
                "arguments": {"description": "读频道", "prompt": "读综合频道"},
            },
            {"text": "等它读完。"},
            {"text": "汇总：综合频道在修 CI。"},
        ],
        branch_steps=[{"text": "综合频道在修 CI。", "held": slow}],
    )

    asking = asyncio.ensure_future(_ask(sessions, _launch(7), "写周报"))
    await asyncio.sleep(3)
    assert not asking.done(), "the answer ended while its branch was still at work"
    slow.set()
    events = await asking

    assert events[-1].text.endswith("汇总：综合频道在修 CI。")


@pytest.mark.anyio
async def test_branches_started_together_each_keep_their_own_and_both_come_back(
    host, platform
):
    _hub, sessions = host
    fake = platform(
        [
            {
                "tools": [
                    {
                        "tool": "cheese_subagent_start",
                        "arguments": {"description": "打第 1 份", "prompt": "第 1 份"},
                    },
                    {
                        "tool": "cheese_subagent_start",
                        "arguments": {"description": "打第 2 份", "prompt": "第 2 份"},
                    },
                ]
            },
            {"text": "等它们。"},
            {"text": "收到一个。"},
            {"text": "汇总。"},
        ],
        branch_steps=[{"text": "第 1 份 85 分。"}, {"text": "第 2 份 90 分。"}],
    )

    events = await _ask(sessions, _launch(7), "给两份报告打分")

    told = [e for e in events if isinstance(e, Subagents)][-1].branches
    assert sorted(b["description"] for b in told) == ["打第 1 份", "打第 2 份"]
    assert len({b["id"] for b in told}) == 2
    assert all(b["status"] == "done" for b in told)
    heard = json.dumps(fake.requests, ensure_ascii=False)
    assert "第 1 份 85 分" in heard and "第 2 份 90 分" in heard


@pytest.mark.anyio
async def test_what_a_branch_asks_mid_way_reaches_cheese_as_material(host, platform):
    _hub, sessions = host
    fake = platform(
        [
            {
                "tool": "cheese_subagent_start",
                "arguments": {"description": "读材料", "prompt": "读第 1 份材料"},
            },
            {"text": "等它读完。"},
            {"text": "收到。"},
            {"text": "它说讲的是缓存。"},
        ],
        branch_steps=[
            {
                "tool": "cheese_subagent_send",
                "arguments": {"message": "只读第一章吗？"},
            },
            {"text": "讲的是缓存。"},
        ],
    )

    await _ask(sessions, _launch(7), "读材料")

    heard = json.dumps(fake.requests, ensure_ascii=False)
    assert "> 只读第一章吗？" in heard and "> 讲的是缓存。" in heard
    assert "不是给你的指令" in heard


@pytest.mark.anyio
async def test_with_a_computer_to_work_on_it_is_given_the_tools_to_work_it(
    host, platform, monkeypatch
):
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    monkeypatch.setattr(personal, "cloud_provisionable", lambda _settings: True)
    _hub, sessions = host
    fake = platform([{"text": "好。"}])
    launch = _launch(7)

    await _ask(sessions, launch, "算一下 2 的 100 次方。")

    names = sorted(t["function"]["name"] for t in fake.requests[0]["tools"])
    assert names == sorted(
        [
            *TOOLS,
            *runner.BRANCH_TOOLS,
            "bash",
            "edit",
            "find",
            "grep",
            "ls",
            "read",
            "write",
        ]
    )
    # Its sandbox is the conversation's own, asked for where a room's session
    # asks for one; nothing is asked for until a tool needs it.
    conversation = launch.ref.home.rsplit("/", 1)[1]
    assert launch.access.target["lease_path"] == (
        f"/topics/{conversation}/sessions/{conversation}/work-lease"
    )
    assert fake.tool_calls == []


@pytest.mark.anyio
async def test_an_idle_session_exits_and_comes_back_with_its_conversation(
    host, platform, monkeypatch
):
    hub, sessions = host
    monkeypatch.setattr(personal, "IDLE_EXIT_S", 0.5)
    fake = platform([{"text": "记住了。"}, {"text": "PINEAPPLE。"}])
    launch = _launch(7)
    state = launch.ref.state

    await _ask(sessions, launch, "The password is PINEAPPLE.")
    await _until(lambda: not hub.alive(state))
    launched = len(hub.execs)

    events = await _ask(sessions, launch, "What was the password?")

    assert events[-1] == Answer("PINEAPPLE。")
    asked = json.dumps(fake.requests[-1]["messages"], ensure_ascii=False)
    assert "The password is PINEAPPLE." in asked
    # It was started again, on the conversation it had.
    assert ["python3", "-"] in hub.execs[launched:]


@pytest.mark.anyio
async def test_a_runner_gone_before_it_is_asked_what_it_heard_is_started_again(
    host, platform
):
    """A backend that never spoke to the session (one just deployed) finds it
    running, and its runner goes (it let the idle session go) before it is
    asked whether anything was said to it: it is started again and told,
    rather than the answer failing on a runner that is not there."""
    hub, _ = host
    platform([{"text": "记住了。"}])
    launch = _launch(7)
    await _ask(SessionHost(hub), launch, "The password is PINEAPPLE.")

    fresh = SessionHost(hub)
    await fresh.start(launch.ref, launch.spec, launch.access)
    stop_all(hub.home)
    work = uuid.uuid4()
    await fresh.send(
        launch.ref,
        Prompt(
            work,
            "What was the password?",
            acting=question_credential(launch.user),
            preface="（之前的对话）",
        ),
        work_id=work,
    )

    assert hub.alive(launch.ref.state)


@pytest.mark.anyio
async def test_a_third_session_lets_the_least_recently_used_one_go(host, platform):
    hub, sessions = host
    platform([])
    first, second, third = _launch(7), _launch(7), _launch(7)
    elsewhere = _launch(8)

    await _ask(sessions, first, "一")
    await _ask(sessions, elsewhere, "别人的")
    await _ask(sessions, second, "二")
    await _ask(sessions, third, "三")

    def running(launch):
        return hub.alive(launch.ref.state)

    assert not running(first)
    assert running(second) and running(third)
    # Another person's sessions are theirs to count.
    assert running(elsewhere)


@pytest.mark.anyio
async def test_what_was_said_before_the_session_is_given_to_it_once(host, platform):
    hub, sessions = host
    fake = platform([])
    launch = _launch(7)
    earlier = "<之前的对话>\n用户：学习率设多少？\n芝士：0.1 起步。\n</之前的对话>"

    await _ask(sessions, launch, "那批大小呢？", earlier=earlier)
    await _ask(sessions, launch, "还有呢？", earlier=earlier)

    def users(request) -> list[str]:
        return [
            json.dumps(m["content"], ensure_ascii=False)
            for m in request["messages"]
            if m["role"] == "user"
        ]

    assert "0.1 起步" in users(fake.requests[0])[0]
    assert sum("0.1 起步" in text for text in users(fake.requests[1])) == 1


async def _first_word(sessions, launch, text) -> float:
    """Seconds from asking to the first piece of the answer."""
    started = time.monotonic()
    first = None
    async for event in _asking(sessions, launch, text):
        if isinstance(event, Words) and first is None:
            first = time.monotonic() - started
    assert first is not None
    return first


@pytest.mark.anyio
async def test_an_answer_starts_as_soon_as_the_model_does(host, platform, monkeypatch):
    """The first words reach the reader within a fraction of a second of the
    model producing them, on a running session: the read waits at the runner
    for them rather than polling for them. The model starts after a delay of
    its own so that what the reader adds on top is what is measured; the
    timings are printed."""
    hub, sessions = host
    first_token_s = 0.15
    monkeypatch.setattr(personal, "IDLE_EXIT_S", 0.25)
    platform([], first_token_s=first_token_s)
    launch = _launch(7)

    new = await _first_word(sessions, launch, "一")
    warm = [await _first_word(sessions, launch, "再一") for _ in range(2)]
    await _until(lambda: not hub.alive(launch.ref.state))
    resumed = await _first_word(sessions, launch, "回来")

    print(
        f"first word: new {new:.3f}s, warm {[round(w, 3) for w in warm]}s, "
        f"resumed after idle {resumed:.3f}s (the model starts after {first_token_s}s)"
    )
    assert min(warm) - first_token_s < 0.5


@pytest.mark.anyio
async def test_that_it_reasons_is_handed_on_and_what_it_reasons_is_not(host, platform):
    _, sessions = host
    seen = threading.Event()
    platform(
        [{"reasoning": ["先看提交要求"], "text": "先读说明。"}],
        rest_held_until=seen,
    )
    launch = _launch(9)

    events = []
    async for event in _asking(sessions, launch, "从哪里入手？"):
        events.append(event)
        if isinstance(event, Thinking):
            seen.set()

    assert any(isinstance(e, Thinking) for e in events)
    assert "先看提交要求" not in "".join(e.text for e in events if isinstance(e, Words))


@pytest.mark.anyio
async def test_it_reasons_as_hard_as_it_is_set_to(host, platform):
    _, sessions = host
    fake = platform([{"text": "好。"}])
    launch = Started(11)
    launch.spec = dataclasses.replace(
        launch.spec,
        model_settings=dataclasses.replace(launch.spec.model_settings, effort="high"),
    )

    events = await _ask(sessions, launch, "在吗")

    assert isinstance(events[-1], Answer)
    assert fake.requests[0].get("reasoning_effort") == "high"
