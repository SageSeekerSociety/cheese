"""Smoke-test a room's session, held by the runner, on an existing Claude subscription."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from acceptance import execution_release, run, setup
from model_fixture import dump, log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--ssh")
    parser.add_argument("--remote-root")
    parser.add_argument("--remote-python", default="python3")
    parser.add_argument("--remote-claude", default="claude")
    options = parser.parse_args()
    folder = options.output.resolve()
    folder.mkdir(parents=True)
    # No platform API here: nothing in this smoke reaches it.
    executor, target = setup(folder, options, "http://127.0.0.1:9")
    home = folder / "home"
    center = home / ".cheese/remote-session/forwarded-project"
    center.mkdir(parents=True)
    session = None
    center_fd = None
    try:
        import runner_fixture

        # Written beneath the mount before there is one, and read back through
        # a descriptor held from before it, so a write that lands centrally
        # instead of on the executor shows.
        (center / "target.txt").write_text("CENTER_SENTINEL\n")
        center_fd = os.open(center, os.O_RDONLY | os.O_DIRECTORY)
        credentials_file = Path.home() / ".claude/.credentials.json"
        if credentials_file.exists():
            credentials = json.loads(credentials_file.read_text())
        else:
            credentials = json.loads(
                run(
                    [
                        "security",
                        "find-generic-password",
                        "-s",
                        "Claude Code-credentials",
                        "-w",
                    ]
                )
            )
        oauth = credentials["claudeAiOauth"]
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            in (
                "PATH",
                "LANG",
                "SHELL",
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "NO_PROXY",
                "NODE_EXTRA_CA_CERTS",
            )
        }
        env.update(
            runner_fixture.room_home(home, target),
            CLAUDE_CODE_OAUTH_TOKEN=oauth["accessToken"],
            CLAUDE_CODE_OAUTH_SCOPES=" ".join(oauth.get("scopes", [])),
        )
        prompt = (
            "Run this acceptance test with the named native tools. "
            "Read target.txt, then Edit BEFORE_EDIT to AFTER_REAL_MODEL. "
            "Use Write to create new.txt containing REAL_MODEL_WRITE. "
            "Use Bash to run: printf '%s' \"$EXECUTION_ENV\" > command.txt. "
            "Call mcp__custom__echo with message REAL_MODEL_CUSTOM. "
            "Invoke Skill remote-check. Do not inspect other projects or change anything else. "
            "After these operations succeed, reply REAL_MODEL_DONE."
        )
        dump(
            folder / "inputs.json",
            {
                "prompt": prompt,
                "version": run([options.claude, "--version"]).strip(),
                "ssh": options.ssh,
            },
        )
        log(folder / "progress.jsonl", {"item": "real-model", "status": "started"})
        command = runner_fixture.room_command(
            home, options.claude, ["--model", "claude-sonnet-4-6"]
        )
        session = runner_fixture.Session.start(folder, command, env, home)
        ended = session.turn(prompt, 180)
        assert "REAL_MODEL_DONE" in (ended.get("result") or ""), ended
        with open(os.open("target.txt", os.O_RDONLY, dir_fd=center_fd)) as central:
            assert central.read() == "CENTER_SENTINEL\n"
        try:
            os.close(os.open("new.txt", os.O_RDONLY, dir_fd=center_fd))
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("Write landed in the central workspace")
        for name, expected in (
            ("target.txt", "AFTER_REAL_MODEL"),
            ("new.txt", "REAL_MODEL_WRITE"),
            ("command.txt", "REMOTE_COMMAND_ENV"),
            ("custom.txt", "REAL_MODEL_CUSTOM"),
        ):
            result = executor.control({"subtype": "read_file", "path": name})
            assert expected in result["contents"], result
        assert "REMOTE_SKILL_SENTINEL" in "".join(
            p.read_text() for p in (home / ".claude/projects").rglob("*.jsonl")
        )
        dump(
            folder / "summary.json",
            {
                "passed": True,
                "real_model": True,
                "central_unchanged": True,
                "ssh": options.ssh,
            },
        )
        log(folder / "progress.jsonl", {"item": "real-model", "status": "passed"})
        print(json.dumps({"passed": True, "output": str(folder)}), flush=True)
    finally:
        if center_fd is not None:
            os.close(center_fd)
        if session is not None:
            session.stop(folder / "journal.jsonl")
        assert execution_release.release_mount(center), center
        subprocess.run(executor.command("stop"), capture_output=True, timeout=20)


if __name__ == "__main__":
    main()
