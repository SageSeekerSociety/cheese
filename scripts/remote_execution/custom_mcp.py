"""A user-defined MCP server that records its actual process environment."""

import json
import os
import socket
import sys
from pathlib import Path

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
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
