"""What the Claude Code runner needs headless stream-json mode to keep doing.

The runner starts the build with `LAUNCH_ARGS` (`claude_code/cli.py`): `-p`
with stream-json on both pipes, every input echoed back once it is read, and
bypassPermissions with AskUserQuestion and the platform-managed tools taken
away, so nothing on stdin ever waits for a person. It holds stdin for the life
of the session: user messages, messages sent mid-turn and controls all go in
there, and the timeline and turn ends come back out of stdout. None of that
protocol is published — the SDKs speak it, but no document pins it — so this
script is the contract. Each check drives the given build with exactly those
arguments against the deterministic Messages API in `model_fixture.py` and
asserts only what the build does on the wire or on disk.

Point it at the pinned build to gate a merge, and at the newest published build
to learn that the next upgrade breaks the driver before the upgrade is what we
are debugging.

The fixture answers each model request from the latest user message: a text
block `DO:{"name": ..., "input": ...}` becomes that tool call, anything else a
plain end_turn. So a subagent, a notification turn or a mid-turn message cannot
shift a positional script.

Usage:
    python3 headless_contract.py --claude <binary> [--output <receipts dir>]
"""

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from model_fixture import Handler, Server  # noqa: E402

# The runner's launch arguments, read from the file the runner reads them from
# (a leaf module with no imports, so this runs without the backend installed).
_spec = importlib.util.spec_from_file_location(
    "launch_args",
    HERE.parents[1] / "backend/app/domain/agent/harness/claude_code/cli.py",
)
_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cli)
DRIVER = _cli.LAUNCH_ARGS
STREAM = [
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
]
MODEL = "claude-sonnet-4-5"


def do(name, **arguments):
    return "DO:" + json.dumps({"name": name, "input": arguments})


def directive(body):
    messages = body.get("messages", [])
    if not messages or messages[-1]["role"] != "user":
        return None
    content = messages[-1]["content"]
    for block in (
        [{"type": "text", "text": content}] if isinstance(content, str) else content
    ):
        text = block.get("text", "") if block.get("type") == "text" else ""
        start = text.find("DO:")
        if start >= 0:
            return json.JSONDecoder().raw_decode(text[start + 3 :])[0]
    return None


class Directives:
    """The fixture's action list: every request is answered by `directive`."""

    def __len__(self):
        return sys.maxsize

    def __getitem__(self, _):
        return directive


class Refusing(Handler):
    """The Messages API refusing the credential, as a revoked key does."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.state["requests"].append(self.path)
        data = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key",
                },
            }
        ).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def blocks(message):
    content = (message.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def last_tool_results(request):
    content = request["messages"][-1]["content"]
    if isinstance(content, str):
        return []
    return [block for block in content if block.get("type") == "tool_result"]


def results_since(session, mark):
    """The tool_result blocks written to stdout since `mark`."""
    return [
        block
        for event in session.events[mark:]
        if event.get("type") == "user"
        for block in blocks(event)
        if block.get("type") == "tool_result"
    ]


def ran_in(path, marker):
    """Whether a transcript file holds a tool_result whose output has `marker`."""
    for line in path.read_text().splitlines():
        for block in blocks(json.loads(line)):
            if block.get("type") == "tool_result" and marker in text_of(block):
                return True
    return False


def text_of(result):
    content = result.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content)
    return str(content)


def workspace(path):
    """A git workspace with one committed file and one uncommitted change."""
    git = [
        "git",
        "-c",
        "user.name=contract",
        "-c",
        "user.email=contract@example.invalid",
    ]
    (path / "target.txt").write_text("TARGET_CONTENT\n")
    subprocess.run([*git, "init", "-q"], cwd=path, check=True)
    subprocess.run([*git, "add", "."], cwd=path, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=path, check=True)
    (path / "target.txt").write_text("TARGET_CONTENT\nCHANGED\n")


def isolated_flags():
    mcp = {
        "mcpServers": {
            "fixture": {
                "command": sys.executable,
                "args": [str(HERE / "custom_mcp.py")],
            }
        }
    }
    return [
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp),
        "--no-chrome",
    ]


def fixture_env(home, root, port):
    """An environment that reaches only the fixture and only the isolated HOME."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_CODE_TMPDIR": str(root / "tmp"),
        "SHELL": "/bin/bash",
        "LANG": "C.UTF-8",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
        "NO_PROXY": "127.0.0.1,localhost",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }


