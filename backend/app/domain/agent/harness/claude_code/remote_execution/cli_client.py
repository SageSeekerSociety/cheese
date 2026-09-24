#!/usr/bin/env python3
"""Forward an executor CLI invocation without loading the command implementation."""

import array
import json
import os
import socket
import sys


def main():
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
