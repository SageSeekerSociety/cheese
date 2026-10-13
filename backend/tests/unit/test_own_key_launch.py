"""A member's own Claude Code on a connection its owner's account holds calls
the model with that connection's key, and with nothing of the platform's
(#2991).

Its session runs on the deployment's session host. Its requests go straight to
the service its owner named, paid by its owner's key: the metering proxy's CA,
tunnel and placeholder login are the project's, and with them a request would
be billed to the platform or fail. The key is in the session's own environment
and nowhere the launch writes down.
"""

import json
import os
import subprocess
import time

import pytest

from app.domain.agent import machine_launcher
from app.domain.agent.harness.claude_code.device_launch import (
    NO_LOGIN_PLACEHOLDER,
    launch_holes,
)
from app.domain.agent.place import (
    OWN_KEY,
    OWN_MODEL,
    OWN_MODEL_AUTH,
    OWN_MODEL_BASE_URL,
    OWN_MODEL_KEY,
)
from tests.unit.test_device_provider import (
    _no_device_identity,  # noqa: F401 — the machine's address, not the database's
    _subscription_screen,
    _subscription_settings,
)

pytestmark = pytest.mark.anyio

KEY = "sk-the-owners-own-key-7f3a"


def _service(**over) -> dict:
    return {
        OWN_KEY: "1",
        OWN_MODEL_BASE_URL: "https://api.moonshot.test/anthropic",
        OWN_MODEL_KEY: KEY,
        OWN_MODEL_AUTH: "bearer",
        OWN_MODEL: "kimi-k2",
        **over,
    }


async def test_an_account_session_is_given_nothing_of_the_metering_proxy(
    monkeypatch, tmp_path
):
    ca = _subscription_settings(monkeypatch, tmp_path)
    hub, env, _project, _topic = await _subscription_screen(env=_service())

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
    assert ca.strip() not in launcher, "the proxy's CA is not put on the host"
    assert NO_LOGIN_PLACEHOLDER not in launcher


async def test_its_key_reaches_the_session_and_is_not_written_into_its_launch(
    monkeypatch, tmp_path
):
    _subscription_settings(monkeypatch, tmp_path)
    hub, env, _project, _topic = await _subscription_screen(env=_service())

    assert env[OWN_MODEL_KEY] == KEY, "the process it starts is handed the key"
    for argv, script in hub.execs:
        assert KEY not in json.dumps(argv)
        assert KEY not in (script or ""), "the launcher file holds no key"


def _launch(tmp_path, service: dict) -> dict:
    """Run the launch the way the session host does, with the platform asking
    for Claude's ``opus`` and a stray login inherited from the host, and
    return the environment the started process has."""
    home = tmp_path / "home"
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
            credentials=launch_holes(state="$HOME/state", own_key=True).credentials,
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
        "CLAUDE_CODE_OAUTH_TOKEN": "the-hosts-own-login",
        "ANTHROPIC_AUTH_TOKEN": "the-platforms-gateway-key",
        **service,
    }
    result = subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(seen.read_text())


def test_a_model_service_is_called_with_its_owners_key_and_model(tmp_path):
    seen = _launch(tmp_path, _service())

    assert seen["ANTHROPIC_BASE_URL"] == "https://api.moonshot.test/anthropic"
    assert seen["ANTHROPIC_AUTH_TOKEN"] == KEY
    for name in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert seen[name] == "kimi-k2", name
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen, "no login stands in for the key"
    assert "ANTHROPIC_API_KEY" not in seen


def test_an_api_key_calls_anthropic_with_it_and_keeps_the_model_asked_for(
    tmp_path,
):
    seen = _launch(
        tmp_path,
        _service(
            **{
                OWN_MODEL_BASE_URL: "https://api.anthropic.com",
                OWN_MODEL_AUTH: "api_key",
            }
        )
        | {OWN_MODEL: ""},
    )

    assert seen["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert seen["ANTHROPIC_API_KEY"] == KEY
    assert "ANTHROPIC_AUTH_TOKEN" not in seen, "not the platform's gateway key"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen
    assert seen["ANTHROPIC_MODEL"] == "opus"


def test_nothing_else_the_launch_starts_finds_the_key_under_its_own_name(tmp_path):
    seen = _launch(tmp_path, _service())

    for name in (OWN_MODEL_KEY, OWN_MODEL_BASE_URL, OWN_MODEL_AUTH, OWN_MODEL):
        assert name not in seen, name
