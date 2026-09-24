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

from app.domain.agent import machine_launcher, remote_control
from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
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
    # 随便一处真的差别就够 —— 这条 fixture 要的只是「旧包和新包不是同一份」，
    # 下面没有一条断言读这个名字。挑的是这条传输自报的名字，因为它谁也不影响：
    # 工具表已经不住在 client.py 里了（它在 `backend/sandbox/cheese`），拿表上
    # 某一样的名字当锚点，会在表搬家的那一天变成一次无声的空替换。
    previous["client.py"] = current["client.py"].replace(
        '"name": "cheese-native-execution"',
        '"name": "cheese-native-execution-before-release"',
    )
    previous["proxy.js"] = current["proxy.js"].replace(
        "result.text.split(execution.central_workspace).join(execution.workspace)",
        "result.text.split(execution.central_workspace).join(execution.workspace)"
        ' + " BEFORE_RELEASE"',
    )
    assert previous["client.py"] != current["client.py"]
    release.sources = lambda: previous
    # The fixture supplies the rendezvous pair the backend would normally derive
    # from a topic id; this launch has none.
    original_env = machine_launcher.screen_env

    def env_with_rendezvous(*args, **kwargs):
        return dict(
            original_env(*args, **kwargs),
            CHEESE_RV_SOCK=str(rendezvous),
            CHEESE_RV_TOKEN_FILE=str(token),
        )

    machine_launcher.screen_env = env_with_rendezvous
    original_send = acceptance.RemoteControlFixture.send

    def send(fixture, payload):
        if payload["type"] != "user":
            return original_send(fixture, payload)
        release.sources = lambda: current
        launch = json.loads((output / "central/launch.json").read_text())
        connections = []

        class Control:
            async def current(self, topic_id, agent_handle=None):
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
        tmux = acceptance.tmux_server(output)
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
        # One model request per scripted tool call plus the opening turn —
        # the count the acceptance sequence produces (`request_count` in its
        # summary), so it moves when that sequence does.
        summary = json.loads((output / "summary.json").read_text())
        assert len(requests) == summary["request_count"], len(requests)
        assert "BEFORE_RELEASE" not in requests[0].read_text()
    finally:
        rendezvous.unlink(missing_ok=True)
        token.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
