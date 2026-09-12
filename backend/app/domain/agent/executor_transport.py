"""Room executor transport shared by agent harnesses."""

import json
import os
import select
import shlex
import subprocess
from urllib.parse import unquote, urlsplit


class RemoteClient:
    def __init__(self, config):
        import threading

        self.config = config
        self.transport = threading.local()

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
            connection, path = self.connection()
            try:
                connection.request(
                    "POST",
                    path,
                    body=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Cheese-Token": os.environ["CHEESE_TOKEN"],
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
            except Exception:
                # A lost response can follow a committed mutation. Reconnect only
                # for the next call; never replay this request automatically.
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
