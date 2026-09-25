"""Exercise a resident helper release against the pinned build, held by the runner."""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import acceptance
from model_fixture import log

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.domain.agent.device_hub import HubScreen
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.claude_code.remote_execution import release


def claude_of(session):
    """The build the runner holds: its one child process."""
    children = subprocess.run(
        ["pgrep", "-P", str(session.process.pid)], capture_output=True, text=True
    ).stdout.split()
    assert len(children) == 1, children
    return children[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", required=True)
    options = parser.parse_args()
    output = options.output.resolve()
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
    # The previous plugin marks every prompt section it sees, so a request that
    # still went through it after the release would carry the mark.
    previous["proxy.js"] = current["proxy.js"].replace(
        "export function register(on) {",
        "export function register(on) {\n"
        '  on("prompt.section", async ($, e, next) => {\n'
        "    const result = await next(e);\n"
        '    return { ...result, text: result.text === null ? null : result.text + " BEFORE_RELEASE" };\n'
        "  });",
    )
    assert previous["client.py"] != current["client.py"]
    assert previous["proxy.js"] != current["proxy.js"]
    # The session starts on the previous release, laid down as the launcher
    # lays one down, and the release below replaces it before the first turn.
    release.sources = lambda: previous

    def before_turn(session, home):
        release.sources = lambda: current

        class Hub:
            calls = 0

            async def exec(self, device, command, *, stdin, timeout):
                self.calls += 1
                result = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    input=stdin,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                )
                return {
                    "exit": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }

            async def call_executor(self, device, state, method, params, timeout):
                # What the connector relays to the runner's socket.
                assert state == str(session.state)
                return await asyncio.to_thread(session.call, method, params, timeout)

        channel = object.__new__(DeviceChannel)
        channel._hub = hub = Hub()
        screen = HubScreen("fixture", "fixture", [], "fixture", 1, "fixture")
        state = str(session.state)

        async def update():
            # Replace an established transport, not one still starting.
            await channel._await_native_connected("fixture", state)
            pid = claude_of(session)
            released = await channel._refresh_resident(
                screen, str(home), state, {"version": release.digest(previous)}
            )
            assert released
            count = hub.calls
            assert not await channel._refresh_resident(
                screen, str(home), state, {"version": release.digest(current)}
            )
            assert hub.calls == count
            return pid

        pid = asyncio.run(update())
        assert claude_of(session) == pid
        log(
            output / "release.jsonl",
            {
                "event": "refreshed",
                "native_pid": pid,
                "previous": release.digest(previous),
                "released": release.digest(current),
            },
        )

    acceptance.before_turn = before_turn
    sys.argv = [
        "acceptance.py",
        "--output",
        str(output),
        "--claude",
        options.claude,
    ]
    acceptance.main()
    requests = sorted(output.glob("request-*.json"))
    # One model request per scripted tool call plus the opening turn —
    # the count the acceptance sequence produces (`request_count` in its
    # summary), so it moves when that sequence does.
    summary = json.loads((output / "summary.json").read_text())
    assert len(requests) == summary["request_count"], len(requests)
    # Every tool ran through the released plugin, never the one it replaced.
    assert not [r for r in requests if "BEFORE_RELEASE" in r.read_text()]


if __name__ == "__main__":
    main()
