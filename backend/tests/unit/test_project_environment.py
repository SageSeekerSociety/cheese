"""Configuration and real installation subprocess behavior."""

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.domain.agent import environment_runner
from app.domain.project.environment import EnvironmentConfig


def test_reset_succeeds_when_agent_exits_after_status_read(tmp_path, monkeypatch):
    import runpy
    import signal

    root = tmp_path / ".cheese-environment"
    root.mkdir()
    child = subprocess.Popen(["sleep", "60"])
    original_kill = os.kill
    environment_runner.write_json(
        root / "status.json",
        {
            "state": "ready",
            "pid": child.pid,
            "process_identity": environment_runner.process_identity(child.pid),
        },
    )
    signals = []

    def exited_before_signal(pid, sig):
        if pid == child.pid and sig == signal.SIGTERM:
            signals.append(pid)
            original_kill(pid, sig)
            child.wait(timeout=5)
            raise ProcessLookupError(pid)
        return original_kill(pid, sig)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [environment_runner.__file__, "reset"])
    monkeypatch.setattr(os, "kill", exited_before_signal)
    try:
        runpy.run_path(environment_runner.__file__, run_name="__main__")
        assert signals == [child.pid]
        assert json.loads((root / "status.json").read_text()) == {"state": "pending"}
    finally:
        if child.poll() is None:
            original_kill(child.pid, signal.SIGTERM)
            child.wait(timeout=5)


