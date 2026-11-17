"""学生的程序在什么样的容器里跑（方案 §安全）：断网、只读、无特权、资源有上限。

规则是安全上的：一个评测容器连不出去、改不了根目录和代码、提不了权、吃不光主机。
这些上限真起作用与否由 ``scripts/remote_execution/judge_acceptance.py`` 在真 docker
上逐条试；这里守的是评测主机收到的每一次 ``docker run`` 都带着它们 —— 包括编译那一次。
"""

import pytest

from app.domain.judge.container import CODE, cpu_seconds, run_args, workdir_of


def _options(args: list[str]) -> dict[str, list[str]]:
    """``docker run``'s options, each flag with every value it was given."""
    assert args[:2] == ["docker", "run"]
    found: dict[str, list[str]] = {}
    i = 2
    while i < len(args) and args[i].startswith("--"):
        flag = args[i]
        if flag in ("--interactive", "--init", "--read-only"):
            found.setdefault(flag, []).append("")
            i += 1
            continue
        found.setdefault(flag, []).append(args[i + 1])
        i += 2
    return found


def _args(**over):
    base = dict(
        name="cheese-judge-x",
        image="judge:test",
        code_dir="/tmp/work/code",
        workdir=CODE,
        command="python3 main.py",
        memory_mb=256,
        cpu_limit_s=1,
    )
    base.update(over)
    return run_args(**base)


@pytest.mark.parametrize("compile_step", [False, True])
def test_a_judged_program_has_no_network_and_no_privileges(compile_step):
    opts = _options(_args(code_writable=compile_step))
    assert opts["--network"] == ["none"]
    assert "--read-only" in opts
    assert opts["--cap-drop"] == ["ALL"]
    assert opts["--security-opt"] == ["no-new-privileges"]
    assert opts["--user"] == ["1000:1000"]
    assert opts["--ipc"] == ["none"]


def test_memory_swap_cpu_and_processes_are_bounded():
    opts = _options(_args(memory_mb=512, cpu_limit_s=2))
    assert opts["--memory"] == ["512m"]
    # Swap equal to memory: no swap at all, so over the limit is killed.
    assert opts["--memory-swap"] == ["512m"]
    assert opts["--cpus"] == ["1"]
    assert opts["--pids-limit"] == ["64"]
    assert "cpu=2:3" in opts["--ulimit"]


def test_the_only_writable_place_is_a_bounded_tmp():
    opts = _options(_args())
    (tmpfs,) = opts["--tmpfs"]
    assert tmpfs.startswith("/tmp:") and "size=256m" in tmpfs


def test_the_code_is_read_only_when_a_case_runs_and_writable_only_to_compile():
    (case_mount,) = _options(_args())["--mount"]
    (build_mount,) = _options(_args(code_writable=True))["--mount"]
    assert "readonly" in case_mount.split(",")
    assert "readonly" not in build_mount.split(",")
    assert f"target={CODE}" in case_mount


def test_nothing_is_logged_or_pulled_while_judging():
    opts = _options(_args())
    assert opts["--log-driver"] == ["none"]
    assert opts["--pull"] == ["never"]


@pytest.mark.parametrize(
    ("limit_ms", "seconds"), [(500, 1), (1000, 1), (1001, 2), (10_000, 10)]
)
def test_the_cpu_ulimit_is_the_time_limit_rounded_up(limit_ms, seconds):
    assert cpu_seconds(limit_ms) == seconds


@pytest.mark.parametrize(
    ("code_dir", "inside"),
    [("", CODE), ("src", f"{CODE}/src"), ("./a/b/", f"{CODE}/a/b"), ("../etc", CODE)],
)
def test_the_working_directory_never_leaves_the_code(code_dir, inside):
    assert workdir_of(code_dir) == inside
