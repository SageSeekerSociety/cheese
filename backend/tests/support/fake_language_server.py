"""A language server small enough to read: the LSP base protocol over stdio.

Installed on a test machine's ``PATH`` under the name the registry starts
(`runtime_language.SERVERS`), so a test needs no real one. It reads framed
requests (``Content-Length``) and answers each: ``whoami`` with where and as
whom it runs, ``exit`` by exiting, anything else by echoing its params. It
writes to stderr as it starts, which must not reach the client.

Standard library only: it runs under the interpreter its ``#!`` line names.
"""

import json
import os
import sys
from pathlib import Path


def install(directory: Path, name: str = "clangd") -> Path:
    """Put this server in ``directory`` as ``name``, runnable."""
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / name
    program.write_text(f"#!{sys.executable}\n" + Path(__file__).read_text())
    program.chmod(0o755)
    return program


def frame(message: dict) -> bytes:
    body = json.dumps(message).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _read(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode().partition(":")
        if name.strip().lower() == "content-length":
            length = int(value)
    return json.loads(stream.read(length or 0))


def main() -> None:
    sys.stderr.write("fake language server: starting\n")
    sys.stderr.flush()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while (message := _read(stdin)) is not None:
        method = message.get("method")
        if method == "exit":
            return
        if method == "whoami":
            result = {
                "cwd": os.getcwd(),
                "pid": os.getpid(),
                "argv": sys.argv[1:],
                "attachment": os.environ.get("CHEESE_ATTACHMENT"),
                "author": os.environ.get("CHEESE_AUTHOR"),
            }
        else:
            result = message.get("params")
        stdout.write(
            frame({"jsonrpc": "2.0", "id": message.get("id"), "result": result})
        )
        stdout.flush()


if __name__ == "__main__":
    main()
