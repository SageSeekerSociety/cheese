"""A user-defined MCP server that records its actual process environment."""

import json
import os
import socket
import sys
import time
from pathlib import Path


def record(event):
    """When this process started and when it was asked to initialize, on the
    host's monotonic clock, for a caller that has to know whether either
    happened before something it did (`headless_contract`)."""
    if os.environ.get("MCP_STARTUP_LOG"):
        with open(os.environ["MCP_STARTUP_LOG"], "a") as log:
            log.write(
                json.dumps(
                    {"pid": os.getpid(), "event": event, "monotonic": time.monotonic()}
                )
                + "\n"
            )


record("start")
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        record("initialize")
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "arbitrary-custom-fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "Record and return an acceptance marker.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                }
            ]
        }
    elif method == "tools/call":
        marker = request["params"]["arguments"]["message"]
        Path("custom.txt").write_text(marker)
        result = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "hostname": socket.gethostname(),
                            "cwd": os.getcwd(),
                            "message": marker,
                            "environment_marker": os.environ.get("MCP_TEST_MARKER"),
                        }
                    ),
                }
            ]
        }
    else:
        result = {}
    print(
        json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
        flush=True,
    )
