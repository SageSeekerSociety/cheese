#!/usr/bin/env python3
"""The mainland redirect: who is sent from one origin of the deployment to
another (frontend/nginx/mainland, frontend/scripts/mainland-redirect.sh).

Runs the real nginx on a temporary copy of the frontend image's configuration,
with the two generated files written by the script itself, and asks it who it
redirects. Each case is one thing the rule promises: a visitor in China opening
a page on the Hong Kong origin is sent to the mainland one, and nobody else is
moved — not a visitor elsewhere, not one signed in here, not an API call, not the
desktop app, not another host of the deployment, and nobody at all when the
deployment never set MAINLAND_ENTRY.
"""

import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from reserved_port import reserve_port

ROOT = Path(__file__).resolve().parents[2]
NGINX = sys.argv[1] if len(sys.argv) > 1 else shutil.which("nginx") or "nginx"

CANONICAL = "https://okcheese.com"
ENTRY = "https://okcheese.cn"
ALIAS = "https://cheese.ruc.edu.cn"
CHINA = "1.2.4.8"  # 1.2.4.0/22, APNIC
FOREIGN = "8.8.8.8"

PLACEHOLDERS = {
    "__API_UPSTREAM__": "127.0.0.1:1",
    "__DEVICE_CONNECTION_UPSTREAM__": "127.0.0.1:1",
    "__FORGEJO_UPSTREAM__": "127.0.0.1:1",
    "__FORGE_EVENTS_UPSTREAM__": "127.0.0.1:1",
    "__COLLAB_UPSTREAM__": "127.0.0.1:1",
    "__OFFICE_EDITOR_UPSTREAM__": "127.0.0.1:1",
}


class Instance:
    """The frontend image's nginx, on a temporary copy, with its own port."""

    def __init__(self, root: Path, port: int) -> None:
        self.root, self.port = root, port

    def get(self, path: str, host: str = "okcheese.com", **headers: str):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        request.add_header("Host", host)
        for name, value in headers.items():
            request.add_header(name.replace("_", "-"), value)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            return opener.open(request, timeout=5)
        except urllib.error.HTTPError as refused:
            return refused


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def build(tmp: Path, port: int, entry: str, aliases: str) -> Instance:
    html, nginx = tmp / "html", tmp / "nginx"
    html.mkdir()
    (html / "index.html").write_text("<html>ok</html>")
    (nginx / "docs").mkdir(parents=True)
    shutil.copytree(ROOT / "frontend/nginx/snippets", nginx / "snippets")
    for generated in ("host.conf", "platform.conf"):
        (nginx / "docs" / generated).write_text("")
    (nginx / "mainland").mkdir()
    for name in ("china-ip.txt", "main.conf"):
        shutil.copy(ROOT / "frontend/nginx/mainland" / name, nginx / "mainland" / name)
    subprocess.run(
        ["sh", str(ROOT / "frontend/scripts/mainland-redirect.sh"), str(nginx)],
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "FRONTEND_URL": CANONICAL,
            "FRONTEND_ALIASES": aliases,
            "MAINLAND_ENTRY": entry,
        },
    )
    config = (ROOT / "frontend/nginx.conf").read_text()
    config = (
        config.replace("/usr/share/nginx/html", str(html))
        .replace("/etc/nginx", str(nginx))
        .replace("listen 80 default_server;", f"listen {port} default_server;")
        .replace("listen [::]:80 default_server;", "")
        .replace("include mime.types;", f"include {nginx}/mime.types;")
        .replace("user nginx;", "")
        .replace("worker_processes auto;", "worker_processes 1;")
        .replace("pid /var/run/nginx.pid;", f"pid {tmp}/nginx.pid;")
        .replace("error_log /var/log/nginx/error.log notice;", f"error_log {tmp}/error.log notice;")
        .replace("access_log /var/log/nginx/access.log main;", f"access_log {tmp}/access.log main;")
    )
    for placeholder, upstream in PLACEHOLDERS.items():
        config = config.replace(placeholder, upstream)
    config = config.replace(
        "http {",
        "http {\n"
        f"  client_body_temp_path {tmp}/body;\n"
        f"  proxy_temp_path {tmp}/proxy;\n"
        f"  fastcgi_temp_path {tmp}/fastcgi;\n"
        f"  uwsgi_temp_path {tmp}/uwsgi;\n"
        f"  scgi_temp_path {tmp}/scgi;",
        1,
    )
    for made in ("body", "proxy", "fastcgi", "uwsgi", "scgi"):
        (tmp / made).mkdir()
    (nginx / "mainland" / "main.conf").write_text(
        (nginx / "mainland" / "main.conf").read_text().replace(
            "/etc/nginx/mainland", f"{nginx}/mainland"
        )
    )
    (nginx / "mime.types").write_text(
        "types { text/html html; text/css css; application/javascript js; }\n"
    )
    (tmp / "nginx.conf").write_text(config)
    subprocess.run([NGINX, "-t", "-p", str(tmp), "-c", str(tmp / "nginx.conf")], check=True)
    subprocess.run([NGINX, "-p", str(tmp), "-c", str(tmp / "nginx.conf")], check=True)
    wait_for(port)
    return Instance(tmp, port)