class Session:
    """One headless Claude Code process in an isolated HOME, driven over stdio."""

    def __init__(
        self,
        binary,
        root,
        name,
        args,
        settings=None,
        refuse=False,
        env=None,
        home=None,
        launch=None,
        port=0,
    ):
        self.name = name
        self.root = root / name
        # A session given `home` shares it — and so the config dir, the
        # workspace and the transcripts — with whoever made it first.
        self.home = Path(home) if home else self.root / "home"
        self.config = self.home / ".claude"
        # Under the isolated HOME: the build looks for CLAUDE.md and .claude/
        # in every directory above its cwd.
        self.workspace = Path(launch["cwd"]) if launch else self.home / "workspace"
        self.fixture = self.root / "fixture"
        for path in (self.config, self.workspace, self.fixture, self.root / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        if not launch and not (self.workspace / ".git").exists():
            workspace(self.workspace)
        if settings is not None:
            (self.config / "settings.json").write_text(json.dumps(settings, indent=2))
        # A given port lets two sessions run one after the other against the
        # same model address.
        self.server = Server(("127.0.0.1", port), Refusing if refuse else Handler)
        self.server.state = {
            "dir": self.fixture,
            "actions": Directives(),
            "requests": [],
        }
        self.serving = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.serving.start()
        # `args` carries the stream flags: the runner's own (DRIVER) or STREAM.
        if launch:
            # The remote-execution launch already carries its own settings
            # source, plugin, MCP config and disallowed tools.
            self.argv = [*launch["command"], "--model", MODEL, *args]
        else:
            self.argv = [binary, "--model", MODEL, *isolated_flags(), *args]
        environment = {
            **fixture_env(self.home, self.root, self.server.server_port),
            **(launch["env"] if launch else {}),
            **(env or {}),
        }
        self.started = time.monotonic()
        self.events = []
        # stdout bytes read up to and including each event, so a turn's share
        # of the journal can be measured between two event indexes.
        self.offsets = []
        self.changed = threading.Condition()
        self.transcript = (self.root / "transcript.jsonl").open("w")
        self.process = subprocess.Popen(
            self.argv,
            cwd=self.workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=(self.root / "stderr.log").open("w"),
            text=True,
            start_new_session=True,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.sequence = 0

    def _record(self, direction, message):
        self.transcript.write(
            json.dumps(
                {
                    "t": round(time.monotonic() - self.started, 2),
                    "dir": direction,
                    "msg": message,
                }
            )
            + "\n"
        )
        self.transcript.flush()

    def _read(self):
        read = 0
        for line in self.process.stdout:
            read += len(line.encode())
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                message = {"type": "_unparsed", "line": line}
            if message.get("type") != "stream_event":
                self._record("out", message)
            with self.changed:
                self.events.append(message)
                self.offsets.append(read)
                self.changed.notify_all()
        with self.changed:
            self.events.append({"type": "_eof", "returncode": self.process.wait()})
            self.offsets.append(read)
            self.changed.notify_all()
        self._record("eof", {"returncode": self.process.returncode})

    def send(self, message):
        self._record("in", message)
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def user(self, text):
        mark = len(self.events)
        self.send(
            {
                "type": "user",
                "message": {"role": "user", "content": text},
                "parent_tool_use_id": None,
                "session_id": "",
            }
        )
        return mark

    def wait(self, predicate, timeout=30, start=0):
        """The index and message of the first event from `start` that matches."""
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                for index in range(start, len(self.events)):
                    if predicate(self.events[index]):
                        return index, self.events[index]
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, None
                self.changed.wait(left)

    def control(self, request, timeout=20):
        self.sequence += 1
        identifier = f"contract_{self.sequence}"
        mark = len(self.events)
        self.send(
            {"type": "control_request", "request_id": identifier, "request": request}
        )
        _, answer = self.wait(
            lambda event: (
                event.get("type") == "control_response"
                and event["response"].get("request_id") == identifier
            ),
            timeout,
            mark,
        )
        return answer["response"] if answer else {"subtype": "timeout"}

    def answer(self, request_id, response):
        self.send(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": response,
                },
            }
        )

    def requests(self):
        return self.server.state["requests"]

    def close_stdin(self):
        self._record("in", {"_close_stdin": True})
        self.process.stdin.close()

    def stop(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait()
        self.reader.join(5)
        self.server.shutdown()
        self.server.server_close()
        self.transcript.close()


def is_(kind, subtype=None, **fields):
    def match(event):
        return (
            event.get("type") == kind
            and (subtype is None or event.get("subtype") == subtype)
            and all(event.get(key) == value for key, value in fields.items())
        )

    return match


def task_patched(task_id, key, value):
    return lambda event: (
        is_("system", "task_updated")(event)
        and event.get("task_id") == task_id
        and (event.get("patch") or {}).get(key) == value
    )


def session_dir(session):
    """The project transcript directory for the session's first `init`."""
    _, init = session.wait(is_("system", "init"), 1)
    matches = list((session.config / "projects").glob(f"*/{init['session_id']}"))
    return matches[0] if matches else None


# Each scenario yields (check, holds, what was observed).


def controls(binary, root):
    """Every control the room uses, sent between turns."""
    session = Session(binary, root, "controls", DRIVER)
    try:
        answer = session.control({"subtype": "initialize"})
        yield (
            "initialize is answered",
            answer.get("subtype") == "success",
            json.dumps(answer)[:160],
        )
        session.user("hello")
        session.wait(is_("result"), 60)
        target = session.workspace / "target.txt"
        expected = {
            "interrupt": ({"subtype": "interrupt"}, None),
            "background_tasks": ({"subtype": "background_tasks"}, None),
            "stop_task": ({"subtype": "stop_task", "task_id": "no-such-task"}, None),
            "set_model": ({"subtype": "set_model", "model": "opus"}, None),
            "set_permission_mode": (
                {"subtype": "set_permission_mode", "mode": "acceptEdits"},
                lambda body: body.get("mode") == "acceptEdits",
            ),
            "set_max_thinking_tokens": (
                {"subtype": "set_max_thinking_tokens", "max_thinking_tokens": 2048},
                None,
            ),
            "apply_flag_settings": (
                {
                    "subtype": "apply_flag_settings",
                    "settings": {"alwaysThinkingEnabled": False},
                },
                None,
            ),
            "rename_session": (
                {"subtype": "rename_session", "title": "Contract"},
                None,
            ),
            "file_suggestions": (
                {"subtype": "file_suggestions", "query": ""},
                lambda body: {"path": "target.txt"} in body.get("suggestions", []),
            ),
            "read_file": (
                {"subtype": "read_file", "path": str(target)},
                lambda body: body.get("contents") == target.read_text(),
            ),
            "get_workspace_diff": (
                {"subtype": "get_workspace_diff"},
                lambda body: body["diff"]["stats"]["filesCount"] == 1,
            ),
            "get_context_usage": (
                {"subtype": "get_context_usage", "detail": "summary"},
                None,
            ),
            "get_usage": ({"subtype": "get_usage", "skip_behaviors": True}, None),
            "mcp_status": (
                {"subtype": "mcp_status"},
                lambda body: (
                    [server["status"] for server in body["mcpServers"]] == ["connected"]
                ),
            ),
            "mcp_reconnect": (
                {"subtype": "mcp_reconnect", "serverName": "fixture"},
                None,
            ),
        }
        for subtype, (request, check) in expected.items():
            answer = session.control(request)
            holds = answer.get("subtype") == "success"
            if holds and check:
                try:
                    holds = bool(check(answer.get("response") or {}))
                except (KeyError, TypeError):
                    holds = False
            yield (f"control {subtype} succeeds", holds, json.dumps(answer)[:200])
        answer = session.control(
            {"subtype": "set_permission_mode", "mode": "bypassPermissions"}
        )
        yield (
            "set_permission_mode can return to bypassPermissions",
            (answer.get("response") or {}).get("mode") == "bypassPermissions",
            json.dumps(answer)[:160],
        )
        answer = session.control({"subtype": "set_color", "color": "blue"})
        yield (
            "set_color is still unsupported (we gave it up; drop this check if it returns)",
            answer.get("subtype") == "error"
            and "Unsupported control request subtype" in answer.get("error", ""),
            json.dumps(answer)[:160],
        )
        mark = session.user("after set_model")
        session.wait(is_("result"), 60, mark)
        models = [request.get("model") for request in session.requests()]
        yield (
            "set_model changes the model the next turn asks for",
            len(models) >= 2 and models[0] == MODEL and "opus" in (models[-1] or ""),
            json.dumps(models),
        )
    finally:
        session.stop()


def background(binary, root, kind):
    """Moving a running foreground tool to the background, as the room's button does."""
    session = Session(binary, root, f"background-{kind}", DRIVER)
    try:
        if kind == "agent":
            session.user(
                do(
                    "Agent",
                    description="sleeper",
                    subagent_type="general-purpose",
                    run_in_background=False,
                    prompt=do(
                        "Bash",
                        command="sleep 12; echo SUB_DONE",
                        description="sub sleep",
                        timeout=120000,
                    ),
                )
            )
            task_type = "local_agent"
        else:
            session.user(
                do(
                    "Bash",
                    command="sleep 12; echo SLEEP_DONE",
                    description="long sleep",
                    timeout=120000,
                )
            )
            task_type = "local_bash"
        _, started = session.wait(
            is_("system", "task_started", task_type=task_type), 60
        )
        if not started:
            yield (f"a foreground {kind} starts as a task", False, "no task_started")
            return
        yield (
            f"a foreground {kind} starts as a task that is not backgrounded",
            started.get("is_backgrounded") is False,
            json.dumps(started)[:200],
        )
        task, tool = started["task_id"], started["tool_use_id"]
        time.sleep(2)
        by_id = kind != "bash-all"
        if by_id:
            wrong = session.control(
                {"subtype": "background_tasks", "tool_use_id": "toolu_wrong"}
            )
            yield (
                "background_tasks for an unknown tool_use_id backgrounds nothing",
                (wrong.get("response") or {}).get("backgrounded") is False,
                json.dumps(wrong)[:160],
            )
        mark = len(session.events)
        request = {
            "subtype": "background_tasks",
            **({"tool_use_id": tool} if by_id else {}),
        }
        answer = session.control(request)
        yield (
            f"background_tasks {'by tool_use_id' if by_id else 'without an id'} is accepted",
            answer.get("subtype") == "success"
            and (
                not by_id or (answer.get("response") or {}).get("backgrounded") is True
            ),
            json.dumps(answer)[:160],
        )
        moved, _ = session.wait(task_patched(task, "is_backgrounded", True), 10, mark)
        changed, _ = session.wait(is_("system", "background_tasks_changed"), 10, mark)
        yield (
            "the task is reported moved: task_updated is_backgrounded and background_tasks_changed",
            moved is not None and changed is not None,
            f"task_updated at {moved}, background_tasks_changed at {changed}",
        )
        ended, result = session.wait(is_("result"), 10, mark)
        finished, _ = session.wait(task_patched(task, "status", "completed"), 30, mark)
        yield (
            "the turn ends (result) while the backgrounded task is still running",
            ended is not None and finished is not None and ended < finished,
            f"result at {ended}, task completed at {finished}",
        )
        noted, notification = session.wait(
            is_("system", "task_notification", task_id=task), 30, mark
        )
        yield (
            "the finished task is announced with task_notification for its tool_use_id",
            notification is not None
            and notification.get("status") == "completed"
            and notification.get("tool_use_id") == tool,
            json.dumps(notification)[:200],
        )
        follow, _ = session.wait(is_("result"), 30, (noted or 0) + 1)
        yield (
            "the notification starts a follow-up turn with its own result",
            noted is not None and follow is not None,
            f"notification at {noted}, follow-up result at {follow}",
        )
    finally:
        session.stop()


def subagents(binary, root):
    """Where a subagent's own messages can be read, and how they are tied to the parent."""
    session = Session(binary, root, "subagents", DRIVER)
    try:
        session.user(
            do(
                "Agent",
                description="child",
                subagent_type="general-purpose",
                prompt=do("Bash", command="echo CHILD_RAN", description="child echo"),
            )
        )
        _, started = session.wait(
            is_("system", "task_started", task_type="local_agent"), 60
        )
        yield (
            "an Agent call without run_in_background starts backgrounded",
            bool(started) and started.get("is_backgrounded") is True,
            json.dumps(started)[:200],
        )
        if not started:
            return
        agent = started["task_id"]
        session.wait(is_("system", "task_notification", task_id=agent), 60)
        session.wait(is_("result"), 30, len(session.events) - 1)
        launched = [
            event
            for event in session.events
            if event.get("type") == "user"
            and (event.get("tool_use_result") or {}).get("agentId")
        ]
        yield (
            "the parent's Agent tool result carries the agentId",
            any(event["tool_use_result"]["agentId"] == agent for event in launched),
            json.dumps([event["tool_use_result"].get("agentId") for event in launched]),
        )
        child_lines = [
            event
            for event in session.events
            if event.get("type") in ("assistant", "user")
            and event.get("parent_tool_use_id") == started["tool_use_id"]
        ]
        yield (
            "the child's messages also appear on stdout, tagged with the parent's tool_use_id",
            any("CHILD_RAN" in json.dumps(event) for event in child_lines),
            f"{len(child_lines)} child message(s) on stdout",
        )
        directory = session_dir(session)
        transcript = (
            directory / "subagents" / f"agent-{agent}.jsonl" if directory else None
        )
        found = transcript is not None and transcript.exists()
        yield (
            "the child's transcript is subagents/agent-<agentId>.jsonl beside a .meta.json",
            found
            and transcript.with_name(f"agent-{agent}.meta.json").exists()
            and "CHILD_RAN" in transcript.read_text(),
            str(transcript) if transcript else "no session directory",
        )
        script = (
            "export const meta = { name: 'contract', description: 'contract probe' }\n"
            "await agent('Reply done', { label: 'one' })\n"
        )
        mark = session.user(do("Workflow", script=script))
        _, workflow = session.wait(
            is_("system", "task_started", task_type="local_workflow"), 60, mark
        )
        if workflow is None:
            _, workflow = session.wait(is_("system", "task_started"), 1, mark)
        if workflow:
            session.wait(
                is_("system", "task_notification", task_id=workflow["task_id"]),
                60,
                mark,
            )
        files = (
            sorted(
                str(path.relative_to(directory))
                for path in (directory / "subagents" / "workflows").glob("wf_*/**/*")
                if path.is_file()
            )
            if directory
            else []
        )
        yield (
            "a workflow's agents are under subagents/workflows/wf_*/",
            any(
                Path(name).name.startswith("agent-") and name.endswith(".jsonl")
                for name in files
            ),
            json.dumps(
                {"task": workflow and workflow.get("task_type"), "files": files}
            )[:300],
        )
    finally:
        session.stop()


def steering(binary, root):
    """A message the user sends while a tool is running."""
    session = Session(binary, root, "steering", DRIVER)
    try:
        session.user(
            do("Bash", command="sleep 6; echo FIRST_DONE", description="sleep")
        )
        started, _ = session.wait(is_("system", "task_started"), 60)
        time.sleep(1)
        sent = session.user("STEER_MARKER also note this")
        ended, _ = session.wait(is_("result"), 60, sent)
        delivered = [
            text_of(result)
            for request in session.requests()
            for result in last_tool_results(request)
            if "STEER_MARKER" in text_of(result)
        ]
        yield (
            "a mid-tool message reaches the model inside the next tool_result",
            len(delivered) == 1
            and "The user sent a new message while you were working" in delivered[0],
            delivered[0][:200] if delivered else "not delivered",
        )
        tool_result, _ = session.wait(
            lambda event: (
                event.get("type") == "user"
                and not event.get("isReplay")
                and any(block.get("type") == "tool_result" for block in blocks(event))
            ),
            1,
            sent,
        )
        replay, _ = session.wait(
            lambda event: (
                event.get("type") == "user"
                and event.get("isReplay")
                and "STEER_MARKER" in json.dumps(event.get("message"))
            ),
            1,
            sent,
        )
        yield (
            "--replay-user-messages echoes it only once delivered, after the tool_result",
            None not in (tool_result, replay, ended) and tool_result < replay < ended,
            f"tool_result at {tool_result}, replay at {replay}, result at {ended}",
        )
    finally:
        session.stop()


def stopping(binary, root):
    """Stopping a running tool, interrupting a turn, stopping a background task."""
    session = Session(binary, root, "stopping", DRIVER)
    try:
        session.control({"subtype": "initialize"})
        mark = session.user(
            do("Bash", command="sleep 30; echo NOT_STOPPED", description="to stop")
        )
        _, started = session.wait(is_("system", "task_started"), 60, mark)
        time.sleep(1)
        answer = session.control(
            {"subtype": "stop_task", "task_id": started["task_id"]}
        )
        _, result = session.wait(is_("result"), 20, mark)
        killed = [
            block
            for event in session.events[mark:]
            if event.get("type") == "user"
            for block in blocks(event)
            if block.get("type") == "tool_result"
        ]
        yield (
            "stop_task on a foreground tool fails that tool and the turn goes on",
            answer.get("subtype") == "success"
            and bool(killed)
            and killed[-1].get("is_error") is True
            and bool(result)
            and result.get("is_error") is False,
            json.dumps(
                {"tool_result": killed[-1:], "result": result and result.get("subtype")}
            )[:240],
        )
        mark = session.user(
            do(
                "Bash",
                command="sleep 30; echo NOT_INTERRUPTED",
                description="to interrupt",
            )
        )
        session.wait(is_("system", "task_started"), 60, mark)
        time.sleep(1)
        answer = session.control({"subtype": "interrupt"})
        _, result = session.wait(is_("result"), 20, mark)
        yield (
            "interrupt ends the turn with an error result",
            answer.get("subtype") == "success"
            and bool(result)
            and result.get("is_error") is True
            and result.get("subtype") == "error_during_execution",
            json.dumps(
                result and {key: result.get(key) for key in ("subtype", "is_error")}
            ),
        )
        mark = session.user(
            do(
                "Bash",
                command="sleep 30; echo NOT_STOPPED",
                description="background",
                run_in_background=True,
            )
        )
        _, started = session.wait(is_("system", "task_started"), 60, mark)
        session.wait(is_("result"), 20, mark)
        answer = session.control(
            {"subtype": "stop_task", "task_id": started["task_id"]}
        )
        _, notification = session.wait(
            is_("system", "task_notification", task_id=started["task_id"]), 10, mark
        )
        yield (
            "stop_task on a background task reports it stopped",
            answer.get("subtype") == "success"
            and bool(notification)
            and notification.get("status") == "stopped",
            json.dumps(notification)[:200],
        )
    finally:
        session.stop()


def hooks(binary, root):
    """Hooks the platform keeps for context injection still fire in -p mode."""
    names = [
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SessionEnd",
    ]
    log = root / "hooks" / "fired.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": {
            name: [
                {
                    **({"matcher": "*"} if "ToolUse" in name else {}),
                    "hooks": [
                        {"type": "command", "command": f"echo {name} >> '{log}'"}
                    ],
                }
            ]
            for name in names
        }
    }
    session = Session(binary, root, "hooks", DRIVER, settings=settings)
    try:
        session.user(do("Bash", command="echo HOOKED", description="echo"))
        session.wait(is_("result"), 60)
        session.close_stdin()
        session.process.wait(30)
        fired = log.read_text().split() if log.exists() else []
        yield (
            "hooks in $CLAUDE_CONFIG_DIR/settings.json fire under --setting-sources user",
            set(names) <= set(fired),
            f"fired {fired}, missing {sorted(set(names) - set(fired))}",
        )
    finally:
        session.stop()


