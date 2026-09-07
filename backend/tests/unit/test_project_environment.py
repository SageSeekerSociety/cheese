"""Configuration and real installation subprocess behavior."""

import json
import os
import subprocess
import sys
import time

import pytest
from pydantic import ValidationError

from app.domain.agent import environment_runner
from app.domain.project.environment import EnvironmentConfig


def launch(tmp_path, config, command=None, *, checkout=True):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    if checkout:
        subprocess.run(["git", "init", "-q", str(work)], check=True)
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


def test_failed_checkout_never_runs_scripts_or_agent(tmp_path):
    config = EnvironmentConfig(setup_script="touch installed")
    assert (
        launch(tmp_path, config, ["touch", "agent"], checkout=False).wait(timeout=5)
        == 1
    )
    assert not (tmp_path / "work/installed").exists()
    assert not (tmp_path / "work/agent").exists()


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


def test_changed_revision_reinitializes_preserving_checkout(tmp_path):
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
        project_id="project",
        topic_id="topic",
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
        project_id="project",
        topic_id="topic",
        token="token",
        env={"CHEESE_ENVIRONMENT": "{}"},
        launch=None,
    )
    assert actual is screen
    channel.confirm_alive.assert_not_awaited()
