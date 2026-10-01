"""pi on the session host, its hands on the room's machine (#1106).

Played on the pinned pi through the real runner, against a scripted model and a
real executor (`remote_execution/runtime.py`) standing for the room's machine,
in a directory of its own. What a person in the room can rely on:

- what pi's tools do happens in the checkout on the machine — a file the shell
  writes, a file `write` creates, a file `read` reads — and nothing of it lands
  where pi itself runs;
- a turn that touches no file takes no machine: the platform is never asked
  for one, and a turn that does touch one asks for it then.
"""

import asyncio
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent.executor_transport import DEFERRED_WORKSPACE
from app.domain.agent.harness import Opening
from app.domain.agent.harness.pi.launch import arguments, extension, provider
from app.domain.agent.harness.pi.runner import Runner
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from tests.pinned_claude import pi_binary
from tests.support.completions_fixture import Completions
from tests.support.room_machine import room_machine

MODEL = "fixture-model"


@pytest.fixture
def machine(tmp_path):
    """A running executor over a checkout of its own: the room's machine."""
    with room_machine(tmp_path / "machine") as target:
        yield target


class Platform:
    """The platform's `work-lease` route: it records every time a machine is
    asked for, and answers with `target`."""

    def __init__(self, target: dict):
        self.asked: list[str] = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fixture.asked.append(self.path)
                body = json.dumps(
                    {"code": 200, "data": {"target": target, "token": "machine"}}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@asynccontextmanager
async def pi(tmp_path: Path, target: dict, route, *, api: str = "http://unused"):
    model = Completions([route] * 50)
    config = tmp_path / "pi-config"
    config.mkdir()
    (config / "models.json").write_text(provider(model.url, MODEL))
    here = tmp_path / "session-host"
    here.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PI_CODING_AGENT_DIR": str(config),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        "CHEESE_API": api,
        "CHEESE_TOKEN": "session-token",
    }
    runner = Runner(tmp_path / "state")
    try:
        await runner.start(
            Opening(system_prompt="FIXTURE", model=MODEL, agent_handle="cheese"),
            binary=pi_binary(),
            cwd=str(here),
            env=env,
            args=arguments(MODEL),
            target=target,
            extension=extension(),
            notice=PLATFORM_NOTICE,
        )
        yield runner, model, here
    finally:
        await runner.close()
        model.close()


async def turn(runner: Runner, model: Completions, text: str, requests: int) -> None:
    await runner.dispatch(
        "send",
        {"input_id": str(uuid.uuid4()), "text": text, "work_id": str(uuid.uuid4())},
    )
    deadline = asyncio.get_running_loop().time() + 60
    while len(model.requests) < requests or runner.working:
        assert asyncio.get_running_loop().time() < deadline, model.requests
        await asyncio.sleep(0.1)


def told(body: dict) -> str:
    """What the model was handed back by its last tool call."""
    return json.dumps(body.get("messages") or [], ensure_ascii=False)


def script(*steps):
    """The model's turn: each step is a tool call, then plain text."""

    def route(body):
        calls = told(body).count('"role": "tool"')
        if calls < len(steps):
            name, arguments = steps[calls]
            return {"tool": name, "arguments": arguments}
        return {"text": "done"}

    return route


async def test_the_shell_runs_in_the_checkout_on_the_machine(tmp_path, machine):
    route = script(("bash", {"command": "echo made > made.txt && pwd"}))
    async with pi(tmp_path, machine, route) as (runner, model, here):
        await turn(runner, model, "做一个文件", 2)

        checkout = Path(machine["workspace"])
        assert (checkout / "made.txt").read_text() == "made\n"
        assert not (here / "made.txt").exists()
        # What the shell printed is what the model reads.
        assert str(checkout) in told(model.requests[1])


async def test_files_are_written_and_read_on_the_machine(tmp_path, machine):
    checkout = Path(machine["workspace"])
    (checkout / "notes.md").write_text("ON THE MACHINE\n")
    route = script(
        ("write", {"path": "sub/plan.md", "content": "# 计划\n"}),
        ("read", {"path": "notes.md"}),
    )
    async with pi(tmp_path, machine, route) as (runner, model, here):
        await turn(runner, model, "写一份计划，再看看笔记", 3)

        assert (checkout / "sub" / "plan.md").read_text() == "# 计划\n"
        assert not (here / "sub").exists()
        assert "ON THE MACHINE" in told(model.requests[2])


async def test_a_question_takes_no_machine_and_work_takes_one(
    tmp_path, machine, monkeypatch
):
    leased = {**machine, "kind": "local", "generation": 1}
    platform = Platform(leased)
    # The runner reaches the platform with the room's own credentials.
    monkeypatch.setenv("CHEESE_API", platform.url)
    monkeypatch.setenv("CHEESE_TOKEN", "session-token")
    placeholder = {
        "kind": "deferred",
        "workspace": DEFERRED_WORKSPACE,
        "lease_path": "/topics/room/sessions/one/work-lease",
        "mcp_servers": [],
    }
    asked_for_work = {"yes": False}

    def route(body):
        if "动手" not in told(body):
            return {"text": "答案是四"}
        if not asked_for_work["yes"]:
            asked_for_work["yes"] = True
            return {
                "tool": "bash",
                "arguments": {"command": f"echo hi > {DEFERRED_WORKSPACE}/hi.txt"},
            }
        return {"text": "done"}

    try:
        async with pi(tmp_path, placeholder, route, api=platform.url) as (
            runner,
            model,
            _,
        ):
            await turn(runner, model, "二加二是几？", 1)
            assert platform.asked == []

            await turn(runner, model, "那就动手吧", 3)
            assert platform.asked, "the work never asked for a machine"
            # The placeholder the session sees is the machine's checkout there.
            assert (Path(machine["workspace"]) / "hi.txt").read_text() == "hi\n"
    finally:
        platform.close()


async def test_the_first_reach_of_the_machine_reads_the_repository_first(
    tmp_path, machine, monkeypatch
):
    """A session started before its machine has not read what the repository
    says about itself. The operation that brings the machine is not run; what
    the repository says is its answer, and the same operation asked again runs."""
    (Path(machine["workspace"]) / "CLAUDE.md").write_text("# 本仓约定\n\n用 pnpm。\n")
    platform = Platform({**machine, "kind": "local", "generation": 1})
    monkeypatch.setenv("CHEESE_API", platform.url)
    monkeypatch.setenv("CHEESE_TOKEN", "session-token")
    placeholder = {
        "kind": "deferred",
        "workspace": DEFERRED_WORKSPACE,
        "lease_path": "/topics/room/sessions/one/work-lease",
        "mcp_servers": [],
    }
    command = f"echo once >> {DEFERRED_WORKSPACE}/runs.txt"
    route = script(("bash", {"command": command}), ("bash", {"command": command}))
    try:
        async with pi(tmp_path, placeholder, route, api=platform.url) as (
            runner,
            model,
            _,
        ):
            await turn(runner, model, "动手", 3)
            assert "用 pnpm。" in told(model.requests[1])
            assert (Path(machine["workspace"]) / "runs.txt").read_text() == "once\n"
    finally:
        platform.close()
