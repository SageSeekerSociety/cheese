#!/usr/bin/env python3
"""Forward an executor CLI invocation without loading the command implementation."""

import array
import json
import os
import runpy
import socket
import sys
from pathlib import Path


def main():
    if sys.platform == "win32":
        # Windows can neither fork a preloaded CLI nor hand it this process's
        # descriptors, so the CLI runs here: bin/cheese -> the release's cheese.
        cli = Path(__file__).resolve().parents[2] / "cheese"
        sys.argv[0] = str(cli)
        runpy.run_path(str(cli), run_name="__main__")
        return
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(os.environ["CHEESE_CLI_SOCKET"])
        connection.sendmsg(
            [b"\0"],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [0, 1, 2]))],
        )
        connection.sendall(
            json.dumps(
                {
                    "argv": sys.argv[1:],
                    "cwd": os.getcwd(),
                    "env": dict(os.environ),
                    "stdio": [
                        {"encoding": stream.encoding, "errors": stream.errors}
                        for stream in (sys.stdin, sys.stdout, sys.stderr)
                    ],
                }
            ).encode()
            + b"\n"
        )
        with connection.makefile("rb") as stream:
            line = stream.readline()
        if not line:
            raise SystemExit(
                "[cheese] 常驻进程已断开；请先查询原请求结果，再决定是否重试。"
            )
        raise SystemExit(json.loads(line)["status"])


if __name__ == "__main__":
    main()
