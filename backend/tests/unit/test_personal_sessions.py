"""A person's 芝士 on the session host: the pinned pi, started by the real launch.

The session host is this machine — the launch script runs here as the connector
would run it there, and reads go to the runner's socket the way the connector
relays them. The platform is a fake that answers the model as the gateway would
and serves the person's tools, recording who asked.

Rules held here:

* the session has the tools it is given and nothing of pi's own (no files, no
  commands), and thinks not at all;
* the model and the tools are reached with the person's credential, and nothing
  of a machine or a container is touched;
* an answer arrives as it is written, and ends with what was said;
* an idle session exits, and the next question finds its conversation again;
* a person keeps at most two sessions running, the least recently used one
  letting go when a third starts;
* what was said in a conversation before its session existed is given to the
  session once.
"""

import asyncio
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_personal_credential
from app.domain.agent.harness.pi import personal
from app.domain.agent.harness.pi.handless import (
    Answered,
    HandlessSessions,
    Looking,
    Said,
)
from app.domain.agent.harness.pi.personal import Launch
from tests.support.hang import HANG_S
from tests.support.session_host import DEVICE, Host, install_pi, stop_all

PROMPT = "你是芝士。只用给你的工具。"
TOOLS = [
    {
        "name": "my_tasks",
        "description": "我参与的题。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search_docs",
        "description": "检索文档。",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


class Platform:
    """The backend as the session reaches it: the model behind ``/llm/v1``,
    the person's tools behind ``/assistant/tools``."""

    def __init__(
        self,
        steps: list,
        *,
        first_token_s: float = 0.0,
        tools_path: str = "/assistant/tools",
        holding: threading.Event | None = None,
    ):
        self.steps = steps
        self.first_token_s = first_token_s
        # Given: an answer stops after its first piece until this is set.
        self.holding = holding
        self.requests: list[dict] = []
        self.model_auth: list[str] = []
        self.tool_calls: list[tuple[str, str, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.startswith(f"{tools_path}/"):
                    name = self.path.rsplit("/", 1)[1]
                    outer.tool_calls.append(
                        (name, self.headers.get("X-Cheese-Token", ""), body)
                    )
                    self._json(
                        {"code": 200, "data": {"text": f"{name} 的结果：一道题"}}
                    )
                    return
                outer.model_auth.append(self.headers.get("Authorization", ""))
                index = len(outer.requests)
                outer.requests.append(body)
                step = (
                    outer.steps[index] if index < len(outer.steps) else {"text": "好。"}
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                time.sleep(outer.first_token_s)
                held = outer.holding is None
                for chunk in _chunks(index, step):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                    if not held and chunk["choices"][0]["delta"].get("content"):
                        held = True
                        outer.holding.wait(HANG_S)
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
    if "tool" in step:
        call = {
            "index": 0,
            "id": f"call_{index}",
            "type": "function",
            "function": {
                "name": step["tool"],
                "arguments": json.dumps(step.get("arguments", {})),
            },
        }
        return [
            chunk({"role": "assistant", "content": None, "tool_calls": [call]}),
            chunk({}, "tool_calls", usage=usage),
        ]
    text = step["text"]
    pieces = [text[i : i + 3] for i in range(0, len(text), 3)]
    return [
        chunk({"role": "assistant", "content": ""}),
        *(chunk({"content": piece}) for piece in pieces),
        chunk({}, "stop", usage=usage),
    ]


@pytest.fixture
def host(tmp_path, monkeypatch):
    home = tmp_path / "host"
    install_pi(home)
    monkeypatch.setattr(settings, "agent_session_device_id", DEVICE)
    hub = Host(home)
    sessions = HandlessSessions(hub)
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


def _launch(user: int, conversation: uuid.UUID | None = None) -> Launch:
    conversation = conversation or uuid.uuid4()
    return Launch(
        user_id=user,
        conversation_id=conversation,
        system_prompt=PROMPT,
        tools_path="/assistant/tools",
        tools=TOOLS,
        token=mint_personal_credential(user_id=user, conversation_id=str(conversation)),
        model="fixture-model",
    )


async def _ask(sessions, launch, text, **options) -> list:
    return [
        event
        async for event in sessions.ask(
            launch, uuid.uuid4(), text, ceiling_s=120, **options
        )
    ]


async def _until(check, timeout: float = 60.0) -> None:
    async with asyncio.timeout(timeout):
        while not check():
            await asyncio.sleep(0.2)


@pytest.mark.anyio
async def test_a_session_has_its_tools_and_nothing_else_and_does_not_think(
    host, platform
):
    hub, sessions = host
    holding = threading.Event()
    fake = platform(
        [{"tool": "my_tasks"}, {"text": "你领了一道题：图像分类基线复现。"}],
        holding=holding,
    )
    launch = _launch(7)

    events = []
    async for event in sessions.ask(
        launch, uuid.uuid4(), "我领了哪些题？", ceiling_s=120
    ):
        events.append(event)
        if isinstance(event, Said):
            holding.set()

    said = [e.text for e in events if isinstance(e, Said)]
    assert "".join(said) == "你领了一道题：图像分类基线复现。"
    assert Looking("my_tasks") in events
    assert events[-1] == Answered("你领了一道题：图像分类基线复现。")
    # The answer is handed on as it is written: its first piece reached the
    # reader while the model held the rest back.
    assert said[0] == "你领了", said

    first = fake.requests[0]
    assert sorted(t["function"]["name"] for t in first["tools"]) == [
        "my_tasks",
        "search_docs",
    ]
    assert first["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in first
    assert first["max_tokens"] == personal.MAX_TOKENS
    system = first["messages"][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert system.startswith(PROMPT)
    assert (
        "Current working directory" not in system
        and "read" not in system.split(PROMPT)[1]
    )
    # Everything reaches the platform as the person, with their credential.
    assert set(fake.model_auth) == {f"Bearer {launch.token}"}
    assert [(name, token) for name, token, _ in fake.tool_calls] == [
        ("my_tasks", launch.token)
    ]
    # The tool's answer went back to the model.
    assert "my_tasks 的结果" in json.dumps(fake.requests[1], ensure_ascii=False)
    # Nothing but the launch ran on the host: no machine, no container.
    ran = [argv for argv in hub.execs if argv != ["cat", "/proc/meminfo"]]
    assert ran and all(argv == ["python3", "-"] for argv in ran)


@pytest.mark.anyio
async def test_an_idle_session_exits_and_comes_back_with_its_conversation(
    host, platform, monkeypatch
):
    hub, sessions = host
    monkeypatch.setattr(personal, "IDLE_EXIT_S", 1.0)
    fake = platform([{"text": "记住了。"}, {"text": "PINEAPPLE。"}])
    launch = _launch(7)
    state = personal.state_dir(7, launch.conversation_id)

    await _ask(sessions, launch, "The password is PINEAPPLE.")
    await _until(lambda: not hub.alive(state))
    launched = len(hub.execs)

    events = await _ask(sessions, launch, "What was the password?")

    assert events[-1] == Answered("PINEAPPLE。")
    asked = json.dumps(fake.requests[-1]["messages"], ensure_ascii=False)
    assert "The password is PINEAPPLE." in asked
    # It was started again, on the conversation it had.
    assert ["python3", "-"] in hub.execs[launched:]


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
        return hub.alive(personal.state_dir(launch.user_id, launch.conversation_id))

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
    async for event in sessions.ask(launch, uuid.uuid4(), text, ceiling_s=120):
        if isinstance(event, Said) and first is None:
            first = time.monotonic() - started
    assert first is not None
    return first


@pytest.mark.anyio
async def test_an_answer_starts_as_soon_as_the_model_does(host, platform, monkeypatch):
    """The first words reach the reader within a fraction of a second of the
    model producing them, on a running session: the read waits at the runner
    for them rather than polling for them. Measured with a model that takes
    as long to start as the gateway's does; the timings are printed."""
    hub, sessions = host
    first_token_s = 0.8
    monkeypatch.setattr(personal, "IDLE_EXIT_S", 1.0)
    platform([], first_token_s=first_token_s)
    launch = _launch(7)

    new = await _first_word(sessions, launch, "一")
    warm = [await _first_word(sessions, launch, "再一") for _ in range(3)]
    await _until(lambda: not hub.alive(personal.state_dir(7, launch.conversation_id)))
    resumed = await _first_word(sessions, launch, "回来")

    print(
        f"first word: new {new:.3f}s, warm {[round(w, 3) for w in warm]}s, "
        f"resumed after idle {resumed:.3f}s (the model starts after {first_token_s}s)"
    )
    assert min(warm) - first_token_s < 0.5
