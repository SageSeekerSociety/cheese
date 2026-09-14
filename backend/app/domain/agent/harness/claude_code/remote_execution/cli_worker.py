"""Single-threaded CLI preload process; each invocation runs in its own child."""

import argparse
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


def _schema(action):
    value: dict[str, Any] = {"type": "string"}
    if action.type is int:
        value = {"type": "integer"}
    elif action.type is float:
        value = {"type": "number"}
    elif action.type is not None and getattr(action.type, "__name__", "") == "UUID":
        value = {"type": "string", "format": "uuid"}
    if action.choices is not None:
        value["enum"] = list(action.choices)
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        value = {"type": "boolean"}
    elif isinstance(action, argparse._AppendAction) or action.nargs in ("*", "+"):
        value = {"type": "array", "items": value}
    if action.help:
        value["description"] = action.help
    if action.default is not argparse.SUPPRESS and isinstance(
        action.default, (str, int, float, bool, list)
    ):
        value["default"] = action.default
    return value


def _leaf_commands(parser, prefix=()):
    subparsers = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
    )
    if subparsers is None:
        yield prefix, parser
        return
    seen = set()
    for name, child in subparsers.choices.items():
        # argparse aliases point to the same parser; publish its canonical name once.
        if id(child) in seen:
            continue
        seen.add(id(child))
        yield from _leaf_commands(child, (*prefix, name))


def _tool_name(command):
    return "cheese_" + "_".join(part.replace("-", "_") for part in command)


def _tools(parser):
    if parser is None:
        raise RuntimeError("Installed Cheese CLI does not publish an argparse parser")
    tools = []
    for command, leaf in _leaf_commands(parser):
        properties = {}
        required = []
        for action in leaf._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            properties[action.dest] = _schema(action)
            positional = not action.option_strings
            if action.required or (positional and action.nargs not in ("?", "*")):
                required.append(action.dest)
        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        tools.append(
            {
                "name": _tool_name(command),
                "description": leaf.description or leaf.format_usage().strip(),
                "inputSchema": schema,
            }
        )
    return tools


def _command(parser, tool, arguments):
    if parser is None:
        raise RuntimeError("Installed Cheese CLI does not publish an argparse parser")
    for command, leaf in _leaf_commands(parser):
        if _tool_name(command) != tool:
            continue
        option_argv: list[str] = []
        positional_argv: list[str] = []
        known = {
            action.dest
            for action in leaf._actions
            if not isinstance(action, argparse._HelpAction)
        }
        unknown = sorted(set(arguments) - known)
        if unknown:
            raise ValueError("Unknown arguments: " + ", ".join(unknown))
        for action in leaf._actions:
            if isinstance(action, argparse._HelpAction) or action.dest not in arguments:
                continue
            value = arguments[action.dest]
            if not action.option_strings:
                if isinstance(value, list):
                    positional_argv.extend(str(item) for item in value)
                else:
                    positional_argv.append(str(value))
            elif isinstance(action, argparse._StoreTrueAction):
                if value:
                    option_argv.append(action.option_strings[0])
            elif isinstance(action, argparse._StoreFalseAction):
                if not value:
                    option_argv.append(action.option_strings[0])
            elif isinstance(action, argparse._AppendAction):
                for item in value:
                    option_argv.append(f"{action.option_strings[0]}={item}")
            elif action.nargs in ("*", "+"):
                option_argv.append(action.option_strings[0])
                option_argv.extend(str(item) for item in value)
            else:
                option_argv.append(f"{action.option_strings[0]}={value}")
        argv = [*command, *option_argv]
        if positional_argv:
            argv.extend(("--", *positional_argv))
        # The parser remains the final authority for required fields and values.
        leaf.parse_args(argv[len(command) :])
        return argv
    raise ValueError(f"Unknown Cheese tool: {tool}")


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
        request = payload.get("mcp")
        if request and request["method"] == "tools/list":
            for descriptor in descriptors:
                os.close(descriptor)
            result = {"status": 0, "result": {"tools": _tools(server.parser)}}
            self.request.sendall(json.dumps(result).encode() + b"\n")
            return
        # Recreate wrappers: inherited file streams retain seekability after dup2.
        for standard_stream in (sys.stdin, sys.stdout, sys.stderr):
            standard_stream.close()
        for target, descriptor in enumerate(descriptors):
            os.dup2(descriptor, target)
            os.close(descriptor)
        sys.stdin = os.fdopen(0, "r", **payload["stdio"][0])
        sys.stdout = os.fdopen(1, "w", **payload["stdio"][1])
        sys.stderr = os.fdopen(2, "w", **payload["stdio"][2])
        done = threading.Event()

        def disconnected():
            if stream.read(1) == b"" and not done.is_set():
                os.killpg(os.getpid(), signal.SIGTERM)

        threading.Thread(target=disconnected, daemon=True).start()
        status = 0
        try:
            argv = (
                _command(server.parser, request["tool"], request.get("arguments", {}))
                if request
                else payload["argv"]
            )
            sys.argv = [str(server.source), *argv]
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
        namespace: dict[str, Any] = {
            "__name__": "preload",
            "__file__": str(self.source),
        }
        exec(self.code, namespace)
        build_parser = namespace.get("build_parser")
        self.parser = build_parser() if build_parser else None
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
