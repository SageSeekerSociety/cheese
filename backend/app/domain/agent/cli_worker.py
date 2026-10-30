"""平台 CLI 在机器上的那一份：一个预加载进程。

Nothing here is about any one harness. What it knows is how to run the
platform CLI without paying the interpreter's startup on every call: the
Claude Code executor preloads it so a Bash `cheese …` starts fast. The CLI's
commands are no tools in any harness; a session runs them as `cheese …` in its
bash. The platform's own tools are the session-side table in the same file
(`PLATFORM_TOOLS`, 结论 63).

It lived inside the Claude Code adapter until 2026-09-17, which is the only
reason this docstring says so — it was the first caller, not the owner, and a
second harness reaching into that package to fetch it was the architecture
guard going off rather than a debt to write down.

Standard library only, and one file. It is shipped to a session machine as a
loose script, so it cannot import a sibling of its own.
"""

import array
import json
import os
import select
import signal
import socket
import socketserver
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, cast


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = cast(Server, self.server)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.setsid()
        _, ancillary, _, _ = self.request.recvmsg(
            1, socket.CMSG_SPACE(3 * array.array("i").itemsize)
        )
        descriptors = array.array("i")
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors.frombytes(data)
        if len(descriptors) != 3:
            raise ValueError("CLI invocation requires stdin, stdout and stderr")
        stream = self.request.makefile("rb")
        payload = json.loads(stream.readline())
        os.chdir(payload["cwd"])
        os.environ.clear()
        os.environ.update(payload["env"])
        # Recreate wrappers: inherited file streams retain seekability after dup2.
        for standard_stream in (sys.stdin, sys.stdout, sys.stderr):
            standard_stream.close()
        for target, descriptor in enumerate(descriptors):
            os.dup2(descriptor, target)
            os.close(descriptor)
        sys.stdin = os.fdopen(0, "r", **payload["stdio"][0])
        sys.stdout = os.fdopen(1, "w", **payload["stdio"][1])
        # A line at a time, as Python's own stderr is: a command's warning must
        # reach the caller while it runs, and one a caller merges with stdout
        # (`2>&1`) has to stay where the command wrote it. Block buffering
        # here held every warning to the end, after stdout's last line — a
        # launch that reads the CLI's last line as its answer got the warning.
        sys.stderr = os.fdopen(2, "w", buffering=1, **payload["stdio"][2])
        done = threading.Event()

        def disconnected():
            if stream.read(1) == b"" and not done.is_set():
                os.killpg(os.getpid(), signal.SIGTERM)

        threading.Thread(target=disconnected, daemon=True).start()
        status = 0
        try:
            sys.argv = [str(server.source), *payload["argv"]]
            exec(
                server.code,
                {
                    "__name__": "__main__",
                    "__file__": str(server.source),
                },
            )
        except SystemExit as exc:
            status = exc.code if isinstance(exc.code, int) else 1 if exc.code else 0
            if exc.code and not isinstance(exc.code, int):
                print(exc.code, file=sys.stderr)
        except BaseException:
            traceback.print_exc()
            status = 1
        # stderr first: a warning written without its newline is still here,
        # and it was written before stdout's last line.
        sys.stderr.flush()
        sys.stdout.flush()
        done.set()
        self.request.sendall(
            json.dumps({"status": status, "pid": os.getpid()}).encode() + b"\n"
        )


class Server(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
    def __init__(self, address, source):
        self.source = Path(source)
        self.signature = None
        self.refresh()
        super().__init__(address, Handler)
        os.chmod(address, 0o600)

    def refresh(self):
        stat = self.source.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if signature == self.signature:
            return
        self.code = compile(self.source.read_text(), str(self.source), "exec")
        namespace: dict[str, Any] = {
            "__name__": "preload",
            "__file__": str(self.source),
        }
        exec(self.code, namespace)
        if threading.active_count() != 1:
            raise RuntimeError("CLI preload must remain single-threaded before fork")
        self.signature = signature

    def process_request(self, request, client_address):
        self.refresh()
        super().process_request(request, client_address)


if __name__ == "__main__":
    with Server(sys.argv[1], sys.argv[2]) as server:
        print("ready", flush=True)
        try:
            while True:
                readable, _, _ = select.select([server, sys.stdin], [], [], 0.1)
                if sys.stdin in readable and not os.read(sys.stdin.fileno(), 1):
                    break
                if server in readable:
                    server.handle_request()
                server.service_actions()
        finally:
            server.collect_children()
            for child in server.active_children or ():
                try:
                    os.killpg(child, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    try:
                        os.kill(child, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
            Path(sys.argv[1]).unlink(missing_ok=True)
