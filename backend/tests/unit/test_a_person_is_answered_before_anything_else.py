"""A person who writes to the room is answered there before the session does
anything else — on every harness, by the same rule.

The platform tells the session so in its prompt, and a model sometimes goes on
with the tool it had planned anyway: the person saw nothing for minutes. So
the session's own tool path refuses every tool but `chat_send` / `cheese_ask`
until it has replied. Each case below runs the real, pinned harness — Claude
Code through its runner and the remote-execution function hook, Codex through
its runner archive with its tools on a real executor, pi through its runner
and the platform extension — against a scripted model, and checks only what a
room would see: which commands ran on the machine, what reached the room, and
what the model was told.

A harness with no binary here is a failure, not a skip: this file exists so no
harness is left out of the rule.
"""

import asyncio
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.bundle import build as claude_archive
from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.remote_execution import client as central
from app.domain.agent.harness.claude_code.remote_execution import release, runtime
from app.domain.agent.harness.codex.bundle import build as codex_archive
from app.domain.agent.harness.codex.host import configure as start_codex
from app.domain.agent.harness.driven.runner import (
    REPLY_INSIST,
    REPLY_OWED,
    SessionStart,
    socket_path,
)
from app.domain.agent.harness.pi.launch import arguments, extension, provider
from app.domain.agent.harness.pi.runner import Runner as PiRunner
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from tests.pinned_claude import claude_binary, codex_binary, pi_binary
from tests.support import executor_release
from tests.support.completions_fixture import Completions
from tests.support.responses_fixture import Responses
from tests.support.room_machine import room_machine

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = BACKEND.parent / "scripts/remote_execution"
CHEESE = BACKEND / "sandbox/cheese"
TOPIC = "fixture-topic"
SUBAGENT_TASK = "SUBAGENT_TASK: run the commands"
#: A piece of the refusal no prompt the platform writes otherwise carries.
REFUSED = REPLY_OWED.split(":")[0]
#: …and of the reminder a turn that tried to end unanswered is held to.
HELD = REPLY_INSIST.split(";")[0]
#: What the person wrote, as the room's record holds it.
IN_THE_ROOM = "the build broke on the arm runner, see the log above"


def shell(command: str) -> tuple[str, str]:
    return ("shell", command)


def publish(text: str) -> tuple[str, str]:
    return ("publish", text)


def read_room() -> tuple[str, str]:
    """The model reads the room's messages."""
    return ("read", "")


def say(text: str) -> tuple[str, str]:
    """An answer the model writes where only it can see, and stops."""
    return ("say", text)


