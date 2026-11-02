"""What a destroyed sandbox says was running in it.

``sandbox_home.destroy`` reads the sandbox's cgroup before it stops the
executor: each process's command line, the executor and the bare shells that
only carry the command beside them left out, what looks like a secret masked,
at most ten lines of two hundred characters. A cgroup the host cannot read
names nothing.

The kernel's files are written here in its own format, and the paths the host
program reads are pointed at them.
"""

import shutil
from pathlib import Path

import pytest

from app.domain.machine import sandbox_home


@pytest.fixture
def kernel(tmp_path, monkeypatch) -> Path:
    cgroups = tmp_path / "cgroup" / "cheese-sandboxes"
    cgroups.mkdir(parents=True)
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setattr(sandbox_home, "SANDBOX_CGROUPS", cgroups)
    monkeypatch.setattr(sandbox_home, "PROC", proc)
    return tmp_path


def _process(kernel, cgroup, pid, *argv):
    (kernel / "cgroup" / "cheese-sandboxes" / cgroup).mkdir(parents=True, exist_ok=True)
    procs = kernel / "cgroup" / "cheese-sandboxes" / cgroup / "cgroup.procs"
    with procs.open("a") as handle:
        handle.write(f"{pid}\n")
    (kernel / "proc" / str(pid)).mkdir()
    (kernel / "proc" / str(pid) / "cmdline").write_bytes(
        b"\0".join(arg.encode() for arg in argv) + b"\0"
    )


def test_the_processes_of_a_sandbox_are_read_from_its_cgroup(kernel):
    _process(kernel, "resource", 101, "python3", "-m", "http.server", "8000")
    _process(kernel, "resource", 102, "npm", "run", "dev")
    _process(kernel, "another", 103, "pytest")

    assert sandbox_home.processes("resource") == [
        "python3 -m http.server 8000",
        "npm run dev",
    ]


def test_the_executor_and_bare_shell_wrappers_are_left_out(kernel):
    _process(
        kernel,
        "resource",
        101,
        "python3",
        "/home/ubuntu/.cheese/remote-execution/runtime.py",
        "serve",
        "--state",
        "/home/ubuntu/.cheese/executor",
    )
    _process(kernel, "resource", 102, "bash", "-c", "npm run dev")
    _process(kernel, "resource", 103, "sudo", "-n", "something")
    _process(kernel, "resource", 104, "node", "server.js")

    assert sandbox_home.processes("resource") == ["node server.js"]


def test_a_secret_in_a_command_line_is_masked(kernel):
    _process(
        kernel,
        "resource",
        101,
        "curl",
        "--token=abc123",
        "https://example.invalid/",
    )
    _process(kernel, "resource", 102, "env", "PASSWORD=hunter2", "bash")
    _process(
        kernel,
        "resource",
        103,
        "curl",
        "-H",
        "Authorization: Bearer abcdef0123456789",
        "https://example.invalid/",
    )
    _process(
        kernel,
        "resource",
        104,
        "aws",
        "s3",
        "cp",
        "abcdef0123456789abcdef0123456789abcd",
    )

    found = sandbox_home.processes("resource")

    assert found[0] == "curl --token=*** https://example.invalid/"
    assert found[1] == "env PASSWORD=*** bash"
    assert found[2] == "curl -H Authorization: *** https://example.invalid/"
    assert found[3] == "aws s3 cp ***"


def test_a_word_or_a_path_is_not_a_secret(kernel):
    _process(
        kernel,
        "resource",
        101,
        "python3",
        "/home/ubuntu/.cheese/home/project/0123456789abcdef0123456789abcdef/work",
        "tokens",
    )

    found = sandbox_home.processes("resource")

    assert "0123456789abcdef0123456789abcdef" in found[0]


def test_at_most_ten_processes_of_two_hundred_characters(kernel):
    for pid in range(200, 215):
        _process(kernel, "resource", pid, "python3", f"pid={pid}", "x" * 190)
    _process(kernel, "resource", 300, "python3", "y" * 300)

    found = sandbox_home.processes("resource")

    assert len(found) == sandbox_home.LOST_PROCESSES_MAX == 10
    assert all(len(one) == sandbox_home.LOST_PROCESS_CHARS == 200 for one in found)


def test_a_cgroup_the_host_cannot_read_names_nothing(kernel):
    assert sandbox_home.processes("gone") == []


def test_destroy_reads_the_processes_before_it_stops_the_executor(kernel, tmp_path):
    home = tmp_path / "resource"
    home.mkdir()
    _process(kernel, "resource", 101, "npm", "run", "dev")
    _process(kernel, "resource", 102, "python3", "server.py")

    def stop_executor(home, resource):
        (tmp_path / "proc" / "101" / "cmdline").unlink()
        (tmp_path / "proc" / "102" / "cmdline").unlink()

    cleanup = {
        "SANDBOX_HOST": str(tmp_path / "no-helper"),
        "run_command": lambda argv, **_kwargs: None,
        "resource_paths": lambda base, project, resource: (home, tmp_path / "work"),
        "stop_executor": stop_executor,
        "end_holders": lambda paths: None,
        "check_no_writers": lambda paths: None,
        "remove_tree": shutil.rmtree,
        "resource_tmp": lambda resource: None,
        "sandbox_marker": lambda home: tmp_path / "marker",
    }

    answer = sandbox_home.destroy(cleanup, "project", "resource")

    assert answer["destroyed"] is True
    assert answer["processes"] == ["npm run dev", "python3 server.py"]
