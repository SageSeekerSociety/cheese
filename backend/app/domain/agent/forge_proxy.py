#!/usr/bin/env python3
"""Host-side CA egress proxy and preview tunnel relay outside the sandbox (#4896).

Shipped to an execution machine and started by `bootstrap.configure_idle`
OUTSIDE the room's sandbox before the sandbox starts. Standard library only,
and no imports from `app.*`.

Two Unix domain sockets live in the room's read-only platform socket directory
(`/tmp/cheese-platform-<uid>-<digest>`):

- `forge.sock`: HTTP `CONNECT` proxy (and HTTP relay for Forgejo) that
  terminates TLS for the project's bound forge hosts using a per-machine CA,
  refuses pull-request merges (`PUT /repos/{owner}/{repo}/pulls/{n}/merge` and
  GraphQL `mergePullRequest` / `enablePullRequestAutoMerge`) with `403`,
  replaces `placeholder` credentials with a cached single-repository token
  obtained from `platform.sock` using `X-Cheese-Proxy-Proof`, and connects
  directly to the forge (or falls back to the platform when the forge is
  unreachable from the execution machine). Other destinations are spliced
  through without terminating TLS, except private/loopback addresses which are
  refused. Every destination is dialled by `ForgeProxyRuntime.dial`, which
  reaches the deployment's site over the machine's forward when it has one
  (`--site-forward`), so the room needs no route of its own to the site.
- `preview.sock`: out-of-sandbox preview WebSocket relay. `cheese-preview.py`
  inside the sandbox connects here with no token; this process obtains a
  short-lived credential on `platform.sock` using `X-Cheese-Proxy-Proof`,
  upgrades the connection to `CHEESE_PREVIEW_URL` outside the sandbox, and
  splices the stream.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import hashlib
import http.client
import ipaddress
import json
import os
import re
import runpy
import signal
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

if sys.platform != "win32":
    import fcntl

PLACEHOLDER_TOKEN = "placeholder"
MERGE_REFUSED_MESSAGE = (
    "[cheese] Merging accepts this task. Use cheese_card(action=request) "
    "to submit it for project review."
)

_REST_MERGE_RE = re.compile(
    r"^(?:/api/v[13])?/repos/[^/]+/[^/]+/pulls/\d+/merge/*$", re.IGNORECASE
)
_GRAPHQL_MERGE_RE = re.compile(rb"\b(?:mergePullRequest|enablePullRequestAutoMerge)\b")
_SYSTEM_CA_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)
_CLIENT_ERRORS = (
    http.client.IncompleteRead,
    http.client.BadStatusLine,
    http.client.LineTooLong,
    http.client.ImproperConnectionState,
)
_REACH_TTL_S = {True: 120.0, False: 600.0}


def _lock(file) -> None:
    if sys.platform != "win32":
        fcntl.flock(file, fcntl.LOCK_EX)


def is_pr_merge_request(method: str, path: str, body: bytes = b"") -> bool:
    """Whether an HTTP request to a forge API attempts to merge a pull request."""
    clean_path = urllib.parse.urlsplit(path).path or "/"
    upper = method.upper()
    if upper == "PUT" and _REST_MERGE_RE.match(clean_path):
        return True
    if upper == "POST" and clean_path.rstrip("/") in ("/graphql", "/api/graphql"):
        return bool(_GRAPHQL_MERGE_RE.search(body))
    return False


def rewrite_authorization(
    authorization: str, username: str, token: str, *, is_git: bool = False
) -> str:
    """Replace a placeholder credential with `token`.

    Unauthenticated requests (such as public release downloads on github.com)
    and requests carrying another credential are left untouched.
    """
    if not token:
        return authorization
    raw = (authorization or "").strip()
    if not raw:
        return ""
    scheme, _, value = raw.partition(" ")
    lower = scheme.lower()
    if lower in ("bearer", "token"):
        if value.strip() == PLACEHOLDER_TOKEN:
            return f"{scheme} {token}"
        return raw
    if lower == "basic":
        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
            user, _, password = decoded.partition(":")
        except (ValueError, UnicodeError, binascii.Error):
            return raw
        if password == PLACEHOLDER_TOKEN:
            pair = f"{user or username or 'x-access-token'}:{token}".encode()
            return "Basic " + base64.b64encode(pair).decode("ascii")
        return raw
    return raw


def _system_ca_text() -> str:
    default_ca = ssl.get_default_verify_paths().cafile
    candidates = [
        *([default_ca] if default_ca else []),
        *_SYSTEM_CA_PATHS,
    ]
    for path in candidates:
        try:
            if path and os.path.isfile(path):
                return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def ensure_ca(ca_dir: Path, platform_dir: Path) -> tuple[Path, Path]:
    """Ensure the machine's CA exists in `ca_dir` and publish `ca.pem` and
    `ca-bundle.pem` into `platform_dir`."""
    ca_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca_dir.chmod(0o700)
    ca_key = ca_dir / "ca.key"
    ca_pem = ca_dir / "ca.pem"
    with (ca_dir / "ca.lock").open("a") as lock_file:
        _lock(lock_file)
        if not (ca_key.is_file() and ca_pem.is_file()):
            tmp_key = ca_dir / f"ca.{os.getpid()}.key"
            tmp_pem = ca_dir / f"ca.{os.getpid()}.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "ec",
                    "-pkeyopt",
                    "ec_paramgen_curve:prime256v1",
                    "-nodes",
                    "-keyout",
                    str(tmp_key),
                    "-out",
                    str(tmp_pem),
                    "-days",
                    "3650",
                    "-subj",
                    "/CN=Cheese Machine CA",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE,pathlen:0",
                    "-addext",
                    "keyUsage=critical,keyCertSign,cRLSign",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            tmp_key.chmod(0o600)
            tmp_pem.chmod(0o644)
            tmp_key.replace(ca_key)
            tmp_pem.replace(ca_pem)
    platform_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca_text = ca_pem.read_text(encoding="utf-8")
    pub_ca = platform_dir / "ca.pem"
    tmp_pub = platform_dir / f"ca.{os.getpid()}.pem"
    tmp_pub.write_text(ca_text, encoding="utf-8")
    tmp_pub.chmod(0o644)
    tmp_pub.replace(pub_ca)

    bundle_path = platform_dir / "ca-bundle.pem"
    tmp_bundle = platform_dir / f"ca-bundle.{os.getpid()}.pem"
    sys_text = _system_ca_text()
    combined = (sys_text.rstrip("\n") + "\n" + ca_text) if sys_text else ca_text
    tmp_bundle.write_text(combined, encoding="utf-8")
    tmp_bundle.chmod(0o644)
    tmp_bundle.replace(bundle_path)
    return pub_ca, bundle_path


class CertificateStore:
    """Per-host TLS server contexts signed by the machine CA."""

    def __init__(self, ca_dir: Path) -> None:
        self.ca_dir = ca_dir
        self.hosts_dir = ca_dir / "hosts"
        self.hosts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.Lock()
        self._contexts: dict[str, ssl.SSLContext] = {}

    def context_for(self, host: str) -> ssl.SSLContext:
        host = host.strip().lower()
        with self._lock:
            cached = self._contexts.get(host)
            if cached is not None:
                return cached
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", host) or "default"
        digest = hashlib.sha256(host.encode()).hexdigest()[:12]
        stem = f"{safe}-{digest}"
        leaf_key = self.hosts_dir / f"{stem}.key"
        leaf_pem = self.hosts_dir / f"{stem}.pem"
        with (self.ca_dir / "ca.lock").open("a") as lock_file:
            _lock(lock_file)
            if not (leaf_key.is_file() and leaf_pem.is_file()):
                self._mint_leaf(host, leaf_key, leaf_pem)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(leaf_pem), keyfile=str(leaf_key))
        with self._lock:
            self._contexts[host] = ctx
        return ctx

    def _mint_leaf(self, host: str, leaf_key: Path, leaf_pem: Path) -> None:
        pid = f"{os.getpid()}.{threading.get_ident()}"
        tmp_key = self.hosts_dir / f"{leaf_key.stem}.{pid}.key"
        tmp_csr = self.hosts_dir / f"{leaf_key.stem}.{pid}.csr"
        tmp_pem = self.hosts_dir / f"{leaf_key.stem}.{pid}.pem"
        tmp_ext = self.hosts_dir / f"{leaf_key.stem}.{pid}.ext"
        try:
            try:
                ipaddress.ip_address(host)
                san = f"IP:{host}"
            except ValueError:
                san = f"DNS:{host}"
            tmp_ext.write_text(
                "basicConstraints=CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n"
                f"subjectAltName={san}\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-newkey",
                    "ec",
                    "-pkeyopt",
                    "ec_paramgen_curve:prime256v1",
                    "-nodes",
                    "-keyout",
                    str(tmp_key),
                    "-out",
                    str(tmp_csr),
                    "-subj",
                    f"/CN={host[:60]}",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    str(tmp_csr),
                    "-CA",
                    str(self.ca_dir / "ca.pem"),
                    "-CAkey",
                    str(self.ca_dir / "ca.key"),
                    "-CAcreateserial",
                    "-out",
                    str(tmp_pem),
                    "-days",
                    "365",
                    "-extfile",
                    str(tmp_ext),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            tmp_key.chmod(0o600)
            tmp_pem.chmod(0o644)
            tmp_key.replace(leaf_key)
            tmp_pem.replace(leaf_pem)
        finally:
            for path in (tmp_key, tmp_csr, tmp_pem, tmp_ext):
                with contextlib.suppress(OSError):
                    path.unlink()


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 30.0) -> None:
        super().__init__("platform.cheese", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class PlatformSocketClient:
    """Talks to `platform.sock` with the host-only `X-Cheese-Proxy-Proof`."""

    def __init__(self, socket_path: str, proof: str) -> None:
        self.socket_path = socket_path
        self.proof = proof
        self._lock = threading.Lock()
        self._cached_forge: dict | None = None
        self._cached_until: float = 0.0
        self._reach: dict[str, tuple[float, bool]] = {}

    def _get_json(self, path: str, timeout: float = 30.0) -> dict:
        conn = _UnixHTTPConnection(self.socket_path, timeout=timeout)
        try:
            headers = {"X-Cheese-Proxy-Proof": self.proof} if self.proof else {}
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read()
        except _CLIENT_ERRORS as exc:
            # i18n-exempt: machine log
            raise OSError(f"platform socket error: {exc}") from exc
        finally:
            conn.close()
        if resp.status != 200:
            # i18n-exempt: machine log
            raise OSError(f"platform socket HTTP {resp.status} for {path}")
        return json.loads(body)

    def forge_data(self, *, force: bool = False) -> dict | None:
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._cached_forge is not None
                and now < self._cached_until
            ):
                return self._cached_forge
        if not os.path.exists(self.socket_path):
            return None
        try:
            data = self._get_json("/api/sandbox/forge-token")["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        with self._lock:
            self._cached_forge = data
            self._cached_until = time.monotonic() + 300.0
        return data

    def proxy_credential(self, timeout: float = 20.0) -> str:
        data = self._get_json("/proxy-credential", timeout=timeout)
        return str(data["token"])

    def reaches(self, url: str) -> bool:
        now = time.monotonic()
        with self._lock:
            cached = self._reach.get(url)
            if cached is not None and now < cached[0]:
                return cached[1]
        try:
            with urllib.request.urlopen(url, timeout=5):
                pass
            ok = True
        except urllib.error.HTTPError:
            ok = urllib.parse.urlsplit(url).scheme == "https"
        except (OSError, urllib.error.URLError):
            ok = False
        with self._lock:
            self._reach[url] = (time.monotonic() + _REACH_TTL_S[ok], ok)
        return ok


# Matches `sandbox_host.PRIVATE` so private/loopback/link-local/CGNAT ranges are
# blocked while transparent-DNS benchmark ranges (198.18.0.0/15) used on cloud
# hosts for external domains remain reachable.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def _is_private_destination(host: str) -> bool:
    if host.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if any(ip in net for net in _BLOCKED_NETWORKS):
            return True
    return False


def _pipe(src: socket.socket, dst: socket.socket, reader=None) -> None:
    try:
        while True:
            data = reader.read1(65536) if reader is not None else src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)


def _splice(a: socket.socket, b: socket.socket, reader=None) -> None:
    t1 = threading.Thread(target=_pipe, args=(a, b, reader), daemon=True)
    t2 = threading.Thread(target=_pipe, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


class _TerminatedHTTPHandler(BaseHTTPRequestHandler):
    """Handles decrypted HTTP/1.1 requests inside a terminated TLS tunnel."""

    protocol_version = "HTTP/1.1"
    raw_requestline: bytes

    def __init__(
        self,
        request: socket.socket,
        client_address,
        server,
        *,
        target_host: str,
        target_port: int,
        target_scheme: str = "https",
    ) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.target_scheme = target_scheme
        super().__init__(request, client_address, server)

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks: list[bytes] = []
            while True:
                line = self.rfile.readline(128)
                if not line:
                    break
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline(8192).strip():
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _handle_one(self) -> None:
        proxy: ForgeProxyRuntime = self.server.proxy  # type: ignore[attr-defined]
        data = proxy.platform.forge_data() or {}
        clean_path = urllib.parse.urlsplit(self.path).path or "/"
        forge_api = urllib.parse.urlsplit(data.get("api_url") or "")
        forge_prefix = forge_api.path.removesuffix("/api/v1").rstrip("/")
        if (
            data.get("kind") == "forgejo"
            and forge_api.hostname == self.target_host
            and forge_prefix
            and clean_path != forge_prefix
            and not clean_path.startswith(forge_prefix + "/")
        ):
            # The site's pages and live connections share the forge's host.
            # Keep their headers and streams intact, without adding credentials.
            connection = _DialledConnection(
                self.target_host,
                self.target_port,
                dial=proxy.dial,
                tls=self.target_scheme == "https",
                timeout=10,
            )
            try:
                connection.connect()
                upstream = connection.sock
                request_headers = "".join(
                    f"{key}: {value}\r\n" for key, value in self.headers.items()
                )
                upstream.sendall(
                    self.raw_requestline + request_headers.encode("latin-1") + b"\r\n"
                )
                self.request.settimeout(None)
                upstream.settimeout(None)
                # The HTTP reader may already hold bytes after the headers.
                _splice(self.request, upstream, self.rfile)
            finally:
                self.close_connection = True
                connection.close()
            return
        body = self._read_body()
        if is_pr_merge_request(self.command, self.path, body):
            payload = json.dumps({"message": MERGE_REFUSED_MESSAGE}).encode("utf-8")
            self.send_response(403, "Forbidden")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
            return

        token = data.get("token") or ""
        if token == PLACEHOLDER_TOKEN:
            token = ""
        username = data.get("username") or "x-access-token"
        is_git = ".git/" in clean_path or clean_path.endswith(".git")

        headers: dict[str, str] = {}
        auth_value = ""
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in (
                "connection",
                "keep-alive",
                "proxy-connection",
                "proxy-authorization",
                "transfer-encoding",
                "content-length",
                "host",
            ):
                continue
            if lower == "authorization":
                auth_value = value
                continue
            headers[key] = value
        headers["Host"] = (
            self.target_host
            if self.target_port in (80, 443)
            else f"{self.target_host}:{self.target_port}"
        )
        rewritten_auth = rewrite_authorization(
            auth_value, username, token, is_git=is_git
        )
        if rewritten_auth:
            headers["Authorization"] = rewritten_auth
        if body or self.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        headers["Connection"] = "close"

        try:
            status, reason, resp_headers, resp_body = proxy.forward_forge_http(
                scheme=self.target_scheme,
                host=self.target_host,
                port=self.target_port,
                method=self.command,
                path=self.path,
                headers=headers,
                body=body,
                forge=data,
            )
        except Exception as exc:
            err = json.dumps({"message": f"forge proxy upstream error: {exc}"}).encode()
            self.send_response(502, "Bad Gateway")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(err)
            self.close_connection = True
            return

        self.send_response(status, reason)
        for k, v in resp_headers:
            if k.lower() in (
                "connection",
                "keep-alive",
                "transfer-encoding",
                "content-length",
            ):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and resp_body:
            self.wfile.write(resp_body)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = (
        _handle_one
    )

    def log_message(self, *_args) -> None:
        pass


class _DialledConnection(http.client.HTTPConnection):
    """An HTTP or HTTPS connection whose socket `ForgeProxyRuntime.dial` opens.
    TLS is verified against the name asked for, wherever the socket leads."""

    def __init__(self, host: str, port: int, *, dial, tls: bool, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._dial = dial
        self._tls = tls

    def connect(self) -> None:
        sock = self._dial(self.host, self.port, self.timeout)
        if self._tls:
            sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=self.host
            )
        self.sock = sock


class ForgeProxyRuntime:
    def __init__(
        self,
        *,
        platform_dir: Path,
        ca_dir: Path,
        api_url: str,
        preview_url: str,
        proof: str,
        site_forward: str = "",
    ) -> None:
        self.platform_dir = platform_dir
        self.ca_dir = ca_dir
        self.api_url = api_url.rstrip("/")
        self.preview_url = preview_url
        self.certs = CertificateStore(ca_dir)
        self.platform = PlatformSocketClient(str(platform_dir / "platform.sock"), proof)
        # `site:port` when this machine forwards the deployment's site over
        # TLS to that loopback port (`machine_address.site_forward`).
        site, _, port = site_forward.rpartition(":")
        self.site_forward = (
            (site.lower(), int(port)) if site and port.isdigit() else None
        )

    def dial(self, host: str, port: int, timeout: float) -> socket.socket:
        """A TCP connection to `host:port`. The site's name on 443 is reached
        over the machine's forward, which serves the site's own certificate:
        its public address leaves the private network for the public relay
        and comes back through its tunnels."""
        if self.site_forward and (host.lower(), port) == (self.site_forward[0], 443):
            return socket.create_connection(
                ("127.0.0.1", self.site_forward[1]), timeout=timeout
            )
        return socket.create_connection((host, port), timeout=timeout)

    def is_forge_host(self, host: str) -> tuple[bool, str]:
        """Whether `host` is one of the project's forge hosts, and its scheme."""
        lower = host.strip().lower()
        if lower in ("github.com", "api.github.com"):
            return True, "https"
        data = self.platform.forge_data()
        if not data:
            return False, "https"
        for key in ("url", "api_url"):
            raw = data.get(key) or ""
            parsed = urllib.parse.urlsplit(raw)
            if (parsed.hostname or "").lower() == lower:
                return True, parsed.scheme or "https"
        return False, "https"

    def forward_forge_http(
        self,
        *,
        scheme: str,
        host: str,
        port: int,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        forge: dict,
    ) -> tuple[int, str, list[tuple[str, str]], bytes]:
        clean_path = urllib.parse.urlsplit(path).path or "/"
        is_git = ".git/" in clean_path or clean_path.endswith(".git")
        conn = _DialledConnection(
            host, port, dial=self.dial, tls=scheme == "https", timeout=10
        )
        connected = False
        try:
            conn.connect()
            connected = True
            conn.sock.settimeout(300)
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.reason, resp.getheaders(), resp.read()
        except OSError:
            if connected:
                raise
        finally:
            conn.close()

        # Fallback through the platform when the forge is unreachable directly.
        project_id = forge.get("project_id") or os.environ.get("CHEESE_PROJECT", "")
        if is_git or forge.get("kind") == "forgejo":
            parsed = urllib.parse.urlsplit(
                f"{self.api_url}/sandbox/forge/{project_id}{path}"
            )
            relay_headers = dict(headers)
            relay_headers["Host"] = parsed.netloc
            if forge.get("kind") == "github_app":
                cred = self.platform.proxy_credential()
                pair = f"x-access-token:{cred}".encode()
                relay_headers["Authorization"] = "Basic " + base64.b64encode(
                    pair
                ).decode("ascii")
            conn_cls = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            conn = conn_cls(parsed.hostname or "", parsed.port, timeout=300)
            try:
                target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                conn.request(method, target, body=body, headers=relay_headers)
                resp = conn.getresponse()
                return resp.status, resp.reason, resp.getheaders(), resp.read()
            finally:
                conn.close()

        # GitHub REST/GraphQL fallback over /sandbox/forge-tunnel/{project_id}.
        helper_path = Path(__file__).resolve().parents[1] / "cheese-tunnel.py"
        if not helper_path.exists():
            helper_path = Path(__file__).with_name("machine_tunnel.py")
        tunnel_mod = runpy.run_path(str(helper_path))
        api_split = urllib.parse.urlsplit(self.api_url)
        tunnel_url = urllib.parse.urlunsplit(
            (
                "wss" if api_split.scheme == "https" else "ws",
                api_split.netloc,
                api_split.path + f"/sandbox/forge-tunnel/{project_id}",
                "",
                "",
            )
        )
        secret = self.platform.proxy_credential()
        left, right = socket.socketpair()
        t = threading.Thread(
            target=tunnel_mod["handle_connection"],
            args=(right, tunnel_url, secret),
            daemon=True,
        )
        t.start()
        try:
            left.settimeout(30)
            left.sendall(
                f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
            )
            head = bytearray()
            while b"\r\n\r\n" not in head:
                chunk = left.recv(4096)
                if not chunk:
                    # i18n-exempt: machine log
                    raise OSError("forge tunnel closed before establishing")
                head.extend(chunk)
            if b" 200 " not in head.split(b"\r\n", 1)[0]:
                # i18n-exempt: machine log
                raise OSError("forge tunnel refused CONNECT")
            tls_ctx = ssl.create_default_context()
            with tls_ctx.wrap_socket(left, server_hostname=host) as tls_sock:
                req_lines = [
                    f"{method} {path} HTTP/1.1",
                    *(f"{k}: {v}" for k, v in headers.items()),
                    "",
                    "",
                ]
                tls_sock.sendall("\r\n".join(req_lines).encode() + body)
                fake_sock = _PreloadedSocket(tls_sock)
                http_resp = http.client.HTTPResponse(fake_sock)  # type: ignore[arg-type]
                http_resp.begin()
                return (
                    http_resp.status,
                    http_resp.reason,
                    http_resp.getheaders(),
                    http_resp.read(),
                )
        finally:
            with contextlib.suppress(OSError):
                left.close()
            with contextlib.suppress(OSError):
                right.close()