class Backend:
    """The platform as a session reaches it: the room's message route, and
    the connector's relay to the room machine's executor."""

    def __init__(self, executor_state: Path | None = None):
        self.published: list[str] = []
        backend = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def answer(self, result):
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                # The room's record, as its history route pages it.
                assert self.path.startswith(f"/topics/{TOPIC}/history"), self.path
                message = {
                    "id": "m1",
                    "created_at": "2026-10-04T00:00:00Z",
                    "author": "someone",
                    "author_type": "user",
                    "kind": "message",
                    "content": IN_THE_ROOM,
                }
                self.answer({"data": {"data": [message], "has_more": False}})

            def do_POST(self):
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    or b"{}"
                )
                if self.path == "/execution" and executor_state is None:
                    # Claude Code's own Bash runs here, with no machine behind
                    # it; the one thing its session asks the machine first is
                    # whether the project's deny rules refuse the call, and
                    # this project has none.
                    result = {"args": body["params"]["args"]}
                elif self.path == "/execution":
                    result = runtime.request(
                        executor_state, body["method"], body.get("params")
                    )
                else:
                    if self.path == f"/topics/{TOPIC}/messages":
                        backend.published.append(body["content"])
                    result = {"data": {"id": str(uuid.uuid4()), **body}}
                self.answer(result)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def room_env(self) -> dict[str, str]:
        return {
            "CHEESE_API": self.url,
            "CHEESE_TOKEN": "fixture",
            "CHEESE_TOPIC": TOPIC,
            "CHEESE_PROJECT": "fixture-project",
            "NO_PROXY": "127.0.0.1,localhost",
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def _exited(pid: int) -> bool:
    """Whether the process is gone. `configure` starts the Codex runner from
    this process, so the runner is our child and stays a zombie, which
    `kill(pid, 0)` still finds, until it is reaped here."""
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _ask(path: str, method: str, params: dict, timeout: float = 30.0) -> dict:
    """One call on a runner's socket, waiting for the socket to appear."""
    deadline = time.monotonic() + timeout
    while True:
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(timeout)
        try:
            client.connect(path)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            client.close()
            assert time.monotonic() < deadline, f"no runner listens at {path}"
            time.sleep(0.1)
    with client:
        client.sendall(
            json.dumps({"method": method, "params": params}).encode() + b"\n"
        )
        answer = json.loads(client.makefile("rb").readline())
    assert "error" not in answer, answer
    return answer["result"]


class Session:
    """What every harness looks like from here: a runner to talk to, the
    machine its commands run on, and what the model was sent."""

    #: The runner's verb for words said to a working session.
    steer_verb = "steer"

    def __init__(self, machine: Path, requests: list, call):
        self.machine = machine
        self.requests = requests
        self.call = call
        self.work = str(uuid.uuid4())

    async def send(self, text: str, *, owes_reply: bool) -> None:
        await self.call(
            "send",
            {
                "input_id": str(uuid.uuid4()),
                "work_id": self.work,
                "text": text,
                **({"owes_reply": True} if owes_reply else {}),
            },
        )

    async def steer(self, text: str, *, owes_reply: bool) -> None:
        await self.call(
            self.steer_verb,
            {
                "input_id": str(uuid.uuid4()),
                "work_id": self.work,
                "text": text,
                **({"owes_reply": True} if owes_reply else {}),
            },
        )

    def working(self, status: dict) -> bool:
        return bool(status.get("working"))

    async def finished(self, requests: int, timeout: float = 90.0) -> None:
        """Until the model has been asked ``requests`` times and the turn is over."""
        deadline = time.monotonic() + timeout
        while len(self.requests) < requests or self.working(
            await self.call("ping", {})
        ):
            assert time.monotonic() < deadline, (
                f"{len(self.requests)} model requests, wanted {requests}"
            )
            await asyncio.sleep(0.2)

    async def ran(self, marker: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while not (self.machine / marker).exists():
            assert time.monotonic() < deadline, f"{marker} never ran"
            await asyncio.sleep(0.1)

    def told(self, request: int) -> str:
        """Everything the model was sent in its ``request``-th request."""
        return json.dumps(self.requests[request], ensure_ascii=False)


# --- Claude Code ---------------------------------------------------------------


@asynccontextmanager
async def claude_code(tmp_path: Path, steps: list):
    """The runner archive over the pinned build, launched with the
    remote-execution function hook and the native MCP server a room gets."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        import headless_contract as contract
    finally:
        sys.path.remove(str(SCRIPTS))
    home = tmp_path / "home"
    config = home / ".claude"
    machine = home / "workspace"
    config.mkdir(parents=True)
    machine.mkdir()
    (tmp_path / "tmp").mkdir()
    backend = Backend()
    translated = {
        "shell": lambda value: {
            "name": "Bash",
            "input": {"command": value, "description": value},
        },
        "publish": lambda value: {
            "name": "mcp__native__chat_send",
            "input": {"content": value},
        },
        "read": lambda value: {"name": "mcp__native__cheese_chat_list", "input": {}},
        # Words that reach no one: the fixture answers with plain text.
        "say": lambda value: None,
        # Only Claude Code's model starts a subagent here; the value is the
        # commands the subagent's own model runs, and it ends after them.
        "subagent": lambda value: {
            "name": "Agent",
            "input": {
                "description": "helper",
                "subagent_type": "general-purpose",
                "run_in_background": False,
                "prompt": SUBAGENT_TASK,
            },
        },
    }
    main, sub = [], []
    for kind, value in steps:
        main.append(translated[kind](value))
        if kind == "subagent":
            sub += [translated["shell"](command) for command in value]

    def act(body):
        # A subagent's conversation opens with the task the session gave it;
        # past its script, either conversation just ends.
        opening = json.dumps(body["messages"][0], ensure_ascii=False)
        script = sub if SUBAGENT_TASK in opening else main
        return script.pop(0) if script else None

    class Script:
        def __len__(self):
            return sys.maxsize

        def __getitem__(self, _):
            return act

    actions = Script()
    model = contract.Server(("127.0.0.1", 0), contract.Handler)
    model.state = {"dir": tmp_path, "actions": actions, "requests": []}
    serving = threading.Thread(target=model.serve_forever, daemon=True)
    serving.start()
    target = {
        "kind": "device",
        "url": backend.url + "/execution",
        "workspace": str(machine),
        "central_hooks": {},
        "central_config": str(config),
        "central_tmp": str(tmp_path / "tmp"),
        "session_workspace": str(machine),
        "mcp_servers": [],
    }
    (tmp_path / "central.json").write_text(json.dumps(target))
    plugin = central.write_plugin(
        tmp_path / "plugin",
        target,
        release.platform_tool_names(CHEESE.read_text()),
        tmp_path / "central.json",
    )
    native = {
        "mcpServers": {
            "native": {
                "type": "stdio",
                "command": sys.executable,
                "args": [central.__file__, "transport", str(tmp_path / "central.json")],
            }
        }
    }
    command = shlex.join(
        [
            claude_binary(),
            "--model",
            contract.MODEL,
            "--setting-sources",
            "user",
            "--plugin-dir",
            str(plugin),
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(native),
            *LAUNCH_ARGS,
        ]
    )
    state = tmp_path / "state"
    artifact = tmp_path / "runner.pyz"
    artifact.write_bytes(claude_archive())
    log = (tmp_path / "runner.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", str(artifact), "--state", str(state)],
        cwd=machine,
        env={
            **contract.fixture_env(home, tmp_path, model.server_port),
            **backend.room_env(),
            "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1",
            "CHEESE_CLAUDE_COMMAND": command,
            "CHEESE_AUTHOR": "cheese",
        },
        stdout=subprocess.DEVNULL,
        stderr=log,
    )

    async def call(method, params):
        return await asyncio.to_thread(_ask, socket_path(state), method, params)

    session = Session(machine, model.state["requests"], call)
    session.backend = backend
    try:
        yield session
    finally:
        process.terminate()
        try:
            process.wait(20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
        model.shutdown()
        model.server_close()
        backend.close()


# --- Codex ---------------------------------------------------------------------


class CodexSession(Session):
    # The runner turns `send` into `turn/steer` while a turn is open.
    steer_verb = "send"

    def working(self, status: dict) -> bool:
        return bool(status.get("turn_id"))


@asynccontextmanager
async def codex(tmp_path: Path, steps: list):
    """The Codex runner archive, its tools on a real executor over the project."""
    home = tmp_path / "executor-home"
    helper = executor_release.install(home / ".cheese")
    executor_state = home / ".cheese/executor"
    machine = tmp_path / "project"
    machine.mkdir()
    subprocess.run(
        [
            sys.executable,
            str(helper),
            "start",
            "--state",
            str(executor_state),
        ],
        input=json.dumps(
            {
                "workspace": str(machine),
                "claude": claude_binary(),
                "env": {},
                "mcp_servers": {},
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    backend = model = started = None
    # Everything after the start is inside: a step that fails before the
    # session is up still leaves an executor to stop.
    try:
        backend = Backend(executor_state)
        model = Responses(
            [
                {"tool": "Bash", "arguments": {"command": value}}
                if kind == "shell"
                else {"text": value}
                if kind == "say"
                else {"tool": "cheese_chat_list", "arguments": {}}
                if kind == "read"
                else {"tool": "chat_send", "arguments": {"content": value}}
                for kind, value in steps
            ]
        )
        workspace = tmp_path / "codex-workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
        state = tmp_path / "runner"
        # Where the launch places the archive before it calls ``configure``.
        artifact = tmp_path / "codex-runner.pyz"
        artifact.write_bytes(codex_archive())
        started = await asyncio.to_thread(
            start_codex,
            {
                "state": str(state),
                "config": {
                    "execution_target": {
                        "kind": "device",
                        "url": backend.url + "/execution",
                        "workspace": str(machine),
                    },
                    "opening": {"system_prompt": "FIXTURE"},
                    "binary": codex_binary(),
                    "cwd": str(workspace),
                },
                "codex_config": model.config(),
                "artifact": str(artifact),
                "env": backend.room_env(),
            },
        )

        async def call(method, params):
            return await asyncio.to_thread(_ask, socket_path(state), method, params)

        session = CodexSession(machine, model.requests, call)
        session.backend = backend
        yield session
    finally:
        if started is not None:
            os.kill(started["pid"], 15)
            for _ in range(100):
                if _exited(started["pid"]):
                    break
                time.sleep(0.1)
        subprocess.run(
            [
                sys.executable,
                str(helper),
                "stop",
                "--state",
                str(executor_state),
            ],
            capture_output=True,
            timeout=30,
        )
        if model is not None:
            model.close()
        if backend is not None:
            backend.close()


# --- pi ------------------------------------------------------------------------


@asynccontextmanager
async def pi(tmp_path: Path, steps: list):
    """The pi runner over the pinned build, with the platform extension."""
    backend = Backend()
    model = Completions(
        [
            {"tool": "bash", "arguments": {"command": value}}
            if kind == "shell"
            else {"text": value}
            if kind == "say"
            else {"tool": "cheese_chat_list", "arguments": {}}
            if kind == "read"
            else {"tool": "chat_send", "arguments": {"content": value}}
            for kind, value in steps
        ]
    )
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "cheese").symlink_to(CHEESE)
    config = tmp_path / "pi-config"
    config.mkdir()
    (config / "models.json").write_text(provider(model.url, "fixture-model"))
    machine = tmp_path / "work"
    env = {
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PI_CODING_AGENT_DIR": str(config),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        **backend.room_env(),
    }
    # The runner runs the platform's tools in its own process, as on the host.
    saved = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    runner = PiRunner(tmp_path / "state")
    here = tmp_path / "session-host"
    here.mkdir()
    with room_machine(
        tmp_path / "machine", env={"PATH": env["PATH"]}, checkout=machine
    ) as target:
        try:
            await runner.start(
                SessionStart(
                    system_prompt="FIXTURE",
                    model="fixture-model",
                    agent_handle="cheese",
                ),
                binary=pi_binary(),
                cwd=str(here),
                env=env,
                args=arguments("fixture-model"),
                target=target,
                extension=extension(),
                notice=PLATFORM_NOTICE,
            )
            session = Session(machine, model.requests, runner.dispatch)
            session.backend = backend
            yield session
        finally:
            await runner.close()
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            model.close()
            backend.close()


HARNESSES = [claude_code, codex, pi]
every_harness = pytest.mark.parametrize(
    "harness", HARNESSES, ids=lambda harness: harness.__name__
)


# --- the rule ------------------------------------------------------------------


@pytest.mark.anyio
@every_harness
async def test_a_person_is_answered_before_the_session_does_anything_else(
    tmp_path, harness
):
    steps = [shell("touch FIRST"), publish("on it"), shell("touch AFTER")]
    async with harness(tmp_path, steps) as session:
        await session.send("[someone]: please fix the build", owes_reply=True)
        await session.finished(len(steps) + 1)

        assert not (session.machine / "FIRST").exists()
        assert REFUSED in session.told(1)
        assert session.backend.published == ["on it"]
        assert (session.machine / "AFTER").exists()


@pytest.mark.anyio
@every_harness
async def test_the_session_may_read_the_room_before_it_answers(tmp_path, harness):
    """What the person wrote is part of answering them: a session that has not
    seen it, or was pointed at messages above, reads the room first — and
    reading is not an answer, so the work still waits for the reply."""
    steps = [
        read_room(),
        shell("touch FIRST"),
        publish("the arm runner, on it"),
        shell("touch AFTER"),
    ]
    async with harness(tmp_path, steps) as session:
        await session.send("[someone]: <@cheese>", owes_reply=True)
        await session.finished(len(steps) + 1)

        assert REFUSED not in session.told(1)
        assert IN_THE_ROOM in session.told(1)
        assert REFUSED in session.told(2)
        assert not (session.machine / "FIRST").exists()
        assert session.backend.published == ["the arm runner, on it"]
        assert (session.machine / "AFTER").exists()


@pytest.mark.anyio
@every_harness
async def test_a_turn_nobody_asked_for_owes_nobody_an_answer(tmp_path, harness):
    steps = [shell("touch FIRST")]
    async with harness(tmp_path, steps) as session:
        await session.send(f"{PLATFORM_NOTICE}\nrun the patrol", owes_reply=False)
        await session.finished(len(steps) + 1)

        assert (session.machine / "FIRST").exists()
        assert REFUSED not in session.told(1)
        assert session.backend.published == []


@pytest.mark.anyio
@every_harness
async def test_a_person_who_writes_mid_turn_is_answered_before_the_next_tool(
    tmp_path, harness
):
    """…and without waiting for the command it was running: that goes on in
    the background, and finishes there."""
    # The slow command holds until the test lets it go, so "it was still
    # running when the person was answered" does not rest on a race with a
    # timer, and the test does not wait out a timer to see it finish.
    steps = [
        shell("touch STARTED; while [ ! -e RELEASE ]; do sleep 0.05; done; touch SLOW"),
        shell("touch NEXT"),
        publish("stopping"),
        shell("touch AFTER"),
    ]
    async with harness(tmp_path, steps) as session:
        await session.send(f"{PLATFORM_NOTICE}\nrun the patrol", owes_reply=False)
        await session.ran("STARTED")
        spoke = time.monotonic()
        await session.steer("[someone]: stop, that device is offline", owes_reply=True)
        await session.ran("AFTER", timeout=15)
        answered = time.monotonic() - spoke

        assert session.backend.published == ["stopping"]
        assert answered < 15
        assert not (session.machine / "SLOW").exists()
        assert not (session.machine / "NEXT").exists()
        assert REFUSED not in session.told(1)
        assert REFUSED in session.told(2)
        (session.machine / "RELEASE").touch()
        await session.ran("SLOW", timeout=30)


@pytest.mark.anyio
@every_harness
async def test_a_platform_notice_mid_turn_holds_nothing_back(tmp_path, harness):
    # The command holds until the notice is in, as the mid-turn case above.
    steps = [
        shell("touch STARTED; while [ ! -e RELEASE ]; do sleep 0.05; done"),
        shell("touch NEXT"),
    ]
    async with harness(tmp_path, steps) as session:
        await session.send(f"{PLATFORM_NOTICE}\nrun the patrol", owes_reply=False)
        await session.ran("STARTED")
        await session.steer(f"{PLATFORM_NOTICE}\nthe doc changed", owes_reply=False)
        (session.machine / "RELEASE").touch()
        await session.finished(len(steps) + 1)

        assert (session.machine / "NEXT").exists()
        assert session.backend.published == []


def subagent(*commands: str) -> tuple[str, tuple[str, ...]]:
    return ("subagent", commands)


@pytest.mark.anyio
async def test_a_subagent_reports_to_its_session_and_is_never_held_back(tmp_path):
    """Claude Code is the harness whose sessions start subagents here. What
    they do is reported to the session that started them, which is the one
    that owes the room its answer."""
    steps = [
        subagent("touch STARTED; sleep 3", "touch CHILD"),
        shell("touch NEXT"),
        publish("stopping"),
    ]
    async with claude_code(tmp_path, steps) as session:
        await session.send(f"{PLATFORM_NOTICE}\nrun the patrol", owes_reply=False)
        await session.ran("STARTED")
        await session.steer("[someone]: stop, that device is offline", owes_reply=True)
        await session.ran("CHILD", timeout=30)
        await session.finished(7)

        assert (session.machine / "CHILD").exists()
        assert not (session.machine / "NEXT").exists()
        assert session.backend.published == ["stopping"]


@pytest.mark.anyio
@every_harness
async def test_a_turn_cannot_end_with_the_person_unanswered(tmp_path, harness):
    """A model that answers only where it alone can see, and stops, is held to
    answering in the room once more — the person always hears back."""
    steps = [say("fixed it"), publish("fixed it, the build is green")]
    async with harness(tmp_path, steps) as session:
        await session.send("[someone]: please fix the build", owes_reply=True)
        await session.finished(len(steps) + 1)

        assert HELD not in session.told(0)
        assert HELD in session.told(1)
        assert session.backend.published == ["fixed it, the build is green"]


@pytest.mark.anyio
@every_harness
async def test_a_session_held_once_that_still_does_not_answer_is_let_go(
    tmp_path, harness
):
    steps = [say("no"), say("still no")]
    async with harness(tmp_path, steps) as session:
        await session.send("[someone]: please fix the build", owes_reply=True)
        await session.finished(len(steps))
        # A session held again would be held as its turn ended, before
        # `finished` saw it idle; this only gives a late one time to show.
        await asyncio.sleep(1)

        assert len(session.requests) == len(steps)
        assert session.backend.published == []
        assert not session.working(await session.call("ping", {}))


@pytest.mark.anyio
@every_harness
async def test_a_turn_nobody_asked_for_ends_when_it_ends(tmp_path, harness):
    steps = [say("patrol done")]
    async with harness(tmp_path, steps) as session:
        await session.send(f"{PLATFORM_NOTICE}\nrun the patrol", owes_reply=False)
        await session.finished(len(steps))
        await asyncio.sleep(3)

        assert len(session.requests) == len(steps)
        assert session.backend.published == []
