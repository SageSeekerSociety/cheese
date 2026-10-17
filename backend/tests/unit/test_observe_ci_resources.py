"""The observer keeps the command's result and says what its samples crossed."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate_storage_observer(monkeypatch, tmp_path):
    from scripts import observe_ci_resources as observer

    monkeypatch.delenv("CHEESE_CI_POSTGRES_PID", raising=False)
    # The observer honours a cap set for the box; a test that needs one sets it.
    monkeypatch.delenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", raising=False)
    # Never read the host's cgroup: a test that needs one builds it with _cgroup.
    monkeypatch.setattr(observer, "CGROUP_ROOT", tmp_path / "no-cgroup")
    monkeypatch.setattr(observer, "PROC_SELF_CGROUP", tmp_path / "no-proc-self-cgroup")
    # The host's own CPU list would otherwise decide the queue's threshold.
    monkeypatch.setattr(observer, "PROC_CPUINFO", tmp_path / "no-cpuinfo")
    monkeypatch.setattr(observer, "PROC_STAT", tmp_path / "no-proc-stat")


def test_storage_sample_counts_whole_filesystem_and_available_memory(
    tmp_path, monkeypatch
):
    from scripts.observe_ci_resources import storage_snapshot

    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16384 kB\nMemFree: 512 kB\nMemAvailable: 4096 kB\n")
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda _: SimpleNamespace(
            f_blocks=1000, f_bfree=700, f_bavail=600, f_frsize=4096
        ),
    )
    assert storage_snapshot(tmp_path, meminfo) == {
        "filesystem_total_bytes": 4096000,
        "filesystem_used_bytes": 1228800,
        "filesystem_available_bytes": 2457600,
        "mem_available_bytes": 4194304,
    }


def test_disk_sample_names_each_filesystem_it_watches(monkeypatch):
    from scripts.observe_ci_resources import disk_snapshot, observed_disks

    monkeypatch.setattr(
        os,
        "statvfs",
        lambda _: SimpleNamespace(f_blocks=1000, f_bavail=600, f_frsize=4096),
    )
    assert disk_snapshot(["/", "/runner/temp"]) == {
        "/": {"total_bytes": 4096000, "available_bytes": 2457600},
        "/runner/temp": {"total_bytes": 4096000, "available_bytes": 2457600},
    }
    monkeypatch.setenv("RUNNER_TEMP", "/runner/temp")
    assert observed_disks()[:2] == ["/", "/runner/temp"]


def test_sigterm_stops_command_and_monitors(tmp_path):
    output = tmp_path / "resources"
    pidfile = tmp_path / "command.pid"
    binary = tmp_path / "vmstat"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(300)\n")
    binary.chmod(0o755)
    command = tmp_path / "command.py"
    command.write_text(
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(300)\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "scripts.observe_ci_resources",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(command),
        ],
        cwd=BACKEND,
        env=os.environ | {"PATH": str(tmp_path)},
    )
    try:
        deadline = time.monotonic() + 10
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pidfile.exists()
        process.terminate()
        assert process.wait(timeout=10) == 143
        events = [
            json.loads(line)
            for line in (output / "lifecycle.jsonl").read_text().splitlines()
        ]
        pids = [int(pidfile.read_text())] + [
            event["pid"] for event in events if event["event"] == "monitor_started"
        ]
        assert len(pids) == 5
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert events[-1]["event"] == "finished"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize("exit_code", [0, 23])
def test_command_result_and_monitor_cleanup(tmp_path, exit_code):
    output = tmp_path / "resources"
    # A real long-running process proves cleanup, even on a host without vmstat.
    binary = tmp_path / "vmstat"
    binary.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(300)\n")
    binary.chmod(0o755)
    command = tmp_path / "command.py"
    command.write_text(
        "import time\nfrom pathlib import Path\n"
        f"postgres = Path({str(output / 'postgres.log')!r})\n"
        f"memory = Path({str(output / 'memory.log')!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while time.monotonic() < deadline:\n"
        "    sampled = memory.exists() and 'mem_used_bytes' in memory.read_text()\n"
        "    if postgres.exists() and postgres.stat().st_size and sampled:\n"
        "        break\n"
        "    time.sleep(0.01)\n"
        f"raise SystemExit({exit_code})\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.observe_ci_resources",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(command),
        ],
        cwd=BACKEND,
        env=os.environ
        | {
            "PATH": str(tmp_path),
            "DATABASE_URL": "postgresql://127.0.0.1:1/no_database",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == exit_code
    events = [
        json.loads(line)
        for line in (output / "lifecycle.jsonl").read_text().splitlines()
    ]
    started = [event for event in events if event["event"] == "monitor_started"]
    assert {event["monitor"] for event in started} == {
        "vmstat",
        "postgres",
        "disks",
        "memory",
    }
    assert len([event for event in events if event["event"] == "monitor_stopped"]) == 4
    for event in started:
        with pytest.raises(ProcessLookupError):
            os.kill(event["pid"], 0)
    # The verdict is the point of recording the samples: the caller must see
    # it in the log and on stdout, not only when a human reads the artifact.
    summaries = [event for event in events if event["event"] == "resources_summary"]
    assert len(summaries) == 1
    assert summaries[0]["verdict"]["conclusion"]
    assert summaries[0]["summary"]["samples"]["postgres"] >= 1
    assert summaries[0]["summary"]["samples"]["memory"] >= 1
    assert "resources:" in result.stdout
    assert events[-1]["event"] == "finished"
    assert "ConnectionRefusedError" in (output / "postgres.log").read_text()


def _vmstat_row(*, runnable, steal, free_kib, buff_kib=0, cache_kib=0):
    """One vmstat data row, in the column order vmstat -t prints."""
    return (
        runnable,
        0,
        0,
        free_kib,
        buff_kib,
        cache_kib,
        0,
        0,
        0,
        0,
        100,
        200,
        1,
        1,
        98,
        0,
        steal,
    )


def _write_observation(
    tmp_path,
    *,
    mem_total_kb=8 * 1024 * 1024,
    mem_available_bytes=None,
    mem_used_bytes=None,
    cpu_count=2,
    processors=None,
    postgres_watched=None,
    vmstat_rows=(),
    postgres_samples=(),
):
    """An observer output directory holding the samples a run would leave."""
    output = tmp_path / "resources"
    output.mkdir()
    (output / "meminfo").write_text(f"MemTotal: {mem_total_kb} kB\n")
    start = {"event": "start", "cpu_count": cpu_count}
    if processors is not None:
        start["processors"] = processors
    if postgres_watched is not None:
        start["postgres"] = postgres_watched
    (output / "lifecycle.jsonl").write_text(json.dumps(start) + "\n")
    if mem_available_bytes is not None:
        (output / "storage.log").write_text(
            json.dumps({"mem_available_bytes": mem_available_bytes}) + "\n"
        )
    if mem_used_bytes is not None:
        usage = mem_used_bytes if isinstance(mem_used_bytes, list) else [mem_used_bytes]
        (output / "memory.log").write_text(
            "".join(json.dumps({"mem_used_bytes": value}) + "\n" for value in usage)
        )
    if vmstat_rows:
        header = (
            "procs -----------memory---------- ---swap-- -----io---- "
            "-system-- ------cpu----- -----timestamp-----\n"
            " r  b   swpd   free   buff  cache   si   so    bi    bo   in   "
            "cs us sy id wa st                 UTC\n"
        )
        rows = "".join(
            " ".join(str(value) for value in row) + " 2026-10-16 12:00:00\n"
            for row in vmstat_rows
        )
        (output / "vmstat.log").write_text(header + rows)
    if postgres_samples:
        (output / "postgres.log").write_text(
            "".join(json.dumps(sample) + "\n" for sample in postgres_samples)
        )
    return output


def _assert_memory_over_is_reported(output):
    """The one assertion both the real case and the deliberate violation run."""
    from scripts import observe_ci_resources as observer

    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    report = "\n".join(observer.report_lines(summary, verdict))
    assert verdict["checks"][0]["state"] == "over"
    assert "memory" in verdict["crossed"]
    assert "memory: over" in report
    assert "narrow the run" in report
    # The unjudged database check stays visible beside the crossing.
    assert "not judged: database" in verdict["conclusion"]
    return report


def test_memory_near_the_sandbox_cap_is_reported_with_a_next_step(
    tmp_path, monkeypatch
):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        # The box's own cgroup says it is using 2.95 GiB of the 3 GiB cap.
        mem_used_bytes=int(2.95 * observer.GIB),
        vmstat_rows=[
            _vmstat_row(
                runnable=1,
                steal=0,
                free_kib=7 * 1024 * 1024,
            )
        ],
    )
    report = _assert_memory_over_is_reported(output)
    assert "resources next: narrow the run" in report


def test_the_peak_is_the_highest_sample_not_the_last_one(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        # The run peaked and came back down before it ended.
        mem_used_bytes=[observer.GIB, int(2.95 * observer.GIB), observer.GIB],
        cpu_count=4,
        vmstat_rows=[
            _vmstat_row(runnable=1, steal=0, free_kib=6 * 1024 * 1024),
            _vmstat_row(runnable=9, steal=7, free_kib=6 * 1024 * 1024),
            _vmstat_row(runnable=2, steal=3, free_kib=6 * 1024 * 1024),
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert summary["memory_peak_bytes"] == int(2.95 * observer.GIB)
    assert summary["runnable_peak"] == 9
    assert summary["steal_peak_percent"] == 7.0
    assert verdict["crossed"] == ["memory", "cpu"]
    assert "memory: over" in "\n".join(observer.report_lines(summary, verdict))


def test_normal_samples_report_no_threshold_crossed(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    mem_total = 8 * observer.GIB
    output = _write_observation(
        tmp_path,
        mem_used_bytes=2 * observer.GIB,
        mem_available_bytes=mem_total - 2 * observer.GIB,
        cpu_count=2,
        vmstat_rows=[
            _vmstat_row(
                runnable=2,
                steal=0,
                free_kib=6 * 1024 * 1024,
            )
        ],
        postgres_samples=[
            {
                "settings": {
                    "fsync": "on",
                    "max_connections": "100",
                    "superuser_reserved_connections": "3",
                }
            },
            {
                "waits": [
                    {"backend_type": "client backend", "count": 12},
                    {"backend_type": "autovacuum worker", "count": 1},
                ]
            },
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    report = "\n".join(observer.report_lines(summary, verdict))
    assert verdict["crossed"] == []
    assert verdict["unknown"] == []
    assert verdict["conclusion"] == "no threshold crossed"
    assert "no action needed" in report


def test_cpu_queue_and_connection_pressure_are_named(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=2 * observer.GIB,
        cpu_count=2,
        vmstat_rows=[
            _vmstat_row(
                runnable=5,
                steal=9,
                free_kib=6 * 1024 * 1024,
            )
        ],
        postgres_samples=[
            {
                "settings": {
                    "max_connections": "100",
                    "superuser_reserved_connections": "3",
                }
            },
            {"waits": [{"backend_type": "client backend", "count": 98}]},
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    report = "\n".join(observer.report_lines(summary, verdict))
    assert verdict["crossed"] == ["cpu", "database"]
    assert "cpu: over" in report
    assert "database: over" in report
    assert "another shard" in report
    assert "connection budget" in report


def test_a_missing_database_sample_is_not_judged_green(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        vmstat_rows=[
            _vmstat_row(
                runnable=1,
                steal=0,
                free_kib=6 * 1024 * 1024,
            )
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert "database" in verdict["unknown"]
    assert "database" not in verdict["crossed"]
    assert "not judged: database" in verdict["conclusion"]


def test_the_over_limit_assertion_goes_red_when_evaluation_ignores_the_cap(
    tmp_path, monkeypatch
):
    """Deliberate violation: an evaluator that always says ok must fail the check."""
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=int(2.95 * observer.GIB),
    )

    def always_ok(_summary):
        return {
            "checks": [
                {"metric": "memory", "state": "ok", "observed": 0, "threshold": 1},
                {"metric": "cpu", "state": "ok", "observed": 0, "threshold": 1},
                {"metric": "database", "state": "ok", "observed": 0, "threshold": 1},
            ],
            "crossed": [],
            "unknown": [],
            "conclusion": "no threshold crossed",
            "next_steps": ["no action needed"],
        }

    monkeypatch.setattr(observer, "evaluate", always_ok)
    with pytest.raises(AssertionError):
        _assert_memory_over_is_reported(output)


def _cgroup(tmp_path, monkeypatch, *, maximum=None, current=None):
    """Point the observer at a cgroup directory the test controls."""
    from scripts import observe_ci_resources as observer

    root = tmp_path / "cgroup"
    root.mkdir()
    if maximum is not None:
        (root / "memory.max").write_text(maximum)
    if current is not None:
        (root / "memory.current").write_text(str(current))
    monkeypatch.setattr(observer, "CGROUP_ROOT", root)
    return root


def test_the_cap_comes_from_the_container_not_a_fixed_number(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    _cgroup(tmp_path, monkeypatch, maximum=str(4 * observer.GIB))
    output = _write_observation(
        tmp_path,
        # A cgroup cap is in force, so the box's own use is what is judged.
        mem_used_bytes=int(3.8 * observer.GIB),
        cpu_count=2,
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=7 * 1024 * 1024)],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    report = "\n".join(observer.report_lines(summary, verdict))
    assert summary["memory_limit_bytes"] == 4 * observer.GIB
    assert summary["memory_peak_bytes"] == int(3.8 * observer.GIB)
    assert verdict["checks"][0]["state"] == "over"
    assert "(cap 4.00 GiB)" in report


def test_a_comfortable_host_is_not_reported_as_over(tmp_path, monkeypatch):
    """A 16 GiB runner using 3 GiB must not be judged against a 3 GiB sandbox."""
    from scripts import observe_ci_resources as observer

    mem_total = 16 * observer.GIB
    _cgroup(tmp_path, monkeypatch)  # no limit, no sample: meminfo decides
    output = _write_observation(
        tmp_path,
        mem_total_kb=16 * 1024 * 1024,
        mem_available_bytes=mem_total - 3 * observer.GIB,
        cpu_count=2,
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=20 * 1024 * 1024)],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    report = "\n".join(observer.report_lines(summary, verdict))
    assert summary["memory_peak_bytes"] == 3 * observer.GIB
    assert verdict["checks"][0]["state"] == "ok"
    assert "threshold 14.40 GiB" in report


def test_steal_time_alone_names_cpu_contention(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        cpu_count=2,
        # The first report's steal is an average since boot; the second one
        # covers a second of this run.
        vmstat_rows=[
            _vmstat_row(runnable=2, steal=0, free_kib=6 * 1024 * 1024),
            _vmstat_row(runnable=2, steal=9, free_kib=6 * 1024 * 1024),
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert summary["steal_peak_percent"] == 9.0
    assert verdict["crossed"] == ["cpu"]
    assert "cpu: over" in "\n".join(observer.report_lines(summary, verdict))


def test_a_single_vmstat_report_cannot_measure_steal(tmp_path, monkeypatch):
    """Its steal percentage averages since boot, so it says nothing about the run."""
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        cpu_count=2,
        vmstat_rows=[_vmstat_row(runnable=2, steal=9, free_kib=6 * 1024 * 1024)],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert summary["runnable_peak"] == 2
    assert summary["steal_peak_percent"] is None
    assert verdict["crossed"] == []
    assert verdict["checks"][1]["state"] == "ok"


def test_the_queue_is_judged_against_the_machines_cpus_not_the_affinity(
    tmp_path, monkeypatch
):
    """vmstat counts the whole machine; a 4-core view would call 20 runnable over."""
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        cpu_count=4,
        processors=64,
        vmstat_rows=[
            _vmstat_row(runnable=20, steal=0, free_kib=6 * 1024 * 1024),
            _vmstat_row(runnable=20, steal=0, free_kib=6 * 1024 * 1024),
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert summary["cpu_count"] == 64
    assert verdict["checks"][1]["state"] == "ok"


def test_the_processor_count_comes_from_the_kernel_view(monkeypatch, tmp_path):
    from scripts import observe_ci_resources as observer

    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(
        "".join(f"processor\t: {index}\nmodel name\t: x\n" for index in range(7))
    )
    monkeypatch.setattr(observer, "PROC_CPUINFO", cpuinfo)
    # The answer must come from the file, not from a host that happens to
    # have the same number of processors as the answer.
    monkeypatch.setattr(observer.os, "cpu_count", lambda: 99)
    assert observer.processor_count() == 7


def test_the_processor_count_falls_back_to_proc_stat(monkeypatch, tmp_path):
    from scripts import observe_ci_resources as observer

    stat = tmp_path / "stat"
    stat.write_text(
        "cpu  1 2 3 4 5 6 7 8 9 10\n"
        "cpu0 1 2 3 4 5 6 7 8 9 10\n"
        "cpu1 1 2 3 4 5 6 7 8 9 10\n"
        "intr 0\n"
    )
    monkeypatch.setattr(observer, "PROC_STAT", stat)
    monkeypatch.setattr(observer.os, "cpu_count", lambda: 99)
    assert observer.processor_count() == 2


def test_the_processor_count_falls_back_to_the_affinity(monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setattr(observer.os, "cpu_count", lambda: 99)
    assert observer.processor_count() == 99


def test_the_cap_is_read_from_the_processes_own_cgroup(tmp_path, monkeypatch):
    """The sandbox's root shows the host; only its own subtree states the cap."""
    from scripts import observe_ci_resources as observer

    root = tmp_path / "cgroup"
    own = root / "cheese-sandboxes" / "box"
    own.mkdir(parents=True)
    (root / "memory.max").write_text("max\n")
    (own / "memory.max").write_text(str(3 * observer.GIB))
    (own / "memory.current").write_text(str(512 * 1024 * 1024))
    proc_self = tmp_path / "proc-self-cgroup"
    proc_self.write_text("0::/cheese-sandboxes/box\n")
    monkeypatch.setattr(observer, "CGROUP_ROOT", root)
    monkeypatch.setattr(observer, "PROC_SELF_CGROUP", proc_self)

    assert observer.memory_limit_bytes(8 * observer.GIB) == 3 * observer.GIB
    assert observer.cgroup_memory_usage() == 512 * 1024 * 1024


