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


def test_storage_sample_counts_whole_filesystem_and_available_memory(
    tmp_path, monkeypatch
):
    from scripts import observe_ci_resources as observer
    from scripts.observe_ci_resources import storage_snapshot

    monkeypatch.setattr(observer, "cgroup_memory_usage", lambda: 1234567)
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
        "mem_used_bytes": 1234567,
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
        assert len(pids) == 4
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
        f"log = Path({str(output / 'postgres.log')!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while time.monotonic() < deadline:\n"
        "    if log.exists() and log.stat().st_size:\n"
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
    assert {event["monitor"] for event in started} == {"vmstat", "postgres", "disks"}
    assert len([event for event in events if event["event"] == "monitor_stopped"]) == 3
    for event in started:
        with pytest.raises(ProcessLookupError):
            os.kill(event["pid"], 0)
    # The verdict is the point of recording the samples: the caller must see
    # it in the log and on stdout, not only when a human reads the artifact.
    summaries = [event for event in events if event["event"] == "resources_summary"]
    assert len(summaries) == 1
    assert summaries[0]["verdict"]["conclusion"]
    assert summaries[0]["summary"]["samples"]["postgres"] >= 1
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
    vmstat_rows=(),
    postgres_samples=(),
):
    """An observer output directory holding the samples a run would leave."""
    output = tmp_path / "resources"
    output.mkdir()
    (output / "meminfo").write_text(f"MemTotal: {mem_total_kb} kB\n")
    (output / "lifecycle.jsonl").write_text(
        json.dumps({"event": "start", "cpu_count": cpu_count}) + "\n"
    )
    sample = {}
    if mem_available_bytes is not None:
        sample["mem_available_bytes"] = mem_available_bytes
    if mem_used_bytes is not None:
        sample["mem_used_bytes"] = mem_used_bytes
    if sample:
        (output / "storage.log").write_text(json.dumps(sample) + "\n")
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
    return report


def test_memory_near_the_sandbox_cap_is_reported_with_a_next_step(
    tmp_path, monkeypatch
):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    mem_total = 8 * observer.GIB
    output = _write_observation(
        tmp_path,
        mem_available_bytes=mem_total - int(2.95 * observer.GIB),
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


def test_normal_samples_report_no_threshold_crossed(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
    mem_total = 8 * observer.GIB
    output = _write_observation(
        tmp_path,
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
    mem_total = 8 * observer.GIB
    output = _write_observation(
        tmp_path,
        mem_available_bytes=mem_total - 2 * observer.GIB,
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
        mem_available_bytes=6 * observer.GIB,
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
    mem_total = 8 * observer.GIB
    output = _write_observation(
        tmp_path,
        mem_available_bytes=mem_total - int(2.95 * observer.GIB),
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
        vmstat_rows=[_vmstat_row(runnable=2, steal=9, free_kib=6 * 1024 * 1024)],
    )
    verdict = observer.evaluate(observer.summarize(output))
    assert verdict["crossed"] == ["cpu"]
    assert "cpu: over" in "\n".join(
        observer.report_lines(observer.summarize(output), verdict)
    )


def test_vmstat_sees_a_memory_spike_a_slow_storage_sample_missed(tmp_path, monkeypatch):
    from scripts import observe_ci_resources as observer

    monkeypatch.setenv("CHEESE_CI_SANDBOX_MEMORY_BYTES", str(3 * observer.GIB))
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
