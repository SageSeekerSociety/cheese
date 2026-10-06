"""Exercise the real private executor, using only disposable containers."""

import argparse
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/app/domain/agent/harness/claude_code/remote_execution"
sys.path.insert(0, str(SOURCE))
from client import RemoteClient  # noqa: E402
from private import (  # noqa: E402
    EGRESS,
    EGRESS_PORT,
    ensure,
    inspect,
    release,
    target,
)

sys.path.append(str(ROOT / "backend/app/domain/fetch"))
from addresses import is_public  # noqa: E402


def host_service():
    """An HTTP service on every interface of the host, as the backend's is."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def bridge_gateway():
    """The host as a container on the default bridge reaches it."""
    found = subprocess.run(
        ["docker", "network", "inspect", "bridge"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(found.stdout)[0]["IPAM"]["Config"][0]["Gateway"]


def host_lan_address():
    """The address the host leaves by: its LAN address on most machines."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("1.1.1.1", 53))
        return probe.getsockname()[0]


#: Run in the container, one line out per probe in: what came of it.
#:   http <url>          through the proxy the environment names: status or error
#:   socks <host> <port> SOCKS5 with the name sent to the proxy: reply code
#:   direct <host> <port> a plain connection, past the proxy: open or error
#:   resolve <name>      the container's own DNS: addresses or error
REACH = r"""
import os, socket, struct, sys, urllib.error, urllib.parse, urllib.request
for probe in sys.argv[1:]:
    kind, *args = probe.split("|")
    try:
        if kind == "http":
            with urllib.request.urlopen(args[0], timeout=15) as response:
                result = f"{response.status} {response.read(64)!r}"
        elif kind == "socks":
            proxy = urllib.parse.urlsplit(os.environ["ALL_PROXY"])
            with socket.create_connection((proxy.hostname, proxy.port), 15) as s:
                s.sendall(b"\x05\x01\x00")
                assert s.recv(2) == b"\x05\x00"
                name = args[0].encode()
                s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                          + struct.pack(">H", int(args[1])))
                result = f"reply {s.recv(10)[1]}"
        elif kind == "direct":
            socket.create_connection((args[0], int(args[1])), timeout=5).close()
            result = "open"
        else:
            result = " ".join(sorted({a[4][0] for a in socket.getaddrinfo(args[0], 443)}))
    except urllib.error.HTTPError as error:
        result = f"{error.code}"
    except Exception as error:
        result = type(error).__name__
    print(probe, result)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-host")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.docker_host:
        os.environ["DOCKER_HOST"] = args.docker_host
    config = target(uuid.uuid4())
    # The platform, on the host as dev's backend is; another service beside it.
    gateway = bridge_gateway()
    platform_port = host_service()
    neighbour_port = host_service()
    env = {
        "CHEESE_API": f"http://{gateway}:{platform_port}",
        "CHEESE_TOPIC": config["topic"],
        "CHEESE_TOKEN": "test-private-token",
        "ANTHROPIC_AUTH_TOKEN": "must-not-enter-executor",
        "HOST_SECRET": "host-only",
    }
    results = []

    def record(name, function):
        started = time.time()
        with (args.output / "progress.jsonl").open("a") as log:
            log.write(
                json.dumps({"time": started, "case": name, "status": "start"}) + "\n"
            )
        value = function()
        results.append(
            {
                "case": name,
                "status": "passed",
                "elapsed": time.time() - started,
                "result": value,
            }
        )
        (args.output / "results.json").write_text(json.dumps(results, indent=2))
        return value

    client = RemoteClient(config)

    def invoke(tool, **arguments):
        receipt = client.call(
            "invoke", {"id": uuid.uuid4().hex, "tool": tool, "args": arguments}
        )
        if "error" in receipt:
            raise AssertionError(receipt["error"])
        return receipt["value"]

    def shell(command):
        value = invoke("Bash", command=command)
        assert not value.get("backgroundTaskId"), value
        return value

    try:
        (args.output / "inputs.json").write_text(
            json.dumps({"target": config, "env": env}, indent=2)
        )
        record("start", lambda: ensure(config, args.output, env))
        record(
            "write",
            lambda: invoke(
                "Write", file_path="/work/draft.md", content="First draft\n"
            ),
        )
        record("read", lambda: invoke("Read", file_path="/work/draft.md"))
        record(
            "edit",
            lambda: invoke(
                "Edit",
                file_path="/work/draft.md",
                old_string="First",
                new_string="Second",
            ),
        )

        def shell_and_reuse():
            result = shell(
                "python3 - <<'PY'\nfrom pathlib import Path\nassert Path('draft.md').read_text() == 'Second draft\\n'\nPath('result.json').write_text('{\"done\":true}')\nprint('script-ok')\nPY"
            )
            assert "script-ok" in result["stdout"], result
            ensure(
                config, args.output, dict(env, CHEESE_TOKEN="refreshed-private-token")
            )
            assert "Second draft" in shell("cat draft.md")["stdout"]
            assert (
                "refreshed-private-token"
                in shell("printf '%s' \"$CHEESE_TOKEN\"")["stdout"]
            )
            return "shell, cross-turn draft, credential refresh"

        record("reuse", shell_and_reuse)

        def controls():
            # Outside the workspace, so the diff below still reads clean.
            home = shell('printf %s "$HOME"')["stdout"].strip()
            shell(f"printf 'input data' > {home}/input.txt")
            assert (
                client.control({"subtype": "read_file", "path": f"{home}/input.txt"})[
                    "contents"
                ]
                == "input data"
            )
            assert (
                "draft.md" in client.control({"subtype": "file_suggestions"})["files"]
            )
            assert client.control({"subtype": "get_workspace_diff"}) == {"diff": ""}
            return "file controls"

        record("file-controls", controls)

        def isolation():
            result = shell(
                "python3 - <<'PY'\nimport os\nfrom pathlib import Path\nassert 'HOST_SECRET' not in os.environ\nassert 'ANTHROPIC_AUTH_TOKEN' not in os.environ\nassert not Path('/var/run/docker.sock').exists()\nassert not Path('/Users/andyl').exists()\ntry:\n Path('/etc/private-probe').write_text('escape')\nexcept OSError:\n print('isolated')\nelse:\n raise AssertionError('root writable')\nPY"
            )
            assert "isolated" in result["stdout"], result
            shell(
                "mkdir -p .claude; printf '{\"hooks\":{}}' > .claude/settings.json; printf 'scratch-injection' > CLAUDE.md"
            )
            context = client.call("context")
            assert (
                context["files"] == {}
                and "scratch-injection" not in context["instructions"]
            )
            return inspect(config)["HostConfig"]["ReadonlyRootfs"]

        record("isolation", isolation)

        def reach(*probes):
            command = "python3 - '" + "' '".join(probes) + "' <<'PY'\n" + REACH + "PY"
            lines = shell(command)["stdout"].split("\n")
            return dict(line.split(" ", 1) for line in lines if line.strip())

        platform = f"http://{gateway}:{platform_port}/"
        public = "https://pypi.org/simple/"

        def network():
            lan = host_lan_address()
            internal = [
                f"http://{gateway}:{neighbour_port}/",  # the host's other services
                f"http://{gateway}:22/",
                "http://169.254.169.254/latest/meta-data/",
                f"http://{EGRESS}:{EGRESS_PORT}/",  # a name that resolves inward
            ]
            if not is_public(lan):
                internal.append(f"http://{lan}:{platform_port}/")
            seen = reach(
                f"http|{platform}",
                f"http|{public}",
                "socks|pypi.org|443",
                f"socks|{gateway}|{neighbour_port}",
                "socks|169.254.169.254|80",
                *(f"http|{url}" for url in internal),
                # Past the proxy, nothing answers at all.
                "direct|151.101.0.223|443",
                f"direct|{gateway}|{platform_port}",
                "resolve|pypi.org",
            )
            assert seen[f"http|{platform}"] == "200 b'ok'", seen
            assert seen[f"http|{public}"].startswith("200 "), seen
            assert seen["socks|pypi.org|443"] == "reply 0", seen
            assert seen[f"socks|{gateway}|{neighbour_port}"] == "reply 2", seen
            assert seen["socks|169.254.169.254|80"] == "reply 2", seen
            for url in internal:
                assert seen[f"http|{url}"] == "403", seen
            assert seen["direct|151.101.0.223|443"] != "open", seen
            assert seen[f"direct|{gateway}|{platform_port}"] != "open", seen
            assert not seen["resolve|pypi.org"][0].isdigit(), seen
            return {"lan": lan, **seen}

        record("network", network)

        def proxy_lost():
            # Without the proxy the container has no way out; the next start
            # brings it back, and the container's scratch with it.
            subprocess.run(
                ["docker", "rm", "--force", EGRESS], check=True, capture_output=True
            )
            gone = reach(f"http|{platform}", f"http|{public}")
            assert not any(v.startswith("200") for v in gone.values()), gone
            ensure(config, args.output, env)
            assert "Second draft" in shell("cat draft.md")["stdout"]
            back = reach(f"http|{platform}", f"http|{public}")
            assert all(v.startswith("200") for v in back.values()), back
            return {"gone": gone, "back": back}

        record("proxy-lost", proxy_lost)

        def quota():
            result = shell(
                "python3 - <<'PY'\nfrom pathlib import Path\np = Path('/work/fill')\ntry:\n with p.open('wb') as f:\n  for _ in range(80): f.write(b'x' * 1024 * 1024)\nexcept OSError as e:\n assert e.errno == 28, e\n print('quota-enforced')\nelse:\n raise AssertionError('quota missing')\nfinally:\n p.unlink(missing_ok=True)\nPY"
            )
            assert "quota-enforced" in result["stdout"], result
            return result["stdout"]

        record("quota", quota)

        def recreate():
            release(config)
            assert inspect(config) is None
            ensure(config, args.output, env)
            result = shell("test ! -e /work/draft.md && printf 'empty-scratch'")
            assert "empty-scratch" in result["stdout"], result
            return result["stdout"]

        record("recreate", recreate)
    finally:
        release(config)
    print(json.dumps({"passed": len(results), "output": str(args.output)}))


if __name__ == "__main__":
    main()
