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
from private import ensure, gate_name, inspect, release, target  # noqa: E402

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


#: Run in the container: one line per address, "open" or why it is not.
REACH = r"""
import socket, sys
for target in sys.argv[1:]:
    host, port = target.rsplit(":", 1)
    try:
        socket.create_connection((host, int(port)), timeout=5).close()
        print(target, "open")
    except OSError as error:
        print(target, type(error).__name__)
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

        def document():
            # A living doc's text is a file on the machine, and the session reads
            # it through the executor, the way `cheese_doc_set` does (结论 63).
            # The backend half is recorded here rather than served: what this
            # acceptance owns is the executor.
            from importlib.machinery import SourceFileLoader

            from executor_transport import read_file_on_the_machine

            cheese = SourceFileLoader(
                "cheese_platform_tools", str(ROOT / "backend/sandbox/cheese")
            ).load_module()
            sent = []

            def through_the_executor(payload, arguments):
                return client.call(
                    "invoke",
                    {"id": payload["id"], "tool": payload["tool"], "args": arguments},
                )

            class Host:
                environ = {"CHEESE_TOPIC": config["topic"], "CHEESE_AUTHOR": "cheese"}
                doc_versions = {config["topic"]: 1}

                def request(self, plan):
                    sent.append(plan)
                    return {"data": {"doc_version": 2}}

                def read_file(self, path):
                    return read_file_on_the_machine(
                        through_the_executor, path, uuid.uuid4().hex
                    )

                def sync_task(self, task_id):
                    raise AssertionError("a living doc pushes no task")

            said = cheese.run_platform_tool(
                "cheese_doc_set", {"path": "/work/draft.md"}, Host()
            )
            assert "已更新" in said, said
            assert sent[0]["body"]["content"] == "Second draft\n", sent
            assert sent[0]["body"]["expected_version"] == 1, sent
            return sent[0]["body"]

        record("document-publication", document)

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

        def reach(*targets):
            command = "python3 - " + " ".join(targets) + " <<'PY'\n" + REACH + "PY"
            lines = shell(command)["stdout"].split("\n")
            return dict(line.split(" ", 1) for line in lines if line.strip())

        def network():
            # Internal addresses are refused; the platform and the internet are not.
            refused = [
                f"{gateway}:{neighbour_port}",
                "169.254.169.254:80",
                f"{gateway}:22",
            ]
            lan = host_lan_address()
            if not is_public(lan):
                refused.append(f"{lan}:{platform_port}")
            seen = reach(f"{gateway}:{platform_port}", *refused)
            assert seen[f"{gateway}:{platform_port}"] == "open", seen
            for address in refused:
                assert seen[address] != "open", seen
            platform = shell(
                'python3 -c "import os, urllib.request; print(urllib.request'
                ".urlopen(os.environ['CHEESE_API'], timeout=5).read().decode())\""
            )
            assert platform["stdout"].strip() == "ok", platform
            # A public name resolves to public addresses, and they answer.
            names = shell(
                "python3 -c \"import socket; print(' '.join(sorted({a[4][0] for a in "
                "socket.getaddrinfo('pypi.org', 443, socket.AF_INET)})))\""
            )["stdout"].split()
            assert names and all(is_public(a) for a in names), names
            assert reach(f"{names[0]}:443")[f"{names[0]}:443"] == "open"
            # Nothing the container runs can lift the rules.
            lifted = shell("iptables -F OUTPUT 2>&1; echo status=$?")["stdout"]
            assert "status=0" not in lifted, lifted
            assert (
                reach(f"{gateway}:{neighbour_port}")[f"{gateway}:{neighbour_port}"]
                != "open"
            )
            return {"public": names, "lan": lan, **seen}

        record("network", network)

        def gate_lost():
            # Without its gate the container has no rules; it is not reused.
            subprocess.run(
                ["docker", "rm", "--force", gate_name(config["topic"])],
                check=True,
                capture_output=True,
            )
            ensure(config, args.output, env)
            result = shell("test ! -e /work/draft.md && printf 'replaced'")
            assert "replaced" in result["stdout"], result
            seen = reach(f"{gateway}:{platform_port}", "169.254.169.254:80")
            assert seen[f"{gateway}:{platform_port}"] == "open", seen
            assert seen["169.254.169.254:80"] != "open", seen
            return seen

        record("gate-lost", gate_lost)

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
