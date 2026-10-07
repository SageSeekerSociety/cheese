"""Whether a machine's owner has logged in their own Claude Code for the platform.

The owner logs it in on the machine (`cheesehost claude login`), under the
platform's directory and with the build the platform pins, and the backend asks
the machine whether that login is good. The answer has to be about THAT login:
not the owner's own `~/.claude`, and not a token the machine's environment
happens to carry, since a session placed on the answer authenticates with that
login and nothing else.
"""

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code import owner_login
from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.place import CLAUDE_LOGIN_DIR, footprint_root
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow
from app.domain.device.supply import Supply
from app.domain.user.models import User

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake build is a shell script"
)

# A stand-in for the pinned build: `auth status` reports a login only when it is
# asked about the platform's login directory and carries no inherited token,
# which is what a real build does with a login in that directory.
FAKE_BUILD = """#!/bin/sh
if [ "$1 $2" != "auth status" ]; then exit 2; fi
if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || [ "$CLAUDE_CONFIG_DIR" != "$EXPECTED_LOGIN" ] \\
    || [ ! -f "$CLAUDE_CONFIG_DIR/.credentials.json" ]; then
  echo '{"loggedIn": false, "authMethod": "none"}'
else
  echo '{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "max"}'
fi
"""


def _machine(tmp_path: Path, *, installed: bool, logged_in: bool) -> dict:
    home = tmp_path / "home"
    root = home / footprint_root()
    login = root / CLAUDE_LOGIN_DIR
    if installed:
        build = root / "claude" / "versions" / CLAUDE_PINNED_VERSION
        build.parent.mkdir(parents=True)
        build.write_text(FAKE_BUILD)
        build.chmod(0o700)
    if logged_in:
        login.mkdir(parents=True)
        (login / ".credentials.json").write_text("{}")
    return {
        **os.environ,
        "HOME": str(home),
        "EXPECTED_LOGIN": str(login),
        # What a machine's own environment may carry: the owner's own config
        # directory and a token of someone else's.
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-not-the-platforms",
    }


def _ask(env: dict) -> owner_login.ClaudeLogin:
    result = subprocess.run(
        [sys.executable, "-"],
        input=owner_login.program(),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return owner_login.read_answer(result.stdout)


def test_a_machine_with_the_platforms_login_answers_logged_in(tmp_path):
    login = _ask(_machine(tmp_path, installed=True, logged_in=True))
    assert login.logged_in
    assert login.subscription_type == "max"


def test_an_inherited_token_or_the_owners_own_login_is_not_the_platforms(tmp_path):
    """The machine carries a token and the owner's own config directory, but no
    login under the platform's directory: that is not logged in."""
    login = _ask(_machine(tmp_path, installed=True, logged_in=False))
    assert login.installed
    assert not login.logged_in


def test_a_machine_without_the_build_is_not_logged_in(tmp_path):
    login = _ask(_machine(tmp_path, installed=False, logged_in=True))
    assert not login.installed
    assert not login.logged_in


@pytest.mark.parametrize("printed", ["", "Traceback (most recent call last)", "[]"])
def test_an_answer_nobody_can_read_is_not_a_login(printed):
    assert not owner_login.read_answer(printed).logged_in


class _Hub:
    def __init__(self, answer: dict):
        self.answer = answer
        self.asked: list[str] = []

    async def exec(self, device_id, argv, *, stdin, timeout):
        self.asked.append(device_id)
        return {"exit": 0, "stdout": json.dumps(self.answer) + "\n", "stderr": ""}


async def _device(factory, supply: Supply) -> str:
    device_id = uuid.uuid4().hex[:12]
    async with factory() as session:
        owner = User(
            username=f"u{device_id}",
            email=f"{device_id}@example.io",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(owner)
        await session.flush()
        session.add(
            DeviceRow(
                device_id=device_id,
                name="laptop",
                token=f"tok-{device_id}",
                owner_user_id=owner.id,
                created_at=datetime.now(UTC),
                supply=supply,
            )
        )
        await session.commit()
    return device_id


async def test_a_persons_machine_is_asked_as_it_connects_and_the_answer_kept(
    db_factory,
):
    device = await _device(db_factory, Supply.self_hosted)
    hub = _Hub(
        {"installed": True, "status": {"loggedIn": True, "authMethod": "claude.ai"}}
    )
    await owner_login.refresh_on_connect(db_factory, hub, device)

    hub.answer = {"installed": True, "status": {"loggedIn": False}}
    await owner_login.refresh_on_connect(db_factory, hub, device)

    async with db_factory() as session:
        kept = await session.get(DeviceClaudeLoginRow, device)
    assert hub.asked == [device, device]
    assert kept is not None and not kept.logged_in, "the latest answer is the one kept"


async def test_a_cloud_machine_carries_no_ones_login_and_is_not_asked(db_factory):
    device = await _device(db_factory, Supply.cloud)
    hub = _Hub({"installed": True, "status": {"loggedIn": True}})
    await owner_login.refresh_on_connect(db_factory, hub, device)
    assert hub.asked == []