def skipping(binary, root):
    """Why the driver cannot use --dangerously-skip-permissions on its own."""
    session = Session(
        binary, root, "skipping", [*STREAM, "--dangerously-skip-permissions"]
    )
    try:
        session.user("hello")
        session.wait(is_("result"), 60)
        tools = (
            [tool["name"] for tool in session.requests()[0].get("tools", [])]
            if session.requests()
            else []
        )
        yield (
            "without a prompt tool, AskUserQuestion is not offered to the model",
            bool(tools) and "AskUserQuestion" not in tools,
            f"{len(tools)} tools offered",
        )
    finally:
        session.stop()


def lifetime(binary, root):
    """The process outlives its turns and is ended by closing stdin."""
    session = Session(binary, root, "lifetime", DRIVER)
    marker = f"contract-lifetime-{os.getpid()}-{time.monotonic_ns()}"
    try:
        session.user(
            do(
                "Bash",
                command=f"sleep 40; echo {marker}",
                description="background",
                run_in_background=True,
            )
        )
        ended, result = session.wait(is_("result"), 60)
        yield (
            "a completed turn is one result with is_error false",
            bool(result)
            and result.get("subtype") == "success"
            and result.get("is_error") is False,
            json.dumps(
                result and {key: result.get(key) for key in ("subtype", "is_error")}
            ),
        )
        time.sleep(2)
        usage = session.control({"subtype": "get_usage", "skip_behaviors": True})
        mark = session.user("second turn")
        second, _ = session.wait(is_("result"), 60, mark)
        running = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True
        ).stdout.split()
        yield (
            "after result the process keeps reading stdin and the background task keeps running",
            session.process.poll() is None
            and usage.get("subtype") == "success"
            and second is not None
            and bool(running),
            f"alive={session.process.poll() is None}, control={usage.get('subtype')}, "
            f"second result at {second}, {len(running)} background process(es)",
        )
        session.close_stdin()
        try:
            code = session.process.wait(30)
        except subprocess.TimeoutExpired:
            code = None
        time.sleep(1)
        left = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True
        ).stdout.split()
        yield (
            "closing stdin exits the process and kills its background tasks",
            code == 0 and not left,
            f"exit {code}, {len(left)} background process(es) left",
        )
    finally:
        session.stop()
        for pid in subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True
        ).stdout.split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass


