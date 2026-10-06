"""A pi session's subagents, played on the pinned pi through the real runner.

pi has no subagents; the platform's extension gives the session ``Task``,
``SendMessage`` and ``TaskStop``, and the runner starts each subagent as a pi of
its own (``harness/pi/subagents.py``). Every test here drives the pinned build
with the platform extension against a scripted model endpoint and a stand-in for
the platform's admission, and reads back what the room reads: the runner's log,
through pi's translator.

Each promise is played: a subagent starts on the model it was given (or the
project's subagent default), its parent can tell it more while it runs, and its
parent can stop it and leave its siblings running — and it ends with its parent.
"""

import asyncio
import json
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.harness.pi.launch import arguments, extension, provider
from app.domain.agent.harness.pi.runner import Runner
from app.domain.agent.harness.pi.subscription import Subscription
from app.domain.agent.harness.prompt import PLATFORM_NOTICE
from app.domain.agent.service import (
    AgentMessage,
    AgentResult,
    AgentToolResult,
)
from tests.pinned_claude import pi_binary
from tests.support.completions_fixture import Completions
from tests.support.room_machine import room_machine

PARENT = "parent-model"


class Admission:
    """The platform's ``/llm/admission``, as a subagent's spawn asks it: the
    model named if it is one this project may run, the subagent default if none
    is named, and a refusal in the platform's words otherwise."""

    def __init__(self, *, default: str, allowed: set[str]):
        self.asked: list[dict] = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                named = self.headers.get("X-Cheese-Child-Model")
                fixture.asked.append(
                    {
                        "path": self.path,
                        "token": self.headers.get("Authorization"),
                        "subagent": self.headers.get("X-Cheese-Subagent"),
                        "model": named,
                    }
                )
                model = named or default
                data = (
                    {"allow": True, "supply": {"pool": "gateway", "model": model}}
                    if model in allowed
                    else {"allow": False, "reason": f"{model} 不在这个项目的模型目录里"}
                )
                body = json.dumps({"code": 200, "data": data}).encode()
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


def said(body: dict) -> str:
    """Everything the model was told in this request, as one string."""
    return json.dumps(body.get("messages") or [], ensure_ascii=False)


def last(body: dict) -> dict:
    return (body.get("messages") or [{}])[-1]


def text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content or ""


def spawned_id(body: dict) -> str | None:
    found = re.findall(r"sub-[0-9a-f]{12}", said(body))
    return found[-1] if found else None


class Session:
    def __init__(self, runner: Runner, model: Completions, admission: Admission):
        self.runner = runner
        self.model = model
        self.admission = admission

    async def send(self, text: str) -> None:
        await self.runner.dispatch(
            "send",
            {"input_id": str(uuid.uuid4()), "text": text, "work_id": str(uuid.uuid4())},
        )

    def events(self) -> list:
        assembler = Assembler("session-1", harness="pi")
        return [
            event for record in self.records() for event in assembler.accept(record)
        ]

    def records(self) -> list[dict]:
        rows, after = [], 0
        while page := self.runner.journal.read(after):
            rows += [row["record"] for row in page]
            after = page[-1]["sequence"]
        return rows

    def requests(self, model: str) -> list[dict]:
        return [body for body in self.model.requests if body.get("model") == model]

    async def until(self, check, timeout: float = 60.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not check():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"timed out; log: {self.records()!r}")
            await asyncio.sleep(0.1)

    def stops(self) -> list[dict]:
        """How each subagent that ended, ended: its description, status and
        the words it handed back."""
        return [
            {"id": agent.id, "description": agent.description, **agent.ended.result()}
            for agent in self.runner.children.started.values()
            if agent.ended.done()
        ]

    def results(self) -> list[AgentResult]:
        return [e for e in self.events() if isinstance(e, AgentResult)]


@asynccontextmanager
async def pi(tmp_path: Path, route, *, default: str = "child-default"):
    """The runner over the pinned pi with the platform extension; every model
    request, the session's and its subagents', is answered by ``route``."""
    model = Completions([route] * 200)
    admission = Admission(default=default, allowed={PARENT, default, "child-model"})
    config = tmp_path / "pi-config"
    config.mkdir()
    (config / "models.json").write_text(provider(model.url, PARENT))
    work = tmp_path / "work"
    work.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PI_CODING_AGENT_DIR": str(config),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        "CHEESE_API": admission.url,
        "CHEESE_TOKEN": "session-token",
    }
    runner = Runner(tmp_path / "state")
    with room_machine(tmp_path / "machine") as target:
        try:
            await runner.start(
                SessionStart(
                    system_prompt="FIXTURE", model=PARENT, agent_handle="cheese"
                ),
                binary=pi_binary(),
                cwd=str(work),
                env=env,
                args=arguments(PARENT),
                target=target,
                extension=extension(),
                notice=PLATFORM_NOTICE,
            )
            yield Session(runner, model, admission)
        finally:
            await runner.close()
            model.close()
            admission.close()


