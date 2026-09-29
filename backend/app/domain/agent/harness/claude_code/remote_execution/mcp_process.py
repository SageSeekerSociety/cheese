"""The executor's client for one stdio MCP process, shipped beside runtime.py.

It speaks to a server the executor started — a checkout's `.mcp.json` server,
an agent type's, or the build's own `mcp serve` — over the process's stdin and
stdout. Standard library only, like everything the executor runs.
"""

import contextlib
import json
import os
import queue
import runpy
import signal
import subprocess
import sys
import threading
from pathlib import Path


def portable():
    """The Windows primitives shipped beside this file; see portable.py."""
    return runpy.run_path(str(Path(__file__).with_name("portable.py")))


class MCPProcess:
    def __init__(self, command, cwd, env, log):
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
        self.lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cheese-execution", "version": "0.1.0"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, value):
        with self.lock:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(value).encode() + b"\n")
            self.process.stdin.flush()

    def _read(self):
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                data = json.loads(line)
                if "method" in data:
                    if "id" in data:
                        self.send(
                            {
                                "jsonrpc": "2.0",
                                "id": data["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Executor has no client-side "
                                    "sampling or elicitation handler",
                                },
                            }
                        )
                    continue
                pending = self.pending.get(data.get("id"))
                if pending:
                    pending.put(data)
        finally:
            for pending in list(self.pending.values()):
                pending.put({"error": {"message": "MCP process disconnected"}})

    def begin(self, method, params=None):
        """Send a request; its answer lands on the returned queue."""
        with self.lock:
            self.sequence += 1
            key = self.sequence
            answers = self.pending[key] = queue.Queue()
        self.send(
            {"jsonrpc": "2.0", "id": key, "method": method, "params": params or {}}
        )
        return key, answers

    def finish(self, key, answers, timeout=300):
        """Take the answer to a request begun earlier, or raise on error."""
        try:
            data = answers.get(timeout=timeout)
        finally:
            self.pending.pop(key, None)
        if "error" in data:
            raise RuntimeError(data["error"]["message"])
        return data["result"]

    def call(self, method, params=None):
        key, answers = self.begin(method, params)
        return self.finish(key, answers)

    def close(self):
        if sys.platform == "win32":
            portable()["terminate_tree"](self.process.pid)
            self.process.wait()
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
