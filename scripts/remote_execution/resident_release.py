"""Exercise a resident helper release against the pinned native client."""

import argparse
import asyncio
import hashlib
import json
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import acceptance
from model_fixture import log

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.domain.agent import remote_control
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code import device_launch
from app.domain.agent.harness.claude_code.remote_execution import release


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", required=True)
    options = parser.parse_args()
    output = options.output.resolve()
    key = hashlib.sha256(str(output).encode()).hexdigest()[:12]
    rendezvous = output.parent / (key + ".sock")
    token = output.parent / (key + ".token")
    assert len(str(rendezvous).encode()) < 104
    current = release.sources()
    previous = dict(current)
    previous["client.py"] = current["client.py"].replace(
        '"name": "chat_send",', '"name": "chat_send_before_release",'
    )
    previous["proxy.js"] = current["proxy.js"].replace(
        "result.text.split(execution.central_workspace).join(execution.workspace)",
        "result.text.split(execution.central_workspace).join(execution.workspace)"
        ' + " BEFORE_RELEASE"',
    )
    assert previous["client.py"] != current["client.py"]
    release.sources = lambda: previous
    original_build = device_launch.build_screen_launch

    def build(**kwargs):
        kwargs["extra_env"] = dict(
            kwargs["extra_env"],
            CHEESE_RV_SOCK=str(rendezvous),
            CHEESE_RV_TOKEN_FILE=str(token),
        )
        return original_build(**kwargs)

    device_launch.build_screen_launch = build
    original_send = acceptance.RemoteControlFixture.send

    def send(fixture, payload):
        if payload["type"] != "user":
            return original_send(fixture, payload)
        release.sources = lambda: current
        launch = json.loads((output / "central/launch.json").read_text())
        connections = []

        class Control:
            async def current(self, topic_id):
                return {"id": fixture.sid, "status": "active"}

            async def enqueue(self, sid, frame, actor):
                original_send(fixture, frame)

            async def result(self, sid, request_id, wait_s):
                deadline = time.monotonic() + wait_s
                while time.monotonic() < deadline:
                    response = fixture.response(request_id)
                    if response:
                        return response
                    await asyncio.sleep(0.1)

        class Hub:
            calls = 0

            async def exec(self, device, command, *, stdin, timeout):
                self.calls += 1
                result = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    input=stdin,
                    env=launch["env"],
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
                return {
                    "exit": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }

            async def call_screen(self, device, sid, method, args):
                assert method == "prompt" and args == ["/reload-plugins"]
                connection = socket.socket(socket.AF_UNIX)
                connection.connect(str(rendezvous))
                for frame in (
                    {"role": "attacher", "auth": token.read_text().strip()},
                    {"type": "reply", "text": args[0]},
                ):
                    connection.sendall((json.dumps(frame) + "\n").encode())
                connections.append(connection)
                return "release-command"

            async def await_call(self, device, call_id, timeout):
                return {"ready": True}

        remote_control.store = Control
        channel = object.__new__(DeviceChannel)
        channel._hub = hub = Hub()
        screen = HubScreen(
            "fixture", "fixture", [], "fixture", 1, "fixture", topic_id=uuid.uuid4()
        )
        tmux = ["tmux", "-L", "cheese-acceptance-" + output.name]
        identity = tmux + ["display-message", "-p", "-t", "agent", "#{pane_pid}"]
        pid = acceptance.run(identity).strip()

        async def update():
            # Exercise replacement of an established transport, not initial startup.
            control = Control()
            deadline = time.monotonic() + 30
            while True:
                request_id = str(uuid.uuid4())
                await control.enqueue(
                    fixture.sid,
                    {
                        "type": "control_request",
                        "request_id": request_id,
                        "request": {"subtype": "mcp_status"},
                    },
                    "fixture",
                )
                status = await control.result(fixture.sid, request_id, 30)
                servers = (
                    (status or {})
                    .get("response", {})
                    .get("response", {})
                    .get("mcpServers", [])
                )
                if any(
                    s.get("name") == "native" and s.get("status") == "connected"
                    for s in servers
                ):
                    break
                assert time.monotonic() < deadline, status
                await asyncio.sleep(0.1)
            home = str(output / "device-home")
            await channel._refresh_resident(
                screen, home, {"version": release.digest(previous)}
            )
            count = hub.calls
            await channel._refresh_resident(
                screen, home, {"version": release.digest(current)}
            )
            assert hub.calls == count

        try:
            asyncio.run(update())
        finally:
            for connection in connections:
                connection.close()
        assert acceptance.run(identity).strip() == pid
        log(
            output / "release.jsonl",
            {
                "event": "refreshed",
                "native_pid": pid,
                "previous": release.digest(previous),
                "released": release.digest(current),
            },
        )
        return original_send(fixture, payload)

    acceptance.RemoteControlFixture.send = send
    sys.argv = [
        "acceptance.py",
        "--output",
        str(output),
        "--claude",
        options.claude,
        "--launcher",
        "device",
        "--rc",
    ]
    try:
        acceptance.main()
        requests = sorted(output.glob("request-*.json"))
        assert len(requests) == 16
        assert "BEFORE_RELEASE" not in requests[0].read_text()
    finally:
        rendezvous.unlink(missing_ok=True)
        token.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