def refused(binary, root):
    """How a refused credential surfaces, so the driver can fail the turn on it.

    The build retries a 401 like any API error (ten times by default, several
    minutes of backoff); one retry keeps the check short without changing what
    each message looks like.
    """
    session = Session(
        binary,
        root,
        "refused",
        DRIVER,
        refuse=True,
        env={"CLAUDE_CODE_MAX_RETRIES": "1"},
    )
    try:
        session.user("hello")
        _, retry = session.wait(is_("system", "api_retry"), 30)
        yield (
            "each retry of a refused request is reported as api_retry with its status",
            bool(retry)
            and retry.get("error_status") == 401
            and retry.get("error") == "authentication_failed",
            json.dumps(
                retry
                and {
                    key: retry.get(key)
                    for key in ("attempt", "max_retries", "error_status", "error")
                }
            ),
        )
        _, result = session.wait(is_("result"), 60)
        _, assistant = session.wait(is_("assistant"), 1)
        yield (
            "the turn ends with is_error true, though its subtype is still success",
            bool(result)
            and result.get("is_error") is True
            and result.get("subtype") == "success"
            and result.get("terminal_reason") == "api_error",
            json.dumps(
                result
                and {
                    key: result.get(key)
                    for key in ("subtype", "is_error", "terminal_reason", "result")
                }
            )[:300],
        )
        yield (
            "the assistant message before it carries the error kind",
            bool(assistant) and assistant.get("error") == "authentication_failed",
            json.dumps(
                assistant
                and {"error": assistant.get("error"), "content": blocks(assistant)}
            )[:300],
        )
        yield (
            "the process survives a refused turn",
            session.process.poll() is None,
            f"returncode {session.process.poll()}",
        )
    finally:
        session.stop()


