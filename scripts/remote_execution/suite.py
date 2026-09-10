"""Run the pinned terminal acceptance and backend regression suite with receipts."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from model_fixture import dump, log

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--redis", default=shutil.which("redis-server"))
    options = parser.parse_args()
    if not options.claude or not options.redis:
        parser.error("Both the pinned Claude Code build and redis-server are required")
    folder = options.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    sources = [
        *ROOT.glob("backend/app/domain/agent/harness/claude_code/**/*.py"),
        *ROOT.glob("backend/app/domain/agent/harness/claude_code/**/*.js"),
        *ROOT.glob("scripts/remote_execution/*.py"),
        *ROOT.glob("backend/tests/unit/test_remote*.py"),
        ROOT / "backend/app/domain/agent/remote_control.py",
        ROOT / "backend/app/api/routes/remote_control.py",
        ROOT / "backend/app/domain/agent/device_provider.py",
        ROOT / "backend/app/core/config.py",
        ROOT / "scripts/remote_execution/package-lock.json",
        ROOT / "backend/uv.lock",
    ]
    manifest = {
        "python": sys.version,
        "claude": subprocess.check_output(
            [options.claude, "--version"], text=True
        ).strip(),
        "source": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(sources))
        },
    }
    manifest_file = folder / "inputs.json"
    if manifest_file.exists() and json.loads(manifest_file.read_text()) != manifest:
        raise RuntimeError("Inputs changed; use a new output directory")
    dump(manifest_file, manifest)
    receipt_file = folder / "results.json"
    receipts = json.loads(receipt_file.read_text()) if receipt_file.exists() else {}

    def case(name, command, *, cwd=ROOT, env=None):
        if receipts.get(name, {}).get("passed"):
            return
        attempt = len(list(folder.glob(name + ".attempt-*.log"))) + 1
        logfile = folder / f"{name}.attempt-{attempt}.log"
        log(
            folder / "progress.jsonl",
            {"item": name, "status": "started", "command": command},
        )
        print(f"Running {name}: {logfile}", flush=True)
        with logfile.open("w") as output:
            process = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=300,
            )
        receipts[name] = {
            "passed": process.returncode == 0,
            "exit_code": process.returncode,
            "log": str(logfile),
        }
        dump(receipt_file, receipts)
        log(
            folder / "progress.jsonl",
            {"item": name, "status": "passed" if process.returncode == 0 else "failed"},
        )
        if process.returncode:
            raise RuntimeError(f"{name} failed; see {logfile}")

    env = dict(os.environ, CHEESE_TEST_CLAUDE=options.claude)
    case(
        "executor",
        [sys.executable, str(ROOT / "backend/tests/unit/test_remote_execution.py")],
        env=env,
    )
    for name, args in (
        ("native-terminal-rc", ["--launcher", "device", "--rc"]),
        # RC acknowledges connection readiness; typing into a terminal after a
        # fixed sleep can leave Enter unprocessed while the native UI mounts.
        ("plugin-disabled", ["--mode", "disabled", "--rc"]),
        ("plugin-throws", ["--mode", "throw", "--rc"]),
        ("plugin-timeout", ["--mode", "timeout", "--rc"]),
        ("executor-disconnected", ["--mode", "disconnect", "--rc"]),
    ):
        attempt = len(list(folder.glob(name + ".attempt-*.log"))) + 1
        case(
            name,
            [
                sys.executable,
                str(ROOT / "scripts/remote_execution/acceptance.py"),
                "--claude",
                options.claude,
                "--output",
                str(folder / f"{name}-{attempt}"),
                *args,
            ],
        )
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    with (folder / "redis.log").open("a") as output:
        redis = subprocess.Popen(
            [
                options.redis,
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
                "--save",
                "",
                "--appendonly",
                "no",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            case(
                "backend-regressions",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/unit/test_remote_control.py",
                    "tests/unit/test_device_launch.py",
                    "tests/unit/test_device_launch_route.py",
                    "-q",
                ],
                cwd=ROOT / "backend",
                env=dict(env, REDIS_URL=f"redis://127.0.0.1:{port}/0"),
            )
        finally:
            redis.terminate()
            redis.wait(timeout=10)
    print(
        json.dumps(
            {
                "passed": all(r["passed"] for r in receipts.values()),
                "cases": len(receipts),
                "output": str(folder),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
