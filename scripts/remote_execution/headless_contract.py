"""What the Claude Code driver needs headless stream-json mode to keep doing.

The driver (#1606) runs `claude -p --input-format stream-json --output-format
stream-json --verbose` and holds its stdin for the life of the session: user
messages, messages sent mid-turn and controls all go in there, and the timeline,
turn ends and questions come back out of stdout. None of that protocol is
published — the SDKs speak it, but no document pins it — so this script is the
contract. Each check drives the given build against the deterministic Messages
API in `model_fixture.py` and asserts only what the build does on the wire or on
disk.

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
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from model_fixture import Handler, Server  # noqa: E402

# The combination the driver launches with: ordinary tools never wait for a
# person, and AskUserQuestion still reaches the driver as `can_use_tool`.
DRIVER = ["--permission-mode", "bypassPermissions", "--permission-prompt-tool", "stdio"]
MODEL = "claude-sonnet-4-5"
QUESTION = {
    "questions": [
        {
            "question": "Which color?",
            "header": "Color",
            "options": [
                {"label": "Red", "description": "red"},
                {"label": "Blue", "description": "blue"},
            ],
            "multiSelect": False,
        }
    ]
}


def do(name, **arguments):
    return "DO:" + json.dumps({"name": name, "input": arguments})


def directive(body):
    messages = body.get("messages", [])
    if not messages or messages[-1]["role"] != "user":
        return None
    content = messages[-1]["content"]
    for block in [{"type": "text", "text": content}] if isinstance(content, str) else content:
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
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
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


def text_of(result):
    content = result.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content)
    return str(content)


class Session:
    """One headless Claude Code process in an isolated HOME, driven over stdio."""

    def __init__(self, binary, root, name, args, settings=None, refuse=False, env=None):
        self.name = name
        self.root = root / name
        self.home = self.root / "home"
        self.config = self.home / ".claude"
        # Under the isolated HOME: the build looks for CLAUDE.md and .claude/
        # in every directory above its cwd.
        self.workspace = self.home / "workspace"
        self.fixture = self.root / "fixture"
        for path in (self.config, self.workspace, self.fixture, self.root / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        git = ["git", "-c", "user.name=contract", "-c", "user.email=contract@example.invalid"]
        (self.workspace / "target.txt").write_text("TARGET_CONTENT\n")
        subprocess.run([*git, "init", "-q"], cwd=self.workspace, check=True)
        subprocess.run([*git, "add", "."], cwd=self.workspace, check=True)
        subprocess.run([*git, "commit", "-qm", "base"], cwd=self.workspace, check=True)
        (self.workspace / "target.txt").write_text("TARGET_CONTENT\nCHANGED\n")
        if settings is not None:
            (self.config / "settings.json").write_text(json.dumps(settings, indent=2))
        self.server = Server(("127.0.0.1", 0), Refusing if refuse else Handler)
        self.server.state = {"dir": self.fixture, "actions": Directives(), "requests": []}
        self.serving = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.serving.start()
        mcp = {
            "mcpServers": {
                "fixture": {"command": sys.executable, "args": [str(HERE / "custom_mcp.py")]}
            }
        }
        self.argv = [
            binary,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            MODEL,
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(mcp),
            "--no-chrome",
            *args,
        ]
        environment = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "CLAUDE_CONFIG_DIR": str(self.config),
            "CLAUDE_CODE_TMPDIR": str(self.root / "tmp"),
            "SHELL": "/bin/bash",
            "LANG": "C.UTF-8",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{self.server.server_port}",
            "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
            "NO_PROXY": "127.0.0.1,localhost",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            **(env or {}),
        }
        self.started = time.monotonic()
        self.events = []
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
        for line in self.process.stdout:
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
                self.changed.notify_all()
        with self.changed:
            self.events.append({"type": "_eof", "returncode": self.process.wait()})
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
            lambda event: event.get("type") == "control_response"
            and event["response"].get("request_id") == identifier,
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
        yield ("initialize is answered", answer.get("subtype") == "success", json.dumps(answer)[:160])
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
                {"subtype": "apply_flag_settings", "settings": {"alwaysThinkingEnabled": False}},
                None,
            ),
            "rename_session": ({"subtype": "rename_session", "title": "Contract"}, None),
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
            "get_context_usage": ({"subtype": "get_context_usage", "detail": "summary"}, None),
            "get_usage": ({"subtype": "get_usage", "skip_behaviors": True}, None),
            "mcp_status": (
                {"subtype": "mcp_status"},
                lambda body: [server["status"] for server in body["mcpServers"]] == ["connected"],
            ),
            "mcp_reconnect": ({"subtype": "mcp_reconnect", "serverName": "fixture"}, None),
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
        answer = session.control({"subtype": "set_permission_mode", "mode": "bypassPermissions"})
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
                    prompt=do("Bash", command="sleep 12; echo SUB_DONE", description="sub sleep", timeout=120000),
                )
            )
            task_type = "local_agent"
        else:
            session.user(do("Bash", command="sleep 12; echo SLEEP_DONE", description="long sleep", timeout=120000))
            task_type = "local_bash"
        _, started = session.wait(is_("system", "task_started", task_type=task_type), 60)
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
            wrong = session.control({"subtype": "background_tasks", "tool_use_id": "toolu_wrong"})
            yield (
                "background_tasks for an unknown tool_use_id backgrounds nothing",
                (wrong.get("response") or {}).get("backgrounded") is False,
                json.dumps(wrong)[:160],
            )
        mark = len(session.events)
        request = {"subtype": "background_tasks", **({"tool_use_id": tool} if by_id else {})}
        answer = session.control(request)
        yield (
            f"background_tasks {'by tool_use_id' if by_id else 'without an id'} is accepted",
            answer.get("subtype") == "success"
            and (not by_id or (answer.get("response") or {}).get("backgrounded") is True),
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
        noted, notification = session.wait(is_("system", "task_notification", task_id=task), 30, mark)
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
        _, started = session.wait(is_("system", "task_started", task_type="local_agent"), 60)
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
        transcript = directory / "subagents" / f"agent-{agent}.jsonl" if directory else None
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
        _, workflow = session.wait(is_("system", "task_started", task_type="local_workflow"), 60, mark)
        if workflow is None:
            _, workflow = session.wait(is_("system", "task_started"), 1, mark)
        if workflow:
            session.wait(is_("system", "task_notification", task_id=workflow["task_id"]), 60, mark)
        files = sorted(
            str(path.relative_to(directory))
            for path in (directory / "subagents" / "workflows").glob("wf_*/**/*")
            if path.is_file()
        ) if directory else []
        yield (
            "a workflow's agents are under subagents/workflows/wf_*/",
            any(Path(name).name.startswith("agent-") and name.endswith(".jsonl") for name in files),
            json.dumps({"task": workflow and workflow.get("task_type"), "files": files})[:300],
        )
    finally:
        session.stop()


def steering(binary, root):
    """A message the user sends while a tool is running."""
    session = Session(binary, root, "steering", [*DRIVER, "--replay-user-messages"])
    try:
        session.user(do("Bash", command="sleep 6; echo FIRST_DONE", description="sleep"))
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
            lambda event: event.get("type") == "user"
            and not event.get("isReplay")
            and any(block.get("type") == "tool_result" for block in blocks(event)),
            1,
            sent,
        )
        replay, _ = session.wait(
            lambda event: event.get("type") == "user"
            and event.get("isReplay")
            and "STEER_MARKER" in json.dumps(event.get("message")),
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
        mark = session.user(do("Bash", command="sleep 30; echo NOT_STOPPED", description="to stop"))
        _, started = session.wait(is_("system", "task_started"), 60, mark)
        time.sleep(1)
        answer = session.control({"subtype": "stop_task", "task_id": started["task_id"]})
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
            json.dumps({"tool_result": killed[-1:], "result": result and result.get("subtype")})[:240],
        )
        mark = session.user(do("Bash", command="sleep 30; echo NOT_INTERRUPTED", description="to interrupt"))
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
            json.dumps(result and {key: result.get(key) for key in ("subtype", "is_error")}),
        )
        mark = session.user(
            do("Bash", command="sleep 30; echo NOT_STOPPED", description="background", run_in_background=True)
        )
        _, started = session.wait(is_("system", "task_started"), 60, mark)
        session.wait(is_("result"), 20, mark)
        answer = session.control({"subtype": "stop_task", "task_id": started["task_id"]})
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
    names = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]
    log = root / "hooks" / "fired.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": {
            name: [
                {
                    **({"matcher": "*"} if "ToolUse" in name else {}),
                    "hooks": [{"type": "command", "command": f"echo {name} >> '{log}'"}],
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


def questions(binary, root):
    """Ordinary tools run unattended; AskUserQuestion alone reaches the driver."""
    session = Session(binary, root, "questions", DRIVER)
    try:
        answer = session.control({"subtype": "initialize"})
        yield (
            "the driver's flags start the session in bypassPermissions",
            (answer.get("response") or {}).get("current_permission_mode") == "bypassPermissions",
            json.dumps((answer.get("response") or {}).get("current_permission_mode")),
        )
        protected = session.workspace / ".claude" / "settings.json"
        mark = 0
        for prompt in (
            do("Bash", command="touch touched.txt && echo TOUCHED", description="touch"),
            do("Write", file_path=str(protected), content="{}"),
        ):
            mark = session.user(prompt)
            session.wait(lambda event: event.get("type") in ("result", "control_request"), 60, mark)
        asked = [event for event in session.events if event.get("type") == "control_request"]
        yield (
            "Bash and a write under .claude/ run without any permission request",
            not asked and (session.workspace / "touched.txt").exists() and protected.exists(),
            f"{len(asked)} control_request(s)",
        )
        mark = session.user(do("AskUserQuestion", **QUESTION))
        _, request = session.wait(
            lambda event: event.get("type") in ("result", "control_request"), 60, mark
        )
        body = (request or {}).get("request") or {}
        yield (
            "AskUserQuestion arrives as can_use_tool needing a person",
            body.get("subtype") == "can_use_tool"
            and body.get("tool_name") == "AskUserQuestion"
            and body.get("requires_user_interaction") is True
            and body.get("input", {}).get("questions") == QUESTION["questions"],
            json.dumps(body)[:240],
        )
        if body.get("subtype") != "can_use_tool":
            return
        session.answer(
            request["request_id"],
            {
                "behavior": "allow",
                "updatedInput": {**body["input"], "answers": {"Which color?": "Blue"}},
            },
        )
        session.wait(is_("result"), 60, mark)
        seen = [
            text_of(result)
            for request in session.requests()
            for result in last_tool_results(request)
            if result.get("tool_use_id") == body.get("tool_use_id")
        ]
        yield (
            "allow with updatedInput.answers gives the model the answer",
            bool(seen) and '"Which color?"="Blue"' in seen[0],
            seen[0][:200] if seen else "no tool_result",
        )
    finally:
        session.stop()


def skipping(binary, root):
    """Why the driver cannot use --dangerously-skip-permissions on its own."""
    session = Session(binary, root, "skipping", ["--dangerously-skip-permissions"])
    try:
        session.user("hello")
        session.wait(is_("result"), 60)
        tools = [tool["name"] for tool in session.requests()[0].get("tools", [])] if session.requests() else []
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
            do("Bash", command=f"sleep 40; echo {marker}", description="background", run_in_background=True)
        )
        ended, result = session.wait(is_("result"), 60)
        yield (
            "a completed turn is one result with is_error false",
            bool(result) and result.get("subtype") == "success" and result.get("is_error") is False,
            json.dumps(result and {key: result.get(key) for key in ("subtype", "is_error")}),
        )
        time.sleep(2)
        usage = session.control({"subtype": "get_usage", "skip_behaviors": True})
        mark = session.user("second turn")
        second, _ = session.wait(is_("result"), 60, mark)
        running = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
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
        left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
        yield (
            "closing stdin exits the process and kills its background tasks",
            code == 0 and not left,
            f"exit {code}, {len(left)} background process(es) left",
        )
    finally:
        session.stop()
        for pid in subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split():
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
        binary, root, "refused", DRIVER, refuse=True, env={"CLAUDE_CODE_MAX_RETRIES": "1"}
    )
    try:
        session.user("hello")
        _, retry = session.wait(is_("system", "api_retry"), 30)
        yield (
            "each retry of a refused request is reported as api_retry with its status",
            bool(retry)
            and retry.get("error_status") == 401
            and retry.get("error") == "authentication_failed",
            json.dumps(retry and {key: retry.get(key) for key in ("attempt", "max_retries", "error_status", "error")}),
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
                and {key: result.get(key) for key in ("subtype", "is_error", "terminal_reason", "result")}
            )[:300],
        )
        yield (
            "the assistant message before it carries the error kind",
            bool(assistant) and assistant.get("error") == "authentication_failed",
            json.dumps(assistant and {"error": assistant.get("error"), "content": blocks(assistant)})[:300],
        )
        yield (
            "the process survives a refused turn",
            session.process.poll() is None,
            f"returncode {session.process.poll()}",
        )
    finally:
        session.stop()


SCENARIOS = {
    "controls": controls,
    "background-bash": lambda binary, root: background(binary, root, "bash"),
    "background-bash-all": lambda binary, root: background(binary, root, "bash-all"),
    "background-agent": lambda binary, root: background(binary, root, "agent"),
    "subagents": subagents,
    "steering": steering,
    "stopping": stopping,
    "hooks": hooks,
    "questions": questions,
    "skipping": skipping,
    "lifetime": lifetime,
    "refused": refused,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True, help="the claude binary to check")
    parser.add_argument("--output", type=Path, help="where to write the receipt")
    parser.add_argument("--only", nargs="*", choices=sorted(SCENARIOS), help="run only these")
    arguments = parser.parse_args()
    binary = str(Path(arguments.claude).resolve())

    version = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.strip()
    root = Path(tempfile.mkdtemp(prefix="headless-contract-"))
    results = []
    try:
        for name in arguments.only or SCENARIOS:
            try:
                for check, holds, observed in SCENARIOS[name](binary, root):
                    results.append({"scenario": name, "check": check, "holds": bool(holds), "observed": observed})
                    print(f"{'PASS' if holds else 'FAIL'}  [{name}] {check}\n      {observed}", flush=True)
            except Exception:
                trace = traceback.format_exc()
                results.append({"scenario": name, "check": "the scenario ran", "holds": False, "observed": trace})
                print(f"FAIL  [{name}] the scenario ran\n{trace}", flush=True)
        receipt = {
            "claude": binary,
            "version": version,
            "checks": results,
            "held": all(result["holds"] for result in results),
        }
        if arguments.output:
            arguments.output.mkdir(parents=True, exist_ok=True)
            (arguments.output / "headless-contract.json").write_text(json.dumps(receipt, indent=2))
            for scenario in root.iterdir():
                for name in ("transcript.jsonl", "stderr.log"):
                    if (scenario / name).exists():
                        (arguments.output / scenario.name).mkdir(exist_ok=True)
                        shutil.copy(scenario / name, arguments.output / scenario.name / name)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{version}: {sum(r['holds'] for r in results)}/{len(results)} held")
    return 0 if receipt["held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
