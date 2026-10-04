"""A repository's skills in a Codex room, as plain Codex offers them.

Plain Codex, opened in a repository, lists the skills in its `.agents/skills`
and `.codex/skills`, injects one a message names with `$`, and notices one
added, edited or removed while it runs. A room also gives every session the
platform's own skills, as it does on Claude Code and pi; Codex finds them as a
user-level root, and lists one beside a repository skill of the same name, as
plain Codex lists a user skill and a repository skill of one name. A room's
Codex runs on the session host, away from the project, so this plays those
against the real pinned binary launched as a room launches it
(`host.configure`), with the project served by a real executor on the
machine that holds it.
"""

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.harness.codex.bundle import build
from app.domain.agent.harness.codex.host import configure
from app.domain.agent.harness.driven.runner import socket_path
from tests.pinned_claude import claude_binary, codex_binary
from tests.support import executor_release

#: Printed only by a command that ran on the machine holding the project.
MACHINE_MARK = "SKILL_FIXTURE_ON_THE_MACHINE"


#: The platform's skills as a room's launch carries them (`session_skill_files`).
PLATFORM = {
    "skills/documents/SKILL.md": (
        "---\nname: documents\ndescription: Office files. PLATFORMDOC_LISTED.\n---\n"
        "PLATFORMDOC_BODY. See references/word.md; run scripts/where.py.\n"
    ),
    "skills/documents/references/word.md": "PLATFORM_REFERENCE\n",
    "skills/documents/scripts/where.py": (
        f'import os\nprint("PLATFORM_SCRIPT", os.environ.get("{MACHINE_MARK}"))\n'
    ),
    # Named like the repository's own skill.
    "skills/greet/SKILL.md": (
        "---\nname: greet\ndescription: The platform greets. PLATFORMGREET_LISTED.\n"
        "---\nPLATFORMGREET_BODY\n"
    ),
}


def skill(directory: Path, name: str, description: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


def project(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    skill(
        path / ".agents/skills/greet",
        "greet",
        "Greets the reader. GREET_LISTED.",
        "GREET_BODY. The details are in reference.md beside this file.",
    )
    (path / ".agents/skills/greet/reference.md").write_text("GREET_REFERENCE\n")
    script = path / ".agents/skills/greet/scripts/hello.sh"
    script.parent.mkdir()
    script.write_text(f'#!/bin/sh\nprintf "HELLO %s\\n" "${MACHINE_MARK}"\n')
    script.chmod(0o755)
    skill(path / ".codex/skills/tidy", "tidy", "Tidies up. TIDY_LISTED.", "TIDY_BODY")
    # Neither is Codex's: plain Codex offers neither.
    skill(path / ".claude/skills/other", "other", "OTHER_LISTED.", "OTHER_BODY")
    skill(path / "pkg/.agents/skills/deep", "deep", "DEEP_LISTED.", "DEEP_BODY")


class Model(BaseHTTPRequestHandler):
    """A Responses endpoint that calls the tool a `DO:` line in the user's
    message names, once, and otherwise answers."""

    requests: list[dict] = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(body)
        number = len(self.requests)
        last = body["input"][-1]
        directive = None
        if last.get("role") == "user":
            for part in last["content"]:
                text = part.get("text", "")
                if "DO:" in text:
                    directive = json.JSONDecoder().raw_decode(
                        text[text.find("DO:") + 3 :]
                    )[0]
        if directive:
            item = {
                "id": f"fc_{number}",
                "type": "function_call",
                "call_id": f"call_{number}",
                "name": directive["name"],
                "arguments": json.dumps(directive["input"]),
                "status": "completed",
            }
        else:
            item = {
                "id": f"msg_{number}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ok", "annotations": []}],
            }
        response = {
            "id": f"resp_{number}",
            "object": "response",
            "status": "completed",
            "output": [item],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        events = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            },
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        data = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def do(name: str, **arguments) -> str:
    return "DO:" + json.dumps({"name": name, "input": arguments})


@pytest.fixture
def machine(tmp_path):
    """The machine holding the project, and its executor."""
    home = tmp_path / "machine"
    helper = executor_release.install(home / ".cheese")
    state = home / ".cheese/executor"
    work = tmp_path / "the project"
    project(work)
    # Where a room's launch plants the platform's skills on the machine, and
    # the config dir its executor runs with.
    bootstrap.plant_native_skills(home, PLATFORM)
    subprocess.run(
        [sys.executable, str(helper), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(work),
                "claude": claude_binary(),
                "env": {MACHINE_MARK: MACHINE_MARK},
                "mcp_servers": {},
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(home / ".claude")},
    )
    target = {
        "command": [sys.executable, str(helper)],
        "state": str(state),
        "workspace": str(work),
        "mcp_servers": [],
    }
    yield target, work
    subprocess.run(
        [sys.executable, str(helper), "stop", "--state", str(state)],
        capture_output=True,
        timeout=15,
    )