def wait_for(port: int) -> None:
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"nginx never answered on {port}")


def status(instance: Instance, *args, **kwargs) -> int:
    with instance.get(*args, **kwargs) as response:
        return response.status


def generated(tmp: Path, entry: str, aliases: str, url: str = CANONICAL) -> dict[str, str]:
    """The files the script writes, without starting nginx: what reaches a map
    key is decided there, and a Host that cannot be a key must not."""
    nginx = tmp / "nginx"
    (nginx / "mainland").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sh", str(ROOT / "frontend/scripts/mainland-redirect.sh"), str(nginx)],
        check=True,
        env={
            "PATH": "/usr/bin:/bin",
            "FRONTEND_URL": url,
            "FRONTEND_ALIASES": aliases,
            "MAINLAND_ENTRY": entry,
        },
    )
    return {name: (nginx / "mainland" / name).read_text() for name in
            ("hosts.map", "decide.map", "send.conf")}


def check_generated_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        files = generated(Path(tmp), ENTRY, '["https://alias.example/path","https://okcheese.com","oops;rm -rf /","https://alias.example/other"]')
        hosts = files["hosts.map"]
        # A path in an alias names the host, which is what `$host` is compared
        # with; a name repeated (with a path, without one) is one key; the entry
        # is never a key, and a value that is not an origin would be read as
        # config and stop the container.
        assert "    alias.example 1;" in hosts, hosts
        assert "okcheese.com 1;" in hosts, hosts
        assert "okcheese.cn 1;" not in hosts, hosts
        assert "oops" not in hosts, hosts
        assert hosts.count("alias.example 1;") == 1, hosts
        # MAINLAND_ENTRY is written verbatim into send.conf, so it is refused
        # outright unless it is a bare origin — a path would smuggle config in
        # past `if (...) { ... }` and fail as a syntax error, not a message.
        for bad in ("not an entry", "https://okcheese.cn/;}", "https://okcheese.cn/app"):
            try:
                generated(Path(tmp) / "bad", bad, "")
            except subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError(f"MAINLAND_ENTRY {bad!r} must be refused")


def main() -> None:
    check_generated_files()
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        on = build(Path(first), reserve_port(), ENTRY, f'["{ENTRY}","{ALIAS}"]')

        page = "/projects/42"
        moved = on.get(page, X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate")
        assert moved.status == 302, moved.status
        assert moved.headers["Location"] == f"{ENTRY}{page}", moved.headers["Location"]

        # A browser too old to send Sec-Fetch-Mode, asking for HTML, is a page.
        old = on.get(page, X_Forwarded_For=CHINA, Accept="text/html,application/xhtml+xml")
        assert old.status == 302 and old.headers["Location"] == f"{ENTRY}{page}"

        # The alias is the deployment's too: its visitors go to the entry.
        alias = on.get(page, host="cheese.ruc.edu.cn", X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate")
        assert alias.status == 302, alias.status

        # Nobody else moves.
        assert status(on, page, X_Forwarded_For=FOREIGN, Sec_Fetch_Mode="navigate") == 200
        # The marker the sign-in leaves at path `/` — the refresh cookie itself
        # is scoped to the auth routes and never arrives with a page.
        assert status(on, page, X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate", Cookie="cheese_signed_in=1") == 200
        assert status(on, page, X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate", User_Agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CheeseDesktop") == 200
        assert status(on, "/api/version", X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate") != 302
        assert status(on, "/connector/install.sh", X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate") != 302
        # A host of the deployment that is not a page origin — the docs' own, a
        # content host — is answered as before.
        assert status(on, page, host="docs.okcheese.com", X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate") == 200

        # The entry itself never redirects to itself: a visitor already there
        # stays there, whatever their address.
        assert status(on, page, host="okcheese.cn", X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate") == 200

        # A subrequest that is not a navigation is not moved either: a chunk or
        # an XHR has to stay on the origin its page is on.
        assert status(on, "/assets/app.js", X_Forwarded_For=CHINA, Sec_Fetch_Mode="cors", Accept="*/*") != 302

        off = build(Path(second), reserve_port(), "", f'["{ENTRY}"]')
        assert status(off, page, X_Forwarded_For=CHINA, Sec_Fetch_Mode="navigate") == 200, (
            "a deployment that never set MAINLAND_ENTRY must redirect nobody"
        )


if __name__ == "__main__":
    main()
