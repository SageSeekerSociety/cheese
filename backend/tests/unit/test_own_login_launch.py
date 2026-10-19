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
from app.domain.agent.place import (
    CLAUDE_LOGIN_DIR,
    CLAUDE_LOGINS_DIR,
    MODEL_SERVICE_FILE,
    OWN_LOGIN_ID,
)
from app.domain.device.login_kind import FIRST_LOGIN
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


def _launch_on_machine(
    tmp_path,
    *,
    service: dict | None = None,
    mac: bool = False,
    login_id: str | None = FIRST_LOGIN,
    other_service: dict | None = None,
    profile: str = ".cheese",
) -> dict:
    """Run the launcher the way a machine does, with the platform asking for
    Claude's ``opus`` on the machine's login ``login_id``, and return the
    environment the started process has.

    The machine holds its first login and one more, ``B7QX``; ``service`` is
    the model service set on the login the session is told to use, and
    ``other_service`` one set on the login it is not.

    The started process looks its login up with `security`, as Claude Code does
    on macOS. ``mac`` makes the machine one: ``uname`` says Darwin, and its
    `security` records the HOME it ran with under ``keychain_home``."""
    home = tmp_path / "home"
    first = home / profile / CLAUDE_LOGIN_DIR
    second = home / profile / CLAUDE_LOGINS_DIR / "B7QX"
    for login in (first, second):
        login.mkdir(parents=True)
    used, unused = (second, first) if login_id == "B7QX" else (first, second)
    if service is not None:
        (used / MODEL_SERVICE_FILE).write_text(json.dumps(service))
    if other_service is not None:
        (unused / MODEL_SERVICE_FILE).write_text(json.dumps(other_service))
    room = home / "room"
    work = room / "work"
    work.mkdir(parents=True)
    seen = tmp_path / "seen.json"
    agent = tmp_path / "agent.sh"
    agent.write_text(
        "#!/bin/sh\n"
        "security find-generic-password -s 'Claude Code-credentials' 2>/dev/null\n"
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
        "CHEESE_PROFILE_ROOT": profile,
        "CHEESE_HOME": str(room),
        "CHEESE_WORK": str(work),
        "CHEESE_TOPIC": "11111111-1111-1111-1111-111111111111",
        "CHEESE_PROJECT": "22222222-2222-2222-2222-222222222222",
        "CHEESE_TOKEN": "scoped-token",
        "CHEESE_TOKEN_EXPIRES": str(int(time.time()) + 3600),
        "ANTHROPIC_MODEL": "opus",
        **({OWN_LOGIN_ID: login_id} if login_id is not None else {}),
    }
    keychain_home = tmp_path / "keychain-home"
    if mac:
        machine = tmp_path / "machine-bin"
        machine.mkdir()
        (machine / "uname").write_text("#!/bin/sh\necho Darwin\n")
        (machine / "security").write_text(
            f'#!/bin/sh\nprintf %s "$HOME" > "{keychain_home}"\n'
        )
        for tool in machine.iterdir():
            tool.chmod(0o755)
        env["PATH"] = f"{machine}:{env['PATH']}"

    result = subprocess.run(
        ["sh", str(launcher)], env=env, capture_output=True, text=True, timeout=30
    )

    if not seen.exists():
        return {"_started": False, "_stderr": result.stderr}
    assert result.returncode == 0, result.stderr
    return {
        **json.loads(seen.read_text()),
        "_home": str(home),
        "_keychain_home": keychain_home.read_text() if keychain_home.exists() else None,
    }


def test_an_own_session_reads_the_login_its_owner_gave_the_platform(tmp_path):
    """The session reads the login directory under the machine owner's home,
    not the room's, and calls no model service the owner did not set."""
    seen = _launch_on_machine(tmp_path)
    assert (
        seen["CLAUDE_SECURESTORAGE_CONFIG_DIR"]
        == f"{seen['_home']}/.cheese/logins/claude-login"
    )
    assert "ANTHROPIC_BASE_URL" not in seen
    assert "ANTHROPIC_AUTH_TOKEN" not in seen


def test_an_own_session_on_another_login_reads_that_login_only(tmp_path):
    """A machine holding two logins: a session added with the second reads the
    second's directory, and the model service set on the first is not its."""
    seen = _launch_on_machine(
        tmp_path,
        login_id="B7QX",
        other_service={
            "base_url": "https://open.bigmodel.cn/api/anthropic",
            "token": "the-first-logins-key",
            "model": "glm-4.6",
        },
    )
    assert (
        seen["CLAUDE_SECURESTORAGE_CONFIG_DIR"]
        == f"{seen['_home']}/.cheese/logins/claude-logins/B7QX"
    )
    assert "ANTHROPIC_BASE_URL" not in seen
    assert "ANTHROPIC_AUTH_TOKEN" not in seen
    assert seen["ANTHROPIC_MODEL"] == "opus"


@pytest.mark.parametrize("login_id", [FIRST_LOGIN, "B7QX"])
def test_session_uses_only_its_enrollments_login(tmp_path, login_id):
    seen = _launch_on_machine(
        tmp_path,
        profile=".cheese/profiles/research",
        login_id=login_id,
        service={
            "base_url": "https://example.test",
            "token": "team-key",
            "model": "team-model",
        },
    )
    assert seen["CLAUDE_SECURESTORAGE_CONFIG_DIR"].startswith(
        f"{seen['_home']}/.cheese/profiles/research/logins/"
    )
    assert seen["ANTHROPIC_AUTH_TOKEN"] == "team-key"
    assert seen["ANTHROPIC_MODEL"] == "team-model"


def test_an_own_session_on_another_login_calls_its_model_service(tmp_path):
    seen = _launch_on_machine(
        tmp_path,
        login_id="B7QX",
        service={
            "base_url": "https://kimi.test/anthropic",
            "token": "k",
            "model": "k2",
        },
    )
    assert seen["ANTHROPIC_BASE_URL"] == "https://kimi.test/anthropic"
    assert seen["ANTHROPIC_MODEL"] == "k2"


def test_an_own_session_not_told_its_login_does_not_start(tmp_path):
    """With two logins on the machine, the launch does not pick one itself."""
    seen = _launch_on_machine(tmp_path, login_id=None)
    assert seen["_started"] is False


def test_an_own_session_on_a_mac_finds_its_owners_login_keychain(tmp_path):
    """On macOS the login is a Keychain item, which `security` finds through
    HOME. The session's keychain lookups run with the machine owner's home,
    while the session itself keeps the room's."""
    seen = _launch_on_machine(tmp_path, mac=True)
    assert seen["_keychain_home"] == seen["_home"]
    assert seen["HOME"] != seen["_home"]


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
