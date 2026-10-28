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
from app.domain.agent.place import (
    CLAUDE_LOGIN_DIR,
    CLAUDE_LOGINS_DIR,
    footprint_root,
)
from app.domain.device.login_kind import FIRST_LOGIN
from app.domain.device.models import DeviceClaudeLoginRow, DeviceRow
from app.domain.device.supply import Supply
from app.domain.device.wiring import sql_device_service
from app.domain.user.models import User
from app.domain.user.tokens import digest_token
from tests.support.devices import enrollment_team

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake build is a shell script"
)

# A stand-in for the pinned build: `auth status` reports a login only when it is
# asked about one of the platform's login directories that holds one and
# carries no inherited token, which is what a real build does. The plan it
# reports is what that directory's credentials say, so two logins on one
# machine answer apart.
FAKE_BUILD = """#!/bin/sh
if [ "$1 $2" != "auth status" ]; then exit 2; fi
case "$CLAUDE_CONFIG_DIR" in
  "$PLATFORM_ROOT"/logins/claude-login|"$PLATFORM_ROOT"/logins/claude-logins/*) ;;
  *) echo '{"loggedIn": false, "authMethod": "none"}'; exit 0 ;;
esac
if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || [ ! -f "$CLAUDE_CONFIG_DIR/.credentials.json" ]; then
  echo '{"loggedIn": false, "authMethod": "none"}'
else
  plan="$(cat "$CLAUDE_CONFIG_DIR/.credentials.json")"
  echo "{\\"loggedIn\\": true, \\"authMethod\\": \\"claude.ai\\", \\"subscriptionType\\": \\"$plan\\"}"
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
        (login / ".credentials.json").write_text("max")
    return {
        **os.environ,
        "HOME": str(home),
        "PLATFORM_ROOT": str(root),
        "FIRST_LOGIN_DIR": str(login),
        # What a machine's own environment may carry: the owner's own config
        # directory and a token of someone else's.
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-not-the-platforms",
    }


def _another_login(env: dict, local_id: str, plan: str | None) -> Path:
    """A further login on the machine, logged in on ``plan`` (or not)."""
    login = Path(env["PLATFORM_ROOT"]) / CLAUDE_LOGINS_DIR / local_id
    login.mkdir(parents=True)
    if plan is not None:
        (login / ".credentials.json").write_text(plan)
    return login


def _answer(env: dict) -> dict[str, owner_login.ClaudeLogin]:
    result = subprocess.run(
        [sys.executable, "-"],
        input=owner_login.program(),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return {login.local_id: login for login in owner_login.read_answer(result.stdout)}


def _ask(env: dict) -> owner_login.ClaudeLogin:
    """What the machine says about its first login."""
    return _answer(env)[FIRST_LOGIN]


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


def test_a_console_login_is_not_a_subscription_resource():
    answer = json.dumps(
        {
            "installed": True,
            "logins": [
                {"id": "default", "status": {"loggedIn": True, "authMethod": "console"}}
            ],
        }
    )
    assert not owner_login.read_answer(answer)[0].logged_in


def test_each_login_on_one_machine_answers_for_itself(tmp_path):
    """A machine holding three logins says what each is: two subscriptions on
    different plans, and one never finished. None is read as another."""
    env = _machine(tmp_path, installed=True, logged_in=True)
    _another_login(env, "B7QX", "pro")
    _another_login(env, "half-done", None)

    logins = _answer(env)

    assert set(logins) == {FIRST_LOGIN, "B7QX", "half-done"}
    assert logins[FIRST_LOGIN].logged_in
    assert logins[FIRST_LOGIN].subscription_type == "max"
    assert logins["B7QX"].logged_in
    assert logins["B7QX"].subscription_type == "pro"
    assert not logins["half-done"].logged_in


def test_a_machine_with_only_further_logins_still_reports_its_first(tmp_path):
    """The first login is asked about even before its directory exists, as it
    was before a machine could hold more: a machine with the build says so."""
    env = _machine(tmp_path, installed=True, logged_in=False)
    _another_login(env, "B7QX", "pro")

    logins = _answer(env)

    assert logins[FIRST_LOGIN].installed and not logins[FIRST_LOGIN].logged_in
    assert logins["B7QX"].logged_in


def test_a_directory_whose_name_cannot_be_a_login_is_not_reported(tmp_path):
    """A login's name goes into a path when a session launches with it."""
    env = _machine(tmp_path, installed=True, logged_in=True)
    _another_login(env, "has space", "pro")

    assert set(_answer(env)) == {FIRST_LOGIN}