class Interactive:
    """The interactive CLI in a tmux pane, on its own server, sharing a HOME.

    `tmux -L` keeps it off the default server: a default server on a
    developer's machine may carry plugins (tmux-resurrect) that act on any
    session started there.
    """

    def __init__(self, binary, root, name, home, args):
        self.root = root / name
        self.fixture = self.root / "fixture"
        self.fixture.mkdir(parents=True, exist_ok=True)
        self.home = Path(home)
        self.workspace = self.home / "workspace"
        self.tmux = ["tmux", "-L", f"headless-contract-{os.getpid()}-{name}"]
        config = self.home / ".claude"
        gate = config / ".claude.json"
        gates = json.loads(gate.read_text()) if gate.exists() else {}
        # The first-run dialogs an operator would click through once.
        gates.update(hasCompletedOnboarding=True, autoUpdates=False)
        # Keyed by the resolved path: on macOS the temp dir is under a symlink.
        gates.setdefault("projects", {}).setdefault(
            str(self.workspace.resolve()), {}
        ).update(hasTrustDialogAccepted=True, hasCompletedProjectOnboarding=True)
        gate.write_text(json.dumps(gates))
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.state = {
            "dir": self.fixture,
            "actions": Directives(),
            "requests": [],
        }
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        environment = fixture_env(self.home, self.root, self.server.server_port)
        (self.root / "tmp").mkdir(exist_ok=True)
        # An API key in the environment makes the interactive CLI ask whether
        # to use it; a bearer token does not.
        environment.pop("ANTHROPIC_API_KEY")
        environment.update(
            ANTHROPIC_AUTH_TOKEN="fixture-not-a-real-key", TERM="xterm-256color"
        )
        argv = [binary, "--model", MODEL, *isolated_flags(), *args]
        command = shlex.join(
            ["env", "-i", *(f"{k}={v}" for k, v in environment.items()), *argv]
        )
        subprocess.run(
            [
                *self.tmux,
                "new-session",
                "-d",
                "-s",
                "cli",
                "-x",
                "160",
                "-y",
                "50",
                "-c",
                str(self.workspace),
                command,
            ],
            check=True,
            capture_output=True,
        )
        self.socket = subprocess.run(
            [*self.tmux, "display-message", "-p", "#{socket_path}"],
            capture_output=True,
            text=True,
        ).stdout.strip()

    def screen(self):
        return subprocess.run(
            [*self.tmux, "capture-pane", "-p", "-t", "cli", "-S", "-200"],
            capture_output=True,
            text=True,
        ).stdout

    def say(self, text, timeout=60):
        """Type a prompt once the input box is up; True once the model is asked it."""
        deadline = time.monotonic() + timeout
        while "shortcuts" not in self.screen() and time.monotonic() < deadline:
            time.sleep(0.5)
        subprocess.run([*self.tmux, "send-keys", "-t", "cli", "-l", text], check=True)
        time.sleep(0.5)
        subprocess.run([*self.tmux, "send-keys", "-t", "cli", "Enter"], check=True)
        while time.monotonic() < deadline:
            if any(
                text in json.dumps(request.get("messages"))
                for request in self.server.state["requests"]
            ):
                return True
            time.sleep(0.2)
        return False

    def stop(self):
        (self.root / "screen.txt").write_text(self.screen())
        subprocess.run([*self.tmux, "kill-server"], capture_output=True, timeout=10)
        # kill-server leaves the socket file behind.
        if self.socket:
            Path(self.socket).unlink(missing_ok=True)
        self.server.shutdown()
        self.server.server_close()