def test_an_unlimited_cgroup_is_not_a_cap(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    _cgroup(tmp_path, monkeypatch, maximum="max\n")
    assert observer.memory_limit_bytes(8 * observer.GIB) == 8 * observer.GIB


def test_the_v1_unlimited_sentinel_is_not_a_cap(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    root = _cgroup(tmp_path, monkeypatch)
    (root / "memory").mkdir()
    (root / "memory" / "memory.limit_in_bytes").write_text(str(2**63 - 4096))
    assert observer.memory_limit_bytes(8 * observer.GIB) == 8 * observer.GIB


def test_a_v1_memory_controller_is_read_from_its_own_mount(tmp_path, monkeypatch):
    """v1 mounts the memory controller at /sys/fs/cgroup/memory, not the root."""
    from scripts import observe_ci_resources as observer

    root = tmp_path / "cgroup"
    own = root / "memory" / "docker" / "abc"
    own.mkdir(parents=True)
    (root / "memory" / "memory.limit_in_bytes").write_text(str(2**63 - 4096))
    (own / "memory.limit_in_bytes").write_text(str(3 * observer.GIB))
    (own / "memory.usage_in_bytes").write_text(str(2 * observer.GIB))
    proc_self = tmp_path / "proc-self-cgroup"
    proc_self.write_text("5:memory:/docker/abc\n")
    monkeypatch.setattr(observer, "CGROUP_ROOT", root)
    monkeypatch.setattr(observer, "PROC_SELF_CGROUP", proc_self)

    assert observer.memory_limit_bytes(8 * observer.GIB) == 3 * observer.GIB
    assert observer.cgroup_memory_usage() == 2 * observer.GIB


def test_a_cap_on_an_ancestor_applies_to_the_box(tmp_path, monkeypatch):
    """cgroup v2 enforces the minimum on the path from the cgroup to the root."""
    from scripts import observe_ci_resources as observer

    root = tmp_path / "cgroup"
    own = root / "cheese-sandboxes" / "box"
    own.mkdir(parents=True)
    (own / "memory.max").write_text(str(6 * observer.GIB))
    (root / "cheese-sandboxes" / "memory.max").write_text(str(2 * observer.GIB))
    (root / "memory.max").write_text("max\n")
    proc_self = tmp_path / "proc-self-cgroup"
    proc_self.write_text("0::/cheese-sandboxes/box\n")
    monkeypatch.setattr(observer, "CGROUP_ROOT", root)
    monkeypatch.setattr(observer, "PROC_SELF_CGROUP", proc_self)

    assert observer.memory_limit_bytes(8 * observer.GIB) == 2 * observer.GIB


def test_a_declared_cap_wins_over_a_looser_cgroup(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    _cgroup(tmp_path, monkeypatch, maximum=str(4 * observer.GIB))
    assert observer.memory_limit_bytes(8 * observer.GIB) == 3 * observer.GIB


def test_a_capped_box_without_a_scoped_sample_is_left_unjudged(tmp_path, monkeypatch):
    """Host numbers against a container cap would judge the neighbours."""
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_available_bytes=observer.GIB,  # the host is busy: 7 of 8 GiB used
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=1024 * 1024)],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert summary["memory_peak_bytes"] is None
    assert verdict["checks"][0]["state"] == "unknown"
    assert "not judged: memory" in verdict["conclusion"]


def test_a_box_sample_beats_host_wide_numbers(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        mem_available_bytes=observer.GIB,  # host-wide says 7 GiB in use
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=1024 * 1024)],
    )
    summary = observer.summarize(output)
    assert summary["memory_peak_bytes"] == observer.GIB
    assert observer.evaluate(summary)["checks"][0]["state"] == "ok"