class _PreloadedSocket:
    def __init__(self, sock: ssl.SSLSocket) -> None:
        self._sock = sock

    def makefile(self, mode: str, *args, **kwargs):
        return self._sock.makefile(mode, *args, **kwargs)  # type: ignore[call-overload]


class _ForgeSocketServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, handler, proxy: ForgeProxyRuntime) -> None:
        self.proxy = proxy
        super().__init__(path, handler)

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)


class _ForgeSocketHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server: _ForgeSocketServer = self.server  # type: ignore[assignment]
        proxy = server.proxy
        sock: socket.socket = self.request
        sock.settimeout(30)
        head = bytearray()
        try:
            while b"\r\n\r\n" not in head:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                head.extend(chunk)
                if len(head) > 65536:
                    return
        except OSError:
            return
        header_bytes, _, remainder = bytes(head).partition(b"\r\n\r\n")
        first_line = header_bytes.split(b"\r\n", 1)[0].decode(
            "latin-1", errors="replace"
        )
        parts = first_line.split()
        if len(parts) < 3:
            return
        method, target = parts[0].upper(), parts[1]
        if method != "CONNECT":
            return
        host, _, port_str = target.rpartition(":")
        host = host.strip("[]")
        try:
            port = int(port_str) if port_str else 443
        except ValueError:
            return

        is_forge, scheme = proxy.is_forge_host(host)
        if not is_forge:
            if _is_private_destination(host):
                with contextlib.suppress(OSError):
                    sock.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                return
            try:
                upstream = proxy.dial(host, port, timeout=15)
            except OSError:
                with contextlib.suppress(OSError):
                    sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            with upstream:
                sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if remainder:
                    upstream.sendall(remainder)
                sock.settimeout(None)
                upstream.settimeout(None)
                _splice(sock, upstream)
            return

        try:
            sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            ctx = proxy.certs.context_for(host)
            with ctx.wrap_socket(sock, server_side=True) as tls_sock:
                tls_sock.settimeout(300)
                _TerminatedHTTPHandler(
                    tls_sock,
                    ("local", 0),
                    server,
                    target_host=host,
                    target_port=port,
                    target_scheme=scheme,
                )
        except OSError:
            return


