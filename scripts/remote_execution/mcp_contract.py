"""What the executor needs `claude mcp serve` to keep doing.

The executor runs its shell commands through the assigned machine's
`claude mcp serve`, so that a command's working directory, its escape back to
the workspace root, and its output all behave the way the same build's own Bash
tool behaves. Three of those properties are not in any contract Anthropic
publishes, and one of them — where a backgrounded command's output lands on
disk — is a path we read directly, because the build offers no way back to a
task its own Bash tool backgrounded: `TaskStop` answers `No task found` for
the id, and 2.1.277 stopped serving `TaskOutput` at all.

So this script is the contract. It runs against a given build and says which
properties still hold. Point it at the pinned build to gate a merge, and at the
newest published build to learn that the next upgrade will break us before the
upgrade is what we are debugging.

The last check is inverted on purpose: it asserts that the build still
offers no way back to a backgrounded task. The day that check fails, the
workaround in `runtime.py` has become dead weight and goes.

The receipt is a JUnit report because that is what
`backend/scripts/assert_suite_ran.py` reads: a check this script never got to
is the same failure as a test the runner skipped, and both are caught by the
one gate rather than by a second thing that counts checks.

Usage:
    python3 mcp_contract.py --claude <binary> [--output <receipts dir>]
"""

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "backend/app/domain/agent/harness/claude_code/remote_execution"
    ),
)
from runtime import serve_task_output


class Serve:
    """One `claude mcp serve` process, driven over stdio."""

    def __init__(self, binary, workspace, temp_root):
        self.workspace = Path(workspace).resolve()
        self.temp_root = Path(temp_root).resolve()
        config = Path(tempfile.mkdtemp(prefix="mcp-contract-config-"))
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


def descendants(pid):
    listing = subprocess.run(
        ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True
    ).stdout
    children = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, queue = [], [pid]
    while queue:
        for child in children.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return found


def checks(serve):
    """Each check yields (name, holds, what was observed)."""
    tools = {tool["name"] for tool in serve.call("tools/list", {})["result"]["tools"]}
    needed = {"Bash", "Read", "Edit", "Write", "NotebookEdit"}
    yield (
        "the tools the executor forwards are all served",
        needed <= tools,
        f"missing {sorted(needed - tools)}"
        if needed - tools
        else f"{len(tools)} tools",
    )

    started = time.monotonic()
    slow = serve.request(
        "tools/call",
        {"name": "Bash", "arguments": {"command": "sleep 3; echo slow"}},
    )
    fast = serve.request(
        "tools/call", {"name": "Bash", "arguments": {"command": "echo fast"}}
    )
    first = serve.read()
    elapsed = time.monotonic() - started
    serve.read()
    yield (
        "two commands run at once rather than queueing",
        first.get("id") == fast and elapsed < 2,
        f"first answer was id={first.get('id')} after {elapsed:.2f}s (slow={slow})",
    )

    (serve.workspace / "sub").mkdir(exist_ok=True)
    serve.tool("Bash", command="cd sub")
    where = serve.tool("Bash", command="pwd").get("stdout", "").strip()
    yield (
        "a command's cd is still in force for the next command",
        where == str(serve.workspace / "sub"),
        where,
    )

    serve.tool("Bash", command="cd /")
    where = serve.tool("Bash", command="pwd").get("stdout", "").strip()
    yield (
        "a command that leaves the workspace does not take the next one with it",
        where.startswith(str(serve.workspace)),
        where,
    )

    background = serve.tool(
        "Bash",
        command="echo first-line; sleep 1; echo second-line",
        run_in_background=True,
    )
    task = background.get("backgroundTaskId")
    yield (
        "a backgrounded command answers with an id",
        bool(task),
        json.dumps(background)[:120],
    )
    if not task:
        return

    deadline, found = time.monotonic() + 10, None
    while time.monotonic() < deadline:
        found = serve_task_output(serve.temp_root, task)
        if found and found.read_text().strip().endswith("second-line"):
            break
        time.sleep(0.2)
    yield (
        "a backgrounded command's whole output is on disk where we look for it",
        bool(found) and found.read_text().split() == ["first-line", "second-line"],
        f"{found}: {found.read_text()!r}" if found else "no file under our temp root",
    )

    serve.tool("Bash", command="sleep 47", run_in_background=True)
    time.sleep(0.5)
    sleepers = [
        pid
        for pid in descendants(serve.process.pid)
        if "sleep 47"
        in subprocess.run(
            ["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True
        ).stdout
    ]
    for pid in sleepers:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    time.sleep(0.5)
    left = subprocess.run(
        ["pgrep", "-f", "sleep 47"], capture_output=True, text=True
    ).stdout
    yield (
        "we can find and stop a running command ourselves",
        bool(sleepers) and not left.strip(),
        f"found {len(sleepers)} process(es), {len(left.split())} left after the kill",
    )

    # Either the tool is not served at all (2.1.277 dropped TaskOutput from
    # serve mode) or it answers `No task found` for the id Bash just handed out
    # (every build so far, for TaskStop). Both mean the same: no way back to
    # the task through the build, which is why the executor keeps its own.
    def blind(name):
        answer = serve.tool(name, task_id=task)
        text = answer.get("error", "") or answer.get("raw", "") or json.dumps(answer)
        return "not found" in text.lower() or "no task found" in text.lower()

    yield (
        "the build STILL offers no way back to a backgrounded task (delete our workaround when this fails)",
        blind("TaskOutput") and blind("TaskStop"),
        "TaskOutput/TaskStop absent or blind"
        if blind("TaskOutput") and blind("TaskStop")
        else "one of them now finds the task",
    )


def junit(binary, version, results):
    """The checks as a JUnit report, so one gate reads every suite's evidence."""
    suite = ET.Element(
        "testsuite",
        name="mcp-contract",
        tests=str(len(results)),
        failures=str(sum(not r["holds"] for r in results)),
    )
    properties = ET.SubElement(suite, "properties")
    ET.SubElement(properties, "property", name="claude", value=binary)
    ET.SubElement(properties, "property", name="version", value=version)
    for result in results:
        case = ET.SubElement(
            suite, "testcase", classname="mcp-contract", name=result["check"]
        )
        if result["holds"]:
            ET.SubElement(case, "system-out").text = result["observed"]
        else:
            ET.SubElement(case, "failure", message=result["observed"])
    return ET.tostring(ET.ElementTree(suite).getroot(), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True, help="the claude binary to check")
    parser.add_argument("--output", type=Path, help="where to write the receipt")
    arguments = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="mcp-contract-"))
    workspace, temp_root = root / "workspace", root / "temp"
    workspace.mkdir()
    temp_root.mkdir()
    version = subprocess.run(
        [arguments.claude, "--version"], capture_output=True, text=True
    ).stdout.strip()
    serve = Serve(arguments.claude, workspace, temp_root)
    results = []
    try:
        for name, holds, observed in checks(serve):
            results.append({"check": name, "holds": holds, "observed": observed})
            print(f"{'PASS' if holds else 'FAIL'}  {name}\n      {observed}")
    finally:
        serve.close()
    held = all(result["holds"] for result in results)
    if arguments.output:
        arguments.output.mkdir(parents=True, exist_ok=True)
        (arguments.output / "results.xml").write_bytes(
            junit(str(arguments.claude), version, results)
        )
    shutil.rmtree(root, ignore_errors=True)
    print(f"\n{version}: {sum(r['holds'] for r in results)}/{len(results)} held")
    return 0 if held else 1


if __name__ == "__main__":
    raise SystemExit(main())
