"""The credential a session's private scratch container is given.

The rules, as a person would state them:

- the container holds no credential (`CHEESE_TOKEN` is blank);
- the harness on the session host keeps its own copy, the one it calls the
  platform and model routes with.
"""

import pytest

from app.core.sandbox_auth import (
    bind_resource_token,
    held_by_a_machine,
    mint_scoped_token,
    scoped_token_claims,
)
from app.domain.agent.session_host.driver import start_private_executor

pytestmark = pytest.mark.anyio


class Wire:
    async def run(self, host, program, **kwargs):
        return {}


class Executor:
    def __init__(self):
        self.env = None

    def private_script(self, target, env):
        self.env = env
        return "program"


def _session_credential() -> str:
    room = mint_scoped_token(project_id="p", topic_id="t")
    return bind_resource_token(room, "resource", session_id="session")


async def test_the_container_holds_no_credential_and_the_host_keeps_its_own():
    token = _session_credential()
    host_env = {"CHEESE_TOKEN": token, "CHEESE_API": "http://api"}
    executor = Executor()

    await start_private_executor(
        Wire(), "host", executor, {"kind": "private"}, host_env, harness="codex"
    )

    assert executor.env is not None
    assert executor.env["CHEESE_TOKEN"] == ""
    assert executor.env["CHEESE_API"] == "http://api"
    own = scoped_token_claims(host_env["CHEESE_TOKEN"])
    assert own is not None and not held_by_a_machine(own)