class _PreviewSocketServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, handler, proxy: ForgeProxyRuntime) -> None:
        self.proxy = proxy
        super().__init__(path, handler)

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)


class _PreviewSocketHandler(socketserver.BaseRequestHandler):
    """Upgrades an unauthenticated local preview socket connection to
    `CHEESE_PREVIEW_URL` outside the sandbox using `proxy_credential`."""

    def handle(self) -> None:
        server: _PreviewSocketServer = self.server  # type: ignore[assignment]
        proxy = server.proxy
        client: socket.socket = self.request
        client.settimeout(15)
        head = bytearray()
        try:
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head.extend(chunk)
                if len(head) > 16384:
                    return
        except OSError:
            return
        header_bytes, _, remainder = bytes(head).partition(b"\r\n\r\n")
        try:
            token = proxy.platform.proxy_credential(timeout=15)
        except Exception as exc:
            msg = f"HTTP/1.1 503 Service Unavailable\r\nX-Cheese-Error: {exc}\r\n\r\n"
            with contextlib.suppress(OSError):
                client.sendall(msg.encode("latin-1", errors="replace"))
            return

        parsed = urllib.parse.urlparse(proxy.preview_url)
        secure = parsed.scheme in ("wss", "https")
        host = parsed.hostname or ""
        port = parsed.port or (443 if secure else 80)
        if not host:
            return
        try:
            raw = socket.create_connection((host, port), timeout=15)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if secure:
                ca_path = os.environ.get("CHEESE_PREVIEW_CA") or None
                ctx = ssl.create_default_context(cafile=ca_path)
                upstream: socket.socket = ctx.wrap_socket(raw, server_hostname=host)
            else:
                upstream = raw
        except OSError:
            with contextlib.suppress(OSError):
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        with upstream:
            lines = [
                line
                for line in header_bytes.decode("latin-1").split("\r\n")
                if not line.lower().startswith("authorization:")
            ]
            lines.append(f"Authorization: Bearer {token}")
            rewritten = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + remainder
            try:
                upstream.sendall(rewritten)
            except OSError:
                return
            client.settimeout(None)
            upstream.settimeout(None)
            _splice(client, upstream)


