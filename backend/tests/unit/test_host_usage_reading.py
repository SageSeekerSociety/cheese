"""What a host says it uses, read from the kernel's own files.

- its memory is reported in bytes, total and available, from /proc/meminfo,
  whose values are in KiB;
- the CPU time its processes have taken is the cgroup root's ``usage_usec``;
- each sandbox's memory, now and at most, is reported under the name of the
  cgroup the sandbox helper made for it, which is its home's;
- a value the machine does not have is left out, not reported as zero.

The files are written here in the kernel's own format; the paths the host
program reads are pointed at them.
"""

from pathlib import Path

import pytest

from app.domain.machine import sandbox_home


@pytest.fixture
def kernel(tmp_path, monkeypatch) -> Path:
    cgroups = tmp_path / "cgroup"
    (cgroups / "cheese-sandboxes").mkdir(parents=True)
    monkeypatch.setattr(sandbox_home, "MEMINFO", tmp_path / "meminfo")
    monkeypatch.setattr(sandbox_home, "CGROUP_ROOT", cgroups)
    monkeypatch.setattr(sandbox_home, "SANDBOX_CGROUPS", cgroups / "cheese-sandboxes")
    return tmp_path


def test_memory_and_cpu_time_are_read_in_bytes_and_microseconds(kernel):
    (kernel / "meminfo").write_text(
        "MemTotal:       16777216 kB\n"
        "MemFree:         1048576 kB\n"
        "MemAvailable:    4194304 kB\n"
    )
    (kernel / "cgroup" / "cpu.stat").write_text(
        "usage_usec 123456789\nuser_usec 100\nsystem_usec 23\n"
    )

    found = sandbox_home.machine()

    assert found["memory"] == 16 * 2**30
    assert found["memory_available"] == 4 * 2**30
    assert found["cpu_usage_usec"] == 123456789


def test_what_a_machine_does_not_have_is_left_out(kernel):
    found = sandbox_home.machine()

    assert "memory" not in found
    assert "cpu_usage_usec" not in found


def test_each_sandbox_memory_is_reported_under_its_home_name(kernel):
    home = "2f0c1a9e-5b7d-4c1e-9f3a-6d8e2b4c7a10"
    group = kernel / "cgroup" / "cheese-sandboxes" / home
    group.mkdir()
    (group / "memory.current").write_text("1073741824\n")
    (group / "memory.peak").write_text("2147483648\n")
    # A kernel too old for memory.peak still reports what is held now.
    older = kernel / "cgroup" / "cheese-sandboxes" / "older"
    older.mkdir()
    (older / "memory.current").write_text("5\n")

    found = {s["resource"]: s for s in sandbox_home.sandboxes()}

    assert found[home] == {
        "resource": home,
        "memory": 2**30,
        "memory_peak": 2**31,
    }
    assert found["older"]["memory_peak"] is None
