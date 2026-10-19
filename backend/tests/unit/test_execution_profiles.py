"""An enrollment executes and reclaims only its own workspaces."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from app.domain.agent import device_provider, resource_cleanup
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.agent.place import launcher_path


@pytest.mark.parametrize("profile", [".cheese", ".cheese/profiles/research"])
def test_launcher_and_cleanup_address_the_enrollments_workspace(
    tmp_path, monkeypatch, profile
):
    monkeypatch.setenv("CHEESE_PROFILE_ROOT", profile)
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    environment = {**os.environ, "HOME": str(tmp_path)}
    paths = (
        device_provider.device_home_dir(project, resource),
        device_provider.device_work_dir(project, resource),
        device_provider.device_store_dir(project),
        launcher_path(uuid.UUID(resource)),
    )
    expanded = [
        subprocess.check_output(
            ["sh", "-c", f'printf %s "{path}"'], env=environment, text=True
        )
        for path in paths
    ]
    root = tmp_path / profile
    assert expanded[:3] == [
        str(root / "home" / project / resource),
        str(root / "work" / project / resource),
        str(root / "store" / project),
    ]
    assert Path(expanded[3]).parent == root / "launch"
    assert resource_cleanup.resource_paths(tmp_path, project, resource) == tuple(
        map(Path, expanded[:2])
    )
    assert bootstrap.release_store(tmp_path) == root / "executor-releases"


def test_cleanup_keeps_the_other_enrollments_files(tmp_path, monkeypatch):
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    first = tmp_path / ".cheese/home" / project / resource
    second = tmp_path / ".cheese/profiles/research/home" / project / resource
    for home in (first, second):
        home.mkdir(parents=True)
        (home / "work.txt").write_text("retained work")
    monkeypatch.setenv("CHEESE_PROFILE_ROOT", ".cheese/profiles/research")
    home, _ = resource_cleanup.resource_paths(tmp_path, project, resource)
    resource_cleanup.remove_tree(home)
    assert not second.exists()
    assert (first / "work.txt").read_text() == "retained work"


@pytest.mark.parametrize("harness", ["codex", "pi"])
def test_runner_archive_is_looked_up_inside_its_execution_profile(tmp_path, harness):
    from app.domain.agent.harness.codex import launch as codex
    from app.domain.agent.harness.pi import launch as pi

    state = "$HOME/${CHEESE_PROFILE_ROOT:-.cheese}/harness/seat"
    if harness == "codex":
        program = codex.script(
            state=state, config={}, codex_config="", env={}, ship=False
        )
    else:
        program = pi.on_host(
            state=state,
            config={"execution_target": None},
            api_base="https://example.test/api",
            model="glm-5.2",
            env={},
        ).program(ship=False)
    completed = subprocess.run(
        [sys.executable, "-"],
        input=program,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "CHEESE_PROFILE_ROOT": ".cheese/profiles/research",
        },
        check=True,
    )
    assert json.loads(completed.stdout) == {"runner": "missing"}
    assert (tmp_path / ".cheese/profiles/research/harness/seat").is_dir()
    assert not (tmp_path / "${CHEESE_PROFILE_ROOT:-.cheese}").exists()


def test_a_claude_runner_keeps_its_state_inside_its_execution_profile(tmp_path):
    from app.domain.agent import machine_launcher
    from app.domain.agent.harness.claude_code.device_launch import ClaudeLaunch
    from app.domain.agent.launch import MachinePlace

    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    state = machine_launcher.state_dir(project, resource, "claude-code", "ops")
    place = MachinePlace(
        home=device_provider.device_home_dir(project, resource),
        workdir=device_provider.device_work_dir(project, resource),
        store="",
        state=state,
        api_base="",
        project_id=project,
        topic_id=resource,
        agent_handle="ops",
    )
    prepare = ClaudeLaunch(system_prompt="x").on(place).prepare
    start = prepare.index("CLAUDE_STATE=")
    block = prepare[start : prepare.index("esac", start) + len("esac")]
    resolved = subprocess.check_output(
        ["bash", "-c", block + '\nprintf %s "$CLAUDE_STATE"'],
        env={
            **os.environ,
            "REAL_HOME": str(tmp_path),
            "CHEESE_PROFILE_ROOT": ".cheese/profiles/research",
        },
        text=True,
    )
    assert Path(resolved).is_relative_to(tmp_path / ".cheese/profiles/research/harness")


class _Transport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


@pytest.mark.parametrize("profiles", [False, True])
async def test_a_connector_is_sent_paths_it_can_resolve(profiles):
    """A connector from before enrollments had roots of their own expands only
    `$HOME` and runs the default profile, so the paths it resolves itself name
    `.cheese`; one that resolves the root is left to name its own."""
    import asyncio

    from app.domain.agent.device_hub import DeviceHub
    from app.domain.agent.place import ROOT_PLACEHOLDER

    hub = DeviceHub()
    transport = _Transport()
    await hub.attach_device("dev1", transport)
    await hub.on_device_message(
        "dev1", {"t": "hello", "executor": True, "profiles": profiles}
    )
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = device_provider.device_home_dir(project, resource)
    await hub.open_screen(
        "dev1", ["true"], agent_user_id=1, agent_handle="a", env={"CHEESE_HOME": home}
    )
    calls = [
        asyncio.create_task(hub.exec("dev1", ["true"], cwd=home, env={"DIR": home})),
        asyncio.create_task(hub.call_executor("dev1", home, "ping", {})),
    ]
    await asyncio.sleep(0)
    assert len(transport.sent) == 4
    frames = json.dumps(transport.sent[1:])
    for call in calls:
        call.cancel()
    await asyncio.gather(*calls, return_exceptions=True)
    if profiles:
        assert frames.count(ROOT_PLACEHOLDER) == 4
    else:
        assert ROOT_PLACEHOLDER not in frames
        assert frames.count(f"$HOME/.cheese/home/{project}/{resource}") == 4


@pytest.mark.parametrize("profile", [".cheese", ".cheese/profiles/research"])
def test_a_running_executor_prepares_its_own_room_and_no_other(
    tmp_path, monkeypatch, profile
):
    from types import SimpleNamespace

    from app.domain.agent.harness.claude_code.remote_execution import runtime_calls

    monkeypatch.setenv("CHEESE_PROFILE_ROOT", profile)
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    state = tmp_path / profile / "home" / project / resource / ".cheese/executor"
    # No programs to run: getting past the ownership check is what is asked.
    executor = SimpleNamespace(state=state, programs=tmp_path / "absent", config={})
    with pytest.raises(FileNotFoundError):
        runtime_calls.prepare(executor, {"project": project, "resource": resource})
    with pytest.raises(ValueError, match="another room"):
        runtime_calls.prepare(
            executor, {"project": project, "resource": str(uuid.uuid4())}
        )