def test_linux_status_identifies_pid_reuse_without_spawning_ps(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(environment_runner.sys, "platform", "linux")
    original = Path.read_text
    start_tick = [12345]
    boot_id = ["first-boot"]

    def read(path, *args, **kwargs):
        if str(path) == "/proc/123/stat":
            # A process name can contain spaces and parentheses.
            fields = ["S", *(["0"] * 18), str(start_tick[0])]
            return "123 (worker (phase 2)) " + " ".join(fields)
        if str(path) == "/proc/sys/kernel/random/boot_id":
            return boot_id[0] + "\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(
        environment_runner.subprocess,
        "run",
        Mock(side_effect=AssertionError("Linux status lookup spawned a subprocess")),
    )
    identity = environment_runner.process_identity(123)
    environment_runner.write_json(
        tmp_path / "status.json",
        {"state": "ready", "pid": 123, "process_identity": identity},
    )
    assert environment_runner.read_status(tmp_path)["state"] == "ready"
    start_tick[0] += 1
    assert environment_runner.read_status(tmp_path)["state"] == "stopped"
    start_tick[0] -= 1
    boot_id[0] = "second-boot"
    assert environment_runner.read_status(tmp_path)["state"] == "stopped"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_status_reads_legacy_process_identity(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(environment_runner.sys, "platform", platform)
    legacy = "Wed Sep  9 12:00:00 2026"
    probe = Mock(return_value=SimpleNamespace(stdout=legacy + "\n"))
    monkeypatch.setattr(environment_runner.subprocess, "run", probe)
    environment_runner.write_json(
        tmp_path / "status.json",
        {"state": "ready", "pid": 123, "process_identity": legacy},
    )
    assert environment_runner.read_status(tmp_path)["state"] == "ready"
    probe.return_value.stdout = ""
    assert environment_runner.read_status(tmp_path)["state"] == "stopped"


def test_linux_missing_process_is_not_alive(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(environment_runner.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=FileNotFoundError))
    assert environment_runner.process_identity(123) == ""


@pytest.mark.parametrize("state", ["ready", "failed"])
def test_wait_status_returns_when_preparation_finishes(tmp_path, monkeypatch, state):
    import threading

    environment_runner.write_json(tmp_path / "status.json", {"state": "pending"})
    checked = threading.Event()
    original = environment_runner.read_status
    result = {"state": state}
    if state == "ready":
        result.update(
            pid=os.getpid(),
            process_identity=environment_runner.process_identity(os.getpid()),
        )
    else:
        result["error"] = "installer failed"

    def observe(directory):
        status = original(directory)
        checked.set()
        return status

    monkeypatch.setattr(environment_runner, "read_status", observe)

    def finish():
        assert checked.wait(2)
        environment_runner.write_json(tmp_path / "status.json", result)

    writer = threading.Thread(target=finish)
    writer.start()
    try:
        assert environment_runner.wait_status(tmp_path) == result
    finally:
        writer.join(timeout=2)


@pytest.mark.parametrize("finish_after", [0.1, 0.65, 1.2])
@pytest.mark.parametrize("state", ["ready", "failed"])
def test_wait_status_observes_short_preparation_without_another_rpc(
    tmp_path, monkeypatch, finish_after, state
):
    clock = [0.0]
    monkeypatch.setattr(environment_runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        environment_runner.time,
        "sleep",
        lambda delay: clock.__setitem__(0, clock[0] + delay),
    )
    monkeypatch.setattr(
        environment_runner,
        "read_status",
        lambda _: {"state": state if clock[0] >= finish_after else "preparing"},
    )
    assert environment_runner.wait_status(tmp_path) == {"state": state}
    assert finish_after <= clock[0] <= finish_after + 0.051


def test_wait_status_bounds_pending_wait(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(environment_runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        environment_runner.time,
        "sleep",
        lambda delay: clock.__setitem__(0, clock[0] + delay),
    )
    assert environment_runner.wait_status(tmp_path) == {"state": "pending"}
    assert clock[0] == pytest.approx(2)


@pytest.fixture(autouse=True)
def active_room_admission(monkeypatch):
    from app.domain.agent.device_provider import DeviceChannel

    async def active_room(_self, topic_id):
        return SimpleNamespace(id=topic_id, resource_id=None)

    monkeypatch.setattr(
        "app.domain.topic.services.TopicService.lock_for_execution", active_room
    )
    monkeypatch.setattr(
        DeviceChannel, "_session_factory", Mock(return_value=AsyncMock()), raising=False
    )


def launch(tmp_path, config, command=None, *, work_dir=True):
    # A room's work directory is a plain directory — the conversation lives
    # there and nothing else, and a task's checkout is made elsewhere by
    # `cheese worktree`. So no repository is created here: that is what a real
    # room looks like.
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir(exist_ok=True)
    if work_dir:
        work.mkdir(exist_ok=True)
    return subprocess.Popen(
        [sys.executable, environment_runner.__file__, *(command or ["true"])],
        env={
            **os.environ,
            "HOME": str(home),
            "CHEESE_WORK": str(work),
            "CHEESE_ENVIRONMENT": json.dumps(config.snapshot()),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def status(tmp_path):
    return json.loads((tmp_path / "home/.cheese-environment/status.json").read_text())


def launch_task(tmp_path):
    return subprocess.run(
        [sys.executable, environment_runner.__file__, "task", str(tmp_path / "work")],
        env={**os.environ, "HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        timeout=5,
    )


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists()


def test_finished_agent_is_stopped_not_failed_preparation(tmp_path):
    process = launch(tmp_path, EnvironmentConfig())
    assert process.wait(timeout=5) == 0
    result = environment_runner.read_status(tmp_path / "home/.cheese-environment")
    assert result["state"] == "stopped"
    assert "error" not in result


@pytest.mark.parametrize("failure", ["setup", "adoption", None])
def test_prepared_agent_adoption_follows_project_setup(tmp_path, monkeypatch, failure):
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CHEESE_WORK", str(work))
    monkeypatch.setenv("CHEESE_ENVIRONMENT", "fixture")
    config = EnvironmentConfig(
        setup_script="exit 9" if failure == "setup" else "echo setup > setup-done",
        startup_script='printf "%s" "$PROJECT_VALUE" > startup-value',
        variables={"PROJECT_VALUE": "project-specific"},
    )
    adopted = []
    root = home / ".cheese-environment"
    with subprocess.Popen(["sleep", "30"]) as native:
        try:

            def adopt(environment, directory):
                assert directory == work.resolve()
                assert (work / "setup-done").read_text() == "setup\n"
                assert not (work / "startup-value").exists()
                assert environment["PROJECT_VALUE"] == "project-specific"
                assert "CHEESE_ENVIRONMENT" not in environment
                assert environment_runner.read_status(root)["state"] == "preparing"
                adopted.append(native.pid)
                if failure == "adoption":
                    raise RuntimeError("room binding failed")
                return native.pid

            code = environment_runner.run(config.snapshot(), root, [], adopt=adopt)
            if failure == "setup":
                assert code == 9
                assert not adopted
                assert environment_runner.read_status(root)["state"] == "failed"
            elif failure == "adoption":
                assert code == 1
                assert adopted == [native.pid]
                result = environment_runner.read_status(root)
                assert result["state"] == "failed"
                assert result["error"] == "room binding failed"
            else:
                assert code == 0
                assert adopted == [native.pid]
                result = environment_runner.read_status(root)
                assert result["state"] == "ready"
                assert result["pid"] == native.pid
        finally:
            native.terminate()
            native.wait(timeout=5)
    if failure is None:
        assert environment_runner.read_status(root)["state"] == "stopped"


def test_dead_installer_is_failed_preparation(tmp_path):
    import signal

    process = launch(
        tmp_path,
        EnvironmentConfig(setup_script="echo $$ > installer-pid\nexec sleep 30"),
    )
    path = tmp_path / "home/.cheese-environment/status.json"
    child = tmp_path / "work/installer-pid"
    try:
        wait_for(child)
        process.kill()
        process.wait(timeout=5)
        result = environment_runner.read_status(path.parent)
        assert result["state"] == "failed"
        assert result["error"] == "preparation process exited"
    finally:
        if child.exists():
            os.killpg(int(child.read_text()), signal.SIGTERM)
        if process.poll() is None:
            process.kill()
            process.wait()


def test_revision_tracks_both_scripts_and_variables():
    a = EnvironmentConfig(variables={"A": "1", "B": "2"})
    b = EnvironmentConfig(variables={"B": "2", "A": "1"})
    assert a.snapshot() == b.snapshot()
    assert a.snapshot() != a.model_copy(update={"startup_script": "true"}).snapshot()
    assert a.snapshot() != a.model_copy(update={"setup_script": "true"}).snapshot()


@pytest.mark.parametrize(
    "name",
    ["HOME", "PATH", "CHEESE_TOKEN", "CLAUDE_MODEL", "http_proxy", "BASH_ENV", "A=B"],
)
def test_variables_cannot_replace_platform_wiring(name):
    with pytest.raises(ValidationError):
        EnvironmentConfig(variables={name: "bad"})


def test_reuse_initialization_but_update_branch_dependencies(tmp_path):
    config = EnvironmentConfig(
        setup_script="echo setup >> setup-count",
        startup_script=(
            "cat dependency-version > installed-version\necho startup >> startup-count"
        ),
    )
    (tmp_path / "work").mkdir()
    (tmp_path / "work/dependency-version").write_text("1")
    first = launch(tmp_path, config)
    assert first.wait(timeout=5) == 0
    assert launch_task(tmp_path).returncode == 0
    (tmp_path / "work/dependency-version").write_text("2")
    second = launch(tmp_path, config)
    assert second.wait(timeout=5) == 0
    assert launch_task(tmp_path).returncode == 0
    assert (tmp_path / "work/setup-count").read_text() == "setup\n"
    assert (tmp_path / "work/startup-count").read_text() == "startup\nstartup\n"
    assert (tmp_path / "work/installed-version").read_text() == "2"
    assert status(tmp_path)["state"] == "ready"


def test_failure_stops_agent_and_retry_can_succeed(tmp_path):
    config = EnvironmentConfig(setup_script="test -f allowed\necho installed")
    first = launch(tmp_path, config, ["touch", "agent-started"])
    assert first.wait(timeout=5) != 0
    assert not (tmp_path / "work/agent-started").exists()
    assert status(tmp_path)["stage"] == "setup"
    assert status(tmp_path)["exit_code"] == 1
    assert not (tmp_path / "home/.cheese-environment/initialized.json").exists()
    (tmp_path / "work/allowed").touch()
    assert launch(tmp_path, config, ["touch", "agent-started"]).wait(timeout=5) == 0
    assert (tmp_path / "work/agent-started").exists()
    assert len(list((tmp_path / "home/.cheese-environment").glob("*.log"))) == 2


def test_variables_reach_both_scripts_and_agent_without_shell_expansion(tmp_path):
    value = "$(touch injected)\n'quoted'"
    config = EnvironmentConfig(
        setup_script='printf "%s" "$CUSTOM" > setup-value\nexport ONLY_SETUP=yes',
        startup_script=(
            'printf "%s" "$CUSTOM" > startup-value\ntest -z "${ONLY_SETUP:-}"'
        ),
        variables={"CUSTOM": value},
    )
    process = launch(
        tmp_path, config, ["bash", "-c", 'printf "%s" "$CUSTOM" > agent-value']
    )
    assert process.wait(timeout=5) == 0
    assert launch_task(tmp_path).returncode == 0
    for name in ("setup-value", "startup-value", "agent-value"):
        assert (tmp_path / "work" / name).read_text() == value
    assert not (tmp_path / "work/injected").exists()


@pytest.mark.parametrize("model_proxy", [False, True])
def test_installers_do_not_route_downloads_through_the_model_meter(
    tmp_path, monkeypatch, model_proxy
):
    monkeypatch.setenv("HTTPS_PROXY", "http://model-only.invalid:8444")
    if model_proxy:
        monkeypatch.setenv("CHEESE_MODEL_PROXY", "1")
    else:
        monkeypatch.delenv("CHEESE_MODEL_PROXY", raising=False)
    config = EnvironmentConfig(
        setup_script='printf "%s" "${HTTPS_PROXY:-}" > setup-proxy',
        startup_script='printf "%s" "${HTTPS_PROXY:-}" > startup-proxy',
    )
    process = launch(
        tmp_path, config, ["bash", "-c", 'printf "%s" "$HTTPS_PROXY" > agent-proxy']
    )
    assert process.wait(timeout=5) == 0
    assert launch_task(tmp_path).returncode == 0
    expected = "" if model_proxy else "http://model-only.invalid:8444"
    assert (tmp_path / "work/setup-proxy").read_text() == expected
    assert (tmp_path / "work/startup-proxy").read_text() == expected
    assert (
        tmp_path / "work/agent-proxy"
    ).read_text() == "http://model-only.invalid:8444"


def test_cancel_terminates_child_and_rejects_concurrent_installation(tmp_path):
    config = EnvironmentConfig(setup_script="sleep 60 &\necho $! > child-pid\nwait")
    process = launch(tmp_path, config)
    try:
        wait_for(tmp_path / "work/child-pid")
        assert launch(tmp_path, config).wait(timeout=5) == 75
        pid = int((tmp_path / "work/child-pid").read_text())
        process.terminate()
        assert process.wait(timeout=5) == 143
        assert status(tmp_path)["state"] == "failed"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_new_machine_initializes_even_with_same_project_revision(tmp_path):
    config = EnvironmentConfig(setup_script="touch initialized")
    for machine in (tmp_path / "first", tmp_path / "replacement"):
        machine.mkdir()
        assert launch(machine, config).wait(timeout=5) == 0
        assert (machine / "work/initialized").exists()


def test_a_room_without_a_repository_still_prepares_and_starts_the_agent(tmp_path):
    """A room is not a checkout, and preparation must not ask it to be one.

    While it did, every room on a deployment failed preparation with
    "repository checkout is not ready" — and since the agent is only exec'd
    after both scripts succeed, every one of those rooms answered nothing at
    all.
    """
    config = EnvironmentConfig(setup_script="touch installed")
    assert launch(tmp_path, config, ["touch", "agent"]).wait(timeout=5) == 0
    assert not (tmp_path / "work/.git").exists()
    assert (tmp_path / "work/installed").exists()
    assert (tmp_path / "work/agent").exists()


def test_a_missing_work_directory_never_runs_scripts_or_agent(tmp_path):
    """What the check is actually for: an install must never land in some other
    cwd because the directory it was meant for is not there."""
    config = EnvironmentConfig(setup_script="touch installed")
    assert (
        launch(tmp_path, config, ["touch", "agent"], work_dir=False).wait(timeout=5)
        == 1
    )
    assert not (tmp_path / "work").exists()
    assert status(tmp_path)["error"] == "work directory is missing"


def test_task_startup_failure_keeps_room_ready_and_setup_receipt_for_retry(tmp_path):
    config = EnvironmentConfig(
        setup_script="echo setup >> count", startup_script="test -f allowed"
    )
    assert launch(tmp_path, config, ["touch", "agent"]).wait(timeout=5) == 0
    failed = launch_task(tmp_path)
    assert failed.returncode == 1
    assert "startup script exited with status 1" in failed.stderr
    assert status(tmp_path)["state"] == "ready"
    assert (tmp_path / "work/agent").exists()
    (tmp_path / "work/allowed").touch()
    assert launch_task(tmp_path).returncode == 0
    assert (tmp_path / "work/count").read_text() == "setup\n"


def test_changed_revision_reinitializes_preserving_the_work_directory(tmp_path):
    first = EnvironmentConfig(setup_script="echo first >> count")
    second = EnvironmentConfig(setup_script="echo second >> count")
    assert launch(tmp_path, first).wait(timeout=5) == 0
    assert launch(tmp_path, second).wait(timeout=5) == 0
    assert (tmp_path / "work/count").read_text() == "first\nsecond\n"


async def test_reconnect_to_preparing_process_never_probes_it_as_dead(monkeypatch):
    from unittest.mock import AsyncMock

    from app.domain.agent import device_provider

    channel = device_provider.DeviceChannel.__new__(device_provider.DeviceChannel)
    channel._hub = object()
    screen = object()
    channel._existing_screen = lambda *args: screen
    channel.confirm_alive = AsyncMock()
    monkeypatch.setattr(
        device_provider,
        "environment_status",
        AsyncMock(return_value={"state": "preparing"}),
    )
    actual = await channel._ensure_screen(
        device_id="machine",
        agent_user_id=1,
        agent_handle="agent",
        project_id=uuid.UUID(int=1),
        topic_id=uuid.UUID(int=2),
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        launch=None,
    )
    assert actual is screen
    channel.confirm_alive.assert_not_awaited()


def _fake_uv(tmp_path, *, fail_export=False):
    """A `uv` that records its arguments instead of touching a network.

    `export` writes the file it was asked for, because the install after it
    reads that file: a double that only records would make the install's input
    untestable, and a mirror install that never had a requirements file to
    install from is exactly the failure worth catching.
    """
    calls = tmp_path / "uv-calls"
    script = tmp_path / "uv"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        + (
            'if [ "$1" = export ]; then echo broken lock >&2; exit 2; fi\n'
            if fail_export
            else 'if [ "$1" = export ]; then : > "$5"; fi\n'
        )
        + "exit 0\n"
    )
    script.chmod(0o755)
    return calls


def _uv_project(tmp_path, *, venv=True):
    work = tmp_path / "work"
    project = work / "backend"
    project.mkdir(parents=True)
    (project / "uv.lock").write_text("version = 1\n")
    (project / "pyproject.toml").write_text("[project]\nname = 'probe'\n")
    if venv:
        (project / ".venv/bin").mkdir(parents=True)
        (project / ".venv/bin/python").write_text("")
    return work, project


def _mirror_env(tmp_path, **extra):
    return {
        "UV_INDEX": "https://mirror.test/simple",
        "UV_DEFAULT_INDEX": "https://pypi.org/simple",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        **extra,
    }


def test_mirror_install_fetches_the_locked_versions_through_the_mirror(tmp_path):
    """The lock's own URLs are what make an install go abroad; the export is
    the same lock with the mirror as its source."""
    work, project = _uv_project(tmp_path)
    calls = _fake_uv(tmp_path)
    log = (tmp_path / "runner.log").open("w")
    try:
        environment_runner.mirror_install(work, _mirror_env(tmp_path), log)
    finally:
        log.close()
    lines = calls.read_text().splitlines()
    assert lines[0].startswith("export --frozen --format requirements-txt")
    assert lines[1].startswith("pip install --quiet")
    assert f"--python {project / '.venv/bin/python'}" in lines[1]
    # The export is scratch: written where the checkout is not, and gone with
    # the run that made it.
    exported = Path(lines[0].split("-o ", 1)[1])
    assert exported.parent.parent == Path(tempfile.gettempdir())
    assert not exported.exists()
    assert "sync" not in calls.read_text()
    # The export is scratch, not something the checkout keeps.
    exported = lines[0].split("-o ")[1]
    assert not Path(exported).parent.is_relative_to(project)
    assert not Path(exported).exists()


def test_mirror_install_creates_the_environment_before_installing(tmp_path):
    work, project = _uv_project(tmp_path, venv=False)
    calls = _fake_uv(tmp_path)
    log = (tmp_path / "runner.log").open("w")
    try:
        environment_runner.mirror_install(work, _mirror_env(tmp_path), log)
    finally:
        log.close()
    lines = calls.read_text().splitlines()
    assert lines[0].startswith("export --frozen")
    assert lines[1].startswith("venv --quiet")
    assert str(project / ".venv") in lines[1]
    assert lines[2].startswith("pip install")


def test_mirror_install_does_nothing_without_a_mirror(tmp_path):
    """`UV_INDEX` is exported only for a mirror that answered this machine."""
    work, _ = _uv_project(tmp_path)
    calls = _fake_uv(tmp_path)
    log = (tmp_path / "runner.log").open("w")
    try:
        environment_runner.mirror_install(
            work, {"PATH": f"{tmp_path}:{os.environ['PATH']}"}, log
        )
    finally:
        log.close()
    assert not calls.exists()


def test_mirror_install_leaves_the_install_to_the_script_when_the_lock_will_not_export(
    tmp_path,
):
    """A lock this uv cannot export must cost the checkout nothing."""
    work, _ = _uv_project(tmp_path)
    calls = _fake_uv(tmp_path, fail_export=True)
    log = (tmp_path / "runner.log").open("w")
    try:
        environment_runner.mirror_install(work, _mirror_env(tmp_path), log)
    finally:
        log.close()
    lines = calls.read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("export --frozen")


def test_task_preparation_installs_through_the_mirror_before_the_script(
    tmp_path, monkeypatch
):
    """The pre-install is part of preparing a checkout.

    Without this, the two above would still pass with nothing calling them —
    which is what the whole change comes down to: a locked install that goes
    through the mirror because the pre-install ran, and the project's own
    `uv sync --frozen` afterwards, which is still the script's to run.
    """
    work, _ = _uv_project(tmp_path)
    calls = _fake_uv(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    config = EnvironmentConfig(
        variables={"UV_INDEX": "https://mirror.test/simple"},
        startup_script="echo startup-ran",
    )
    assert launch(tmp_path, config).wait(timeout=5) == 0
    assert launch_task(tmp_path).returncode == 0
    assert calls.read_text().splitlines()[0].startswith("export --frozen")
    task_logs = (tmp_path / "home/.cheese-environment/tasks/work").glob("*.log")
    lines = [line for log in task_logs for line in log.read_text().splitlines()]
    assert any("startup: started" in line for line in lines)


def test_task_startup_runs_after_a_failed_mirror_install(tmp_path, monkeypatch):
    """A mirror install that fails must not cost the checkout its startup."""
    work, _ = _uv_project(tmp_path)
    _fake_uv(tmp_path, fail_export=True)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    config = EnvironmentConfig(
        variables={"UV_INDEX": "https://mirror.test/simple"},
        startup_script="touch startup-ran",
    )
    assert launch(tmp_path, config).wait(timeout=5) == 0
    assert launch_task(tmp_path).returncode == 0
    assert (work / "startup-ran").exists()