def child_says(body: dict) -> dict | None:
    """A subagent scripted by its prompt: ``SAY:<words>`` answers with them."""
    match = re.search(r"SAY:([^\"\\]+)", text_of(last(body)))
    return {"text": match.group(1)} if match else None


# --- starting one, on a model -------------------------------------------------


async def test_a_subagent_runs_the_model_it_was_given_and_reports_back(tmp_path):
    def route(body):
        if body["model"] != PARENT:
            return child_says(body) or {"text": "?"}
        if '"toolCallId"' not in said(body) and "tool_call_id" not in said(body):
            return {
                "tool": "Task",
                "arguments": {
                    "description": "查一下分页",
                    "prompt": "SAY:查到了",
                    "model": "child-model",
                },
            }
        return {"text": "收到"}

    async with pi(tmp_path, route) as session:
        await session.send("帮我查分页")
        await session.until(lambda: len(session.results()) == 1)

        # The platform was asked, with the session's credential, for exactly
        # the model the call named — and that is the model the subagent's
        # requests went out on.
        (asked,) = session.admission.asked
        assert asked["path"] == "/llm/admission"
        assert asked["subagent"] == "1"
        assert asked["model"] == "child-model"
        assert asked["token"] == "Bearer session-token"
        assert session.requests("child-model")
        assert not session.requests("child-default")

        events = session.events()
        assert [s["text"] for s in session.stops()] == ["查到了"]
        words = [e.text for e in events if isinstance(e, AgentMessage)]
        assert words == ["查到了", "收到"]
        (returned,) = [e for e in events if isinstance(e, AgentToolResult)]
        assert (returned.name, returned.text, returned.description) == (
            "Task",
            "查到了",
            "查一下分页",
        )
        # One turn, ended by the session and not by its subagent.
        assert session.results()[0].text == "收到"
        ends = [
            r
            for r in session.records()
            if Subscription.ends_turn(None, r, None)  # type: ignore[arg-type]
        ]
        assert [r["message"]["content"] for r in ends] == [
            [{"type": "text", "text": "收到"}]
        ]


async def test_a_subagent_given_no_model_runs_the_project_subagent_default(tmp_path):
    def route(body):
        if body["model"] != PARENT:
            return child_says(body) or {"text": "?"}
        if "tool_call_id" not in said(body):
            return {
                "tool": "Task",
                "arguments": {"description": "默认", "prompt": "SAY:用的默认"},
            }
        return {"text": "好"}

    async with pi(tmp_path, route, default="child-default") as session:
        await session.send("派一个")
        await session.until(lambda: len(session.results()) == 1)

        assert [a["model"] for a in session.admission.asked] == [None]
        assert session.requests("child-default")
        assert [s["text"] for s in session.stops()] == ["用的默认"]


async def test_a_model_the_platform_refuses_starts_nothing(tmp_path):
    def route(body):
        if body["model"] != PARENT:
            return {"text": "不该有我"}
        if "tool_call_id" not in said(body):
            return {
                "tool": "Task",
                "arguments": {
                    "description": "越档",
                    "prompt": "SAY:x",
                    "model": "somebody-elses-model",
                },
            }
        return {"text": "好"}

    async with pi(tmp_path, route) as session:
        await session.send("派一个")
        await session.until(lambda: len(session.results()) == 1)

        assert [body["model"] for body in session.model.requests] == [PARENT, PARENT]
        assert not session.runner.children.started
        # The model was told why, in the platform's words.
        told = text_of(last(session.requests(PARENT)[-1]))
        assert "不在这个项目的模型目录里" in told


# --- the parent's hand --------------------------------------------------------


