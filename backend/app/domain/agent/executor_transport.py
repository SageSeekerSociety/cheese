"""Room executor transport shared by agent harnesses."""

import json
import os
import re
import select
import shlex
import subprocess
import time
import uuid
from urllib.parse import unquote, urlsplit

# How long a request waits out a platform endpoint that is not listening.
# An app deploy recreates the backend container, and the central session reaches
# it directly, so every tool call and every chat publication in every live room
# gets ECONNREFUSED for as long as the recreate takes — measured 2026-09-15:
# a room went four minutes with `Bash`, `Read` and chat all refused, and the
# agent read that as the sandbox being broken. Waiting is the honest answer: a
# refused connect means the request never left this process, so nothing can have
# happened twice, and the deploy that caused it ends by itself.
CONNECT_RETRY_WINDOW_S = 180
CONNECT_RETRY_MAX_DELAY_S = 5


def _retry_connect(attempt: int, deadline: float) -> bool:
    """Sleep before the next attempt, or say the window is over.

    ONLY for a connection that was refused: `http.client` raises that before it
    writes anything, which is what makes the replay safe. A failure any later —
    a reset, a lost response — can follow a mutation the server already
    committed, and those still travel straight up (see `call`).
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(2**attempt * 0.5, CONNECT_RETRY_MAX_DELAY_S, remaining))
    return True


class RemoteClient:
    def __init__(self, config, *, shared_connection=False):
        import threading
        from types import SimpleNamespace

        self.config = config
        self.transport = SimpleNamespace() if shared_connection else threading.local()
        self.publication_lock = threading.Lock()
        self.publication = None
        self.platform_lock = threading.Lock()
        self.platform = None

    def execution_token(self):
        token_file = self.config.get("token_file")
        if token_file:
            with open(token_file) as stream:
                return stream.read().strip()
        return os.environ["CHEESE_TOKEN"]

    def platform_request(self, args):
        method = args.get("method", "GET").upper()
        path = args.get("path", "")
        parsed = urlsplit(path)
        if (
            method not in ("GET", "POST", "PUT", "PATCH", "DELETE")
            or not path.startswith("/")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
        ):
            raise ValueError("Platform requests require a relative API path and method")
        api = os.environ.get("CHEESE_API", "").rstrip("/")
        token = os.environ.get("CHEESE_TOKEN", "")
        if not api or not token:
            raise RuntimeError("Platform requests require room credentials")
        with self.platform_lock:
            if self.platform is None or self.platform.config["url"] != api:
                if self.platform is not None:
                    previous = getattr(self.platform.transport, "connection", None)
                    if previous is not None:
                        previous.close()
                self.platform = RemoteClient({"url": api}, shared_connection=True)
            transport = self.platform
            connection, base = transport.connection()
            try:
                connection.request(
                    method,
                    base.rstrip("/") + path,
                    body=json.dumps(args["body"]).encode() if "body" in args else None,
                    headers={
                        **transport.transport.headers,
                        "Content-Type": "application/json",
                        "X-Cheese-Token": token,
                        **(
                            {"X-Cheese-Turn": os.environ["CHEESE_TURN"]}
                            if os.environ.get("CHEESE_TURN")
                            else {}
                        ),
                    },
                )
                response = connection.getresponse()
                body = response.read().decode()
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"Platform HTTP {response.status}: {body}")
            except Exception:
                connection.close()
                transport.transport.connection = None
                raise
        return {"value": {"stdout": body, "stderr": ""}}

    def connection(self):
        # Shell forwarding exits before creating a client; keep its startup
        # independent of HTTP, TLS and proxy discovery imports.
        import base64
        import http.client
        from urllib.request import getproxies, proxy_bypass

        if getattr(self.transport, "connection", None) is not None:
            connection = self.transport.connection
            # Idle HTTP connections can be closed by the gateway between turns.
            # Reconnect before writing when the socket already has EOF/data.
            if connection.sock and select.select([connection.sock], [], [], 0)[0]:
                connection.close()
            return connection, self.transport.path
        target = urlsplit(str(self.config["url"]))
        if not target.hostname:
            raise ValueError("Executor URL requires a hostname")
        proxy = (
            getproxies().get(target.scheme)
            if not proxy_bypass(target.hostname)
            else None
        )
        address = urlsplit(proxy) if proxy else target
        hostname = address.hostname
        if not hostname:
            raise ValueError("Executor proxy URL requires a hostname")
        factory = (
            http.client.HTTPSConnection
            if address.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = factory(hostname, address.port, timeout=660)
        path = target.path or "/"
        if target.query:
            path += "?" + target.query
        self.transport.headers = {}
        if proxy:
            headers = {}
            if address.username is not None:
                auth = unquote(address.username) + ":" + unquote(address.password or "")
                headers["Proxy-Authorization"] = (
                    "Basic " + base64.b64encode(auth.encode()).decode()
                )
            if target.scheme == "https":
                if address.scheme != "http":
                    raise ValueError(
                        "Executor HTTPS requests require an HTTP CONNECT proxy"
                    )
                connection = http.client.HTTPSConnection(
                    hostname, address.port or 80, timeout=660
                )
                connection.set_tunnel(target.hostname, target.port or 443, headers)
            else:
                path = self.config["url"]
                self.transport.headers = headers
        self.transport.connection, self.transport.path = connection, path
        return connection, path

    def publish_chat(self, payload, args):
        if payload["tool"] != "Bash" or not isinstance(args.get("command"), str):
            return None
        command = args["command"].strip()
        # Only literal inline messages: files and shell syntax belong to the device.
        match = re.fullmatch(r"cheese\s+chat\s+send\s+(['\"])([^'\"\n]*)\1", command)
        if not match or any(char in command for char in ";&|<>`$\\\r"):
            return None
        content = match[2]
        if not content.strip() or content.startswith("-"):
            return None
        if not all(
            os.environ.get(name)
            for name in ("CHEESE_API", "CHEESE_TOPIC", "CHEESE_TOKEN")
        ):
            return None
        return self.publish_message(payload, {"content": content})

    def publish_message(self, payload, args):
        with self.publication_lock:
            return self._publish_message(payload, args)

    def _publish_message(self, payload, args):
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Chat content must be a nonempty string")
        api = os.environ.get("CHEESE_API", "").rstrip("/")
        topic = os.environ.get("CHEESE_TOPIC", "")
        token = os.environ.get("CHEESE_TOKEN", "")
        if not api or not topic or not token:
            raise RuntimeError("Chat publication requires room credentials")
        publication_id = str(
            uuid.UUID(args["request_id"])
            if args.get("request_id")
            else uuid.uuid5(
                uuid.NAMESPACE_URL, f"{topic}/{payload['session_id']}/{payload['id']}"
            )
        )
        url = f"{api}/topics/{topic}/messages"
        if self.publication is None or self.publication.config["url"] != url:
            # Publication is serialized across MCP workers; its connection must
            # outlive the worker thread that happened to make the first call.
            self.publication = RemoteClient({"url": url}, shared_connection=True)
        publisher = self.publication
        body = json.dumps(
            {
                "content": content,
                "request_id": publication_id,
                **({"reply_to": args["reply_to"]} if args.get("reply_to") else {}),
            }
        ).encode()
        deadline = time.monotonic() + CONNECT_RETRY_WINDOW_S
        attempt = 0
        while True:
            try:
                result = self._publish_once(publisher, body, token, publication_id)
                break
            except ConnectionRefusedError:
                # The room is mid-deploy: nothing was sent, so waiting cannot
                # publish the same message twice.
                if not _retry_connect(attempt, deadline):
                    raise RuntimeError(
                        "Chat publication failed; the platform refused connections "
                        f"for {CONNECT_RETRY_WINDOW_S}s; "
                        f"request_id={publication_id}"
                    ) from None
                attempt += 1
        return {
            "value": {
                "stdout": json.dumps(result["data"], ensure_ascii=False),
                "stderr": f"[cheese] request_id={publication_id}",
                "interrupted": False,
                "noOutputExpected": False,
                "returnCodeInterpretation": "Exit code 0",
            }
        }

    @staticmethod
    def _publish_once(publisher, body, token, publication_id):
        connection, path = publisher.connection()
        try:
            connection.request(
                "POST",
                path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Cheese-Token": token,
                    **publisher.transport.headers,
                    **(
                        {"X-Cheese-Turn": os.environ["CHEESE_TURN"]}
                        if os.environ.get("CHEESE_TURN")
                        else {}
                    ),
                },
            )
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise RuntimeError(
                    f"Chat publication failed: HTTP {response.status}; "
                    f"request_id={publication_id}"
                )
            return json.loads(data)
        except ConnectionRefusedError:
            connection.close()
            publisher.transport.connection = None
            raise
        except Exception as exc:
            connection.close()
            publisher.transport.connection = None
            raise RuntimeError(
                f"Chat publication failed; request_id={publication_id}: {exc}"
            ) from exc

    def command(self, mode, server=None):
        command = [*self.config["command"], mode, "--state", self.config["state"]]
        if server:
            command.extend(["--server", server])
        # SSH joins all arguments after the host with spaces. Quote the remote
        # command once, rather than letting workspace names become shell syntax.
        if self.config.get("ssh"):
            command = [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                self.config["ssh"],
                shlex.join(command),
            ]
        return command

    def call(self, method, params=None):
        if self.config.get("kind") == "device":
            payload = json.dumps({"method": method, "params": params or {}}).encode()
            deadline = time.monotonic() + CONNECT_RETRY_WINDOW_S
            attempt = 0
            while True:
                connection, path = self.connection()
                try:
                    connection.request(
                        "POST",
                        path,
                        body=payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-Cheese-Token": self.execution_token(),
                            **self.transport.headers,
                        },
                    )
                    response = connection.getresponse()
                    data = response.read()
                    if response.status != 200:
                        raise RuntimeError(
                            f"Executor HTTP request failed: {response.status}"
                        )
                    return json.loads(data)
                except ConnectionRefusedError:
                    # Nothing was sent, so this is the one failure worth waiting
                    # out: the platform endpoint is being replaced.
                    connection.close()
                    self.transport.connection = None
                    if not _retry_connect(attempt, deadline):
                        raise
                    attempt += 1
                except Exception:
                    # A lost response can follow a committed mutation. Reconnect
                    # only for the next call; never replay this one.
                    connection.close()
                    self.transport.connection = None
                    raise
        result = subprocess.run(
            self.command("request"),
            input=json.dumps({"method": method, "params": params or {}}),
            text=True,
            capture_output=True,
            timeout=660,
        )
        if result.returncode:
            raise RuntimeError(
                "Remote executor request failed: " + result.stderr.strip()
            )
        return json.loads(result.stdout)

    def control(self, request):
        request = dict(request)
        if "path" in request:
            request["path"] = self.remote_path(request["path"])
        return self.call("control", request)

    def remote_path(self, path):
        center = self.config.get("central_workspace", "")
        if center and (path == center or path.startswith(center + "/")):
            return self.config["workspace"] + path[len(center) :]
        return path
