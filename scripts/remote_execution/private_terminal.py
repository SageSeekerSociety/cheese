"""Drive central chat turns through Claude Code, held by the runner as a room holds it."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from model_fixture import Handler, Server, dump

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/app/domain/agent/harness/claude_code/remote_execution"
sys.path.insert(0, str(SOURCE))
import release as execution_release  # noqa: E402 — from the source tree above

sys.path.insert(0, str(ROOT / "backend"))
from tests.support.harness_prompts import event_prompts, system_prompt  # noqa: E402

sys.path.insert(0, str(SOURCE))
from client import RemoteClient  # noqa: E402
from private import release, target  # noqa: E402

import runner_fixture  # noqa: E402


def checkout_after_the_round(room):
    """Read the checkout's own working-tree status once the round is over.

    This is the acceptance behind 结论 49 / 不变量 I21b, and it is the half the
    unit guards cannot reach. They see nothing of the files the platform lays
    down by shipping shell to `hub.exec`, nothing of what the agent's own
    harness writes, and nothing of a path assembled at runtime. The checkout
    can answer all of that itself, because git already tracks exactly the
    distinction the rule is about: what is in the tree, and what is sitting
    untracked beside it in somebody's repository waiting for them to wonder who
    put it there.
    """

    def git(*arguments):
        return subprocess.run(
            ["git", "-C", str(room.work), *arguments],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    # Named one by one, not "non-empty is fine": the three files this round's
    # own actions create are the agent's work, and a platform file mixed in
    # among them is precisely what would otherwise read as normal.
    porcelain = git("status", "--porcelain", "--untracked-files=all").splitlines()
    assert set(porcelain) == {"?? custom.txt", "?? draft.md", "?? setup-result"}, (
        porcelain
    )
    # Both lists, because the rule has two halves and they fail differently: a
    # platform directory committed is in `ls-files`, one merely dropped beside
    # the work is only in the untracked half — and that is the half nobody
    # notices until it is in someone else's `git status` forever.
    present = set(git("ls-files").split()) | {line[3:] for line in porcelain}
    for name in (".claude", ".cheese", "docs/topics"):
        intruders = sorted(
            path for path in present if path == name or path.startswith(name + "/")
        )
        assert not intruders, (name, intruders)
    return porcelain


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ordinary", action="store_true")
    args = parser.parse_args()
    platform_system = system_prompt()
    platform_events = list(event_prompts().values())
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    prompt_file = folder / "system-prompt.md"
    prompt_file.write_text(platform_system)
    config = target(uuid.uuid4())
    room = None
    handler = Handler
    work = "/work"
    if args.ordinary:
        from room_fixture import RoomExecutor

        room = RoomExecutor(folder, args.claude)
        config = room.target
        work = str(room.work)
        os.environ["CHEESE_TOKEN"] = "room-fixture-token"
        handler = room.handler(handler)
    server = Server(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{server.server_port}"
    if room:
        config["url"] = base + "/execution"
        room.set_api(base)
    actions = [
        {
            "name": "Write",
            "input": {"file_path": work + "/draft.md", "content": "Private draft\n"},
        },
        {"name": "Read", "input": {"file_path": work + "/draft.md"}},
        {
            "name": "Edit",
            "input": {
                "file_path": work + "/draft.md",
                "old_string": "Private",
                "new_string": "Revised",
            },
        },
    ]
    server.state = {"dir": folder, "actions": actions, "requests": []}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sessions = []

    def turn(text, count):
        ended = sessions[-1].turn(text)
        assert len(server.state["requests"]) == count, ended
        assert ended.get("is_error") is False, ended
        results = [
            block
            for message in server.state["requests"][-1]["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        assert results and not any(block.get("is_error") for block in results), results

    def start(home, name, resume=None):
        env = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "LANG", "SHELL", "DOCKER_HOST", "DOCKER_CONTEXT")
        }
        env.update(
            runner_fixture.room_home(home, config),
            ANTHROPIC_BASE_URL=base,
            ANTHROPIC_AUTH_TOKEN="fixture-no-real-credential",
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
        )
        if room:
            env["CHEESE_TOKEN"] = "room-fixture-token"
        if resume:
            env["CHEESE_RESUME_SESSION"] = resume
        command = runner_fixture.room_command(
            home, args.claude, ["--append-system-prompt-file", str(prompt_file)]
        )
        dump(folder / f"{name}-launch.json", {"command": command, "env": env})
        sessions.append(runner_fixture.Session.start(folder, command, env, home, name))
        return home / ".cheese/remote-session"

    homes = [folder / "home"]
    try:
        prepared = start(homes[0], "runner")
        turn("Prepare and revise a private document draft.\n" + platform_events[0], 4)
        actions.extend(
            [
                None,
                {
                    "name": "Bash",
                    "input": {
                        "command": "python3 - <<'PY'\nfrom pathlib import Path\nassert Path('draft.md').read_text() == 'Revised draft\\n'\nprint('CROSS_TURN_SHELL_OK')\nPY"
                    },
                },
            ]
        )
        if room:
            actions.append(
                {"name": "mcp__custom__echo", "input": {"message": "ROOM_CUSTOM_MCP"}}
            )
            actions.append(
                {"name": "Bash", "input": {"command": f"cheese worktree {room.task}"}}
            )
        turn(
            "Continue processing the draft from the last message.\n"
            + platform_events[1],
            8 if room else 6,
        )
        assert "CROSS_TURN_SHELL_OK" in json.dumps(server.state["requests"][-1])
        central = Path(json.loads((prepared / "launch.json").read_text())["cwd"])
        assert not (central / "draft.md").exists()
        assert (
            RemoteClient(config).control(
                {"subtype": "read_file", "path": work + "/draft.md"}
            )["contents"]
            == "Revised draft\n"
        )
        if room:
            assert (room.work / "custom.txt").read_text() == "ROOM_CUSTOM_MCP"
            assert (room.work / "setup-result").read_text() == "executor-env"
            assert not (room.work / "backend").exists()
            task_work = room.home / ".cheese/tasks" / room.task
            assert (task_work / "backend/startup-result").read_text() == "executor-env"
            assert not (central / "custom.txt").exists()
            # Resume the original bytes under a different HOME and cwd, as a
            # session migration does. The chunked transfer has its own test.
            sessions[-1].stop(folder / "runner-journal.jsonl")
            source = homes[0] / ".claude/projects"
            originals = {
                p.relative_to(source): p.read_bytes() for p in source.rglob("*.jsonl")
            }
            assert len(originals) == 1, list(originals)
            resume = next(iter(originals)).stem
            homes.append(folder / "resumed-home")
            shutil.copytree(source, homes[1] / ".claude/projects")
            resumed = start(homes[1], "resumed-runner", resume)
            actions.extend(
                [None, {"name": "Read", "input": {"file_path": work + "/draft.md"}}]
            )
            turn(
                "Resume the same conversation and read the revised draft.\n"
                + platform_events[2],
                10,
            )
            assert sessions[-1].call("ping")["session_id"] == resume
            history = json.dumps(server.state["requests"][-1]["messages"])
            assert "Prepare and revise a private document draft." in history
            assert "ROOM_CUSTOM_MCP" in history
            assert "Revised draft" in history
            resumed_cwd = json.loads((resumed / "launch.json").read_text())["cwd"]
            assert not (Path(resumed_cwd) / "draft.md").exists()
            assert {
                p.relative_to(source): p.read_bytes() for p in source.rglob("*.jsonl")
            } == originals
        for event in platform_events[3 if room else 2 :]:
            turn(event, len(server.state["requests"]) + 1)
        requests = server.state["requests"]
        for request in requests:
            blocks = request["system"]
            system = "\n".join(block.get("text", "") for block in blocks)
            assert system.count(platform_system) == 1, system
            assert "Bash" in {tool["name"] for tool in request["tools"]}
        history = requests[-1]["messages"]
        user_text = "\n".join(
            message["content"]
            if isinstance(message["content"], str)
            else "\n".join(block.get("text", "") for block in message["content"])
            for message in history
            if message["role"] == "user"
        )
        for event in platform_events:
            assert event in user_text, event
        checkout = None
        if room:
            checkout = checkout_after_the_round(room)
            assert room.snapshots, "room turns did not upload a task snapshot"
            snapshot = next(reversed(room.snapshots.values()))
            recovered = folder / "snapshot-recovered"
            subprocess.run(
                ["git", "clone", "-q", room.remote, str(recovered)], check=True
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(recovered),
                    "fetch",
                    snapshot["bundle"],
                    f"refs/cheese/snapshots/{room.task}",
                ],
                check=True,
                capture_output=True,
            )
            restored = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(recovered),
                    "show",
                    "FETCH_HEAD:backend/startup-result",
                ],
                text=True,
            )
            assert restored == "executor-env", restored
            dump(folder / "snapshot-receipts.json", list(room.snapshots.values()))
        server.assert_healthy()
        dump(folder / "provider-requests.json", requests)
        summary = {
            "passed": True,
            "turns": len(platform_events),
            "platform_system_exact": True,
            "platform_events": list(event_prompts()),
            "model_requests": len(server.state["requests"]),
            "central_file_unchanged": True,
            **({"checkout_after_the_round": checkout} if checkout else {}),
        }
    finally:
        for index, session in enumerate(sessions):
            session.stop(folder / f"journal-{index}.jsonl")
        if room:
            for mountpoint in (
                *(home / ".cheese/remote-session/forwarded-project" for home in homes),
                room.home / ".cheese/remote-session/forwarded-project",
            ):
                # See acceptance.py: a dead mount is the one that has to go, and
                # it is the one `os.path.ismount` reports as nothing at all.
                assert execution_release.release_mount(mountpoint), mountpoint
        try:
            if room:
                room.close()
            else:
                release(config)
        finally:
            server.shutdown()
            server.server_close()

    dump(folder / "summary.json", summary)


if __name__ == "__main__":
    main()
