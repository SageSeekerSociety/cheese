"""A member's own Claude Code signs in with its owner's login, and nothing of
the platform's metering reaches it (#2991).

Its requests go from its owner's machine to the vendor on the login the owner
gave the platform there (`cheesehost claude login`). The platform's credential,
the metering proxy's CA and the tunnel to it are for sessions the project pays
for; put on the owner's machine, they would route the owner's own requests
through the platform, or fail them.
"""

import json
import os
import subprocess
import time

import pytest

from app.core.config import settings
from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code.device_launch import (
    NO_LOGIN_PLACEHOLDER,
    launch_holes,
)
from app.domain.agent.place import MODEL_SERVICE_FILE
from tests.unit.test_device_provider import (
    _no_device_identity,  # noqa: F401 — the machine's address, not the database's
    _subscription_screen,
    _subscription_settings,
)

pytestmark = pytest.mark.anyio

OWN = {"CHEESE_OWN_LOGIN": "1"}


async def test_an_own_session_is_given_nothing_of_the_metering_proxy(
    monkeypatch, tmp_path
):
    ca = _subscription_settings(monkeypatch, tmp_path)
    hub, env, _project, _topic = await _subscription_screen(env=dict(OWN))

    for name in (
        "HTTPS_PROXY",
        "CHEESE_TUNNEL_URL",
        "CHEESE_CONNECT_TOKEN",
        "CHEESE_MODEL_PROXY",
        "NODE_EXTRA_CA_CERTS",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        assert name not in env, name
    launcher = "\n".join(script for _argv, script in hub.execs if script)
    assert ca.strip() not in launcher, "the proxy's CA is not put on the machine"
    assert NO_LOGIN_PLACEHOLDER not in launcher


def _launch_on_machine(tmp_path, *, service: dict | None = None) -> dict:
    """Run the launcher the way a machine does, with the platform asking for
    Claude's ``opus``, and return the environment the started process has."""
    home = tmp_path / "home"
    login = home / ".cheese" / "claude-login"
    login.mkdir(parents=True)
    if service is not None:
        (login / MODEL_SERVICE_FILE).write_text(json.dumps(service))
    room = home / "room"
    work = room / "work"
    work.mkdir(parents=True)
    seen = tmp_path / "seen.json"
    agent = tmp_path / "agent.sh"
    agent.write_text(
        "#!/bin/sh\n"
        'python3 -c "import json, os; print(json.dumps(dict(os.environ)))"'
        f' > "{seen}"\n'
    )
    agent.chmod(0o755)
    launcher = tmp_path / "launch.sh"
    launcher.write_text(
        machine_launcher.launch_script(
            credentials=launch_holes(state="$HOME/state", own_login=True).credentials,
            prepare=f'AGENT="{agent}"\n',
            command="$AGENT",
        )
    )
    env = {
        **os.environ,
        "HOME": str(home),
        "CHEESE_HOME": str(room),
        "CHEESE_WORK": str(work),
        "CHEESE_TOPIC": "11111111-1111-1111-1111-111111111111",
        "CHEESE_PROJECT": "22222222-2222-2222-2222-222222222222",
        "CHEESE_TOKEN": "scoped-token",
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
        "ANTHROPIC_MODEL": "opus",
    }

    result = subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, result.stderr
    return {**json.loads(seen.read_text()), "_home": str(home)}


def test_an_own_session_reads_the_login_its_owner_gave_the_platform(tmp_path):
    """The session reads the login directory under the machine owner's home,
    not the room's, and calls no model service the owner did not set."""
    seen = _launch_on_machine(tmp_path)
    assert (
        seen["CLAUDE_SECURESTORAGE_CONFIG_DIR"]
        == f"{seen['_home']}/.cheese/claude-login"
    )
    assert "ANTHROPIC_BASE_URL" not in seen
    assert "ANTHROPIC_AUTH_TOKEN" not in seen


def test_an_own_session_calls_the_model_service_its_owner_set(tmp_path):
    """The owner pointed their Claude Code at another service: the session
    calls it with the owner's key, and every model it asks for is the one the
    owner named, since such a service serves none of Claude's."""
    seen = _launch_on_machine(
        tmp_path,
        service={
            "base_url": "https://open.bigmodel.cn/api/anthropic",
            "token": "it's-the-owners key",
            "model": "glm-4.6",
        },
    )
    assert seen["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert seen["ANTHROPIC_AUTH_TOKEN"] == "it's-the-owners key"
    for name in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert seen[name] == "glm-4.6", name


def test_an_unreadable_model_service_leaves_the_session_on_the_login(tmp_path):
    seen = _launch_on_machine(tmp_path, service={"model": "glm-4.6"})
    assert "ANTHROPIC_BASE_URL" not in seen
    assert seen["ANTHROPIC_MODEL"] == "opus"


async def test_an_own_session_starts_on_a_deployment_with_no_metering_proxy(
    monkeypatch, tmp_path
):
    """The proxy's CA is a requirement of a subscription session the platform
    pays for; a deployment without one still runs its members' own."""
    monkeypatch.setattr(settings, "subscription_ca_backend_path", "")
    _hub, env, _project, _topic = await _subscription_screen(env=dict(OWN))
    assert "CHEESE_TUNNEL_URL" not in env