async def test_a_parent_tells_a_running_subagent_more_and_it_does_that(tmp_path):
    """Retasking is read off the work it does next, not off a field."""

    def route(body):
        if body["model"] != PARENT:
            if "只改后端" in said(body):
                return {"text": "改成只动后端了"}
            if "tool_call_id" not in said(body):
                return {"tool": "bash", "arguments": {"command": "sleep 4"}}
            return {"text": "按原计划做完了"}
        told = said(body)
        if "tool_call_id" not in told:
            return {
                "tool": "Task",
                "arguments": {
                    "description": "改分页",
                    "prompt": "先改前端",
                    "run_in_background": True,
                },
            }
        if "已发给" not in told and "已结束" not in told:
            return {
                "tool": "SendMessage",
                "arguments": {"to": spawned_id(body), "message": "先只改后端"},
            }
        return {"text": "好"}

    async with pi(tmp_path, route) as session:
        await session.send("派一个去改分页")
        await session.until(lambda: len(session.stops()) == 1, timeout=90)

        (stop,) = session.stops()
        assert stop["text"] == "改成只动后端了"
        # It read the new instruction as a message of its own thread.
        child = [r for r in session.records() if r.get("subagent")]
        assert any("先只改后端" in json.dumps(r, ensure_ascii=False) for r in child)
        # And the session was told when its background subagent ended.
        await session.until(
            lambda: any("交回的结论" in said(b) for b in session.requests(PARENT))
        )


async def test_stopping_one_subagent_leaves_its_sibling_running(tmp_path):
    def route(body):
        if body["model"] != PARENT:
            first = text_of((body.get("messages") or [{}, {}])[1])
            if "tool_call_id" not in said(body):
                return {"tool": "bash", "arguments": {"command": "sleep 3"}}
            return {"text": "写完用例" if "写用例" in first else "查完分页"}
        told = said(body)
        if told.count("已在后台起了分身") == 0:
            return {
                "tool": "Task",
                "arguments": {
                    "description": "查分页",
                    "prompt": "查分页",
                    "run_in_background": True,
                },
            }
        if told.count("已在后台起了分身") == 1:
            return {
                "tool": "Task",
                "arguments": {
                    "description": "写用例",
                    "prompt": "写用例",
                    "run_in_background": True,
                },
            }
        if "已停下" not in told:
            stopped = re.findall(r"sub-[0-9a-f]{12}", told)[0]
            return {"tool": "TaskStop", "arguments": {"task_id": stopped}}
        return {"text": "好"}

    async with pi(tmp_path, route) as session:
        await session.send("派两个")
        await session.until(lambda: len(session.stops()) == 2, timeout=90)

        by_work = {s["description"]: s for s in session.stops()}
        status = {work: s["status"] for work, s in by_work.items()}
        assert status == {"查分页": "stopped", "写用例": "completed"}
        assert by_work["写用例"]["text"] == "写完用例"
        # Stopped before it said anything, it hands back nothing — not the
        # abort pi records for the call it cut short.
        assert by_work["查分页"]["text"] == ""
        # A stopped subagent takes no more instructions.
        with pytest.raises(ValueError, match="已经停了"):
            await session.runner.children.send(by_work["查分页"]["id"], "再改一版")


async def test_subagents_end_with_the_session_that_started_them(tmp_path):
    def route(body):
        if body["model"] != PARENT:
            return {"tool": "bash", "arguments": {"command": "sleep 60"}}
        if "tool_call_id" not in said(body):
            return {
                "tool": "Task",
                "arguments": {
                    "description": "慢活",
                    "prompt": "慢慢做",
                    "run_in_background": True,
                },
            }
        return {"text": "好"}

    async with pi(tmp_path, route) as session:
        await session.send("派一个慢的")
        await session.until(lambda: session.requests("child-default"))
        (agent,) = session.runner.children.started.values()
        process = agent.process
        assert process is not None and process.returncode is None

    assert process.returncode is not None


async def test_interrupting_the_session_stops_its_subagents(tmp_path):
    def route(body):
        if body["model"] != PARENT:
            return {"tool": "bash", "arguments": {"command": "sleep 60"}}
        if "tool_call_id" not in said(body):
            return {
                "tool": "Task",
                "arguments": {
                    "description": "慢活",
                    "prompt": "慢慢做",
                },
            }
        return {"text": "停了"}

    async with pi(tmp_path, route) as session:
        await session.send("派一个慢的，等它")
        await session.until(lambda: session.requests("child-default"))

        assert (await session.runner.dispatch("abort", {}))["aborted"] is True

        (stop,) = session.stops()
        assert stop["status"] == "stopped"
        (agent,) = session.runner.children.started.values()
        assert agent.status == "stopped"
        assert agent.process is not None and agent.process.returncode is not None