def test_a_pure_run_is_not_advised_to_collect_a_database_sample(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        cpu_count=2,
        postgres_watched=False,
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=6 * 1024 * 1024)],
    )
    verdict = observer.evaluate(observer.summarize(output))
    assert verdict["unknown"] == ["database"]
    assert verdict["next_steps"] == ["no action needed"]


def test_a_missing_check_that_can_be_sampled_gets_the_advice():
    from scripts import observe_ci_resources as observer

    assert observer._next_steps([], ["cpu"]) == [
        "collect a sample before judging the missing checks"
    ]
    # A database the run never started cannot produce a sample.
    assert observer._next_steps(["memory"], ["database"]) == [
        "narrow the run: fewer selected files or workers",
        "hand the run to CI when the local box is shared",
        "collect a sample before judging the missing checks",
    ]
    assert observer._next_steps(["memory"], ["database"], postgres_watched=False)[
        -1
    ] == ("hand the run to CI when the local box is shared")


def test_the_start_event_records_what_the_run_judges_against(tmp_path, monkeypatch):
    """The observers' own wiring: the summary can only read what start wrote."""
    from scripts import observe_ci_resources as observer

    monkeypatch.setattr(observer, "processor_count", lambda: 999)
    output = tmp_path / "resources"
    observer.observe(output, [sys.executable, "-c", "pass"], postgres=False)
    events = [
        json.loads(line)
        for line in (output / "lifecycle.jsonl").read_text().splitlines()
    ]
    start = next(event for event in events if event["event"] == "start")
    assert start["processors"] == 999
    assert start["postgres"] is False


