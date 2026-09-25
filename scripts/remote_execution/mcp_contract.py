"""What the executor needs `claude mcp serve` to keep doing.

The executor runs the room's file operations through the assigned machine's
`claude mcp serve`, so that Read, Edit, Write and NotebookEdit behave the way
the same build's own tools do. Its shell commands it runs as processes of its
own, each starting from the snapshot of the machine's shell that a native
session there would source; the executor has the build write that snapshot by
calling serve's Bash tool once (`runtime.Executor.shell_snapshot`). Where the
snapshot lands and what it holds are in no contract Anthropic publishes.

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
import tempfile
from pathlib import Path


class Serve:
    """One `claude mcp serve` process, driven over stdio."""

    def __init__(self, binary, workspace, temp_root, home):
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
            # The environment the executor gives serve: bash, and a HOME whose
            # shell setup the snapshot has to carry.
            "HOME": str(home),
            "SHELL": shutil.which("bash") or "/bin/bash",
            "PATH": "/opt/mcp-contract-only" + os.pathsep + os.environ.get("PATH", ""),
        }
        for name in (
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_SHELL_PREFIX",
            "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS",
        ):
            environment.pop(name, None)
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


def checks(serve):
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
    snapshots = sorted((serve.config / "shell-snapshots").glob("snapshot-bash-*.sh"))
    yield (
        "one Bash call writes a bash snapshot into CLAUDE_CONFIG_DIR/shell-snapshots",
        len(snapshots) == 1,
        f"{[path.name for path in snapshots]} after {json.dumps(answer)[:80]}",
    )
    if not snapshots:
        return
    probe = subprocess.run(
        [
            shutil.which("bash") or "/bin/bash",
            "-c",
            # One line each: an alias is expanded only on a line read after
            # the one that defined it, as in a native session's next command.
            f"source {shlex.quote(str(snapshots[0]))}\ncontract_alias\n"
            "contract_function\nprintf '%s' \"$PATH\"",
        ],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        capture_output=True,
        text=True,
    )
    lines = probe.stdout.splitlines()
    yield (
        "sourcing it reproduces the HOME's alias, function and serve's PATH",
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
    (home / ".bashrc").write_text(
        "alias contract_alias='echo CONTRACT_ALIAS'\n"
        "contract_function() { echo CONTRACT_FUNCTION; }\n"
    )
    version = subprocess.run(
        [arguments.claude, "--version"], capture_output=True, text=True
    ).stdout.strip()
    serve = Serve(arguments.claude, workspace, temp_root, home)
    results = []
    try:
        for name, holds, observed in checks(serve):
            results.append({"check": name, "holds": holds, "observed": observed})
            print(f"{'PASS' if holds else 'FAIL'}  {name}\n      {observed}")
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
