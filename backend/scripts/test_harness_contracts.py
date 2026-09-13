"""Install isolated harness binaries and retain the provider contract evidence."""

import ast
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    backend = Path(__file__).resolve().parents[1]
    root = backend.parent
    run = (
        root / "logs/harness-contracts" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    run.mkdir(parents=True)
    tools = root / "tmp/harness-contract-tools"
    source = backend / "app/domain/agent/harness/claude_code/remote_execution/client.py"
    claude_version = next(
        ast.literal_eval(node.value)
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PINNED_VERSION"
            for target in node.targets
        )
    )
    packages = [f"@anthropic-ai/claude-code@{claude_version}", "@openai/codex@0.154.0"]
    (run / "inputs.json").write_text(json.dumps({"packages": packages}, indent=2))
    print(f"Contract evidence: {run}", flush=True)
    with (run / "install.log").open("w") as log:
        subprocess.run(
            [
                "npm",
                "install",
                "--prefix",
                str(tools),
                "--no-audit",
                "--no-fund",
                *packages,
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    env = {
        **os.environ,
        "PATH": str(tools / "node_modules/.bin") + os.pathsep + os.environ["PATH"],
    }
    with (run / "versions.log").open("w") as log:
        for binary in ("claude", "codex"):
            subprocess.run(
                [binary, "--version"],
                check=True,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--junitxml",
        str(run / "results.xml"),
        "--basetemp",
        str(run / "pytest"),
        "tests/unit/test_harness_prompt_contract.py",
        "tests/unit/test_codex_app_server.py",
        "tests/unit/test_codex_session.py",
        "tests/unit/test_codex_runner.py",
        "tests/unit/test_codex_events.py",
        "tests/unit/test_codex_backlog.py",
        "tests/unit/test_codex_host.py",
        "tests/unit/test_codex_subscription.py",
        "tests/unit/test_codex_runtime.py",
        "tests/unit/test_codex_runner_process.py",
        "tests/unit/test_codex_tools.py",
        "tests/unit/test_codex_provider_requests.py",
        "tests/unit/test_claude_provider_requests.py",
    ]
    with (run / "pytest.log").open("w") as log:
        result = subprocess.run(
            command, cwd=backend, env=env, stdout=log, stderr=subprocess.STDOUT
        )
    print((run / "pytest.log").read_text(), end="")
    if result.returncode == 0 and ET.parse(run / "results.xml").findall(".//skipped"):
        print("Required harness contracts were skipped; acceptance is incomplete.")
        return 1
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