def transcript_path(home, session_id):
    matches = list((Path(home) / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def settled(path, text, timeout=30):
    """Wait until the transcript at `path` records `text`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path and path.exists() and text in path.read_text():
            return True
        time.sleep(0.2)
    return False


def resuming(binary, root):
    """A session started in one mode continues in the other, in the same HOME and cwd."""
    home = root / "resuming-home"
    for path in (home / "workspace", home / ".claude"):
        path.mkdir(parents=True)
    workspace(home / "workspace")

    first = str(uuid.uuid4())
    cli = Interactive(binary, root, "resuming-cli", home, ["--session-id", first])
    try:
        asked = cli.say("INTERACTIVE_MARKER first turn")
        written = settled(transcript_path(home, first), "ACCEPTANCE_DONE")
    finally:
        cli.stop()
    yield (
        "the interactive CLI writes its transcript under the shared config dir",
        asked and written,
        f"asked={asked}, transcript={transcript_path(home, first)}",
    )
    session = Session(
        binary, root, "resuming-headless", [*DRIVER, "--resume", first], home=home
    )
    try:
        mark = session.user("HEADLESS_AFTER second turn")
        _, init = session.wait(is_("system", "init"), 30, mark)
        _, result = session.wait(is_("result"), 60, mark)
        request = next(
            (
                r
                for r in session.requests()
                if "HEADLESS_AFTER" in json.dumps(r.get("messages"))
            ),
            None,
        )
        history = json.dumps(request.get("messages")) if request else ""
        yield (
            "-p --resume continues an interactive session: the model sees its history",
            bool(result)
            and result.get("is_error") is False
            and "INTERACTIVE_MARKER" in history,
            f"init session {init and init.get('session_id')}, history carries the interactive turn: "
            f"{'INTERACTIVE_MARKER' in history}",
        )
        yield (
            "-p --resume keeps the session id and appends to the same transcript",
            bool(init)
            and init.get("session_id") == first
            and settled(transcript_path(home, first), "HEADLESS_AFTER", 5),
            f"resumed {first}, init reports {init and init.get('session_id')}",
        )
        session.close_stdin()
        session.process.wait(30)
    finally:
        session.stop()

    second = str(uuid.uuid4())
    session = Session(
        binary, root, "resuming-origin", [*DRIVER, "--session-id", second], home=home
    )
    try:
        mark = session.user("HEADLESS_MARKER first turn")
        session.wait(is_("result"), 60, mark)
        session.close_stdin()
        session.process.wait(30)
    finally:
        session.stop()
    cli = Interactive(binary, root, "resuming-cli-after", home, ["--resume", second])
    try:
        asked = cli.say("INTERACTIVE_AFTER second turn")
        request = next(
            (
                r
                for r in cli.server.state["requests"]
                if "INTERACTIVE_AFTER" in json.dumps(r.get("messages"))
            ),
            None,
        )
        history = json.dumps(request.get("messages")) if request else ""
        appended = settled(transcript_path(home, second), "INTERACTIVE_AFTER")
    finally:
        cli.stop()
    yield (
        "interactive --resume continues a -p session: the model sees its history",
        asked and "HEADLESS_MARKER" in history and appended,
        f"asked={asked}, history carries the -p turn: {'HEADLESS_MARKER' in history}, appended={appended}",
    )


def projectdirs(binary, root):
    """Where the build keeps a session's transcript is named for its working
    directory, and a room's session names that directory itself when it moves
    its conversation to the path a relaunch starts it at
    (`remote_execution/client.py` `project_dir`). A path long enough to be cut
    and hashed, with a character outside the Basic Multilingual Plane in it."""
    spec = importlib.util.spec_from_file_location(
        "execution_client",
        HERE.parents[1]
        / "backend/app/domain/agent/harness/claude_code/remote_execution/client.py",
    )
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    home = root / "projectdirs-home"
    (home / ".claude").mkdir(parents=True)
    for name, cwd in (
        ("short", root / "project dir"),
        ("long", root / "长 ✓ 🧀 project" / ("a" * 120) / ("b" * 120)),
    ):
        cwd.mkdir(parents=True)
        workspace(cwd)
        session = Session(
            binary,
            root,
            f"projectdirs-{name}",
            STREAM,
            home=home,
            launch={"command": [binary], "env": {}, "cwd": str(cwd)},
        )
        try:
            mark = session.user("PROJECT_DIR_MARKER")
            _, init = session.wait(is_("system", "init"), 30, mark)
            session.wait(is_("result"), 60, mark)
        finally:
            session.stop()
        found = init and transcript_path(home, init["session_id"])
        # The build names the directory it runs in as it resolves it.
        resolved = str(cwd.resolve())
        expected = init and client.project_dir(home / ".claude", resolved) / (
            init["session_id"] + ".jsonl"
        )
        yield (
            f"the transcript of a session started at a {name} path is where "
            "project_dir says",
            bool(found) and found == expected,
            f"found {found}, project_dir says {expected}",
        )


def probe(session, mark):
    """The kinds of event on stdout since `mark`, for a receipt."""
    return [
        {
            key: event.get(key)
            for key in ("type", "subtype", "parent_tool_use_id")
            if event.get(key)
        }
        for event in session.events[mark:]
    ]


def reloading(binary, root):
    """/reload-plugins and /reload-skills written to stdin as user messages."""
    plugin = root / "reloading-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "contract-plugin", "version": "0.1.0"})
    )
    session = Session(binary, root, "reloading", [*DRIVER, "--plugin-dir", str(plugin)])
    try:
        mark = session.user("hello")
        _, init = session.wait(is_("system", "init"), 30, mark)
        session.wait(is_("result"), 60, mark)
        commands = (init or {}).get("slash_commands", [])
        yield (
            "init lists reload-plugins and reload-skills among the slash commands",
            {"reload-plugins", "reload-skills"} <= set(commands),
            json.dumps(sorted(c for c in commands if "reload" in c)),
        )
        for kind, created in (
            ("skills", session.config / "skills" / "late-skill" / "SKILL.md"),
            ("plugins", plugin / "skills" / "late-plugin-skill" / "SKILL.md"),
        ):
            created.parent.mkdir(parents=True)
            name = created.parent.name
            created.write_text(
                f"---\nname: {name}\ndescription: Added after launch {name.upper()}_SENTINEL.\n---\nBody.\n"
            )
            before = len(session.requests())
            mark = session.user(f"/reload-{kind}")
            _, result = session.wait(is_("result"), 60, mark)
            asked = [
                r
                for r in session.requests()[before:]
                if f"/reload-{kind}" in json.dumps(r.get("messages"))
            ]
            yield (
                f"/reload-{kind} runs as a command: a result, and the model is not asked about it",
                bool(result) and result.get("is_error") is False and not asked,
                json.dumps(
                    {
                        "result": result and result.get("result"),
                        "model_requests": len(session.requests()) - before,
                        "events": probe(session, mark),
                    }
                )[:600],
            )
            mark = session.user("after reload")
            session.wait(is_("result"), 60, mark)
            listed = f"{name.upper()}_SENTINEL" in json.dumps(session.requests()[-1])
            yield (
                f"after /reload-{kind} the model is offered the {kind[:-1]} added after launch",
                listed,
                f"{name} offered: {listed}",
            )
    finally:
        session.stop()


def nesting(binary, root):
    """Agents a subagent or a workflow starts: what reaches stdout and what only the files hold."""
    session = Session(binary, root, "nesting", DRIVER)
    try:
        inner = do(
            "Agent",
            description="grandchild",
            subagent_type="general-purpose",
            run_in_background=False,
            prompt=do("Bash", command="echo NESTED_RAN", description="grandchild echo"),
        )
        mark = session.user(
            do(
                "Agent",
                description="child",
                subagent_type="general-purpose",
                run_in_background=False,
                prompt=inner,
            )
        )
        session.wait(is_("result"), 90, mark)
        started = [
            e
            for e in session.events[mark:]
            if is_("system", "task_started", task_type="local_agent")(e)
        ]
        outer = started[0]["tool_use_id"] if started else None
        calls = [
            b["id"]
            for e in session.events[mark:]
            if e.get("type") == "assistant" and e.get("parent_tool_use_id") == outer
            for b in blocks(e)
            if b.get("type") == "tool_use" and b.get("name") == "Agent"
        ]
        nested = next((e for e in started if e["tool_use_id"] in calls), None)
        yield (
            "a subagent's Agent call starts a nested agent, reported as task_started for that call",
            len(started) == 2 and nested is not None,
            json.dumps(
                [{k: e.get(k) for k in ("task_id", "tool_use_id")} for e in started]
            ),
        )
        if nested is None:
            return
        tags = sorted(
            {
                e["parent_tool_use_id"]
                for e in session.events[mark:]
                if e.get("parent_tool_use_id")
            }
        )
        yield (
            "a nested agent's own messages are not on stdout: only its parent subagent's are",
            tags == [outer],
            f"parent_tool_use_id values on stdout: {tags}; nested agent's call {nested['tool_use_id']}",
        )
        directory = session_dir(session)
        transcript = directory / "subagents" / f"agent-{nested['task_id']}.jsonl"
        yield (
            "a nested agent's transcript is subagents/agent-<agentId>.jsonl, beside its parent's",
            transcript.exists() and ran_in(transcript, "NESTED_RAN"),
            str(transcript.relative_to(directory)),
        )
        script = (
            "export const meta = { name: 'contract', description: 'contract probe' }\n"
            f"await agent({json.dumps(do('Bash', command='echo WORKFLOW_RAN', description='workflow echo'))}, {{ label: 'one' }})\n"
        )
        mark = session.user(do("Workflow", script=script))
        _, workflow = session.wait(
            is_("system", "task_started", task_type="local_workflow"), 60, mark
        )
        if not workflow:
            yield (
                "a workflow starts as a local_workflow task",
                False,
                "no task_started",
            )
            return
        session.wait(
            is_("system", "task_notification", task_id=workflow["task_id"]), 90, mark
        )
        session.wait(is_("result"), 30, len(session.events) - 1)
        tagged = [e for e in session.events[mark:] if e.get("parent_tool_use_id")]
        agents = {
            entry["agentId"]
            for e in session.events[mark:]
            if is_("system", "task_progress", task_id=workflow["task_id"])(e)
            for entry in e.get("workflow_progress") or []
            if entry.get("agentId")
        }
        files = [
            path
            for agent in agents
            for path in (directory / "subagents" / "workflows").glob(
                f"wf_*/agent-{agent}.jsonl"
            )
        ]
        yield (
            "a workflow agent's messages are not on stdout",
            not tagged,
            f"{len(tagged)} message(s) with parent_tool_use_id",
        )
        yield (
            "task_progress names each workflow agent's agentId, whose transcript is wf_*/agent-<agentId>.jsonl",
            len(agents) == 1 and len(files) == 1 and ran_in(files[0], "WORKFLOW_RAN"),
            json.dumps(
                {
                    "agents": sorted(agents),
                    "files": [str(f.relative_to(directory)) for f in files],
                }
            ),
        )
    finally:
        session.stop()


def unattended(binary, root):
    """The runner's flags: bypassPermissions with no prompt tool, so no tool ever asks the driver."""
    session = Session(binary, root, "unattended", DRIVER)
    try:
        protected = session.workspace / ".claude" / "settings.json"
        outside = session.home / "outside.txt"
        calls = {
            "Bash": do(
                "Bash", command="touch touched.txt && echo TOUCHED", description="touch"
            ),
            "Write under .claude/": do("Write", file_path=str(protected), content="{}"),
            "Write outside the workspace": do(
                "Write", file_path=str(outside), content="OUTSIDE"
            ),
            # WebFetch upgrades http to https, so against the plain-HTTP fixture
            # it fails on the TLS handshake — after deciding it may run.
            "WebFetch": do(
                "WebFetch",
                url=f"http://127.0.0.1:{session.server.server_port}/",
                prompt="Summarise",
            ),
            "an MCP tool": do("mcp__fixture__echo", message="MCP_MARKER"),
        }
        outputs = {}
        for name, prompt in calls.items():
            mark = session.user(prompt)
            session.wait(
                lambda e: e.get("type") in ("result", "control_request"), 60, mark
            )
            outputs[name] = " ".join(text_of(r) for r in results_since(session, mark))
        asked = [e for e in session.events if e.get("type") == "control_request"]
        yield (
            "without a prompt tool no control_request arrives for Bash, Write, WebFetch or an MCP tool",
            not asked,
            f"{len(asked)} control_request(s)",
        )
        ran = {
            "Bash": "TOUCHED" in outputs["Bash"]
            and (session.workspace / "touched.txt").exists(),
            "Write under .claude/": protected.exists(),
            "Write outside the workspace": outside.exists(),
            "WebFetch": "ssl" in outputs["WebFetch"].lower()
            and "permission" not in outputs["WebFetch"].lower(),
            "an MCP tool": "MCP_MARKER" in outputs["an MCP tool"],
        }
        yield (
            "and each of them ran rather than being refused",
            all(ran.values()),
            json.dumps({name: [ok, outputs[name][:100]] for name, ok in ran.items()})[
                :700
            ],
        )
    finally:
        session.stop()


LARGE = "yes 0123456789abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTU | head -c 1000000"


def turn_bytes(session, command):
    """Run one Bash turn; stdout bytes it cost and the tool_result event's own share."""
    mark = session.user(do("Bash", command=command, description="output"))
    end, _ = session.wait(is_("result"), 90, mark)
    if end is None:
        return None
    before = session.offsets[mark - 1] if mark else 0
    results = [
        (session.offsets[i] - session.offsets[i - 1], session.events[i])
        for i in range(mark, end + 1)
        if session.events[i].get("type") == "user"
        and any(b.get("type") == "tool_result" for b in blocks(session.events[i]))
    ]
    size, event = results[-1] if results else (0, {})
    return {
        "turn": session.offsets[end] - before,
        "tool_result_event": size,
        "tool_use_result": event.get("tool_use_result")
        if isinstance(event.get("tool_use_result"), dict)
        else {},
        "model": sum(len(text_of(r)) for r in last_tool_results(session.requests()[-1]))
        if session.requests()
        else 0,
    }


def summary(measured):
    return {
        "turn_bytes": measured["turn"],
        "tool_result_event_bytes": measured["tool_result_event"],
        "tool_use_result.stdout_chars": len(
            measured["tool_use_result"].get("stdout", "")
        ),
        "persistedOutputSize": measured["tool_use_result"].get("persistedOutputSize"),
        "model_chars": measured["model"],
    }


def journal(binary, root):
    """How much of a large tool output stdout carries."""
    session = Session(binary, root, "journal", DRIVER)
    try:
        small = turn_bytes(session, "echo SMALL")
        large = turn_bytes(session, LARGE)
        yield (
            "a Bash printing 1 MB is not echoed in full: stdout carries at most ~30 KB of it, the rest is persisted",
            bool(small and large)
            and len(large["tool_use_result"].get("stdout", "")) <= 32_000
            and large["tool_use_result"].get("persistedOutputSize") == 1_000_000
            and large["turn"] < 64_000,
            json.dumps(
                {"small": small and summary(small), "1MB": large and summary(large)}
            ),
        )
    finally:
        session.stop()
    session = remote(binary, root, "journal-remote", DRIVER)
    try:
        large = turn_bytes(session, LARGE)
        yield (
            "the same holds for a Bash the shell prefix runs on the executor",
            bool(large) and large["turn"] < 64_000,
            json.dumps(large and summary(large)),
        )
    finally:
        stop_remote(session)


def remote(binary, root, name, args, env=None, trusted_hook=None, untrusted_hook=None):
    """A -p session launched the way a room's central session is, against a local executor.

    The executor is the acceptance fixture's (`acceptance.setup`): runtime.py
    over a project seeded by seed.py, running commands as its own processes
    and file operations through `claude mcp serve` of the same build. The
    central side is `client.prepare`, so the function hook (proxy.js, which
    keeps the file tools), the shell prefix (which carries the build's own
    Bash to the executor) and the PreToolUse guard are exactly what a room
    launches with. Two things differ from a room, neither of which the
    checks read: the forwarded project view is a FUSE mount this fixture does
    not have, so the central workspace is an empty directory, and the version
    pin is relaxed so the daily job can run the newest build.
    """
    import acceptance

    folder = root / name
    home = folder / "home"
    executor_home = folder / "executor-home"
    for path in (home / ".claude", executor_home / ".claude"):
        path.mkdir(parents=True)
    saved = {key: os.environ.get(key) for key in ("HOME", "CLAUDE_CONFIG_DIR")}
    # The executor's `claude mcp serve` inherits this process's environment.
    os.environ.update(
        HOME=str(executor_home), CLAUDE_CONFIG_DIR=str(executor_home / ".claude")
    )
    try:
        executor, target = acceptance.setup(
            folder, argparse.Namespace(ssh=None, claude=binary), "http://127.0.0.1:9"
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    release = acceptance.execution_release
    release.mount_state = lambda path: release.MOUNT_LIVE
    acceptance.client.PINNED_VERSION = subprocess.run(
        [binary, "--version"], capture_output=True, text=True
    ).stdout.split()[0]
    settings = (
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": trusted_hook}]}]}}
        if trusted_hook
        else None
    )
    launch = acceptance.client.prepare(
        home / "session",
        target,
        claude=binary,
        base_settings=settings,
        home_override=home,
        config_override=home / ".claude",
    )
    if untrusted_hook:
        # Added after prepare, so the shell prefix does not know it: the
        # prefix sends any command it does not know to the executor.
        written = json.loads((home / ".claude" / "settings.json").read_text())
        written["hooks"].setdefault("Stop", []).append(
            {"hooks": [{"type": "command", "command": untrusted_hook}]}
        )
        (home / ".claude" / "settings.json").write_text(json.dumps(written))
    session = Session(binary, root, name, args, launch=launch, env=env)
    session.executor = executor
    session.remote_workspace = Path(
        json.loads((home / "session" / "execution.json").read_text())["workspace"]
    )
    session.execution = home / "session" / "execution.json"
    return session


def stop_remote(session):
    session.stop()
    subprocess.run(session.executor.command("stop"), capture_output=True, timeout=20)


def functionhooks(binary, root):
    """The remote-execution plugin under -p: tools, the shell prefix and the guard."""
    # In the session's own home: what the session creates directly in a
    # directory leading down to the executor's path stays in its namespace
    # (`client.py enter`).
    trusted = root / "functionhooks" / "home" / "trusted-hook.txt"
    session = remote(
        binary,
        root,
        "functionhooks",
        DRIVER,
        trusted_hook=f"printf CENTRAL > {shlex.quote(str(trusted))}",
        untrusted_hook="printf PREFIXED > prefix-marker.txt",
    )
    try:
        mark = session.user(
            do(
                "Bash",
                command='printf "%s " "$EXECUTION_ENV"; pwd',
                description="where",
            )
        )
        session.wait(is_("result"), 60, mark)
        out = [text_of(r) for r in results_since(session, mark)]
        yield (
            "function hooks: Bash runs on the executor, in its workspace and environment",
            len(out) == 1
            and "REMOTE_COMMAND_ENV" in out[0]
            and str(session.remote_workspace) in out[0],
            json.dumps(out)[:200],
        )
        # The session sees the project at the executor's path; this host's
        # view of it is elsewhere.
        target = session.workspace / "new.txt"
        written = session.remote_workspace / "new.txt"
        mark = session.user(do("Write", file_path=str(written), content="REMOTE_WRITE"))
        session.wait(is_("result"), 60, mark)
        yield (
            "function hooks: Write lands in the executor workspace, not the central one",
            written.exists()
            and written.read_text() == "REMOTE_WRITE"
            and not target.exists(),
            f"executor {written.exists()}, central {target.exists()}",
        )
        # Stop hooks run after the result; give them a moment.
        deadline = time.monotonic() + 10
        while (
            not (
                trusted.exists()
                and (session.remote_workspace / "prefix-marker.txt").exists()
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.2)
        yield (
            "CLAUDE_CODE_SHELL_PREFIX: a platform hook runs centrally, any other command on the executor",
            trusted.exists()
            and (session.remote_workspace / "prefix-marker.txt").exists()
            and not (session.workspace / "prefix-marker.txt").exists(),
            f"platform hook ran: {trusted.exists()}, unknown hook on executor: "
            f"{(session.remote_workspace / 'prefix-marker.txt').exists()}, "
            f"centrally: {(session.workspace / 'prefix-marker.txt').exists()}",
        )
    finally:
        stop_remote(session)
    # Without the function hook the native tool reaches the harness, and the
    # PreToolUse guard prepare installs is what stops it running centrally.
    session = remote(
        binary,
        root,
        "functionhooks-off",
        DRIVER,
        env={"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "0"},
    )
    try:
        target = session.workspace / "new.txt"
        mark = session.user(do("Write", file_path=str(target), content="CENTRAL_WRITE"))
        _, result = session.wait(is_("result"), 60, mark)
        out = results_since(session, mark)
        yield (
            "the PreToolUse guard denies a native call the plugin did not take, and the turn goes on",
            len(out) == 1
            and out[0].get("is_error") is True
            and "the remote execution plugin did not handle this call"
            in text_of(out[0])
            and not target.exists()
            and not (session.remote_workspace / "new.txt").exists()
            and bool(result)
            and result.get("is_error") is False,
            json.dumps(
                {
                    "tool_result": text_of(out[0])[:200] if out else None,
                    "result": result and result.get("is_error"),
                }
            ),
        )
    finally:
        stop_remote(session)


def alive(argv):
    """Whether a process whose whole command line is `argv` is running."""
    return subprocess.run(["pgrep", "-xf", argv], capture_output=True).returncode == 0


def remotebackground(binary, root):
    """A Bash the build sends through the shell prefix to the executor.

    The build runs that Bash itself — the prefix only carries it to the
    executor — so it is the build's own task: reported, moved to the
    background by the room's button on stdin, and announced when it ends.
    """
    session = remote(binary, root, "remotebackground", DRIVER)
    try:
        mark = session.user(
            do(
                "Bash",
                command="sleep 12; echo REMOTE_SLEPT",
                description="remote sleep",
                timeout=120000,
            )
        )
        _, started = session.wait(
            is_("system", "task_started", task_type="local_bash"), 60, mark
        )
        yield (
            "a Bash the prefix sends to the executor is a harness task: task_started local_bash",
            started is not None and started.get("is_backgrounded") is False,
            json.dumps(started)[:200],
        )
        if not started:
            return
        time.sleep(2)
        moved_at = len(session.events)
        answer = session.control(
            {"subtype": "background_tasks", "tool_use_id": started["tool_use_id"]}
        )
        ended, _ = session.wait(is_("result"), 10, moved_at)
        running = alive("sleep 12")
        out = [text_of(r) for r in results_since(session, mark)]
        yield (
            "stdin background_tasks moves it: backgrounded, the turn ends, the command keeps running on the executor",
            (answer.get("response") or {}).get("backgrounded") is True
            and ended is not None
            and running
            and len(out) == 1
            and f"backgrounded by user with ID: {started['task_id']}" in out[0],
            json.dumps(
                {
                    "answer": answer.get("response"),
                    "result": ended,
                    "running": running,
                    "tool_result": out[0][:120] if out else None,
                }
            ),
        )
        noted, notification = session.wait(
            is_("system", "task_notification", task_id=started["task_id"]),
            30,
            moved_at,
        )
        follow, _ = session.wait(is_("result"), 30, (noted or 0) + 1)
        output = (
            Path(notification["output_file"]).read_text()
            if notification and notification.get("output_file")
            else ""
        )
        yield (
            "its completion is the build's own: task_notification completed and a follow-up turn",
            notification is not None
            and notification.get("status") == "completed"
            and follow is not None
            and "REMOTE_SLEPT" in output,
            json.dumps(
                {
                    "notification": notification
                    and {k: notification.get(k) for k in ("status", "summary")},
                    "follow-up result at": follow,
                    "output": output[:80],
                }
            ),
        )
    finally:
        stop_remote(session)


SCENARIOS = {
    "controls": controls,
    "background-bash": lambda binary, root: background(binary, root, "bash"),
    "background-bash-all": lambda binary, root: background(binary, root, "bash-all"),
    "background-agent": lambda binary, root: background(binary, root, "agent"),
    "subagents": subagents,
    "steering": steering,
    "stopping": stopping,
    "hooks": hooks,
    "skipping": skipping,
    "lifetime": lifetime,
    "refused": refused,
    "resuming": resuming,
    "projectdirs": projectdirs,
    "reloading": reloading,
    "nesting": nesting,
    "unattended": unattended,
    "journal": journal,
    "functionhooks": functionhooks,
    "remotebackground": remotebackground,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True, help="the claude binary to check")
    parser.add_argument("--output", type=Path, help="where to write the receipt")
    parser.add_argument(
        "--only", nargs="*", choices=sorted(SCENARIOS), help="run only these"
    )
    arguments = parser.parse_args()
    binary = str(Path(arguments.claude).resolve())

    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True
    ).stdout.strip()
    root = Path(tempfile.mkdtemp(prefix="headless-contract-"))
    results = []
    try:
        for name in arguments.only or SCENARIOS:
            try:
                for check, holds, observed in SCENARIOS[name](binary, root):
                    results.append(
                        {
                            "scenario": name,
                            "check": check,
                            "holds": bool(holds),
                            "observed": observed,
                        }
                    )
                    print(
                        f"{'PASS' if holds else 'FAIL'}  [{name}] {check}\n      {observed}",
                        flush=True,
                    )
            except Exception:
                trace = traceback.format_exc()
                results.append(
                    {
                        "scenario": name,
                        "check": "the scenario ran",
                        "holds": False,
                        "observed": trace,
                    }
                )
                print(f"FAIL  [{name}] the scenario ran\n{trace}", flush=True)
        receipt = {
            "claude": binary,
            "version": version,
            "checks": results,
            "held": all(result["holds"] for result in results),
        }
        if arguments.output:
            arguments.output.mkdir(parents=True, exist_ok=True)
            (arguments.output / "headless-contract.json").write_text(
                json.dumps(receipt, indent=2)
            )
            for scenario in root.iterdir():
                for name in ("transcript.jsonl", "stderr.log", "screen.txt"):
                    if (scenario / name).exists():
                        (arguments.output / scenario.name).mkdir(exist_ok=True)
                        shutil.copy(
                            scenario / name, arguments.output / scenario.name / name
                        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{version}: {sum(r['holds'] for r in results)}/{len(results)} held")
    return 0 if receipt["held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
