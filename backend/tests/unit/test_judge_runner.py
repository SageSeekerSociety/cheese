"""评测主机上那段脚本给每个用例判的状态（方案 §一次评测怎么跑、§看结果）。

docker 是 ``tests/support/fake_docker.py`` 那个替身：命令在本机用 ``sh -c`` 跑，所以
这里看得到的是脚本自己的判断 —— 比对、超时、输出超限、运行出错、编译失败、超内存，
以及回传的结果够小、临时目录不留。容器的上限是不是真起作用在真 docker 上试
（``scripts/remote_execution/judge_acceptance.py``）。
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
from pathlib import Path

import pytest

from app.domain.judge import runner
from app.domain.judge.script import RUNNER_SRC

FAKE = Path(__file__).resolve().parents[1] / "support" / "fake_docker.py"


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "docker"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    shim.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(runner.tempfile, "tempdir", None)
    monkeypatch.setattr(runner, "STARTUP_S", 0.5)
    return scratch


def _code(files: dict[str, str]) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return base64.b64encode(buffer.getvalue()).decode()


def _job(run_cmd: str, cases, **over) -> dict:
    job = {
        "kind": "IO",
        "image": "judge:test",
        "code": _code({"main.sh": "cat\n"}),
        "codeDir": "",
        "compileCmd": "",
        "runCmd": run_cmd,
        "timeLimitMs": 500,
        "memoryMb": 256,
        "compare": "TRIM_TRAILING",
        "tolerance": 0.0,
        "cases": [
            {"index": i, "input": given, "expected": wanted}
            for i, (given, wanted) in enumerate(cases)
        ],
    }
    job.update(over)
    return job


def _statuses(reply: dict) -> list[str]:
    assert reply["ok"], reply
    return [case["status"] for case in reply["cases"]]


def test_a_right_answer_passes_and_a_wrong_one_does_not(fake_docker):
    reply = runner.judge(_job("sh main.sh", [("3\n", "3\n"), ("4\n", "5\n")]))
    assert _statuses(reply) == ["ok", "wrong"]
    assert reply["cases"][1]["actual"] == "4\n"
    assert reply["machineMs"] >= 0


def test_the_setters_compare_mode_decides(fake_docker):
    reply = runner.judge(
        _job("sh main.sh", [("3.14159", "3.1416")], compare="NUMERIC", tolerance=1e-4)
    )
    assert _statuses(reply) == ["ok"]


def test_a_program_that_sleeps_past_the_wall_clock_is_a_timeout(fake_docker):
    reply = runner.judge(_job("sleep 30", [("", "")]))
    assert _statuses(reply) == ["timeout"]


def test_output_past_the_limit_is_cut_off_and_said_so(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "OUTPUT_LIMIT_BYTES", 64 * 1024)
    reply = runner.judge(_job("head -c 1000000 /dev/zero", [("", "")]))
    assert _statuses(reply) == ["output_limit"]


def test_output_past_the_limit_is_killed_while_stdout_keeps_draining(
    fake_docker, monkeypatch
):
    # A real docker does not let a killed container exit while its stdout is
    # unread, so a kill waited on by the thread that reads stdout never
    # returns (seen on the CI's docker: 47 s, then a platform failure). The
    # kill has to come from the thread that waits on the container.
    monkeypatch.setattr(runner, "OUTPUT_LIMIT_BYTES", 64 * 1024)
    real = runner._docker
    killers = []

    def docker(*args, **kwargs):
        if args[:1] == ("kill",):
            killers.append(threading.current_thread() is threading.main_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(runner, "_docker", docker)
    reply = runner.judge(_job("while :; do head -c 65536 /dev/zero; done", [("", "")]))
    assert _statuses(reply) == ["output_limit"]
    assert killers == [True]


def test_a_crash_is_a_runtime_error_with_what_it_said(fake_docker):
    reply = runner.judge(_job("echo boom >&2; exit 3", [("", "")]))
    assert _statuses(reply) == ["runtime_error"]
    assert "boom" in reply["cases"][0]["stderr"]


def test_a_program_the_memory_limit_killed_is_out_of_memory(fake_docker):
    reply = runner.judge(_job("python3 -c 1 # __oom__", [("", "")]))
    assert _statuses(reply) == ["memory"]


def test_a_failed_compile_fails_every_case_and_shows_the_compiler(fake_docker):
    reply = runner.judge(
        _job("sh main.sh", [("1", "1"), ("2", "2")], compileCmd="echo broken; exit 1")
    )
    assert _statuses(reply) == ["compile_error", "compile_error"]
    assert "broken" in reply["compile"]["output"]


def test_what_the_compile_built_is_what_the_cases_run(fake_docker):
    reply = runner.judge(
        _job(
            "sh built.sh",
            [("7\n", "7\n")],
            compileCmd="cp main.sh built.sh",
        )
    )
    assert _statuses(reply) == ["ok"]


def test_a_missing_code_directory_is_a_runtime_error_not_a_crash(fake_docker):
    reply = runner.judge(_job("sh main.sh", [("1", "1")], codeDir="nowhere"))
    assert _statuses(reply) == ["runtime_error"]


def test_a_kind_it_cannot_judge_yet_is_said_not_guessed(fake_docker):
    reply = runner.judge(_job("sh main.sh", [], kind="NOTEBOOK"))
    assert reply["ok"] is False and reply["error"] == "unsupported"


def test_an_archive_reaching_outside_its_directory_never_lands(fake_docker):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("../escaped.sh")
        info.size = 4
        archive.addfile(info, io.BytesIO(b"cat\n"))
    code = base64.b64encode(buffer.getvalue()).decode()
    reply = runner.judge(_job("sh main.sh", [("1", "1")], code=code))
    assert reply["ok"] is False and reply["error"] == "platform"
    assert not list(fake_docker.parent.rglob("escaped.sh"))


def test_many_outputs_are_shrunk_to_fit_the_reply(fake_docker, monkeypatch):
    monkeypatch.setattr(runner, "REPLY_BUDGET", 4 * 1024)
    code = _code({"main.sh": "head -c 8000 /dev/zero | tr '\\0' x\n"})
    reply = runner.judge(_job("sh main.sh", [("", "")] * 4, code=code))
    assert _statuses(reply) == ["wrong"] * 4
    assert all(len(case["actual"]) <= 1024 for case in reply["cases"])


def test_the_work_directory_is_gone_afterwards(fake_docker):
    runner.judge(_job("sh main.sh", [("1", "1")]))
    runner.judge(_job("sh main.sh", [("1", "1")], compileCmd="exit 1"))
    assert not list(fake_docker.iterdir())


def test_the_script_sent_to_the_host_runs_on_its_own(fake_docker):
    """What the judge host runs is the three files as one script, with no
    backend to import from."""
    job = _job("sh main.sh", [("3\n", "3\n"), ("4\n", "5\n")])
    done = subprocess.run(
        [sys.executable, "-I", "-c", RUNNER_SRC],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ},
        check=True,
    )
    assert _statuses(json.loads(done.stdout)) == ["ok", "wrong"]
