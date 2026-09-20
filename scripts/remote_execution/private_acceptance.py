"""Exercise the real private executor, using only disposable containers."""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/app/domain/agent/harness/claude_code/remote_execution"
sys.path.insert(0, str(SOURCE))
from client import RemoteClient  # noqa: E402
from private import ensure, inspect, release, target  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-host")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.docker_host:
        os.environ["DOCKER_HOST"] = args.docker_host
    config = target(uuid.uuid4())
    env = {
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
            # The HTTP fixture lives inside the disposable executor and opens no
            # host port. It records the same request consumed by the backend.
            source = """from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import json
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers()
  self.wfile.write(b'{"data":{"content":"","doc_version":1}}')
 def do_PUT(self):
  value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert value['expected_version'] == 1
  Path('/work/published.json').write_text(json.dumps(value))
  self.send_response(200); self.end_headers()
  self.wfile.write(b'{"data":{"doc_version":2}}')
HTTPServer(('127.0.0.1', 8765), Handler).serve_forever()
"""
            invoke("Write", file_path="/work/api-fixture.py", content=source)
            job = invoke(
                "Bash", command="python3 /work/api-fixture.py", run_in_background=True
            )
            result = shell(
                "python3 - <<'PY'\nimport socket, time\nfor _ in range(50):\n try:\n  socket.create_connection(('127.0.0.1',8765),.1).close(); break\n except OSError: time.sleep(.1)\nelse: raise RuntimeError('fixture unavailable')\nPY\nexport CHEESE_API=http://127.0.0.1:8765\ncheese doc get && cheese doc set /work/draft.md"
            )
            assert "已更新" in result["stdout"], result
            saved = json.loads(shell("cat published.json")["stdout"])
            assert saved["content"] == "Second draft\n"
            client.control({"subtype": "stop_task", "task_id": job["backgroundTaskId"]})
            return saved

        record("document-publication", document)

        def controls():
            import base64

            client.control(
                {
                    "subtype": "stage_file",
                    "path": "uploads/input.txt",
                    "data": base64.b64encode(b"input data").decode(),
                }
            )
            assert (
                client.control(
                    {"subtype": "read_file", "path": "/work/uploads/input.txt"}
                )["contents"]
                == "input data"
            )
            assert (
                "draft.md" in client.control({"subtype": "file_suggestions"})["files"]
            )
            assert client.control({"subtype": "get_workspace_diff"}) == {"diff": ""}
            return "staged input and RC file controls"

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