def serve(
    *,
    platform_dir: Path,
    ca_dir: Path,
    api_url: str,
    preview_url: str,
    proof: str,
    site_forward: str = "",
) -> int:
    ensure_ca(ca_dir, platform_dir)
    forge_sock = platform_dir / "forge.sock"
    preview_sock = platform_dir / "preview.sock"
    for sock_path in (forge_sock, preview_sock):
        sock_path.unlink(missing_ok=True)

    proxy = ForgeProxyRuntime(
        platform_dir=platform_dir,
        ca_dir=ca_dir,
        api_url=api_url,
        preview_url=preview_url,
        proof=proof,
        site_forward=site_forward,
    )
    forge_srv = _ForgeSocketServer(str(forge_sock), _ForgeSocketHandler, proxy)
    preview_srv = _PreviewSocketServer(str(preview_sock), _PreviewSocketHandler, proxy)
    os.chmod(forge_sock, 0o600)
    os.chmod(preview_sock, 0o600)

    threading.Thread(target=forge_srv.serve_forever, daemon=True).start()
    threading.Thread(target=preview_srv.serve_forever, daemon=True).start()

    record = platform_dir / "forge-proxy.json"
    tmp_record = platform_dir / f"forge-proxy.{os.getpid()}.json"
    tmp_record.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    tmp_record.replace(record)

    stop_event = threading.Event()

    def _stop(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        while not stop_event.wait(2.0):
            if not forge_sock.exists():
                break
    finally:
        forge_srv.shutdown()
        preview_srv.shutdown()
        forge_srv.server_close()
        preview_srv.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host-side forge & preview proxy")
    parser.add_argument("--platform-dir", required=True)
    parser.add_argument("--ca-dir", required=True)
    parser.add_argument("--api-url", default=os.environ.get("CHEESE_API", ""))
    parser.add_argument(
        "--preview-url", default=os.environ.get("CHEESE_PREVIEW_URL", "")
    )
    parser.add_argument(
        "--site-forward", default=os.environ.get("CHEESE_SITE_FORWARD", "")
    )
    args = parser.parse_args(argv)
    return serve(
        platform_dir=Path(args.platform_dir),
        ca_dir=Path(args.ca_dir),
        api_url=args.api_url,
        preview_url=args.preview_url,
        proof=os.environ.get("CHEESE_PROXY_PROOF", ""),
        site_forward=args.site_forward,
    )


if __name__ == "__main__":
    raise SystemExit(main())