def test_memory_sampling_records_the_boxs_own_usage(monkeypatch):
    from scripts import observe_ci_resources as observer

    class StopSampling(Exception):
        pass

    class Sink:
        def __init__(self):
            self.text = ""

        def write(self, text):
            self.text += text

        def flush(self):
            pass

    sink = Sink()
    monkeypatch.setattr(observer, "cgroup_memory_usage", lambda: 12345)
    monkeypatch.setattr(observer.time, "sleep", _raise(StopSampling))
    monkeypatch.setattr(observer.sys, "stdout", sink)
    with pytest.raises(StopSampling):
        observer.sample_memory()
    records = [json.loads(line) for line in sink.text.splitlines()]
    assert records[0]["event"] == "sampler_started"
    assert records[1]["mem_used_bytes"] == 12345


def _raise(error):
    def wait(_seconds):
        raise error

    return wait


def test_the_memory_sampler_is_a_command_of_its_own(monkeypatch):
    """The observer starts it as a child process, so main must dispatch it."""
    from scripts import observe_ci_resources as observer

    class Started(Exception):
        pass

    def started():
        raise Started

    monkeypatch.setattr(observer, "sample_memory", started)
    monkeypatch.setattr(sys, "argv", ["observe_ci_resources", "--sample-memory"])
    with pytest.raises(Started):
        observer.main()


