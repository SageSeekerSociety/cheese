"""What the executor needs `claude mcp serve` to keep doing.

The executor runs the room's file operations through the assigned machine's
`claude mcp serve`, so that Read, Edit, Write and NotebookEdit behave the way
the same build's own tools do. Its shell commands it runs as processes of its
own, each starting from the snapshot of the machine's shell that a native
session there would source; the executor has the build write that snapshot by
calling serve's Bash tool once (`runtime.Executor.shell_snapshot`), holding it
to the shell the build would choose on that machine by itself
(`runtime.claude_shell`). Where the snapshot lands, what it holds and how the
build chooses its shell are in no contract Anthropic publishes.

So this script is the contract. It runs against a given build and says which
properties still hold. Point it at the pinned build to gate a merge, and at the
newest published build to learn that the next upgrade will break us before the
upgrade is what we are debugging.

Usage:
    python3 mcp_contract.py --claude <binary> [--output <receipts dir>]
"""

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "backend/app/domain/agent/harness/claude_code/remote_execution"
    ),
)
import runtime  # noqa: E402


class Serve:
    """One `claude mcp serve` process, driven over stdio."""

    def __init__(self, binary, workspace, temp_root, home, shell_env):
        self.workspace = Path(workspace).resolve()
        self.temp_root = Path(temp_root).resolve()
        self.config = config = Path(tempfile.mkdtemp(prefix="mcp-contract-config-"))
        (config / ".claude.json").write_text(
            json.dumps(
                {
                    "hasCompletedOnboarding": True,
                    "bypassPermissionsModeAccepted": True,
                    "projects": {
                        str(self.workspace): {
                            "hasTrustDialogAccepted": True,
                            "hasCompletedProjectOnboarding": True,
                        }
                    },
                }
            )
        )
        environment = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(config),
            "CLAUDE_CODE_TMPDIR": str(self.temp_root),
            "ANTHROPIC_API_KEY": "execution-only-no-model",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            # A HOME whose shell setup the snapshot has to carry.
            "HOME": str(home),
            "PATH": "/opt/mcp-contract-only" + os.pathsep + os.environ.get("PATH", ""),
        }
        for name in (
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_SHELL_PREFIX",
            "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS",
            "CLAUDE_CODE_SHELL",
            "SHELL",
        ):
            environment.pop(name, None)
        # The shell settings under test: `SHELL`, and `CLAUDE_CODE_SHELL`,
        # which the executor sets to its own choice.
        environment.update(shell_env)
        self.environment = environment
        self.process = subprocess.Popen(
            [binary, "--setting-sources", "", "mcp", "serve"],
            cwd=self.workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.sequence = 0
        self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cheese-contract", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def notify(self, method, params=None):
        self.process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
            + "\n"
        )
        self.process.stdin.flush()

    def request(self, method, params):
        self.sequence += 1
        identifier = self.sequence
        self.process.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
            )
            + "\n"
        )
        self.process.stdin.flush()
        return identifier

    def read(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(
                "serve closed stdout: " + self.process.stderr.read()[-400:]
            )
        return json.loads(line)

    def call(self, method, params):
        identifier = self.request(method, params)
        while True:
            answer = self.read()
            if answer.get("id") == identifier:
                return answer

    def tool(self, name, **arguments):
        answer = self.call("tools/call", {"name": name, "arguments": arguments})
        if "error" in answer and "result" not in answer:
            return {"error": json.dumps(answer["error"])}
        body = answer.get("result") or {}
        text = (body.get("content") or [{}])[0].get("text", "")
        if body.get("isError"):
            return {"error": text}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()


def snapshots(serve):
    return sorted((serve.config / "shell-snapshots").glob("snapshot-*.sh"))


def kind(shell):
    """The name the build gives a shell in its snapshot's file name."""
    return "zsh" if "zsh" in shell else "bash" if "bash" in shell else "sh"


def checks(serve, shell):
    """Each check yields (name, holds, what was observed)."""
    tools = {tool["name"] for tool in serve.call("tools/list", {})["result"]["tools"]}
    needed = {"Bash", "Read", "Edit", "Write", "NotebookEdit"}
    yield (
        "the file tools the executor forwards, and the Bash it writes its snapshot with, are served",
        needed <= tools,
        f"missing {sorted(needed - tools)}"
        if needed - tools
        else f"{len(tools)} tools",
    )
    yield (
        "search uses Bash because native Glob/Grep are not served",
        not ({"Glob", "Grep"} & tools),
        "native search became available; replace the explicit plugin refusal"
        if {"Glob", "Grep"} & tools
        else "Glob/Grep unavailable; Bash is the supported remote search path",
    )

    answer = serve.tool("Bash", command="true")
    written = [
        path
        for path in snapshots(serve)
        if path.name.startswith(f"snapshot-{kind(shell)}-")
    ]
    yield (
        f"with CLAUDE_CODE_SHELL naming {kind(shell)}, one Bash call writes a "
        f"snapshot-{kind(shell)}-* into CLAUDE_CONFIG_DIR/shell-snapshots",
        len(written) == 1 and len(snapshots(serve)) == 1,
        f"{[path.name for path in snapshots(serve)]} after {json.dumps(answer)[:80]}",
    )
    if not written:
        return
    probe = subprocess.run(
        [
            shell,
            "-c",
            # As the build runs a command: the snapshot, then the command
            # through `eval`, which parses it only once the snapshot's
            # aliases exist (zsh parses a whole `-c` string before running
            # any of it).
            f"source {shlex.quote(str(written[0]))} && eval "
            + shlex.quote("contract_alias\ncontract_function\nprintf '%s' \"$PATH\""),
        ],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        capture_output=True,
        text=True,
    )
    lines = probe.stdout.splitlines()
    yield (
        f"sourcing it in {kind(shell)} reproduces the HOME's alias, function "
        "and serve's PATH",
        lines[:2] == ["CONTRACT_ALIAS", "CONTRACT_FUNCTION"]
        and len(lines) == 3
        and lines[2].split(os.pathsep)[0] == "/opt/mcp-contract-only",
        f"rc={probe.returncode} out={probe.stdout[-200:]!r} err={probe.stderr[-200:]!r}",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True, help="the claude binary to check")
    parser.add_argument("--output", type=Path, help="where to write the receipt")
    arguments = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="mcp-contract-"))
    workspace, temp_root, home = root / "workspace", root / "temp", root / "home"
    workspace.mkdir()
    temp_root.mkdir()
    home.mkdir()
    for startup in (".bashrc", ".zshrc"):
        (home / startup).write_text(
            "alias contract_alias='echo CONTRACT_ALIAS'\n"
            "contract_function() { echo CONTRACT_FUNCTION; }\n"
        )
    version = subprocess.run(
        [arguments.claude, "--version"], capture_output=True, text=True
    ).stdout.strip()
    results = []

    def report(name, holds, observed):
        results.append({"check": name, "holds": holds, "observed": observed})
        print(f"{'PASS' if holds else 'FAIL'}  {name}\n      {observed}")

    shells = [path for path in map(shutil.which, ("bash", "zsh")) if path]
    for shell in shells:
        serve = Serve(
            arguments.claude,
            workspace,
            temp_root,
            home,
            {"SHELL": shell, "CLAUDE_CODE_SHELL": shell},
        )
        try:
            for check in checks(serve, shell):
                report(*check)
        finally:
            serve.close()
    # The shell the build picks by itself, from the machine's `SHELL` alone,
    # is the one the executor picks for it.
    for label, value in (
        ("names bash", shutil.which("bash")),
        ("names zsh", shutil.which("zsh")),
        ("names another shell", "/usr/bin/fish"),
        ("is unset", None),
    ):
        if label == "names zsh" and not value:
            continue
        serve = Serve(
            arguments.claude,
            workspace,
            temp_root,
            home,
            {"SHELL": value} if value else {},
        )
        try:
            serve.tool("Bash", command="true")
            chosen = [path.name.split("-")[1] for path in snapshots(serve)]
            expected = kind(runtime.claude_shell(serve.environment))
            report(
                f"when SHELL {label}, the build's shell is the executor's choice",
                chosen == [expected],
                f"build wrote {chosen}, executor picks {expected}",
            )
        finally:
            serve.close()
    receipt = {
        "claude": str(arguments.claude),
        "version": version,
        "checks": results,
        "held": all(result["holds"] for result in results),
    }
    if arguments.output:
        arguments.output.mkdir(parents=True, exist_ok=True)
        (arguments.output / "mcp-contract.json").write_text(
            json.dumps(receipt, indent=2)
        )
    shutil.rmtree(root, ignore_errors=True)
    print(f"\n{version}: {sum(r['holds'] for r in results)}/{len(results)} held")
    return 0 if receipt["held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
