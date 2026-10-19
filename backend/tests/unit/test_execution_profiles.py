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
