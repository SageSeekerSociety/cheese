"""Smoke-test the native terminal using an existing Claude subscription."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from acceptance import client, run, setup
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
    executor, target = setup(folder, options)
    tmux = ["tmux", "-L", "cheese-real-model-" + folder.name]
    try:
        launch = client.prepare(
            folder / "central",
            target,
            claude=options.claude,
            extra_args=[
                "--model",
                "claude-sonnet-4-6",
                "--dangerously-skip-permissions",
            ],
        )
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
            launch["env"],
            TERM="xterm-256color",
            CLAUDE_CODE_OAUTH_TOKEN=oauth["accessToken"],
            CLAUDE_CODE_OAUTH_SCOPES=" ".join(oauth.get("scopes", [])),
        )
        center = Path(launch["cwd"])
        (center / "target.txt").write_text("CENTER_SENTINEL\n")
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
        run(
            tmux
            + [
                "new-session",
                "-d",
                "-s",
                "agent",
                "-x",
                "120",
                "-y",
                "40",
                "-c",
                str(center),
                shlex.join(launch["command"]),
            ],
            env=env,
        )
        time.sleep(5)
        run(tmux + ["send-keys", "-t", "agent", "-l", prompt])
        run(tmux + ["send-keys", "-t", "agent", "Enter"])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            terminal = run(tmux + ["capture-pane", "-p", "-t", "agent", "-S", "-300"])
            (folder / "terminal.txt").write_text(terminal)
            # The response follows the prompt, which also contains the marker.
            if "⏺ REAL_MODEL_DONE" in terminal:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Real model did not finish; see terminal.txt")
        assert (center / "target.txt").read_text() == "CENTER_SENTINEL\n"
        assert not (center / "new.txt").exists()
        for name, expected in (
            ("target.txt", "AFTER_REAL_MODEL"),
            ("new.txt", "REAL_MODEL_WRITE"),
            ("command.txt", "REMOTE_COMMAND_ENV"),
            ("custom.txt", "REAL_MODEL_CUSTOM"),
        ):
            result = executor.control({"subtype": "read_file", "path": name})
            assert expected in result["contents"], result
        assert "REMOTE_SKILL_SENTINEL" in "".join(
            p.read_text() for p in (folder / "central/config/projects").rglob("*.jsonl")
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
        subprocess.run(tmux + ["kill-server"], capture_output=True, timeout=10)
        subprocess.run(executor.command("stop"), capture_output=True, timeout=20)


if __name__ == "__main__":
    main()