def test_vmstat_sees_a_memory_spike_a_slow_storage_sample_missed(tmp_path):
    from scripts import observe_ci_resources as observer

    # The box is the whole machine here, so host-wide samples are its own.
    mem_total = 8 * observer.GIB
    output = _write_observation(
        tmp_path,
        mem_available_bytes=mem_total - observer.GIB,
        cpu_count=2,
        # vmstat saw 4 GiB in use where the storage sample saw 1 GiB.
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=4 * 1024 * 1024)],
    )
    summary = observer.summarize(output)
    assert summary["memory_peak_bytes"] == 4 * observer.GIB


def test_background_workers_are_not_counted_as_client_connections(
    tmp_path, monkeypatch
):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    output = _write_observation(
        tmp_path,
        mem_used_bytes=observer.GIB,
        cpu_count=2,
        vmstat_rows=[_vmstat_row(runnable=1, steal=0, free_kib=6 * 1024 * 1024)],
        postgres_samples=[
            {"settings": {"max_connections": "100"}},
            {
                "waits": [
                    {"backend_type": "client backend", "count": 96},
                    {"backend_type": "autovacuum worker", "count": 10},
                ]
            },
        ],
    )
    summary = observer.summarize(output)
    verdict = observer.evaluate(summary)
    assert summary["client_backends_peak"] == 96
    assert summary["db_connection_limit"] == 97
    assert verdict["checks"][2]["state"] == "ok"


