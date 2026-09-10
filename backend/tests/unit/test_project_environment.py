"""Configuration and real installation subprocess behavior."""

import json
import os
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.domain.agent import environment_runner
from app.domain.project.environment import EnvironmentConfig


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
                assert (work / "startup-value").read_text() == "project-specific"
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
    (tmp_path / "work/dependency-version").write_text("2")
    second = launch(tmp_path, config)
    assert second.wait(timeout=5) == 0
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


def test_startup_failure_keeps_setup_receipt_for_retry(tmp_path):
    config = EnvironmentConfig(
        setup_script="echo setup >> count", startup_script="test -f allowed"
    )
    assert launch(tmp_path, config, ["touch", "agent"]).wait(timeout=5) == 1
    assert status(tmp_path)["stage"] == "startup"
    assert not (tmp_path / "work/agent").exists()
    (tmp_path / "work/allowed").touch()
    assert launch(tmp_path, config, ["touch", "agent"]).wait(timeout=5) == 0
    assert (tmp_path / "work/count").read_text() == "setup\n"


def test_changed_revision_reinitializes_preserving_the_work_directory(tmp_path):
    first = EnvironmentConfig(setup_script="echo first >> count")
    second = EnvironmentConfig(setup_script="echo second >> count")
    assert launch(tmp_path, first).wait(timeout=5) == 0
    assert launch(tmp_path, second).wait(timeout=5) == 0
    assert (tmp_path / "work/count").read_text() == "first\nsecond\n"


@pytest.mark.parametrize("channel_name", ["DeviceChannel", "CloudChannel"])
async def test_channels_ignore_old_failure_but_wait_for_new_attempt(
    monkeypatch, channel_name
):
    from unittest.mock import AsyncMock

    from app.domain.agent import cloud_provider, device_provider

    cls = getattr(
        device_provider if channel_name == "DeviceChannel" else cloud_provider,
        channel_name,
    )
    channel = cls.__new__(cls)
    channel._hub = object()
    channel._subscription_devices = {}
    channel._existing_screen = lambda *args: None
    screen = object()
    channel._ensure_screen = AsyncMock(return_value=screen)
    read = AsyncMock(
        side_effect=[
            {"state": "failed", "attempt": "old"},
            {"state": "failed", "attempt": "old"},
            {"state": "preparing", "attempt": "new"},
            {"state": "ready", "attempt": "new"},
        ]
    )
    monkeypatch.setattr(device_provider, "environment_status", read)
    monkeypatch.setattr(device_provider.asyncio, "sleep", AsyncMock())
    actual = await channel.ensure_ready(
        project_id=uuid.UUID(int=1),
        topic_id=uuid.UUID(int=2),
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        memory_scope=None,
        owner=None,
        turn_id=None,
        launch=None,
        precheck=("machine", 1, "agent"),
    )
    assert actual is screen
    assert read.await_count == 4


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


async def test_reconnect_uses_initial_environment_read_until_next_poll(monkeypatch):
    from unittest.mock import AsyncMock

    from app.domain.agent import device_provider

    channel = device_provider.DeviceChannel.__new__(device_provider.DeviceChannel)
    channel._hub = object()
    channel._subscription_devices = {}
    screen = object()
    channel._existing_screen = lambda *args: screen
    channel.confirm_alive = AsyncMock()
    read = AsyncMock(side_effect=[{"state": "preparing"}, {"state": "ready"}])
    monkeypatch.setattr(device_provider, "environment_status", read)
    actual = await channel.ensure_ready(
        project_id=uuid.UUID(int=1),
        topic_id=uuid.UUID(int=2),
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        memory_scope=None,
        owner=None,
        turn_id=None,
        launch=None,
        precheck=("machine", 1, "agent"),
    )
    assert actual is screen
    assert read.await_count == 2
    channel.confirm_alive.assert_not_awaited()


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("next_state", ["ready", "failed"])
async def test_ready_environment_is_rechecked_only_after_screen_replacement(
    monkeypatch, replacement, next_state
):
    from unittest.mock import AsyncMock

    from app.domain.agent import device_provider

    channel = device_provider.DeviceChannel.__new__(device_provider.DeviceChannel)
    channel._hub = object()
    channel._subscription_devices = {}
    original = object()
    screen = object() if replacement else original
    channel._existing_screen = lambda *args: original
    channel._ensure_screen = AsyncMock(return_value=screen)
    read = AsyncMock(
        side_effect=[
            {"state": "ready", "attempt": "old"},
            {"state": next_state, "attempt": "new"},
        ]
    )
    monkeypatch.setattr(device_provider, "environment_status", read)
    request = channel.ensure_ready(
        project_id=uuid.UUID(int=1),
        topic_id=uuid.UUID(int=2),
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        memory_scope=None,
        owner=None,
        turn_id=None,
        launch=None,
        precheck=("machine", 1, "agent"),
    )
    if replacement and next_state == "failed":
        with pytest.raises(device_provider.EnvironmentPreparationError):
            await request
    else:
        assert await request is screen
    assert read.await_count == (2 if replacement else 1)


async def test_fast_environment_is_observed_without_two_second_wait(monkeypatch):
    from unittest.mock import AsyncMock

    from app.domain.agent import device_provider

    channel = device_provider.DeviceChannel.__new__(device_provider.DeviceChannel)
    channel._hub = object()
    channel._subscription_devices = {}
    channel._existing_screen = lambda *args: None
    screen = object()
    channel._ensure_screen = AsyncMock(return_value=screen)
    started = time.monotonic()

    async def read(*args, **kwargs):
        state = "ready" if time.monotonic() - started >= 0.05 else "pending"
        return {"state": state, "attempt": "new" if state == "ready" else None}

    monkeypatch.setattr(device_provider, "environment_status", read)
    actual = await channel.ensure_ready(
        project_id=uuid.UUID(int=1),
        topic_id=uuid.UUID(int=2),
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        memory_scope=None,
        owner=None,
        turn_id=None,
        launch=None,
        precheck=("machine", 1, "agent"),
    )
    assert actual is screen
    assert time.monotonic() - started < 1


async def test_long_environment_returns_to_low_frequency_checks(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.domain.agent import device_provider

    clock = SimpleNamespace(seconds=0.0)
    reads = []

    async def sleep(seconds):
        clock.seconds += seconds

    async def read(*args, **kwargs):
        reads.append(clock.seconds)
        return {
            "state": "ready" if clock.seconds >= 14 else "preparing",
            "attempt": "new",
        }

    channel = device_provider.DeviceChannel.__new__(device_provider.DeviceChannel)
    channel._hub = object()
    channel._subscription_devices = {}
    channel._existing_screen = lambda *args: None
    screen = object()
    channel._ensure_screen = AsyncMock(return_value=screen)
    monkeypatch.setattr(
        device_provider, "time", SimpleNamespace(monotonic=lambda: clock.seconds)
    )
    monkeypatch.setattr(device_provider.asyncio, "sleep", sleep)
    monkeypatch.setattr(device_provider, "environment_status", read)
    actual = await channel.ensure_ready(
        project_id=uuid.UUID(int=1),
        topic_id=uuid.UUID(int=2),
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        memory_scope=None,
        owner=None,
        turn_id=None,
        launch=None,
        precheck=("machine", 1, "agent"),
    )
    assert actual is screen
    assert 14 <= clock.seconds < 16
    assert len([at for at in reads if at >= 10]) <= 3
