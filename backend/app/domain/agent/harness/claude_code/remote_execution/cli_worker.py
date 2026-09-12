"""Single-threaded CLI preload process; each invocation runs in its own child."""

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
from typing import cast


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
        sys.argv = [str(server.source), *payload["argv"]]
        for target, descriptor in enumerate(descriptors):
            os.dup2(descriptor, target)
            os.close(descriptor)
        done = threading.Event()

        def disconnected():
            if stream.read(1) == b"" and not done.is_set():
                os.killpg(os.getpid(), signal.SIGTERM)

        threading.Thread(target=disconnected, daemon=True).start()
        status = 0
        try:
            exec(
                server.code,
                {
                    "__name__": "__main__",
                    "__file__": str(server.source),
                    "__cheese_worker__": True,
                },
            )
        except SystemExit as exc:
            status = exc.code if isinstance(exc.code, int) else 1 if exc.code else 0
            if exc.code and not isinstance(exc.code, int):
                print(exc.code, file=sys.stderr)
        except BaseException:
            traceback.print_exc()
            status = 1
        sys.stdout.flush()
        sys.stderr.flush()
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
        exec(self.code, {"__name__": "preload", "__file__": str(self.source)})
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
                except ProcessLookupError:
                    try:
                        os.kill(child, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            Path(sys.argv[1]).unlink(missing_ok=True)