def test_settings_are_read_back_from_the_server(monkeypatch):
    """The sampler's own write is what make the database check judgeable."""
    import asyncio as asyncio_module

    from scripts import observe_ci_resources as observer

    class Connection:
        async def fetchval(self, query):
            return {
                "SHOW fsync": "on",
                "SHOW max_connections": "100",
                "SHOW superuser_reserved_connections": "3",
            }[query]

        async def fetch(self, _query):
            return []

        async def close(self, timeout=None):
            pass

    class FakeAsyncpg:
        @staticmethod
        async def connect(*_args, **_kwargs):
            return Connection()

    class StopSampling(Exception):
        pass

    async def stop_after_the_first_sample(_seconds):
        raise StopSampling

    class Sink:
        def __init__(self):
            self.text = ""

        def write(self, text):
            self.text += text

        def flush(self):
            pass

    sink = Sink()
    monkeypatch.setitem(sys.modules, "asyncpg", FakeAsyncpg)
    monkeypatch.setattr(observer.asyncio, "sleep", stop_after_the_first_sample)
    monkeypatch.setattr(observer.sys, "stdout", sink)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u@localhost/db")
    with pytest.raises(StopSampling):
        asyncio_module.run(observer.sample_postgres())
    records = [json.loads(line) for line in sink.text.splitlines()]
    settings = next(record["settings"] for record in records if "settings" in record)
    assert settings == {
        "fsync": "on",
        "max_connections": 100,
        "superuser_reserved_connections": 3,
    }


def test_missing_vmstat_does_not_replace_a_command_failure(tmp_path):
    output = tmp_path / "resources"
    command = tmp_path / "command.py"
    command.write_text("raise SystemExit(17)\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.observe_ci_resources",
            "--output",
            str(output),
            "--",
            sys.executable,
            str(command),
        ],
        cwd=BACKEND,
        env=os.environ | {"PATH": str(tmp_path)},
        timeout=15,
    )
    assert result.returncode == 17
    events = [
        json.loads(line)
        for line in (output / "lifecycle.jsonl").read_text().splitlines()
    ]
    assert any(
        event["event"] == "unavailable" and event["monitor"] == "vmstat"
        for event in events
    )
