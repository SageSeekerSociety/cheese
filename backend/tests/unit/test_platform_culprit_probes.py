"""A platform probe distinguishes a test's verdict from its setup's failure."""

import base64
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.domain.agent import culprit_probe_script
from app.domain.agent.culprit_probes import PlatformProbes


@pytest.mark.parametrize(
    "setup,test,expected",
    [
        (0, 0, "pass"),
        (0, 1, "fail"),
        (1, 0, "unknown"),
        (0, 3, "unknown"),
        (0, 4, "unknown"),
    ],
)
def test_probe_reads_the_verdict_from_the_test_report(tmp_path, setup, test, expected):
    source = tmp_path / "rocket"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "pyproject.toml").write_text('[project]\nname = "rocket"\n')
    (source / "uv.lock").write_text("locked\n")
    (source / "test_launch.py").write_text("def test_launch(): pass\n")
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "add",
            "pyproject.toml",
            "uv.lock",
            "test_launch.py",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Initial rocket",
        ],
        check=True,
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    uv = binary / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        f"if sys.argv[1] == 'sync': sys.exit({setup})\n"
        "path = next(a.split('=',1)[1] for a in sys.argv if a.startswith('--junitxml='))\n"
        f"failure = '<failure/>' if {test} == 1 else ''\n"
        f"error = '<error/>' if {test} == 3 else ''\n"
        f"skipped = '<skipped/>' if {test} == 4 else ''\n"
        "pathlib.Path(path).write_text('<testsuite><testcase>' + failure + error + skipped + '</testcase></testsuite>')\n"
        f"sys.exit(0 if {test} == 4 else {test})\n"
    )
    uv.chmod(0o755)
    directory = tmp_path / "probe"
    directory.mkdir()
    import os
    from unittest.mock import patch

    with patch.dict(
        os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    ):
        assert (
            culprit_probe_script.probe(source, ["test_launch.py"], directory)
            == expected
        )
    ledger = [
        json.loads(line)
        for line in (directory / "commands.jsonl").read_text().splitlines()
    ]
    assert any("uv" in event.get("argv", []) for event in ledger)
    assert (directory / "output.log").is_file()


async def test_probe_uses_platform_executor_without_forge_workflow(monkeypatch):
    calls = []
    session = uuid.uuid4()
    project = uuid.uuid4()
    lease = {"device_id": "rocket-sandbox", "state": "/sandbox/executor"}
    answers = [{"started": True}, {}, {"exit": 1}]

    async def call(target, method, params):
        calls.append((target, method, params))
        return answers.pop(0)

    probes = PlatformProbes(None, call=call)

    async def target(project_id, session_id=None):
        assert project_id == project
        return session, lease

    monkeypatch.setattr(probes, "_target", target)
    run = await probes.start(project, "a" * 40, ["test_launch.py"], "nonce")
    assert run == session.hex + ":nonce"
    assert await probes.verdict(project, run) == "running"
    assert await probes.verdict(project, run) == "fail"
    assert all(target == lease and method == "control" for target, method, _ in calls)
    program = base64.b64decode(calls[0][2]["stdin"])
    assert program == Path(culprit_probe_script.__file__).read_bytes()


def test_unexpected_program_error_is_unknown(tmp_path):
    result = subprocess.run(
        [sys.executable, culprit_probe_script.__file__], cwd=tmp_path
    )
    assert result.returncode == 2