@pytest.mark.anyio
async def test_a_repositorys_skills_work_in_a_codex_room(tmp_path, machine):
    target, work = machine
    Model.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    state = tmp_path / "session host/runner"
    artifact = tmp_path / "runner.pyz"
    artifact.write_bytes(build())
    launch = {
        "state": str(state),
        "config": {
            "execution_target": target,
            "opening": {"system_prompt": "ROOM"},
            "binary": codex_binary(),
            "skills": PLATFORM,
        },
        "codex_config": (
            'model = "gpt-5.3-codex"\nmodel_provider = "fixture"\n'
            '[model_providers.fixture]\nname = "fixture"\n'
            f'base_url = "{endpoint}/v1"\nwire_api = "responses"\n'
            'env_key = "CHEESE_TOKEN"\nrequires_openai_auth = false\n'
            "[analytics]\nenabled = false\n"
        ),
        "artifact": str(artifact),
        "env": {"CHEESE_TOKEN": "fixture", "NO_PROXY": "127.0.0.1"},
    }
    await asyncio.to_thread(configure, launch)
    cursor = 0

    async def rpc(method, params=None):
        reader, writer = await asyncio.open_unix_connection(
            socket_path(state), limit=4 * 1024 * 1024
        )
        try:
            writer.write(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            await writer.drain()
            result = json.loads(await reader.readline())
            assert "error" not in result, result
            return result["result"]
        finally:
            writer.close()
            await writer.wait_closed()

    async def turn(text):
        """The requests one turn made of the model."""
        nonlocal cursor
        before = len(Model.requests)
        await rpc("send", {"input_id": f"input-{before}-{cursor}", "text": text})
        while True:
            events = (await rpc("events", {"after": cursor}))["events"]
            done = False
            for event in events:
                cursor = event["sequence"]
                if event["record"]["method"] == "turn/completed":
                    done = True
            if done:
                return Model.requests[before:]
            await asyncio.sleep(0.02)

    def offered(requests):
        """The skill list the model has now: Codex sends the whole list again,
        as a developer message, each time it changes."""
        lists = [
            part["text"]
            for item in requests[-1]["input"]
            if item.get("role") == "developer"
            for part in item["content"]
            if "## Skills" in part.get("text", "")
        ]
        return lists[-1]

    def handed_back(requests):
        return json.dumps(
            [
                item
                for request in requests
                for item in request["input"]
                if item.get("type") == "function_call_output"
            ]
        )

    def root(requests, of, marker):
        """The directory the skill list names for the root of the skill `of`
        whose description carries `marker`."""
        roots = dict(re.findall(r"`(r\d+)` = `([^`]+)`", offered(requests)))
        (alias,) = re.findall(
            rf"- {of}: [^\n]*?{marker}[^\n]*?\(file: (r\d+)/{of}/SKILL.md\)",
            offered(requests),
        )
        return roots[alias]

    try:
        async with asyncio.timeout(120):
            first = await turn("hello")
            listing = offered(first)
            assert "GREET_LISTED" in listing
            assert "TIDY_LISTED" in listing
            assert "OTHER_LISTED" not in listing
            assert "DEEP_LISTED" not in listing
            greet = root(first, "greet", "Greets the reader")

            # The platform's skills, and both skills named greet.
            assert "PLATFORMDOC_LISTED" in listing
            assert "PLATFORMGREET_LISTED" in listing
            documents = root(first, "documents", "PLATFORMDOC_LISTED")
            read = await turn(do("Read", file_path=f"{documents}/documents/SKILL.md"))
            assert "PLATFORMDOC_BODY" in handed_back(read)
            read = await turn(
                do("Read", file_path=f"{documents}/documents/references/word.md")
            )
            assert "PLATFORM_REFERENCE" in handed_back(read)
            ran = await turn(
                do("Bash", command=f'python3 "{documents}/documents/scripts/where.py"')
            )
            assert f"PLATFORM_SCRIPT {MACHINE_MARK}" in handed_back(ran)

            # The skill's own file and a file beside it, at the paths the list
            # gave, through the room's file tool.
            read = await turn(do("Read", file_path=f"{greet}/greet/SKILL.md"))
            assert "GREET_BODY" in handed_back(read)
            read = await turn(do("Read", file_path=f"{greet}/greet/reference.md"))
            assert "GREET_REFERENCE" in handed_back(read)

            mentioned = await turn("please $greet me")
            assert "GREET_BODY" in json.dumps(mentioned[-1]["input"])

            ran = await turn(do("Bash", command=f'"{greet}/greet/scripts/hello.sh"'))
            assert f"HELLO {MACHINE_MARK}" in handed_back(ran)

            # Changed on the machine while the session runs.
            skill(work / ".agents/skills/late", "late", "LATE_LISTED.", "LATE_BODY")
            assert "LATE_LISTED" in offered(await turn("anything new?"))
            skill(work / ".agents/skills/greet", "greet", "GREET_EDITED.", "X")
            assert "GREET_EDITED" in offered(await turn("anything new?"))
            shutil.rmtree(work / ".agents/skills/late")
            assert "LATE_LISTED" not in offered(await turn("anything new?"))
    finally:
        try:
            pid = (await rpc("ping"))["pid"]
        except Exception:  # noqa: BLE001 — nothing left to stop
            pid = None
        if pid:
            os.kill(pid, signal.SIGTERM)
        server.shutdown()
        server.server_close()