def test_probe_reads_only_the_connected_enrollment(tmp_path):
    env = _machine(tmp_path, installed=True, logged_in=True)
    _another_login(env, "private", "max")
    root = Path(env["HOME"]) / ".cheese/profiles/research"
    login = root / "logins/claude-login"
    login.mkdir(parents=True)
    (login / ".credentials.json").write_text("pro")
    env.update(CHEESE_PROFILE_ROOT=".cheese/profiles/research", PLATFORM_ROOT=str(root))

    logins = _answer(env)

    assert set(logins) == {FIRST_LOGIN}
    assert logins[FIRST_LOGIN].logged_in
    assert logins[FIRST_LOGIN].subscription_type == "pro"


def test_probe_uses_the_moved_logins_keychain_command(tmp_path):
    env = _machine(tmp_path, installed=True, logged_in=True)
    login = Path(env["FIRST_LOGIN_DIR"])
    command = login / "keychain/security"
    command.parent.mkdir()
    command.write_text(
        '#!/bin/sh\necho \'{"loggedIn":true,"authMethod":"claude.ai"}\'\n'
    )
    command.chmod(0o700)
    other = tmp_path / "other-bin"
    other.mkdir()
    (other / "security").write_text("#!/bin/sh\necho '{\"loggedIn\":false}'\n")
    (other / "security").chmod(0o700)
    env["PATH"] = f"{other}:{env['PATH']}"
    binary = Path(env["PLATFORM_ROOT"]) / "claude/versions" / CLAUDE_PINNED_VERSION
    binary.write_text(
        "#!/bin/sh\nexec security find-generic-password -w -s synthetic-service\n"
    )
    assert _ask(env).logged_in


@pytest.mark.parametrize(
    "printed",
    [
        "",
        "Traceback (most recent call last)",
        "[]",
        '{"installed": true, "status": {"loggedIn": true}}',
        '{"installed": true, "logins": [{"id": "../up", "status": {"loggedIn": true}}]}',
    ],
)
def test_an_answer_nobody_can_read_is_not_a_login(printed):
    assert not any(login.logged_in for login in owner_login.read_answer(printed))


def _one_login(status: dict) -> dict:
    """What a machine with only its first login prints."""
    return {"installed": True, "logins": [{"id": FIRST_LOGIN, "status": status}]}


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
                team_id=await enrollment_team(sql_device_service(session), owner.id),
                device_id=device_id,
                name="laptop",
                token_digest=digest_token(f"tok-{device_id}"),
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
    async with db_factory() as db:
        enrolled = await db.get(DeviceRow, device)
        db.add(
            DeviceClaudeLoginRow(
                id=device,
                device_id=device,
                team_id=enrolled.team_id,
                installed=False,
                logged_in=False,
                checked_at=datetime.now(UTC),
            )
        )
        await db.commit()
    hub = _Hub(_one_login({"loggedIn": True, "authMethod": "claude.ai"}))
    await owner_login.refresh_on_connect(db_factory, hub, device)

    hub.answer = _one_login({"loggedIn": False})
    await owner_login.refresh_on_connect(db_factory, hub, device)

    async with db_factory() as session:
        kept = await session.get(DeviceClaudeLoginRow, device)
    assert hub.asked == [device, device]
    assert kept is not None and not kept.logged_in, "the latest answer is the one kept"


async def test_a_cloud_machine_carries_no_ones_login_and_is_not_asked(db_factory):
    device = await _device(db_factory, Supply.cloud)
    hub = _Hub(_one_login({"loggedIn": True}))
    await owner_login.refresh_on_connect(db_factory, hub, device)
    assert hub.asked == []
